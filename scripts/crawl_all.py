#!/usr/bin/env python3
"""전체 골프장 홈페이지를 돌며 티타임을 수집한다.

골프장마다 홈페이지에 직접 들어가 예약 화면을 읽는다.
결과는 스냅샷으로 저장되고, 검색은 그 스냅샷을 읽는다.

    python3 scripts/crawl_all.py                      # 오늘부터 7일치
    python3 scripts/crawl_all.py --days 14
    python3 scripts/crawl_all.py --date 2026-10-15    # 특정 날짜만
    python3 scripts/crawl_all.py --region 경기,충북    # 지역 한정
    python3 scripts/crawl_all.py --limit 20           # 20곳만 (시험용)
    python3 scripts/crawl_all.py --retry-failed       # 실패했던 곳도 다시
    python3 scripts/crawl_all.py --report             # 크롤링하지 않고 현황만

수백 곳을 도는 작업이라 시간이 걸립니다. 처음에는 --limit 20 으로
몇 곳만 시험해 보고 늘리세요.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from golf import snapshot                                        # noqa: E402
from golf.courses import DEFAULT_PATH as COURSES_DEFAULT         # noqa: E402
from golf.courses import CourseBook                              # noqa: E402
from golf.profiles import (STATUS_EMPTY, STATUS_ERROR, STATUS_JS,  # noqa: E402
                           STATUS_LOGIN, STATUS_NO_LINK, STATUS_NO_SITE,
                           STATUS_OK, ProfileStore)
from golf.sources.site_crawler import SiteCrawler                # noqa: E402

STATUS_LABEL = {
    STATUS_OK: "수집 성공",
    STATUS_EMPTY: "매물 없음",
    STATUS_LOGIN: "로그인 필요",
    STATUS_JS: "자바스크립트 화면",
    STATUS_NO_LINK: "예약 링크 못 찾음",
    STATUS_NO_SITE: "홈페이지 주소 없음",
    STATUS_ERROR: "접속 오류",
}


def report(store: ProfileStore, book: CourseBook) -> int:
    """크롤링하지 않고 현재 상태만 정리해 보여 준다."""
    total = len(book)
    with_home = sum(1 for c in book.courses if c.homepage)
    print(f"골프장 DB {total}곳 중 홈페이지 주소가 있는 곳 {with_home}곳")
    if total and with_home < total:
        print(f"  {total - with_home}곳은 홈페이지를 몰라 수집할 수 없습니다.")
        print("  data/golf/courses.csv 의 homepage 칸을 채우면 대상에 들어갑니다.")

    if not len(store):
        print("\n아직 크롤링 기록이 없습니다. python3 scripts/crawl_all.py 를 실행하세요.")
        return 0

    print(f"\n크롤링 기록 {len(store)}곳")
    summary = store.summary()
    for status, n in sorted(summary.items(), key=lambda kv: kv[1], reverse=True):
        print(f"  {STATUS_LABEL.get(status, status):20s} {n:5d}곳")

    working = store.working()
    if working:
        print(f"\n수집되는 골프장 {len(working)}곳 (최근 수집량 순)")
        for p in sorted(working, key=lambda x: x.last_count, reverse=True)[:15]:
            print(f"  {p.course_name[:20]:20s} {p.last_count:4d}건  {p.booking_url[:55]}")

    groups = store.tech_groups()
    multi = [g for g in groups if g[1] >= 2]
    if multi:
        print(f"\n같은 예약 솔루션을 쓰는 골프장 묶음 (한 곳을 풀면 나머지도 풀립니다)")
        for tech, n, names in multi[:10]:
            print(f"  {tech[:45]:45s} {n:3d}곳")
            print(f"      {', '.join(names[:6])}{' ...' if len(names) > 6 else ''}")

    # 로그인 벽이 많은지 알려 준다. 이 프로그램의 한계를 솔직히 보여 주는 숫자다.
    login_n = summary.get(STATUS_LOGIN, 0)
    if login_n:
        print(f"\n{login_n}곳은 로그인해야 티타임이 보여 수집 대상에서 제외됩니다.")
        print("  이런 곳은 플랫폼(엑스골프/카카오골프예약/골팡)으로 보는 편이 낫습니다:")
        print("    python3 scripts/setup_sites.py")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="전체 골프장 홈페이지 크롤링")
    ap.add_argument("--days", type=int, default=7, help="오늘부터 며칠치 (기본 7)")
    ap.add_argument("--date", help="특정 날짜만 (예: 2026-10-15)")
    ap.add_argument("--region", help="지역 한정. 콤마 구분")
    ap.add_argument("--limit", type=int, help="골프장 수 제한 (시험용)")
    ap.add_argument("--workers", type=int, default=6,
                    help="동시 처리 수 (기본 6). 너무 높이지 마세요")
    ap.add_argument("--delay", type=float, default=1.5,
                    help="한 사이트 안에서의 요청 간격(초)")
    ap.add_argument("--retry-failed", action="store_true",
                    help="실패로 기록된 곳도 다시 시도")
    ap.add_argument("--report", action="store_true", help="크롤링하지 않고 현황만")
    ap.add_argument("--courses", default=COURSES_DEFAULT)
    ap.add_argument("--profiles", help="크롤링 프로필 파일 경로")
    ap.add_argument("--snapshot-dir", help="스냅샷 저장 폴더")
    ap.add_argument("--no-save", action="store_true", help="스냅샷을 저장하지 않는다")
    args = ap.parse_args()

    book = CourseBook.load(args.courses)
    if not len(book):
        print(f"골프장 DB가 비어 있습니다: {args.courses}")
        print("먼저 실행하세요: python3 scripts/fetch_golf_courses.py")
        return 1

    store = ProfileStore.load(args.profiles) if args.profiles else ProfileStore.load()
    snap_dir = args.snapshot_dir or snapshot.SNAPSHOT_DIR

    if args.report:
        return report(store, book)

    # 대상 고르기
    targets = [c for c in book.courses if c.homepage]
    if args.region:
        wanted = [r.strip() for r in args.region.split(",") if r.strip()]
        targets = [c for c in targets if any(r in c.region for r in wanted)]
    if not args.retry_failed:
        targets = [c for c in targets if not store.get(c.course_id, c.name).should_skip()]
    if args.limit:
        targets = targets[: args.limit]

    if not targets:
        print("수집할 골프장이 없습니다.")
        no_home = sum(1 for c in book.courses if not c.homepage)
        if no_home:
            print(f"  골프장 {len(book)}곳 중 {no_home}곳이 홈페이지 주소가 없습니다.")
            print("  data/golf/courses.csv 의 homepage 칸을 채워 주세요.")
            print("  OpenStreetMap에 website 태그가 있는 곳만 자동으로 채워집니다.")
        if not args.retry_failed and len(store):
            print("  이전에 실패한 곳을 다시 시도하려면 --retry-failed 를 붙이세요.")
        return 1

    # 날짜
    if args.date:
        from golf.models import parse_date
        d = parse_date(args.date)
        if d is None:
            print(f"날짜를 이해할 수 없습니다: {args.date}")
            return 1
        dates = [d]
    else:
        today = date.today()
        dates = [today + timedelta(days=i) for i in range(args.days)]

    print(f"골프장 {len(targets)}곳 × 날짜 {len(dates)}일치를 수집합니다")
    print(f"  동시 {args.workers}곳, 사이트당 요청 간격 {args.delay}초")
    print(f"  예상 소요: 최소 {len(targets) * args.delay / args.workers / 60:.0f}분\n")

    lock = threading.Lock()
    done = [0]
    all_rows = []
    started = time.time()

    def work(course):
        # 골프장마다 크롤러를 따로 둔다. 프로필 저장소는 공유하되 락으로 보호한다.
        crawler = SiteCrawler([course], store, delay_seconds=args.delay,
                              retry_failed=args.retry_failed)
        try:
            rows = crawler.crawl_course(course, dates)
        except Exception as exc:
            with lock:
                store.get(course.course_id, course.name).record_failure(
                    STATUS_ERROR, f"예외: {exc}")
            rows = []
        with lock:
            done[0] += 1
            status = store.get(course.course_id).status
            label = STATUS_LABEL.get(status, status)
            elapsed = time.time() - started
            rate = done[0] / elapsed if elapsed else 0
            eta = (len(targets) - done[0]) / rate if rate else 0
            print(f"  [{done[0]:4d}/{len(targets)}] {course.name[:18]:18s} "
                  f"{len(rows):4d}건  {label:18s} (남은 시간 약 {eta/60:.0f}분)")
            all_rows.extend(rows)
        return rows

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(work, c) for c in targets]
            for _ in as_completed(futures):
                pass
    except KeyboardInterrupt:
        print("\n중단했습니다. 여기까지의 결과를 저장합니다.")

    store.save()
    elapsed = time.time() - started

    print(f"\n{'=' * 60}")
    print(f"  {len(all_rows):,}건 수집  ({elapsed/60:.1f}분 소요)")
    print(f"{'=' * 60}")

    counts: dict[str, int] = {}
    for c in targets:
        st = store.get(c.course_id).status
        counts[st] = counts.get(st, 0) + 1
    for status, n in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        print(f"  {STATUS_LABEL.get(status, status):20s} {n:5d}곳")

    if not args.no_save and all_rows:
        prev_path = snapshot.previous_path(snap_dir)
        prev_rows, _ = snapshot.load(prev_path) if prev_path else ([], {})

        path = snapshot.save(all_rows, directory=snap_dir,
                             stats={"counts": counts,
                                    "courses": len(targets),
                                    "elapsed_sec": round(elapsed, 1)})
        print(f"\n저장: {path}")

        if prev_rows:
            changes = snapshot.diff(prev_rows, all_rows)
            print(f"\n이전 수집({os.path.basename(prev_path)}) 대비 변동")
            print(snapshot.format_changes(changes))

    print("\n이제 검색할 수 있습니다:")
    print("  python3 golf_web.py --snapshot")
    print("  python3 golf_cli.py --snapshot --from 37.4979,127.0276 --max-drive 90")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
