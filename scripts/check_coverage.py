#!/usr/bin/env python3
"""수집한 티타임의 골프장 이름이 좌표 DB에 몇 개나 잡히는지 확인한다.

이동시간으로 거르려면 골프장 좌표가 있어야 한다. 좌표를 못 찾은 골프장의
티타임은 **검색 결과에서 통째로 빠진다.** 조용히 사라지는 쪽이라, 수집 뒤
반드시 이걸로 확인한다.

    python3 scripts/check_coverage.py                       # 최근 스냅샷 기준
    python3 scripts/check_coverage.py reports/golfpang.csv  # 특정 CSV 기준
    python3 scripts/check_coverage.py --alias-template      # 못 찾은 것 채울 틀 출력

못 찾은 이름은 data/golf/courses.csv 의 aliases 칸에 넣으면 다음부터 잡힌다.
"""

from __future__ import annotations

import argparse
import difflib
import os
import sys
from collections import Counter

SHAKY_RATIO = 0.45      # 이보다 이름이 다르면 사람이 한 번 봐야 한다

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from golf import snapshot                                    # noqa: E402
from golf.courses import DEFAULT_PATH as COURSES_DEFAULT     # noqa: E402
from golf.courses import CourseBook                          # noqa: E402
from golf.models import name_variants                        # noqa: E402
from golf.sources.csv_source import CsvSource                # noqa: E402


def load_tee_times(path: str):
    if path:
        src = CsvSource(path, source_id="check")
        return src.fetch([])
    rows, _ = snapshot.load(snapshot.latest_path())
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(
        description="수집한 골프장 이름이 좌표 DB에 잡히는지 확인")
    ap.add_argument("csv", nargs="?", default="",
                    help="확인할 CSV (없으면 최근 스냅샷)")
    ap.add_argument("--courses", default=COURSES_DEFAULT)
    ap.add_argument("--alias-template", action="store_true",
                    help="못 찾은 이름을 aliases 칸에 넣을 틀로 출력")
    ap.add_argument("--limit", type=int, default=40,
                    help="못 찾은 이름을 몇 개까지 보여 줄지")
    args = ap.parse_args()

    book = CourseBook.load(args.courses)
    if not len(book):
        print(f"골프장 좌표 DB가 비어 있습니다: {args.courses}")
        print("\n  python3 scripts/fetch_golf_courses.py")
        print("\n좌표가 없으면 이동시간을 잴 수 없고, 출발지 기준 검색이")
        print("아예 동작하지 않습니다. 이것부터 채워야 합니다.")
        return 1

    tee_times = load_tee_times(args.csv)
    if not tee_times:
        print("확인할 티타임이 없습니다. 먼저 수집하세요:")
        print("  python3 scripts/collect_full.py golfpang --days 3")
        return 1

    counts = Counter(t.course_name for t in tee_times)
    hit, miss = {}, {}
    for name, n in counts.items():
        course = book.match(name)
        (hit if course else miss)[name] = (n, course)

    lost = sum(n for n, _ in miss.values())
    total = sum(counts.values())
    print(f"좌표 DB {len(book):,}곳 / 티타임 {total:,}건, 골프장 이름 {len(counts)}종\n")
    print(f"  좌표 찾음   {len(hit):4d}종   티타임 {total - lost:6,d}건"
          f"  ({(total - lost) / total * 100:.1f}%)")
    print(f"  못 찾음     {len(miss):4d}종   티타임 {lost:6,d}건"
          f"  ({lost / total * 100:.1f}%)  ← 출발지 검색에서 빠집니다")

    # 붙기는 붙었는데 이름이 많이 다른 것들. 틀린 좌표는 없는 것보다 나쁘다 —
    # 없으면 결과에서 빠지지만, 틀리면 엉뚱한 지역이 이동시간 안에 들어온다.
    shaky = []
    for name, (n, course) in hit.items():
        ratio = difflib.SequenceMatcher(None, name, course.name).ratio()
        if ratio < SHAKY_RATIO:
            shaky.append((ratio, name, course, n))
    if shaky:
        print(f"\n눈으로 확인이 필요한 매칭 {len(shaky)}종 (이름이 많이 다름)")
        for ratio, name, course, n in sorted(shaky)[:args.limit]:
            print(f"  {n:5,d}건  '{name}'  →  '{course.name}' ({course.region})")
        print("  틀린 것이 있으면 그 골프장의 aliases 칸을 채워 바로잡으세요.")

    if not miss:
        print("\n전부 잡힙니다. 출발지 기준 검색이 모든 티타임을 대상으로 돕니다.")
        return 0

    print(f"\n못 찾은 이름 (티타임 많은 순, {args.limit}개까지)")
    for name, (n, _) in sorted(miss.items(), key=lambda kv: -kv[1][0])[:args.limit]:
        print(f"  {n:5,d}건  {name}")
        print(f"          찾아본 이름: {', '.join(name_variants(name))}")

    if args.alias_template:
        print("\n" + "=" * 64)
        print("  아래를 data/golf/courses.csv 의 해당 골프장 aliases 칸에 넣으세요")
        print("  (여러 개는 | 로 구분합니다)")
        print("=" * 64)
        for name in sorted(miss):
            print(name)
    else:
        print("\n이 이름들을 data/golf/courses.csv 의 aliases 칸에 넣으면 다음부터 잡힙니다.")
        print("틀로 뽑으려면: python3 scripts/check_coverage.py --alias-template")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
