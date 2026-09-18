#!/usr/bin/env python3
"""예약 사이트에서 날짜를 눌러 가며 자동으로 티타임을 모은다.

카카오골프예약처럼 날짜를 클릭해야 목록이 바뀌는 사이트를 위한 것이다.
주소만 주면 브라우저가 열려서 날짜를 차례로 눌러 가며 모아 온다.

    python3 scripts/auto_collect.py "https://golf.kakao.com/..." --days 7
    python3 scripts/auto_collect.py "주소" --dates 2026-09-20,2026-09-21
    python3 scripts/auto_collect.py "주소" --show        # 브라우저를 보면서
    python3 scripts/auto_collect.py "주소" --connect http://localhost:9222
    python3 scripts/auto_collect.py "주소" --search 검색   # 날짜 고른 뒤 누를 버튼

날짜를 누른 뒤 목록이 실제로 바뀌었는지 확인합니다. 바뀌지 않으면 그 날짜는
건너뜁니다. 확인하지 않으면 같은 목록을 여러 날짜 것으로 저장하게 됩니다.

처음에는 --show --days 2 로 몇 개만 시험해 보세요. 브라우저가 무엇을 누르는지
눈으로 볼 수 있습니다.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from golf import snapshot                                      # noqa: E402
from golf.courses import DEFAULT_PATH as COURSES_DEFAULT       # noqa: E402
from golf.courses import CourseBook                            # noqa: E402
from golf.models import parse_date                             # noqa: E402
from golf.sources.browser_source import (BrowserSource,        # noqa: E402
                                         playwright_available,
                                         resolve_browser)
from golf.sources.csv_source import CsvSource                  # noqa: E402


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
    ap = argparse.ArgumentParser(
        description="날짜를 눌러 가며 티타임을 자동으로 모은다")
    ap.add_argument("url", help="티타임 목록이 나오는 주소")
    ap.add_argument("--days", type=int, default=7, help="오늘부터 며칠치 (기본 7)")
    ap.add_argument("--start", help="시작 날짜 (기본 오늘)")
    ap.add_argument("--dates", help="날짜를 직접 지정. 콤마 구분")
    ap.add_argument("--source", default="kakao", help="이 수집에 붙일 이름")
    ap.add_argument("--show", action="store_true",
                    help="브라우저 창을 보면서 진행 (처음에는 이걸 권합니다)")
    ap.add_argument("--browser", default="", help="쓸 브라우저 이름이나 경로")
    ap.add_argument("--connect", default="",
                    help="이미 열어 둔 브라우저에 붙기 (예: http://localhost:9222)")
    ap.add_argument("--search", default="",
                    help="날짜를 고른 뒤 눌러야 하는 버튼 글자 (예: 검색)")
    ap.add_argument("--wait", type=int, default=4000,
                    help="화면이 그려질 때까지 기다릴 시간(밀리초)")
    ap.add_argument("--scrolls", type=int, default=3, help="아래로 내려 볼 횟수")
    ap.add_argument("--save", help="결과를 CSV로 저장할 경로")
    ap.add_argument("--no-snapshot", action="store_true",
                    help="수집 결과에 저장하지 않는다")
    ap.add_argument("--courses", default=COURSES_DEFAULT)
    args = ap.parse_args()

    # 주소부터 확인한다. 안내문의 예시 글자를 그대로 넣는 일이 흔한데,
    # 그대로 진행하면 0건이 나와 원인을 찾기 어려워진다.
    if not args.url.startswith(("http://", "https://")):
        print(f"주소가 아닙니다: {args.url!r}")
        print("\n  http:// 또는 https:// 로 시작하는 실제 주소가 필요합니다.")
        print("\n  주소를 얻는 방법:")
        print("    1) 브라우저에서 예약 사이트를 엽니다")
        print("    2) 날짜·지역을 골라 티타임이 줄줄이 보이는 화면을 만듭니다")
        print("    3) 주소창의 주소를 복사해 따옴표 안에 넣습니다")
        print('\n       python3 scripts/auto_collect.py "https://golf.kakao.com/..." --show')
        print("\n  주소를 복사하기 번거롭다면, 보고 있는 화면을 그대로 읽는 방법도 있습니다:")
        print('\n       open -a "Aside" --args --remote-debugging-port=9222')
        print("       python3 scripts/grab.py")
        return 1

    if not playwright_available():
        print("브라우저 기능에는 Playwright 가 필요합니다:")
        print("    pip3 install playwright")
        print("    python3 -m playwright install chromium")
        return 1

    exe = ""
    if args.browser:
        exe = resolve_browser(args.browser)
        if not exe:
            print(f"'{args.browser}' 브라우저를 찾지 못했습니다.")
            print("설치된 목록: python3 scripts/login.py --list-browsers")
            return 1

    dates = parse_dates(args)
    print(f"{args.url}")
    print(f"날짜 {len(dates)}일치를 모읍니다: "
          f"{dates[0]} ~ {dates[-1]}" if len(dates) > 1 else f"날짜: {dates[0]}")
    if args.connect:
        print(f"이미 열어 둔 브라우저에 붙습니다: {args.connect}")
    elif args.show:
        print("브라우저 창을 띄웁니다. 무엇을 누르는지 보실 수 있습니다.")
    print()

    src = BrowserSource(args.url, source_id=args.source, name=args.source,
                        headless=not args.show, wait_ms=args.wait,
                        scrolls=args.scrolls, executable_path=exe,
                        cdp_url=args.connect)

    def progress(d: date, result):
        n = len(result.tee_times)
        mark = f"{n:4d}건" if n else "   -  "
        print(f"  {d}  {mark}  {result.reason[:74]}")

    results = src.collect_dates(dates, search_text=args.search,
                                on_progress=progress)

    all_rows = []
    for d in dates:
        r = results.get(d)
        if r:
            all_rows.extend(r.tee_times)

    print(f"\n{'=' * 60}")
    print(f"  모두 {len(all_rows)}건")
    print(f"{'=' * 60}")

    if not all_rows:
        print("\n티타임을 찾지 못했습니다. 확인해 볼 것:")
        print("  - 그 주소가 티타임이 나열되는 화면인가요?")
        print("  - --show 를 붙여 브라우저를 보면서 무엇이 일어나는지 확인해 보세요")
        print("  - 날짜를 고른 뒤 '검색' 버튼을 눌러야 한다면 --search 검색")
        print("  - 로그인이 필요하면: python3 scripts/login.py <사이트>")
        print("  - 직접 화면을 만들어 읽히려면: python3 scripts/grab.py")

        for d in dates:
            r = results.get(d)
            if r and r.date_controls:
                print(f"\n  화면에서 찾은 날짜 관련 요소 ({d} 시도 중):")
                for line in r.date_controls[:8]:
                    print(f"    {line}")
                break
        return 1

    # 어느 날짜가 비었는지 알려 준다
    empty = [d for d in dates if not (results.get(d) and results[d].tee_times)]
    if empty:
        print(f"\n  비어 있는 날짜 {len(empty)}일: "
              f"{', '.join(str(d) for d in empty[:7])}")
        print("  그 날짜에 매물이 없거나, 날짜를 누르지 못했을 수 있습니다.")

    book = CourseBook.load(args.courses)
    unmatched = set()
    if len(book):
        for t in all_rows:
            if not book.match(t.course_name):
                unmatched.add(t.course_name)

    print(f"\n  앞부분 미리보기")
    for t in all_rows[:10]:
        fee = f"{t.green_fee:,}원" if t.green_fee >= 0 else "가격미상"
        mark = "  ← DB에 없음" if t.course_name in unmatched else ""
        print(f"    {t.course_name[:18]:18s} {t.play_date} {t.tee_time:%H:%M} "
              f"{fee:>12s}{mark}")
    if len(all_rows) > 10:
        print(f"    ... 외 {len(all_rows) - 10}건")

    if unmatched:
        print(f"\n  골프장 DB와 매칭 안 된 이름 {len(unmatched)}종: "
              f"{', '.join(sorted(unmatched)[:8])}")
        print("  data/golf/courses.csv 의 aliases 칸에 넣으면 다음부터 잡힙니다.")
    if not len(book):
        print("\n  골프장 DB가 비어 있어 이동시간을 계산할 수 없습니다.")
        print("  python3 scripts/fetch_golf_courses.py 를 실행하세요.")

    # 목록 API 를 찾았다면 알려 준다. 이후에는 브라우저 없이 수집할 수 있다.
    apis = [a for r in results.values() for a in (r.apis if r else [])]
    if apis:
        best = max(apis, key=lambda a: a.tee_count)
        print(f"\n  목록 API 를 찾았습니다: {best.url[:72]}")
        print("  이걸 설정에 넣으면 브라우저 없이 훨씬 빠르게 수집합니다:")
        print(f"    python3 scripts/setup_sites.py {args.source} --connect "
              f"{args.connect or 'http://localhost:9222'}")

    if args.save:
        CsvSource.write(args.save, all_rows)
        print(f"\n저장: {args.save}")

    if not args.no_snapshot:
        existing, _ = snapshot.load(snapshot.latest_path())
        keep = [t for t in existing if t.source != args.source]
        merged = keep + all_rows
        path = snapshot.save(merged, stats={"source": args.source,
                                            "collected": len(all_rows)})
        print(f"\n수집 결과에 저장했습니다: {path} (전체 {len(merged)}건)")
        print("\n이제 검색할 수 있습니다:")
        print("  python3 golf_web.py --snapshot")
        print("  python3 golf_cli.py --snapshot --from 37.4979,127.0276 \\")
        print("      --time 11:00-15:00 --max-price 150000 --max-drive 90")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
