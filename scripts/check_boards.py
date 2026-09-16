#!/usr/bin/env python3
"""국내 연구기관 게시판에 '전날' 올라온 자료 제목을 메일로 보낸다. (로컬 실행용)

기본 사용
  export GMAIL_ADDRESS=you@gmail.com
  export GMAIL_APP_PASSWORD=앱비밀번호16자리
  export ALERT_TO=you@gmail.com            # 여러 명이면 콤마로 구분
  python3 scripts/check_boards.py

새 게시판을 추가할 때 (셀렉터 확인)
  python3 scripts/check_boards.py --probe "https://www.example.re.kr/board/list.do"

메일 없이 결과만 보기
  python3 scripts/check_boards.py --dry-run --days 7 --no-state
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boarddigest.config import ConfigError, enabled_sites, load_config  # noqa: E402
from boarddigest.dates import format_kr, yesterday_kst  # noqa: E402
from boarddigest.fetch import FetchError, fetch_text  # noqa: E402
from boarddigest.mail import build_subject, build_text, send_email  # noqa: E402
from boarddigest.parse import parse_board, parse_feed  # noqa: E402
from boarddigest.runner import commit_state, run_digest  # noqa: E402
from boarddigest.state import FileStore, MemoryStore  # noqa: E402

DEFAULT_STATE = Path(__file__).resolve().parent.parent / "data" / "seen_board_items.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="연구기관 게시판 전날 신규 자료 알림",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", help="사이트 설정 파일 경로 (기본: 저장소 루트 sites.json)")
    parser.add_argument("--site", action="append", dest="sites", metavar="ID",
                        help="특정 사이트만 조회 (여러 번 지정 가능)")
    parser.add_argument("--days", type=int, default=1,
                        help="조회 기간(일). 1이면 전날 하루 (기본값: 1)")
    parser.add_argument("--date", dest="end_date", metavar="YYYY-MM-DD",
                        help="기준 날짜를 직접 지정 (기본: 어제)")
    parser.add_argument("--dry-run", action="store_true", help="메일을 보내지 않고 결과만 출력")
    parser.add_argument("--no-state", action="store_true",
                        help="중복 발송 방지 기록을 무시(테스트용)")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE), help="상태 파일 경로")
    parser.add_argument("--list", action="store_true", dest="list_sites",
                        help="설정된 사이트 목록만 출력")
    parser.add_argument("--probe", metavar="URL_또는_사이트ID",
                        help="게시판 구조를 분석해 추천 셀렉터와 샘플을 출력")
    parser.add_argument("--html-file", help="--probe와 함께: 저장해둔 HTML 파일로 분석")
    return parser.parse_args(argv)


def cmd_list(config: dict) -> int:
    if not config["sites"]:
        print("설정된 사이트가 없습니다. sites.json의 sites 배열에 게시판을 추가하세요.")
        return 1
    print(f"설정 파일: {config['source']}")
    for site in config["sites"]:
        mark = "○" if site.get("enabled", True) else "×"
        mode = "자동탐지" if not site.get("row_selector") else "셀렉터"
        print(f"  {mark} {site['id']:<24} {site['name']}  [{site.get('type')}/{mode}]")
        print(f"      {site['list_url']}")
    return 0


def cmd_probe(config: dict, target: str, html_file: str | None) -> int:
    site = next((s for s in config["sites"] if s["id"] == target), None)
    cfg = dict(site) if site else {"id": "probe", "name": "probe", "list_url": target}
    url = cfg["list_url"]

    if html_file:
        text = Path(html_file).read_text(encoding="utf-8", errors="replace")
        print(f"[분석] 로컬 파일: {html_file}")
    else:
        print(f"[분석] {url}")
        try:
            text = fetch_text(
                url,
                params=cfg.get("params") or None,
                headers=cfg.get("headers"),
                encoding=cfg.get("encoding"),
                timeout=int(cfg.get("timeout", 20)),
                data=cfg.get("data") or None,
            )
        except FetchError as e:
            print(f"  수집 실패: {e}")
            print("  - 해외 IP를 차단하는 사이트일 수 있습니다(국내 PC에서 실행해 보세요).")
            return 1
        print(f"  수신 {len(text):,}자")

    # 1) 설정된 셀렉터 그대로 (있는 경우)
    if cfg.get("row_selector"):
        configured = parse_board(text, url, cfg)
        _print_result("설정된 셀렉터 기준", configured)

    # 2) 자동탐지 (셀렉터를 비운 설정으로 재분석)
    auto_cfg = {k: v for k, v in cfg.items()
                if k not in ("row_selector", "title_selector", "date_selector", "link_selector")}
    auto = parse_feed(text, auto_cfg) if cfg.get("type") == "rss" else parse_board(text, url, auto_cfg)
    _print_result("자동탐지 결과", auto)

    if auto.detected:
        suggestion = {
            "id": cfg.get("id", "새_사이트_id"),
            "name": cfg.get("name", "표시할 기관·게시판 이름"),
            "list_url": url,
            **{k: v for k, v in auto.detected.items() if v},
        }
        if auto.items and not any(i.url for i in auto.items):
            suggestion["detail_url_template"] = "https://…/view.do?no={arg0}"
        print("\n[sites.json에 붙여넣을 설정]")
        print(json.dumps(suggestion, ensure_ascii=False, indent=2))
    return 0


def _print_result(label: str, result) -> None:
    print(f"\n[{label}] 추출 {len(result.items)}건")
    by_date: dict[str, int] = {}
    for item in result.items[:15]:
        date_label = format_kr(item.posted) if item.posted else f"(날짜 미인식: {item.raw_date!r})"
        print(f"  {date_label}  {item.title[:60]}")
        print(f"      {item.url or '(링크 없음)'}")
    for item in result.items:
        key = format_kr(item.posted) if item.posted else "미인식"
        by_date[key] = by_date.get(key, 0) + 1
    if len(result.items) > 15:
        print(f"  ... 외 {len(result.items) - 15}건")
    if by_date:
        summary = ", ".join(f"{k} {v}건" for k, v in sorted(by_date.items(), reverse=True)[:8])
        print(f"  날짜 분포: {summary}")
    for warning in result.warnings:
        print(f"  ⚠ {warning}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 2

    if args.list_sites:
        return cmd_list(config)
    if args.probe:
        return cmd_probe(config, args.probe, args.html_file)

    if not enabled_sites(config, args.sites):
        print("조회할 사이트가 없습니다. sites.json에 게시판을 추가하고 enabled를 true로 두세요.")
        return 1

    end: date | None = None
    if args.end_date:
        try:
            end = datetime.strptime(args.end_date, "%Y-%m-%d").date()
        except ValueError:
            print("--date 형식은 YYYY-MM-DD 입니다.", file=sys.stderr)
            return 2

    store = MemoryStore() if args.no_state else FileStore(args.state_file)
    try:
        report = run_digest(
            config, store,
            only=args.sites, days=args.days, end=end, use_state=not args.no_state,
        )
    except ConfigError as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 2

    print(f"기준일: {report.target_label} (어제 = {format_kr(yesterday_kst())})")
    for site in report.sites:
        status = f"{len(site.items)}건" if site.ok else f"실패: {site.error}"
        print(f"  - {site.site_name}: {status} (목록에서 {site.scanned}건 확인)")
        for warning in site.warnings:
            print(f"      ⚠ {warning}")

    print("\n" + build_text(report))

    if args.dry_run:
        print("[--dry-run] 메일을 보내지 않았습니다.")
        return 0
    if not report.items and not report.failed:
        print("신규 자료가 없어 메일을 보내지 않았습니다.")
        return 0

    gmail_address = os.environ.get("GMAIL_ADDRESS")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_address or not gmail_password:
        print("GMAIL_ADDRESS / GMAIL_APP_PASSWORD 환경변수가 필요합니다.", file=sys.stderr)
        return 2
    to_addrs = [a.strip() for a in (os.environ.get("ALERT_TO") or gmail_address).split(",") if a.strip()]

    subject = send_email(
        report,
        gmail_address=gmail_address,
        gmail_app_password=gmail_password,
        to_addrs=to_addrs,
        subject=build_subject(report),
    )
    if not args.no_state:
        commit_state(store, report)  # 발송 성공 후에만 '보냄'으로 기록
    print(f"메일 발송 완료: {subject} -> {', '.join(to_addrs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
