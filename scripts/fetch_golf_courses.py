#!/usr/bin/env python3
"""OpenStreetMap에서 국내 골프장 목록과 좌표를 받아 data/golf/courses.csv를 만든다.

한 번만 실행해 두면 되고, 이후에는 검색할 때마다 이 CSV를 읽는다.
Overpass API는 무료이고 키가 필요 없다.

사용법:
    python3 scripts/fetch_golf_courses.py
    python3 scripts/fetch_golf_courses.py --reverse-geocode   # 시도 정보 보강 (느림)
    python3 scripts/fetch_golf_courses.py --merge             # 기존 별칭을 보존하며 갱신
    python3 scripts/fetch_golf_courses.py --source nominatim  # Overpass 가 막힌 망에서

수집 경로는 두 가지다.

  overpass  : 한 번의 질의로 전국 golf_course 를 통째로 받는다. 가장 정확하고 빠르다.
  nominatim : Overpass 가 막힌 망에서 쓰는 우회로. 전국을 격자로 나누어
              "[golf_course]" 범주 검색을 격자마다 돌린다. 한 번에 50건까지만
              돌려주므로 50건이 꽉 찬 칸은 네 칸으로 쪼개어 다시 본다.
              요청이 수백 건이 되므로 1.2초 간격을 지킨다 (Nominatim 이용 정책).

기본값(auto)은 Overpass 를 먼저 시도하고, 실패하면 Nominatim 격자로 넘어간다.

주의: 이 스크립트는 인터넷 연결이 필요하다. 회사망이나 해외 IP에서 막히면
국내 PC에서 실행하면 된다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from golf.courses import DEFAULT_PATH, CourseBook          # noqa: E402
from golf.geo import in_korea                              # noqa: E402
from golf.models import (Course, normalize_course_name,    # noqa: E402
                         region_hint)

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

USER_AGENT = "golf-finder/0.1 (+https://github.com/kido-15/dk)"

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


# OSM 은 전라남도와 광주광역시를 "전남광주통합특별시" 라는 한 이름으로 적어 둔다.
# 그대로 두면 두 시도가 한 덩어리가 되고, 낱말 포함으로 풀면 전남 골프장이
# 전부 "광주" 가 된다(실제로 78곳이 그렇게 잘못 붙었다).
# 아래 단계(시·군·구)를 보고 가른다 — 광주광역시는 자치구(○○구)로 나뉘고
# 전라남도는 시·군으로 나뉜다.
MERGED_REGION_NAMES = {"전남광주통합특별시"}


def normalize_region(value: str, sub: str = "") -> str:
    """시도명을 표준 표기로. sub 는 그 아래 단계(시·군·구) 이름."""
    if not value:
        return ""
    v = value.strip()

    if v in MERGED_REGION_NAMES:
        s = (sub or "").strip()
        if s in ("광주", "광주시", "광주광역시") or s.endswith("구"):
            return "광주"
        return "전남"

    if v in REGION_ALIASES:
        return REGION_ALIASES[v]
    low = v.lower()
    if low in REGION_ALIASES:
        return REGION_ALIASES[low]
    # 긴 이름부터 본다. 짧은 이름이 긴 이름 안에 우연히 들어 있는 경우를 피한다.
    for key, std in sorted(REGION_ALIASES.items(), key=lambda kv: -len(kv[0])):
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
    raise RuntimeError(
        f"Overpass API 조회에 모두 실패했습니다. 마지막 오류: {last_error}"
    )


# --------------------------------------------------------------------------
# Nominatim 격자 수집 — Overpass 가 막힌 망에서 쓰는 우회로
# --------------------------------------------------------------------------

NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"

# 남한 육지를 넉넉히 덮는 범위. 바다 칸은 0건으로 금방 끝난다.
KOREA_SCAN_BBOX = (33.0, 125.6, 38.7, 129.8)   # (min_lat, min_lon, max_lat, max_lon)
CELL_DEG = 0.5            # 처음 격자 한 칸의 크기
MIN_CELL_DEG = 0.0625     # 이보다 더 잘게 쪼개지 않는다
PAGE_LIMIT = 50           # Nominatim 이 한 번에 돌려주는 최대 건수
CAP_HINT = 45             # 이만큼 차면 잘린 것으로 보고 칸을 쪼갠다
NOMINATIM_INTERVAL = 1.2  # 이용 정책상 초당 1회. 여유를 둔다.

# leisure=golf_course 로 태깅돼 있지만 정규 코스가 아닌 것들.
# 이름만 보고 지우면 진짜 골프장을 잃을 수 있어 확실한 낱말만 쓴다.
PRACTICE_WORDS = ("연습장", "연습", "스크린", "퍼팅", "파크골프", "골프타운", "practice",
                  "driving range", "screen")

_last_call = 0.0


def _nominatim(**params):
    """Nominatim 한 번 호출. 호출 간격을 강제로 지킨다."""
    global _last_call
    wait = NOMINATIM_INTERVAL - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()

    params.setdefault("format", "jsonv2")
    url = NOMINATIM_SEARCH + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


# 이름이 딱 이것뿐이면 스크린골프 매장이다. "골프존카운티 ○○" 같은 실제
# 골프장과 섞이지 않도록 완전 일치로만 본다.
PRACTICE_EXACT = {"골프존", "골프존파크", "골프존파크 ", "kakaovx", "프렌즈스크린"}


def _is_practice(name: str, extratags: dict) -> bool:
    golf_tag = (extratags.get("golf") or "").lower()
    if golf_tag in ("driving_range", "practice", "pitch_and_putt"):
        return True
    low = (name or "").strip().lower()
    if low in {w.strip().lower() for w in PRACTICE_EXACT}:
        return True
    return any(w in low for w in PRACTICE_WORDS)


def fetch_nominatim_grid(stats: dict | None = None) -> list[dict]:
    """전국을 격자로 훑어 leisure=golf_course 를 모은다.

    Nominatim 은 한 번에 50건까지만 돌려주므로, 꽉 찬 칸은 네 칸으로 쪼개어
    다시 본다. 같은 골프장이 이웃 칸에도 걸리므로 osm_type+osm_id 로 거른다.
    """
    stats = stats if stats is not None else {}
    stats.setdefault("requests", 0)
    stats.setdefault("cells", 0)
    stats.setdefault("split", 0)
    stats.setdefault("errors", 0)
    stats.setdefault("practice_skipped", 0)

    found: dict[str, dict] = {}
    min_lat, min_lon, max_lat, max_lon = KOREA_SCAN_BBOX

    queue: list[tuple[float, float, float, float]] = []
    lat = min_lat
    while lat < max_lat:
        lon = min_lon
        while lon < max_lon:
            queue.append((lat, lon, min(lat + CELL_DEG, max_lat),
                          min(lon + CELL_DEG, max_lon)))
            lon += CELL_DEG
        lat += CELL_DEG

    print(f"  격자 {len(queue)}칸으로 시작합니다 "
          f"(한 칸 {CELL_DEG}도, 요청 간격 {NOMINATIM_INTERVAL}초)")

    while queue:
        la, lo, LA, LO = queue.pop(0)
        viewbox = f"{lo},{LA},{LO},{la}"          # left,top,right,bottom
        try:
            res = _nominatim(
                q="[golf_course]",
                viewbox=viewbox,
                bounded=1,
                limit=PAGE_LIMIT,
                addressdetails=1,
                extratags=1,
                namedetails=1,
                countrycodes="kr",      # 대마도 등 이웃 나라 골프장이 섞이는 것을 막는다
                **{"accept-language": "ko"},
            )
        except Exception as exc:
            stats["errors"] += 1
            stats["requests"] += 1
            print(f"    실패 {viewbox}: {exc}")
            continue

        stats["requests"] += 1
        stats["cells"] += 1

        for rec in res:
            if rec.get("type") != "golf_course":
                continue
            key = f"{rec.get('osm_type')}-{rec.get('osm_id')}"
            if key not in found:
                found[key] = rec

        if len(res) >= CAP_HINT and (LA - la) > MIN_CELL_DEG:
            # 잘렸다고 보고 네 칸으로 쪼갠다
            stats["split"] += 1
            mid_lat, mid_lon = (la + LA) / 2, (lo + LO) / 2
            queue.extend([
                (la, lo, mid_lat, mid_lon),
                (la, mid_lon, mid_lat, LO),
                (mid_lat, lo, LA, mid_lon),
                (mid_lat, mid_lon, LA, LO),
            ])

        if stats["cells"] % 25 == 0:
            print(f"    {stats['cells']}칸 조회 / 대기 {len(queue)}칸 "
                  f"/ 누적 {len(found)}곳")

    print(f"  격자 조회 끝: 요청 {stats['requests']}건, "
          f"쪼갠 칸 {stats['split']}개, 오류 {stats['errors']}건")
    return list(found.values())


def fetch_nominatim_names(names: list[str], stats: dict | None = None) -> list[Course]:
    """이름을 하나씩 대고 그 골프장만 찾아 온다.

    격자 훑기는 지도에 `leisure=golf_course` 로 찍힌 곳만 가져온다. 실제로는
    지도에 그렇게 안 찍혀 있어 빠지는 골프장이 많다. 예약 사이트에는 매일
    수백 건씩 올라오는데 좌표가 없어 검색에서 통째로 빠지는 곳들이다.

    이름을 직접 대고 찾으면 그런 곳도 잡힌다. 다만 **엉뚱한 곳이 잡히기 쉬워**
    검사를 붙인다.

      - 한국 안이어야 한다
      - 찾은 이름에 우리가 댄 이름이 실제로 들어 있어야 한다
      - 골프와 무관한 종류(식당·상점 등)는 버린다

    검사에 걸린 것은 버리고 그 사실을 남긴다. 좌표가 틀리면 없는 것보다 나쁘다.
    """
    # 골프장일 법한 종류만 받는다. Nominatim 은 이름이 비슷한 가게도 돌려준다.
    # "라싸" 로 찾으면 "라싸커피" 가 나오는 식이다. 그 좌표가 박히면
    # 카페까지 걸리는 거리로 골프장을 고르게 된다.
    ok_class = {"leisure", "landuse", "tourism"}
    # 종류가 달라도 이름에 골프가 들어 있으면 받는다.
    golf_words = ("골프", "cc", "gc", "컨트리", "golf", "country club")
    out: list[Course] = []
    for raw in names:
        query = _search_name(raw)
        hint = region_hint(raw)
        if not query:
            continue
        found = None
        for suffix in ("골프장", "CC", "골프클럽", ""):
            term = f"{query} {suffix}".strip()
            try:
                rows = _nominatim(q=term, countrycodes="kr", limit=5,
                                  addressdetails=1, extratags=1, namedetails=1)
            except Exception as exc:
                _note(stats, "errors", f"{raw}: {str(exc)[:60]}")
                continue
            for rec in rows:
                label = (rec.get("display_name") or rec.get("name") or "").lower()
                if ((rec.get("class") or "") not in ok_class
                        and not any(w in label for w in golf_words)):
                    continue
                course = nominatim_to_course(rec, stats)
                if course is None:
                    continue
                if not _same_place(query, course, hint):
                    _note(stats, "name_mismatch", f"{raw} → {course.name}")
                    continue
                found = course
                break
            if found:
                break
        if found:
            # 예약 사이트가 쓰는 이름으로도 찾을 수 있게 별칭에 넣는다.
            if raw not in found.aliases and raw != found.name:
                found.aliases.append(raw)
            out.append(found)
            print(f"    {raw} → {found.name} ({found.region})")
        else:
            _note(stats, "not_found", raw)
            print(f"    {raw} → 찾지 못함")
    return out


def _search_name(raw: str) -> str:
    """검색에 쓸 이름. 예약 사이트가 붙인 꼬리표를 떼되 지역은 남긴다.

    괄호를 통째로 지우면 안 된다. "포웰(안성)cc" 의 안성은 **어느 포웰인지**를
    가르는 말이다. 떼고 찾으면 경남 포웰이 나와 경기 골프장에 경남 좌표가
    박힌다. 판매 조건만 떼고 지역은 검색어에 남긴다.
    """
    hint = region_hint(raw)
    name = re.sub(r"[(\[][^)\]]*[)\]]", " ", raw or "")
    name = re.sub(r"[-–]\s*퍼\s*9|퍼블릭|비공개|병행|대중제|회원제", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    if hint and hint not in name:
        name = f"{name} {hint}".strip()
    return name


def _same_place(query: str, course: Course, hint: str = "") -> bool:
    """찾아온 것이 정말 그 이름·그 지역인지. 아니면 엉뚱한 좌표가 박힌다."""
    want = normalize_course_name(query)
    if not want:
        return False
    if hint and hint not in f"{course.name} {course.address} {course.region}":
        return False
    for candidate in [course.name, *course.aliases]:
        key = normalize_course_name(candidate)
        if key and (want in key or key in want):
            return True
    return False


def _note(stats: dict | None, key: str, value: str) -> None:
    if stats is not None:
        stats.setdefault(key, []).append(value)


def nominatim_to_course(rec: dict, stats: dict | None = None) -> Course | None:
    """Nominatim 검색 결과 한 건을 Course 로 바꾼다."""
    names = rec.get("namedetails") or {}
    extra = rec.get("extratags") or {}
    name = (names.get("name:ko") or names.get("name") or rec.get("name") or "").strip()
    if not name:
        return None

    try:
        lat, lon = float(rec["lat"]), float(rec["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not in_korea(lat, lon):
        return None

    if _is_practice(name, extra):
        if stats is not None:
            stats["practice_skipped"] = stats.get("practice_skipped", 0) + 1
        return None

    addr = rec.get("address") or {}
    if (addr.get("country_code") or "kr").lower() != "kr":
        return None                      # 이웃 나라(대마도 등) 결과는 버린다
    region = normalize_region(
        addr.get("province") or addr.get("state") or addr.get("city") or "",
        addr.get("city") or addr.get("county") or addr.get("borough") or "")
    address = " ".join(
        p for p in [
            addr.get("province", "") or addr.get("state", ""),
            addr.get("city", "") or addr.get("county", ""),
            addr.get("town", "") or addr.get("village", "") or addr.get("borough", ""),
            addr.get("road", ""),
        ] if p
    ).strip()

    holes = None
    raw_holes = extra.get("golf:holes") or extra.get("holes") or ""
    if str(raw_holes).strip().isdigit():
        holes = int(raw_holes)

    aliases = []
    for key in ("name:en", "alt_name", "official_name", "short_name", "name"):
        val = (names.get(key) or "").strip()
        if val and val != name and val not in aliases:
            aliases.append(val)

    return Course(
        course_id=f"osm-{rec.get('osm_type', 'x')}-{rec.get('osm_id')}",
        name=name,
        lat=round(lat, 6),
        lon=round(lon, 6),
        region=region,
        address=address,
        holes=holes,
        phone=(extra.get("phone") or extra.get("contact:phone") or "").strip(),
        homepage=(extra.get("website") or extra.get("contact:website")
                  or extra.get("url") or "").strip(),
        source="osm-nominatim",
        aliases=aliases,
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
        homepage=(tags.get("website") or tags.get("contact:website")
                  or tags.get("url") or "").strip(),
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


def add_by_name(names_path: str, out_path: str) -> int:
    """이름 목록으로 좌표를 찾아 기존 CSV 에 더한다."""
    with open(names_path, encoding="utf-8") as f:
        wanted = [line.strip() for line in f if line.strip()]
    if not wanted:
        print(f"이름이 없습니다: {names_path}")
        return 1

    book = CourseBook.load(out_path)
    print(f"기존 {len(book)}곳에 {len(wanted)}개 이름을 찾아 더합니다.")
    print("요청 간격을 지키므로 이름 하나에 몇 초 걸립니다.\n")

    stats: dict = {}
    found = fetch_nominatim_names(wanted, stats)

    added = 0
    for course in found:
        if book.match(course.name):
            continue                     # 이미 있는 곳
        book.add(course)
        added += 1

    book.save(out_path)
    print(f"\n{added}곳을 더했습니다 (전체 {len(book)}곳) → {out_path}")
    for key, label in (("not_found", "찾지 못함"),
                       ("name_mismatch", "이름이 달라 버림"),
                       ("errors", "오류")):
        rows = stats.get(key) or []
        if rows:
            print(f"  {label} {len(rows)}건: {', '.join(rows[:6])}")
    print("\n확인: python3 scripts/check_coverage.py")
    return 0


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
    ap.add_argument(
        "--source",
        choices=["auto", "overpass", "nominatim"],
        default="auto",
        help="auto=Overpass 먼저 시도하고 막히면 Nominatim 격자로 (기본값)",
    )
    ap.add_argument(
        "--names",
        help="이 파일에 한 줄씩 적힌 골프장 이름만 찾아 기존 CSV에 더한다. "
             "scripts/check_coverage.py 가 못 찾은 이름을 뽑아 준다.",
    )
    args = ap.parse_args()

    if args.names:
        return add_by_name(args.names, args.out)

    stats: dict = {}
    print("OpenStreetMap에서 국내 골프장을 조회합니다...")

    elements: list[dict] = []
    used = ""
    if args.source in ("auto", "overpass"):
        try:
            elements = fetch_overpass()
            used = "overpass"
        except RuntimeError as exc:
            print(f"  {exc}")
            if args.source == "overpass":
                return 1
            print("  → Nominatim 격자 수집으로 넘어갑니다 (느리지만 같은 OSM 데이터입니다)")
    if not elements:
        elements = fetch_nominatim_grid(stats)
        used = "nominatim"

    print(f"  {len(elements)}건 수신 (경로: {used})")

    to_course = element_to_course if used == "overpass" else (
        lambda el: nominatim_to_course(el, stats))

    courses: list[Course] = []
    seen_names: dict[str, Course] = {}
    for el in elements:
        c = to_course(el)
        if c is None:
            continue
        # 같은 골프장이 way와 relation으로 중복되는 경우가 있어 이름+좌표로 중복 제거
        key = (normalize_course_name(c.name), round(c.lat, 2), round(c.lon, 2))
        if key in seen_names:
            continue
        seen_names[key] = c
        courses.append(c)

    print(f"  이름과 좌표가 있는 골프장 {len(courses)}곳")
    if stats.get("practice_skipped"):
        print(f"  (연습장·스크린 등으로 보여 제외한 것 {stats['practice_skipped']}곳)")

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
            # 직접 찾아 넣은 홈페이지 주소도 보존한다 (OSM에 없는 경우가 많다)
            if match and oc.homepage and not match.homepage:
                match.homepage = oc.homepage
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
