#!/usr/bin/env python3
"""설정에 적힌 목록 주소에서 티타임을 **전량** 모은다.

브라우저로 화면을 읽는 방식(auto_collect.py)은 화면에 보이는 한 페이지만
가져온다. 골팡은 하루에만 9천 건이 넘는데 한 페이지가 100건이다.
이 스크립트는 목록 주소를 직접 불러 마지막 페이지까지 넘긴다.

    python3 scripts/collect_full.py golfpang --days 7
    python3 scripts/collect_full.py golfpang --dates 2026-09-19,2026-09-20
    python3 scripts/collect_full.py golfpang --days 3 --max-pages 5   # 먼저 조금만
    python3 scripts/collect_full.py golfpang --dry-run               # 1페이지만 확인

설정은 config/sources.json 을 먼저 보고, 없으면 config/sources.<이름>.json 을 본다.
골팡 설정(config/sources.golfpang.json)은 저장소에 들어 있다.

처음 쓸 때는 --dry-run 으로 한 페이지만 받아 칸이 제대로 잡히는지 보세요.
전량은 요청이 수백 번이라 몇 분 걸립니다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from golf import snapshot                                      # noqa: E402
from golf.courses import DEFAULT_PATH as COURSES_DEFAULT       # noqa: E402
from golf.courses import CourseBook                            # noqa: E402
from golf.models import parse_date                             # noqa: E402
from golf.sources.csv_source import CsvSource                  # noqa: E402
from golf.sources.web_source import WebSource                  # noqa: E402

CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")


def load_config(source_id: str, explicit: str = "") -> dict:
    """그 사이트의 설정 하나를 찾아 온다."""
    candidates = [explicit] if explicit else [
        os.path.join(CONFIG_DIR, "sources.json"),
        os.path.join(CONFIG_DIR, f"sources.{source_id}.json"),
    ]
    tried = []
    for path in candidates:
        if not path or not os.path.exists(path):
            tried.append(path)
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        configs = data.get("sources") if isinstance(data, dict) else data
        for c in configs or []:
            if isinstance(c, dict) and c.get("id") == source_id:
                return c
        tried.append(f"{path} (그 안에 '{source_id}' 가 없음)")
    print(f"'{source_id}' 설정을 찾지 못했습니다. 찾아본 곳:")
    for t in tried:
        print(f"  - {t}")
    print("\n설정을 새로 만들려면 config/sources.example.json 을 참고하세요.")
    raise SystemExit(1)


def parse_dates(args) -> list[date]:
    if args.dates:
        out = []
        for raw in args.dates.split(","):
            d = parse_date(raw.strip())
            if d is None:
                print(f"날짜를 이해할 수 없습니다: {raw}")
                raise SystemExit(1)
            out.append(d)
        return out
    start = parse_date(args.start) if args.start else date.today()
    if start is None:
        print(f"시작 날짜를 이해할 수 없습니다: {args.start}")
        raise SystemExit(1)
    return [start + timedelta(days=i) for i in range(args.days)]


def main() -> int:
    ap = argparse.ArgumentParser(description="목록 주소에서 티타임을 전량 모은다")
    ap.add_argument("source", help="사이트 이름 (예: golfpang)")
    ap.add_argument("--days", type=int, default=7, help="오늘부터 며칠치 (기본 7)")
    ap.add_argument("--start", help="시작 날짜 (기본 오늘)")
    ap.add_argument("--dates", help="날짜를 직접 지정: 2026-09-19,2026-09-20")
    ap.add_argument("--config", default="", help="설정 파일을 직접 지정")
    ap.add_argument("--max-pages", type=int, default=0,
                    help="날짜당 최대 페이지 수 (시험용으로 줄일 때)")
    ap.add_argument("--delay", type=float, default=0.0,
                    help="요청 간격(초). 설정값보다 줄이지는 않습니다")
    ap.add_argument("--dry-run", action="store_true",
                    help="첫 날짜 1페이지만 받아 칸이 제대로 잡히는지 확인")
    ap.add_argument("--save", help="결과를 CSV 로 저장할 경로")
    ap.add_argument("--no-snapshot", action="store_true",
                    help="검색용 스냅샷에 저장하지 않음")
    ap.add_argument("--courses", default=COURSES_DEFAULT)
    args = ap.parse_args()

    cfg = load_config(args.source, args.config)
    dates = parse_dates(args)
    req = cfg.setdefault("request", {})

    if args.dry_run:
        dates = dates[:1]
        req.setdefault("pages", {})["max"] = 1
        req["max_requests"] = 1
    elif args.max_pages:
        req.setdefault("pages", {})["max"] = args.max_pages

    # 간격은 늘릴 수만 있게 한다. 설정에 적어 둔 예의를 명령줄로 깎지 않기 위해서다.
    if args.delay:
        req["delay_seconds"] = max(args.delay, float(req.get("delay_seconds", 0)))

    src = WebSource(cfg)
    pages_cfg = req.get("pages") or {}
    print(f"{cfg.get('name') or args.source} — {req.get('url', '')}")
    print(f"날짜 {len(dates)}일치: {dates[0]} ~ {dates[-1]}"
          f"  (날짜당 최대 {pages_cfg.get('max', 1)}페이지,"
          f" 요청 간격 {req.get('delay_seconds', 1.0)}초)")
    if src.respect_robots:
        print("robots.txt 를 확인하고 막힌 주소는 건너뜁니다.")
    print()

    started = time.time()
    per_date: Counter = Counter()

    def progress(d: date, page: int, rows: int, total: int):
        per_date[d] += rows
        elapsed = int(time.time() - started)
        tail = "  (빈 페이지 — 여기서 멈춤)" if not rows else ""
        print(f"  {d}  {page:3d}페이지  {rows:4d}건   누적 {total:6,d}건"
              f"   {elapsed // 60}분{elapsed % 60:02d}초{tail}")

    rows = src.fetch(dates, on_progress=progress)
    stats = src.last_stats

    print(f"\n{'=' * 64}")
    print(f"  모두 {len(rows):,}건   요청 {stats.get('requests', 0)}회"
          f"   {int(time.time() - started)}초")
    print(f"{'=' * 64}")

    for line in stats.get("stopped") or []:
        print(f"  멈춤: {line}")
    for err in (stats.get("errors") or [])[:5]:
        print(f"  오류: {err}")

    # 중간에 끊긴 날짜가 있으면 건수만 보고 다 받았다고 믿으면 안 된다.
    # 요청 한 번이 실패해도 그 날짜만 조용히 덜 받은 채 끝나기 때문이다.
    incomplete = stats.get("incomplete") or []
    if incomplete:
        print("\n  ⚠ 끝까지 받지 못한 날짜가 있습니다. 아래 건수는 전량이 아닙니다.")
        for line in incomplete:
            print(f"    - {line}")

    if not rows:
        print("\n티타임을 받지 못했습니다. 확인해 볼 것:")
        print("  - --dry-run 으로 한 페이지만 받아 보세요")
        print("  - list_selector 와 fields 의 선택자가 맞는지")
        print("  - 그 사이트가 이 환경에서 열리는지 (프록시가 막을 수 있습니다)")
        return 1

    # 요청한 날짜와 다른 날짜의 행이 섞였는지 본다. 목록이 아직 안 바뀐 채로
    # 읽혔을 때 이렇게 되는데, 조용히 틀리는 종류라 반드시 알려야 한다.
    asked = set(dates)
    strays = [t for t in rows if t.play_date not in asked]
    if strays:
        got = Counter(str(t.play_date) for t in strays)
        print(f"\n  ⚠ 요청하지 않은 날짜의 행 {len(strays)}건: "
              f"{', '.join(f'{k} {v}건' for k, v in got.most_common(5))}")
        print("  목록이 아직 안 바뀐 채로 읽혔을 수 있습니다. 확인이 필요합니다.")

    print("\n  날짜별")
    for d in dates:
        n = sum(1 for t in rows if t.play_date == d)
        print(f"    {d}   {n:6,d}건")

    fees = [t.green_fee for t in rows if t.green_fee >= 0]
    if fees:
        fees.sort()
        mid = fees[len(fees) // 2]
        print(f"\n  그린피  최저 {fees[0]:,}원 / 중앙값 {mid:,}원 / 최고 {fees[-1]:,}원")
    missing = len(rows) - len(fees)
    if missing:
        print(f"  가격이 적혀 있지 않은 건 {missing}건 (사이트에 '가격문의' 로 표시된 것)")

    courses = Counter(t.course_name for t in rows)
    print(f"  골프장 {len(courses)}곳")

    book = CourseBook.load(args.courses)
    if len(book):
        unmatched = sorted({n for n in courses if not book.match(n)})
        if unmatched:
            print(f"\n  골프장 DB와 매칭 안 된 이름 {len(unmatched)}종: "
                  f"{', '.join(unmatched[:8])}")
            print("  data/golf/courses.csv 의 aliases 칸에 넣으면 다음부터 잡힙니다.")
    else:
        print("\n  골프장 DB가 비어 있어 이동시간을 계산할 수 없습니다.")
        print("  python3 scripts/fetch_golf_courses.py 를 실행하세요.")

    print("\n  앞부분 미리보기")
    for t in rows[:8]:
        fee = f"{t.green_fee:,}원" if t.green_fee >= 0 else "가격미상"
        print(f"    {t.course_name[:18]:18s} {t.play_date} {t.tee_time:%H:%M} "
              f"{fee:>12s}")
    if len(rows) > 8:
        print(f"    ... 외 {len(rows) - 8:,}건")

    if args.save:
        CsvSource.write(args.save, rows)
        print(f"\n저장: {args.save}")

    if args.dry_run:
        print("\n--dry-run 이라 저장하지 않았습니다. 칸이 제대로 잡혔으면 빼고 다시 실행하세요.")
        return 0

    if incomplete:
        print("\n  ⚠ 위 날짜는 다시 받아야 합니다:")
        for line in incomplete:
            day = line.split(":")[0]
            print(f"      python3 scripts/collect_full.py {args.source} --dates {day}")

    if not args.no_snapshot:
        existing, _ = snapshot.load(snapshot.latest_path())
        keep = [t for t in existing if t.source != src.id]
        merged = keep + rows
        path = snapshot.save(merged, stats={"source": src.id,
                                            "collected": len(rows),
                                            "requests": stats.get("requests", 0)})
        print(f"\n수집 결과에 저장했습니다: {path} (전체 {len(merged):,}건)")
        print("\n이제 검색할 수 있습니다:")
        print("  python3 golf_web.py --snapshot")
        print("  python3 golf_cli.py --snapshot --from 37.4979,127.0276 \\")
        print("      --time 11:00-15:00 --max-price 150000 --max-drive 90")
    return 2 if incomplete else 0


if __name__ == "__main__":
    raise SystemExit(main())
