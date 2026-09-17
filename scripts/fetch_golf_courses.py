#!/usr/bin/env python3
"""OpenStreetMap에서 국내 골프장 목록과 좌표를 받아 data/golf/courses.csv를 만든다.

한 번만 실행해 두면 되고, 이후에는 검색할 때마다 이 CSV를 읽는다.
Overpass API는 무료이고 키가 필요 없다.

사용법:
    python3 scripts/fetch_golf_courses.py
    python3 scripts/fetch_golf_courses.py --reverse-geocode   # 시도 정보 보강 (느림)
    python3 scripts/fetch_golf_courses.py --merge             # 기존 별칭을 보존하며 갱신

주의: 이 스크립트는 인터넷 연결이 필요하다. 회사망이나 해외 IP에서 막히면
국내 PC에서 실행하면 된다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from golf.courses import DEFAULT_PATH, CourseBook          # noqa: E402
from golf.geo import in_korea                              # noqa: E402
from golf.models import Course, normalize_course_name      # noqa: E402

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

QUERY = """
[out:json][timeout:180];
area["ISO3166-1"="KR"][admin_level=2]->.kr;
(
  way["leisure"="golf_course"](area.kr);
  relation["leisure"="golf_course"](area.kr);
  node["leisure"="golf_course"](area.kr);
);
out center tags;
"""

USER_AGENT = "golf-finder/0.1 (personal use)"

# OSM 주소 태그에 한자/영문 시도명이 섞여 들어오는 경우가 있어 표준 표기로 정리한다.
REGION_ALIASES = {
    "서울": "서울", "서울특별시": "서울", "seoul": "서울",
    "부산": "부산", "부산광역시": "부산", "busan": "부산",
    "대구": "대구", "대구광역시": "대구", "daegu": "대구",
    "인천": "인천", "인천광역시": "인천", "incheon": "인천",
    "광주": "광주", "광주광역시": "광주", "gwangju": "광주",
    "대전": "대전", "대전광역시": "대전", "daejeon": "대전",
    "울산": "울산", "울산광역시": "울산", "ulsan": "울산",
    "세종": "세종", "세종특별자치시": "세종", "sejong": "세종",
    "경기": "경기", "경기도": "경기", "gyeonggi": "경기", "gyeonggi-do": "경기",
    "강원": "강원", "강원도": "강원", "강원특별자치도": "강원", "gangwon": "강원",
    "충북": "충북", "충청북도": "충북", "chungcheongbuk-do": "충북",
    "충남": "충남", "충청남도": "충남", "chungcheongnam-do": "충남",
    "전북": "전북", "전라북도": "전북", "전북특별자치도": "전북", "jeollabuk-do": "전북",
    "전남": "전남", "전라남도": "전남", "jeollanam-do": "전남",
    "경북": "경북", "경상북도": "경북", "gyeongsangbuk-do": "경북",
    "경남": "경남", "경상남도": "경남", "gyeongsangnam-do": "경남",
    "제주": "제주", "제주도": "제주", "제주특별자치도": "제주", "jeju": "제주",
}


def normalize_region(value: str) -> str:
    if not value:
        return ""
    v = value.strip()
    if v in REGION_ALIASES:
        return REGION_ALIASES[v]
    low = v.lower()
    if low in REGION_ALIASES:
        return REGION_ALIASES[low]
    for key, std in REGION_ALIASES.items():
        if key and len(key) >= 2 and key in v:
            return std
    return v


def fetch_overpass() -> list[dict]:
    """Overpass에 질의한다. 엔드포인트를 순서대로 시도한다."""
    body = urllib.parse.urlencode({"data": QUERY}).encode("utf-8")
    last_error = None
    for endpoint in OVERPASS_ENDPOINTS:
        print(f"  조회 중: {endpoint}")
        req = urllib.request.Request(
            endpoint,
            data=body,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=200) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            elements = data.get("elements") or []
            if elements:
                return elements
            last_error = "결과가 비어 있음"
        except Exception as exc:            # 네트워크/타임아웃/JSON 오류 모두 포함
            last_error = exc
            print(f"    실패: {exc}")
            time.sleep(2)
    raise SystemExit(
        f"Overpass API 조회에 모두 실패했습니다. 마지막 오류: {last_error}\n"
        "인터넷 연결(특히 회사망 방화벽)을 확인한 뒤 다시 실행해 주세요."
    )


def element_to_course(el: dict) -> Course | None:
    tags = el.get("tags") or {}
    name = (tags.get("name:ko") or tags.get("name") or "").strip()
    if not name:
        return None

    if el.get("type") == "node":
        lat, lon = el.get("lat"), el.get("lon")
    else:
        center = el.get("center") or {}
        lat, lon = center.get("lat"), center.get("lon")
    if lat is None or lon is None or not in_korea(float(lat), float(lon)):
        return None

    region = normalize_region(
        tags.get("addr:province")
        or tags.get("addr:state")
        or tags.get("addr:city")
        or ""
    )
    address = " ".join(
        p for p in [
            tags.get("addr:province", ""),
            tags.get("addr:city", ""),
            tags.get("addr:county", ""),
            tags.get("addr:town", ""),
            tags.get("addr:suburb", ""),
            tags.get("addr:street", ""),
            tags.get("addr:housenumber", ""),
        ] if p
    ).strip()

    holes = None
    raw_holes = tags.get("golf:holes") or tags.get("holes") or ""
    if str(raw_holes).strip().isdigit():
        holes = int(raw_holes)

    aliases = []
    for key in ("name:en", "alt_name", "official_name", "short_name"):
        val = (tags.get(key) or "").strip()
        if val and val != name:
            aliases.append(val)

    return Course(
        course_id=f"osm-{el.get('type','x')}-{el.get('id')}",
        name=name,
        lat=round(float(lat), 6),
        lon=round(float(lon), 6),
        region=region,
        address=address,
        holes=holes,
        phone=(tags.get("phone") or tags.get("contact:phone") or "").strip(),
        source="osm",
        aliases=aliases,
    )


def reverse_geocode_region(lat: float, lon: float) -> str:
    """Nominatim 역지오코딩으로 시도명을 얻는다. 초당 1회 제한을 지킨다."""
    url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(
        {"lat": lat, "lon": lon, "format": "json", "zoom": 8, "accept-language": "ko"}
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return ""
    addr = data.get("address") or {}
    return normalize_region(addr.get("province") or addr.get("state") or addr.get("city") or "")


def main() -> int:
    ap = argparse.ArgumentParser(description="OSM에서 국내 골프장 좌표 수집")
    ap.add_argument("--out", default=DEFAULT_PATH, help="저장할 CSV 경로")
    ap.add_argument(
        "--reverse-geocode",
        action="store_true",
        help="시도 정보가 빈 골프장을 역지오코딩으로 보강한다 (한 곳당 약 1초)",
    )
    ap.add_argument(
        "--merge",
        action="store_true",
        help="기존 CSV에 직접 추가한 골프장과 별칭(aliases)을 보존한다",
    )
    args = ap.parse_args()

    print("OpenStreetMap에서 국내 골프장을 조회합니다...")
    elements = fetch_overpass()
    print(f"  {len(elements)}건 수신")

    courses: list[Course] = []
    seen_names: dict[str, Course] = {}
    for el in elements:
        c = element_to_course(el)
        if c is None:
            continue
        # 같은 골프장이 way와 relation으로 중복되는 경우가 있어 이름+좌표로 중복 제거
        key = (normalize_course_name(c.name), round(c.lat, 2), round(c.lon, 2))
        if key in seen_names:
            continue
        seen_names[key] = c
        courses.append(c)

    print(f"  이름과 좌표가 있는 골프장 {len(courses)}곳")

    if args.reverse_geocode:
        blanks = [c for c in courses if not c.region]
        print(f"  시도 정보가 빈 {len(blanks)}곳을 역지오코딩합니다 (약 {len(blanks)}초)...")
        for i, c in enumerate(blanks, 1):
            c.region = reverse_geocode_region(c.lat, c.lon)
            time.sleep(1.1)
            if i % 25 == 0:
                print(f"    {i}/{len(blanks)}")

    book = CourseBook(courses)

    if args.merge and os.path.exists(args.out):
        old = CourseBook.load(args.out)
        new_keys = {normalize_course_name(c.name) for c in courses}
        kept = 0
        for oc in old.courses:
            # 직접 넣어 둔 별칭은 새 데이터로 옮겨 준다
            match = book.match(oc.name)
            if match and oc.aliases:
                for a in oc.aliases:
                    if a not in match.aliases:
                        match.aliases.append(a)
            # OSM에 없는 수동 등록 골프장은 그대로 남긴다
            if normalize_course_name(oc.name) not in new_keys and oc.source != "osm":
                book.add(oc)
                kept += 1
        print(f"  기존 CSV에서 수동 등록 {kept}곳과 별칭을 보존했습니다")

    n = book.save(args.out)
    print(f"저장 완료: {args.out} ({n}곳)")

    by_region: dict[str, int] = {}
    for c in book.courses:
        by_region[c.region or "(미상)"] = by_region.get(c.region or "(미상)", 0) + 1
    print("\n지역별 분포:")
    for region, cnt in sorted(by_region.items(), key=lambda kv: kv[1], reverse=True):
        print(f"  {region:10s} {cnt:4d}")

    missing = by_region.get("(미상)", 0)
    if missing:
        print(
            f"\n시도 정보가 빈 곳이 {missing}곳 있습니다. "
            "--reverse-geocode 옵션으로 보강할 수 있습니다."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
