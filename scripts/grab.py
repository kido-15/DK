#!/usr/bin/env python3
"""지금 브라우저에 떠 있는 화면을 그대로 읽어 티타임을 수집한다.

예약 사이트는 날짜·지역·시간을 드롭다운으로 고르고 검색을 눌러야 목록이 나오는
경우가 많다. 그런 화면은 주소만으로는 재현되지 않는다.

그래서 이렇게 한다.

    1. 브라우저에서 직접 로그인하고, 조건을 고르고, 검색을 누른다
    2. 티타임이 줄줄이 보이는 그 화면을 그대로 둔 채
    3. 이 명령을 실행하면 그 화면을 읽어 온다

브라우저를 원격 디버깅과 함께 띄워 두어야 한다.

    open -a "브라우저이름" --args --remote-debugging-port=9222

사용법:
    python3 scripts/grab.py                      # 열린 탭을 고르게 해 준다
    python3 scripts/grab.py --tab 2              # 2번 탭을 바로 읽는다
    python3 scripts/grab.py --list               # 열린 탭 목록만 보기
    python3 scripts/grab.py --date 2026-10-15    # 화면에 날짜가 없을 때 지정
    python3 scripts/grab.py --save 결과.csv       # CSV로 저장
    python3 scripts/grab.py --add-snapshot       # 검색에 바로 쓰이도록 저장

페이지를 새로 열거나 이동시키지 않는다. 보고 있는 화면을 그대로 읽는다.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from golf import snapshot                                      # noqa: E402
from golf.courses import DEFAULT_PATH as COURSES_DEFAULT       # noqa: E402
from golf.courses import CourseBook                            # noqa: E402
from golf.models import parse_date                             # noqa: E402
from golf.sources.browser_source import (capture_open_tab,     # noqa: E402
                                         list_open_tabs,
                                         playwright_available)
from golf.sources.csv_source import CsvSource                  # noqa: E402

DEFAULT_CDP = "http://localhost:9222"


def print_connect_help(cdp: str) -> None:
    print(f"\n  {cdp} 에 붙지 못했습니다.")
    print("\n  브라우저를 원격 디버깅과 함께 띄워야 합니다:")
    print("\n    1) 브라우저를 완전히 종료 (창만 닫지 말고 ⌘Q)")
    print("    2) 터미널에서 아래 실행 (브라우저 이름만 바꾸세요)")
    print('\n       open -a "Aside" --args --remote-debugging-port=9222')
    print('       open -a "Google Chrome" --args --remote-debugging-port=9222')
    print("\n    3) 그 브라우저에서 예약 사이트에 로그인하고")
    print("       조건을 골라 티타임이 보이는 화면을 만든 뒤")
    print("    4) 이 명령을 다시 실행하세요")
    print("\n  설치된 브라우저 보기: python3 scripts/login.py --list-browsers")


def show_tabs(tabs: list[dict]) -> None:
    print(f"\n열려 있는 탭 {len(tabs)}개\n")
    for i, t in enumerate(tabs, 1):
        print(f"  [{i}] {t['title'][:56]}")
        print(f"      {t['url'][:78]}")


def choose_tab(tabs: list[dict]) -> int:
    if len(tabs) == 1:
        return 0
    print("\n티타임이 보이는 탭의 번호를 입력하세요. (그냥 Enter 면 1번)")
    try:
        raw = input("  번호: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(130)
    if not raw:
        return 0
    if not raw.isdigit() or not (1 <= int(raw) <= len(tabs)):
        print(f"  1 부터 {len(tabs)} 사이의 번호를 넣어 주세요.")
        raise SystemExit(1)
    return int(raw) - 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="브라우저에 떠 있는 화면을 그대로 읽어 티타임을 수집")
    ap.add_argument("--connect", default=DEFAULT_CDP,
                    help=f"붙을 주소 (기본 {DEFAULT_CDP})")
    ap.add_argument("--tab", type=int, help="읽을 탭 번호 (--list 로 확인)")
    ap.add_argument("--list", action="store_true", help="열린 탭 목록만 보기")
    ap.add_argument("--date", help="화면에 날짜가 없을 때 쓸 플레이 날짜")
    ap.add_argument("--source", default="grab", help="이 수집에 붙일 이름")
    ap.add_argument("--save", help="결과를 CSV로 저장할 경로")
    ap.add_argument("--add-snapshot", action="store_true",
                    help="검색에 바로 쓰이도록 수집 결과에 저장")
    ap.add_argument("--courses", default=COURSES_DEFAULT)
    ap.add_argument("--no-scroll", action="store_true",
                    help="화면을 스크롤하지 않는다")
    args = ap.parse_args()

    if not playwright_available():
        print("브라우저 기능에는 Playwright 가 필요합니다:")
        print("    pip3 install playwright")
        print("    python3 -m playwright install chromium")
        return 1

    cdp = args.connect if args.connect.startswith("http") else f"http://{args.connect}"
    tabs = list_open_tabs(cdp)
    if not tabs:
        print_connect_help(cdp)
        return 1

    if args.list:
        show_tabs(tabs)
        print("\n읽으려면: python3 scripts/grab.py --tab <번호>")
        return 0

    show_tabs(tabs)
    if args.tab is not None:
        if not (1 <= args.tab <= len(tabs)):
            print(f"\n탭 번호는 1 부터 {len(tabs)} 사이여야 합니다.")
            return 1
        index = args.tab - 1
    else:
        index = choose_tab(tabs)

    target = tabs[index]
    print(f"\n[{index + 1}] {target['title'][:60]} 을(를) 읽습니다...")
    if not args.no_scroll:
        print("  (목록을 더 불러오려고 잠시 스크롤합니다)")

    play_date = parse_date(args.date) if args.date else None
    result = capture_open_tab(cdp, tab_url=target["url"], source_id=args.source,
                              play_date=play_date, scroll=not args.no_scroll)

    print(f"\n  {result.reason}")

    if not result.tee_times:
        print("\n티타임을 찾지 못했습니다. 확인해 볼 것:")
        print("  - 그 탭에 티타임이 실제로 나열돼 있나요? (시각과 금액이 보여야 합니다)")
        print("  - 조건을 고르고 '검색' 을 눌러 목록이 나온 상태인가요?")
        print("  - 로그인이 필요한 화면이라면 먼저 로그인해 주세요")
        print("  - 목록이 표가 아니라 그림으로 그려져 있으면 읽지 못합니다")
        return 1

    print(f"\n티타임 {len(result.tee_times)}건\n")
    book = CourseBook.load(args.courses)
    unmatched = []

    for t in result.tee_times:
        fee = f"{t.green_fee:,}원" if t.green_fee >= 0 else "가격미상"
        mark = ""
        if len(book) and not book.match(t.course_name):
            mark = "  ← 골프장 DB에 없음"
            unmatched.append(t.course_name)
        print(f"  {t.course_name[:20]:20s} {t.play_date} {t.tee_time:%H:%M} "
              f"{fee:>12s}{mark}")

    if play_date is None and any(t.play_date == date.today() for t in result.tee_times):
        print("\n  [주의] 화면에서 날짜를 찾지 못해 오늘 날짜로 넣었습니다.")
        print("  실제 플레이 날짜가 다르면 --date 2026-10-15 처럼 지정하세요.")

    if unmatched:
        uniq = sorted(set(unmatched))
        print(f"\n  골프장 DB와 매칭 안 된 이름 {len(uniq)}종: {', '.join(uniq[:8])}")
        print("  data/golf/courses.csv 의 aliases 칸에 넣으면 다음부터 잡힙니다.")
    if not len(book):
        print("\n  골프장 DB가 비어 있어 이동시간을 계산할 수 없습니다.")
        print("  python3 scripts/fetch_golf_courses.py 를 실행하세요.")

    if result.apis:
        best = max(result.apis, key=lambda a: a.tee_count)
        print(f"\n  목록 API 를 찾았습니다: {best.url[:76]}")
        print("  이 주소를 설정에 넣으면 브라우저 없이 자동으로 수집할 수 있습니다:")
        print(f"    python3 scripts/setup_sites.py <사이트> --connect {cdp}")

    if args.save:
        CsvSource.write(args.save, result.tee_times)
        print(f"\n저장: {args.save}")

    if args.add_snapshot:
        existing, _ = snapshot.load(snapshot.latest_path())
        merged = existing + result.tee_times
        path = snapshot.save(merged, stats={"source": "grab",
                                            "added": len(result.tee_times)})
        print(f"\n수집 결과에 더했습니다: {path} (전체 {len(merged)}건)")
        print("\n이제 검색할 수 있습니다:")
        print("  python3 golf_web.py --snapshot")
        print("  python3 golf_cli.py --snapshot --from 37.4979,127.0276 "
              "--time 11:00-15:00 --max-price 150000")
    else:
        print("\n검색에 쓰려면 --add-snapshot 을 붙여 다시 실행하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
