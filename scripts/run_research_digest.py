#!/usr/bin/env python3
"""
AI 연구자료 알림을 로컬 PC에서 실행한다 (국내망에서 돌려야 국내 기관 사이트가 열린다).

상태는 data/seen_research.json 에 저장한다.

사용 예:
    # 메일 보내지 않고 수집 결과만 확인
    python3 scripts/run_research_digest.py --dry-run

    # 실제 발송
    export GMAIL_ADDRESS=you@gmail.com
    export GMAIL_APP_PASSWORD=앱비밀번호16자리
    export ALERT_TO=you@gmail.com
    python3 scripts/run_research_digest.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "research"))

import collector  # noqa: E402
import digest  # noqa: E402

STATE_PATH = os.path.join(ROOT, "data", "seen_research.json")


def load_seen() -> tuple[dict, bool]:
    if not os.path.exists(STATE_PATH):
        return {}, True
    with open(STATE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        data = {k: "1970-01-01" for k in data}
    return data, len(data) == 0


def save_seen(seen: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="메일을 보내지 않고 수집 결과만 출력한다 (상태 파일도 건드리지 않음)")
    ap.add_argument("--all", action="store_true",
                    help="신규 여부와 무관하게 수집된 전체 목록을 출력한다")
    ap.add_argument("--max-age-days", type=int, default=None)
    ap.add_argument("--only", default="", help="특정 소스만 수집 (id 콤마 구분)")
    args = ap.parse_args()

    config = collector.load_config()
    if args.max_age_days is not None:
        config["max_age_days"] = args.max_age_days
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        config["sources"] = [s for s in config["sources"] if s["id"] in wanted]

    result = collector.collect(config)
    print(f"수집 {len(result.items)}건 / 실패 소스 {len(result.errors)}개\n")
    for error in result.errors:
        print(f"  [수집실패] {error}")
    if result.errors:
        print()

    seen, is_first_run = load_seen()
    new_items = result.items if args.all else digest.filter_new(result.items, seen)

    if new_items:
        print(digest.build_text(new_items, []))
    else:
        print("신규 자료 없음")

    if args.dry_run:
        print("\n(--dry-run: 메일 발송과 상태 저장을 건너뜁니다)")
        return 0

    if is_first_run:
        print("\n최초 실행이라 기준 데이터만 저장하고 메일은 보내지 않습니다.")
    elif new_items:
        gmail_addr = os.environ.get("GMAIL_ADDRESS")
        gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
        if not gmail_addr or not gmail_pass:
            print("\nGMAIL_ADDRESS / GMAIL_APP_PASSWORD 환경변수가 없어 발송을 건너뜁니다.")
            return 1
        to_addrs = [a.strip() for a in (os.environ.get("ALERT_TO") or gmail_addr).split(",") if a.strip()]
        digest.send_email(new_items, result.errors, gmail_addr, gmail_pass, to_addrs)
        print(f"\n{', '.join(to_addrs)} 로 {len(new_items)}건 발송 완료")

    save_seen(digest.update_seen(seen, result.items))
    print(f"상태 저장: {STATE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
