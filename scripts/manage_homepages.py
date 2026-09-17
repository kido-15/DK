#!/usr/bin/env python3
"""골프장 홈페이지 주소를 채운다.

개별 골프장 수집은 홈페이지 주소를 알아야 시작할 수 있다.
OpenStreetMap에 website 태그가 있는 곳은 자동으로 채워지지만 그렇지 않은 곳이
많아서, 나머지를 채우는 작업이 필요하다.

    python3 scripts/manage_homepages.py --status
    python3 scripts/manage_homepages.py --export 채울목록.csv
        (엑셀에서 homepage 칸을 채운 뒤)
    python3 scripts/manage_homepages.py --import 채울목록.csv

    python3 scripts/manage_homepages.py --search          # 네이버 검색 API로 자동 찾기
    python3 scripts/manage_homepages.py --set "남서울CC=https://..."
    python3 scripts/manage_homepages.py --check           # 주소가 살아 있는지 확인

네이버 검색 API를 쓰려면 환경변수가 필요합니다 (무료, 하루 25,000건).
    export NAVER_CLIENT_ID="..."
    export NAVER_CLIENT_SECRET="..."
  발급: https://developers.naver.com/apps/#/register (검색 API 선택)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from golf.courses import DEFAULT_PATH as COURSES_DEFAULT      # noqa: E402
from golf.courses import CourseBook                           # noqa: E402
from golf.sources.web_source import HttpClient                # noqa: E402

EXPORT_COLS = ["course_id", "name", "region", "address", "phone", "homepage"]

# 검색 결과에서 골프장 공식 홈페이지가 아닌 것을 걸러낸다
_BAD_HOST = re.compile(
    r"(blog|cafe|post|news|tistory|naver\.me|instagram|facebook|youtube|"
    r"wikipedia|namu\.wiki|dcinside|clien|ppomppu|golfzon\.com/community)",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")


def cmd_status(book: CourseBook) -> int:
    total = len(book)
    filled = [c for c in book.courses if c.homepage]
    print(f"골프장 {total}곳 중 홈페이지 주소가 있는 곳 {len(filled)}곳 "
          f"({len(filled)/total*100:.0f}%)" if total else "골프장 DB가 비어 있습니다")
    if not total:
        return 1

    missing = [c for c in book.courses if not c.homepage]
    if not missing:
        print("모두 채워져 있습니다.")
        return 0

    by_region: dict[str, int] = {}
    for c in missing:
        by_region[c.region or "(미상)"] = by_region.get(c.region or "(미상)", 0) + 1
    print(f"\n비어 있는 {len(missing)}곳의 지역별 분포:")
    for region, n in sorted(by_region.items(), key=lambda kv: kv[1], reverse=True):
        print(f"  {region:10s} {n:4d}")

    print(f"\n예시 (앞 10곳):")
    for c in missing[:10]:
        print(f"  {c.name[:24]:24s} {c.region:6s} {c.address[:30]}")

    print("\n채우는 방법:")
    print("  1) 엑셀로: --export 파일.csv → 채우기 → --import 파일.csv")
    print("  2) 자동으로: --search  (네이버 검색 API 키 필요)")
    print("  3) 한 곳씩: --set \"골프장이름=https://주소\"")
    return 0


def cmd_export(book: CourseBook, path: str, only_missing: bool = True) -> int:
    rows = [c for c in book.courses if (not c.homepage) or not only_missing]
    if not rows:
        print("내보낼 것이 없습니다 (모두 채워져 있습니다).")
        return 0
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=EXPORT_COLS)
        w.writeheader()
        for c in rows:
            w.writerow({"course_id": c.course_id, "name": c.name, "region": c.region,
                        "address": c.address, "phone": c.phone, "homepage": c.homepage})
    print(f"{len(rows)}곳을 내보냈습니다: {path}")
    print("\n엑셀에서 열어 homepage 칸을 채운 뒤 저장하고 실행하세요:")
    print(f"  python3 scripts/manage_homepages.py --import {path}")
    print("\n  - 주소는 https:// 로 시작해야 합니다")
    print("  - 예약 페이지가 아니라 홈페이지 첫 화면 주소를 넣으세요")
    print("    (예약 페이지는 프로그램이 알아서 찾습니다)")
    return 0


def cmd_import(book: CourseBook, path: str, courses_path: str) -> int:
    if not os.path.exists(path):
        print(f"파일이 없습니다: {path}")
        return 1

    by_id = {c.course_id: c for c in book.courses}
    updated = skipped = bad = 0

    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            url = (row.get("homepage") or "").strip()
            if not url:
                continue
            if not url.startswith(("http://", "https://")):
                # 흔한 실수: www. 만 적은 경우는 고쳐 준다
                if url.startswith("www."):
                    url = "https://" + url
                else:
                    print(f"  건너뜀 (주소 형식 아님): {row.get('name','?')} → {url}")
                    bad += 1
                    continue

            course = by_id.get((row.get("course_id") or "").strip())
            if course is None:
                course = book.match(row.get("name") or "")
            if course is None:
                print(f"  건너뜀 (골프장을 못 찾음): {row.get('name','?')}")
                skipped += 1
                continue

            if course.homepage == url:
                continue
            course.homepage = url
            updated += 1

    book.save(courses_path)
    print(f"\n{updated}곳의 홈페이지를 채웠습니다"
          + (f" (형식 오류 {bad}곳, 매칭 실패 {skipped}곳)" if bad or skipped else ""))
    if updated:
        print("\n이제 수집할 수 있습니다:")
        print("  python3 scripts/crawl_all.py --limit 20")
    return 0


def cmd_set(book: CourseBook, pairs: list[str], courses_path: str) -> int:
    updated = 0
    for pair in pairs:
        if "=" not in pair:
            print(f"형식이 잘못됐습니다: {pair}  (골프장이름=https://주소)")
            continue
        name, _, url = pair.partition("=")
        course = book.match(name.strip())
        if course is None:
            print(f"골프장을 찾지 못했습니다: {name}")
            continue
        course.homepage = url.strip()
        print(f"  {course.name} → {url.strip()}")
        updated += 1
    if updated:
        book.save(courses_path)
        print(f"\n{updated}곳 저장했습니다.")
    return 0


def naver_search(query: str, client_id: str, secret: str) -> list[dict]:
    url = "https://openapi.naver.com/v1/search/webkr.json?" + urllib.parse.urlencode(
        {"query": query, "display": 5})
    req = urllib.request.Request(url, headers={
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": secret,
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return (json.loads(resp.read().decode("utf-8")) or {}).get("items") or []


def cmd_search(book: CourseBook, courses_path: str, limit: int = 0) -> int:
    client_id = os.environ.get("NAVER_CLIENT_ID", "")
    secret = os.environ.get("NAVER_CLIENT_SECRET", "")
    if not client_id or not secret:
        print("네이버 검색 API 키가 없습니다.")
        print("  export NAVER_CLIENT_ID=\"...\"")
        print("  export NAVER_CLIENT_SECRET=\"...\"")
        print("  발급: https://developers.naver.com/apps/#/register (검색 API 선택)")
        print("\n키 없이 채우려면 --export / --import 를 쓰세요.")
        return 1

    missing = [c for c in book.courses if not c.homepage]
    if limit:
        missing = missing[:limit]
    if not missing:
        print("채울 것이 없습니다.")
        return 0

    print(f"{len(missing)}곳을 검색합니다. 찾은 주소는 확인 후 저장됩니다.\n")
    found = 0

    for i, course in enumerate(missing, 1):
        query = f"{course.name} 골프장 홈페이지"
        try:
            items = naver_search(query, client_id, secret)
        except Exception as exc:
            print(f"  [{i}/{len(missing)}] {course.name[:20]:20s} 검색 실패: {exc}")
            time.sleep(1)
            continue

        pick = ""
        for item in items:
            link = item.get("link") or ""
            host = urllib.parse.urlsplit(link).netloc
            if not host or _BAD_HOST.search(host):
                continue
            title = _TAG_RE.sub("", item.get("title") or "")
            # 골프장 이름의 앞 두 글자가 제목에 있으면 공식 사이트로 본다
            if course.name[:2] and course.name[:2] in title:
                pick = f"{urllib.parse.urlsplit(link).scheme}://{host}/"
                break

        if pick:
            course.homepage = pick
            found += 1
            print(f"  [{i}/{len(missing)}] {course.name[:20]:20s} → {pick}")
        else:
            print(f"  [{i}/{len(missing)}] {course.name[:20]:20s} 찾지 못함")

        time.sleep(0.15)     # API 호출 간격
        if i % 50 == 0:
            book.save(courses_path)

    book.save(courses_path)
    print(f"\n{found}곳을 채웠습니다.")
    print("자동 검색 결과는 틀릴 수 있습니다. 확인하려면:")
    print("  python3 scripts/manage_homepages.py --check")
    return 0


def cmd_check(book: CourseBook, courses_path: str, clear_dead: bool = False) -> int:
    """홈페이지 주소가 실제로 열리는지 확인한다."""
    targets = [c for c in book.courses if c.homepage]
    if not targets:
        print("확인할 주소가 없습니다.")
        return 1

    print(f"{len(targets)}곳의 주소를 확인합니다...\n")
    client = HttpClient(timeout=10, retries=0)
    dead = []

    for i, course in enumerate(targets, 1):
        try:
            client.get(course.homepage)
            ok = True
        except Exception as exc:
            ok = False
            dead.append((course, str(exc)[:60]))
        mark = "정상" if ok else "실패"
        print(f"  [{i}/{len(targets)}] {course.name[:20]:20s} {mark}")
        time.sleep(0.3)

    print(f"\n정상 {len(targets)-len(dead)}곳 / 실패 {len(dead)}곳")
    if dead:
        print("\n열리지 않는 주소:")
        for course, err in dead[:20]:
            print(f"  {course.name[:20]:20s} {course.homepage[:45]}  {err}")
        if clear_dead:
            for course, _ in dead:
                course.homepage = ""
            book.save(courses_path)
            print(f"\n{len(dead)}곳의 주소를 지웠습니다.")
        else:
            print("\n지우려면 --clear-dead 를 붙여 다시 실행하세요.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="골프장 홈페이지 주소 관리")
    ap.add_argument("--courses", default=COURSES_DEFAULT)
    ap.add_argument("--status", action="store_true", help="채워진 정도 확인")
    ap.add_argument("--export", metavar="CSV", help="빈 곳을 CSV로 내보내기")
    ap.add_argument("--export-all", action="store_true", help="채워진 곳도 함께 내보내기")
    ap.add_argument("--import", dest="import_path", metavar="CSV", help="CSV에서 가져오기")
    ap.add_argument("--set", action="append", metavar="이름=주소", help="한 곳씩 지정")
    ap.add_argument("--search", action="store_true", help="네이버 검색 API로 자동 찾기")
    ap.add_argument("--search-limit", type=int, default=0, help="검색할 곳 수 제한")
    ap.add_argument("--check", action="store_true", help="주소가 열리는지 확인")
    ap.add_argument("--clear-dead", action="store_true", help="확인 후 죽은 주소 지우기")
    args = ap.parse_args()

    book = CourseBook.load(args.courses)
    if not len(book):
        print(f"골프장 DB가 비어 있습니다: {args.courses}")
        print("먼저 실행하세요: python3 scripts/fetch_golf_courses.py")
        return 1

    if args.export:
        return cmd_export(book, args.export, only_missing=not args.export_all)
    if args.import_path:
        return cmd_import(book, args.import_path, args.courses)
    if args.set:
        return cmd_set(book, args.set, args.courses)
    if args.search:
        return cmd_search(book, args.courses, args.search_limit)
    if args.check:
        return cmd_check(book, args.courses, args.clear_dead)
    return cmd_status(book)


if __name__ == "__main__":
    raise SystemExit(main())
