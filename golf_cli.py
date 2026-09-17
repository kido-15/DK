#!/usr/bin/env python3
"""골프장 티타임 검색 — 터미널 버전.

    python3 golf_cli.py --from "서울 강남구 테헤란로 152" --time 06:00-09:00 --max-price 200000
    python3 golf_cli.py --from 37.4979,127.0276 --date 2026-09-20 --max-drive 90
    python3 golf_cli.py --test-source site-a      # 소스 설정이 맞는지 확인
    python3 golf_cli.py --list-sources
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

from golf.courses import DEFAULT_PATH as COURSES_DEFAULT
from golf.courses import CourseBook
from golf.geo import Geocoder
from golf.models import SearchQuery, parse_date, parse_time
from golf.routing import Router
from golf.search import GolfSearch
from golf.sources import CsvSource, SnapshotSource
from golf.sources.web_source import DEFAULT_CONFIG_PATH as SOURCES_DEFAULT
from golf.sources.web_source import load_sources


def parse_time_range(text: str):
    """'06:00-09:00' 또는 '06:00~09:00' 을 (시작, 끝)으로."""
    if not text:
        return None, None
    for sep in ("-", "~", "–"):
        if sep in text:
            a, _, b = text.partition(sep)
            return parse_time(a), parse_time(b)
    return parse_time(text), None


def cmd_list_sources(args) -> int:
    sources = load_sources(args.sources or SOURCES_DEFAULT, only_enabled=False)
    if not sources:
        print(f"설정된 소스가 없습니다: {args.sources or SOURCES_DEFAULT}")
        print("config/sources.example.json 을 복사해 sources.json 을 만들고,")
        print("scripts/probe_source.py 로 셀렉터를 찾아 채워 넣으세요.")
        return 1
    print(f"{'ID':16s} {'상태':8s} {'형식':6s} 이름")
    print("-" * 60)
    for s in sources:
        print(f"{s.id:16s} {'사용' if s.enabled else '중지':8s} {s.format:6s} {s.name}")
    return 0


def cmd_test_source(args) -> int:
    """소스 하나를 실제로 호출해 몇 건이 나오는지 보여 준다."""
    sources = load_sources(args.sources or SOURCES_DEFAULT, only_enabled=False)
    target = next((s for s in sources if s.id == args.test_source), None)
    if target is None:
        print(f"'{args.test_source}' 소스를 찾을 수 없습니다.")
        return cmd_list_sources(args)

    d = parse_date(args.date) or (date.today() + timedelta(days=7))
    print(f"소스 '{target.name}' ({target.id}) 를 {d} 기준으로 시험 호출합니다...\n")
    rows = target.fetch([d])

    stats = target.last_stats or {}
    print(f"요청 {stats.get('requests', 0)}회 → 티타임 {len(rows)}건")
    for err in (stats.get("errors") or [])[:5]:
        print(f"  오류: {err}")
    if target.last_error and not (stats.get("errors") or []):
        print(f"  오류: {target.last_error}")

    if not rows:
        print("\n결과가 0건입니다. 확인해 볼 것:")
        print("  1. request.url 이 브라우저에서 실제로 목록을 보여 주는 주소인가")
        print("  2. list_selector / records_path 가 맞는가")
        print("  3. 목록이 자바스크립트로 그려진다면 XHR 주소를 대신 써야 합니다")
        print(f"\n  python3 scripts/probe_source.py '<주소>' --id {target.id}")
        return 1

    print()
    book = CourseBook.load(args.courses or COURSES_DEFAULT)
    for r in rows[:15]:
        match = book.match(r.course_name) if len(book) else None
        flag = "" if match else "  ← 골프장 DB 매칭 실패"
        print(f"  {r.course_name:20s} {r.play_date} {r.tee_time:%H:%M} "
              f"{r.green_fee:>9,}원{flag}")
    if len(rows) > 15:
        print(f"  ... 외 {len(rows) - 15}건")

    if len(book):
        unmatched = sum(1 for r in rows if not book.match(r.course_name))
        if unmatched:
            print(f"\n{unmatched}건이 골프장 DB와 매칭되지 않았습니다.")
            print("data/golf/courses.csv 의 aliases 칸에 해당 이름을 넣어 주세요.")
    else:
        print("\n골프장 DB가 비어 있습니다. scripts/fetch_golf_courses.py 를 실행하세요.")
    return 0


def cmd_search(args) -> int:
    book = CourseBook.load(args.courses or COURSES_DEFAULT)
    if not len(book):
        print("골프장 DB가 비어 있습니다. 먼저 실행하세요:")
        print("  python3 scripts/fetch_golf_courses.py")
        return 1

    sources = []
    if args.snapshot:
        # 개별 골프장 홈페이지에서 모아 둔 결과만 본다
        sources.append(SnapshotSource(args.snapshot_path))
    else:
        sources.extend(load_sources(args.sources or SOURCES_DEFAULT))
        if args.with_snapshot:
            sources.append(SnapshotSource(args.snapshot_path))
    if args.teetimes:
        sources.append(CsvSource(args.teetimes))
    if not sources:
        print("사용 가능한 소스가 없습니다.")
        print("  아래 중 하나를 하세요:")
        print("    python3 scripts/setup_sites.py     (엑스골프/카카오/골팡 연결)")
        print("    python3 scripts/crawl_all.py       (골프장 홈페이지 직접 수집)")
        print("    --teetimes 로 CSV 지정")
        return 1

    geocoder = Geocoder(kakao_key=os.environ.get("KAKAO_REST_API_KEY", ""))
    coords = geocoder.geocode(args.origin)
    if coords is None:
        print(f"'{args.origin}' 의 좌표를 찾지 못했습니다.")
        print("  주소를 더 구체적으로 적거나 '37.4979,127.0276' 처럼 좌표를 직접 넣어 보세요.")
        return 1

    tee_from, tee_to = parse_time_range(args.time or "")
    providers = [p.strip() for p in (args.routing or "").split(",") if p.strip()] or None

    q = SearchQuery(
        origin=args.origin,
        origin_lat=coords[0],
        origin_lon=coords[1],
        play_date=parse_date(args.date),
        tee_from=tee_from,
        tee_to=tee_to,
        max_drive_minutes=args.max_drive,
        max_price=args.max_price,
        min_price=args.min_price,
        include_unknown_price=args.include_unknown_price,
        regions=[r.strip() for r in (args.region or "").split(",") if r.strip()],
        sort=args.sort,
        limit=args.limit,
    )

    print(f"검색 조건 — {q.describe()}")
    print(f"출발 좌표 — {coords[0]:.5f}, {coords[1]:.5f}\n")

    engine = GolfSearch(book, sources, Router(providers=providers))
    results, stats = engine.search(q)

    if not results:
        print("조건에 맞는 티타임이 없습니다.")
        print(f"  수집 {stats.fetched} → 조건필터 {stats.after_basic} "
              f"→ 거리필터 {stats.after_prefilter} → 최종 0")
        for sid, err in (stats.source_errors or {}).items():
            print(f"  [{sid}] {err}")
        return 0

    header = (f"{'골프장':<18} {'날짜':<11} {'티오프':<7} {'그린피':>10} "
              f"{'이동':>7} {'거리':>8}  소스")
    print(header)
    print("-" * len(header))
    for r in results:
        d = r.to_dict()
        drive = f"{d['drive_minutes']}분" if d["drive_minutes"] is not None else "-"
        dist = f"{d['distance_km']}km" if d["distance_km"] is not None else "-"
        fee = f"{d['green_fee']:,}" if d["green_fee"] >= 0 else "-"
        print(f"{d['display_name'][:18]:<18} {d['play_date']:<11} {d['tee_time']:<7} "
              f"{fee:>10} {drive:>7} {dist:>8}  {d['source']}")

    print(f"\n{len(results)}건 (수집 {stats.fetched} → 조건 {stats.after_basic} "
          f"→ 거리 {stats.after_prefilter} → 최종 {stats.final}, {stats.elapsed_sec}초)")

    ways = ", ".join(f"{k} {v}건" for k, v in (stats.route_providers or {}).items())
    if ways:
        print(f"이동시간 계산: {ways}")
    if "estimate" in (stats.route_providers or {}):
        print("  ※ estimate는 직선거리 기반 추정값입니다. 실제 도로 소요시간과 다를 수 있습니다.")
    if stats.unmatched:
        print(f"골프장 DB 매칭 실패 {stats.unmatched}건: "
              f"{', '.join(stats.unmatched_names[:8])}")

    if args.save:
        from golf.sources.csv_source import CsvSource as _C
        _C.write(args.save, [r.tee_time for r in results])
        print(f"\n결과 저장: {args.save}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="출발지·시간·가격으로 예약 가능한 골프장 티타임을 찾는다",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--from", dest="origin", help="출발 위치 (주소 또는 '위도,경도')")
    ap.add_argument("--date", help="플레이 날짜 (예: 2026-09-20)")
    ap.add_argument("--time", help="희망 티오프 시간대 (예: 06:00-09:00)")
    ap.add_argument("--max-drive", type=int, help="편도 이동 시간 상한 (분)")
    ap.add_argument("--max-price", type=int, help="1인 그린피 상한 (원)")
    ap.add_argument("--min-price", type=int, help="1인 그린피 하한 (원)")
    ap.add_argument("--include-unknown-price", action="store_true",
                    help="가격이 안 적힌 티타임도 결과에 포함")
    ap.add_argument("--region", help="지역 필터. 콤마 구분 (예: 경기,충북)")
    ap.add_argument("--sort", default="score",
                    choices=["score", "price", "drive", "tee_time"], help="정렬 기준")
    ap.add_argument("--limit", type=int, default=50, help="최대 결과 수")
    ap.add_argument("--routing", default="", help="길찾기 제공자 순서 (예: kakao,osrm,estimate)")
    ap.add_argument("--courses", help="골프장 CSV 경로")
    ap.add_argument("--sources", help="소스 설정 JSON 경로")
    ap.add_argument("--teetimes", help="티타임 CSV를 소스로 추가")
    ap.add_argument("--snapshot", action="store_true",
                    help="골프장 홈페이지에서 모아 둔 결과로 검색 (crawl_all.py 실행 필요)")
    ap.add_argument("--with-snapshot", action="store_true",
                    help="플랫폼 소스에 더해 수집 결과도 함께 본다")
    ap.add_argument("--snapshot-path", help="스냅샷 파일 경로 (기본: 최신)")
    ap.add_argument("--save", help="검색 결과를 CSV로 저장할 경로")
    ap.add_argument("--list-sources", action="store_true", help="설정된 소스 목록 보기")
    ap.add_argument("--test-source", help="소스 하나를 시험 호출해 본다")
    args = ap.parse_args()

    if args.list_sources:
        return cmd_list_sources(args)
    if args.test_source:
        return cmd_test_source(args)
    if not args.origin:
        ap.print_help()
        print("\n출발 위치(--from)가 필요합니다.")
        return 1
    return cmd_search(args)


if __name__ == "__main__":
    raise SystemExit(main())
