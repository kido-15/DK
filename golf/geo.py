"""좌표 계산과 지오코딩(주소 → 좌표).

지오코딩은 외부 API가 필요하지만, 사용자가 "37.5,127.0" 처럼 좌표를 직접
넣으면 네트워크 없이도 동작한다.
"""

from __future__ import annotations

import json
import math
import re
import time as _time
import urllib.parse
import urllib.request
from typing import Optional

USER_AGENT = "golf-finder/0.1 (personal use)"
EARTH_RADIUS_KM = 6371.0088

# 한반도 대략 범위 — 지오코딩 결과가 엉뚱한 나라로 튀는 것을 거른다.
KOREA_BBOX = (33.0, 124.0, 39.5, 132.0)  # (min_lat, min_lon, max_lat, max_lon)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 좌표 사이의 직선(대권) 거리를 km로 반환한다."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def in_korea(lat: float, lon: float) -> bool:
    min_lat, min_lon, max_lat, max_lon = KOREA_BBOX
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


_COORD_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


def parse_coords(text: str) -> Optional[tuple[float, float]]:
    """'37.4979,127.0276' 형태의 문자열을 (위도, 경도)로 바꾼다.

    한국 좌표는 위도 33~39, 경도 124~132 범위라 순서가 뒤바뀌어 들어와도
    바로잡아 준다.
    """
    if not text:
        return None
    m = _COORD_RE.match(text)
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    if in_korea(a, b):
        return (a, b)
    if in_korea(b, a):   # 경도,위도 순으로 들어온 경우
        return (b, a)
    return (a, b)


def _http_json(url: str, *, headers: Optional[dict] = None, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class Geocoder:
    """주소를 좌표로 바꾼다.

    카카오 키가 있으면 카카오를, 없으면 OpenStreetMap Nominatim을 쓴다.
    Nominatim은 초당 1회 제한이 있으므로 호출 간격을 지킨다.
    """

    def __init__(self, kakao_key: Optional[str] = None, cache: Optional[dict] = None):
        self.kakao_key = kakao_key
        self.cache: dict[str, Optional[tuple[float, float]]] = cache if cache is not None else {}
        self._last_nominatim_call = 0.0

    def geocode(self, query: str) -> Optional[tuple[float, float]]:
        """주소나 장소명을 (위도, 경도)로. 실패하면 None."""
        if not query or not query.strip():
            return None
        query = query.strip()

        coords = parse_coords(query)
        if coords:
            return coords

        if query in self.cache:
            return self.cache[query]

        result = None
        if self.kakao_key:
            result = self._kakao(query)
        if result is None:
            result = self._nominatim(query)

        self.cache[query] = result
        return result

    def _kakao(self, query: str) -> Optional[tuple[float, float]]:
        """카카오 로컬 API. 주소 검색 → 실패 시 키워드(장소명) 검색."""
        headers = {"Authorization": f"KakaoAK {self.kakao_key}"}
        endpoints = [
            "https://dapi.kakao.com/v2/local/search/address.json?",
            "https://dapi.kakao.com/v2/local/search/keyword.json?",
        ]
        for base in endpoints:
            url = base + urllib.parse.urlencode({"query": query, "size": 1})
            try:
                data = _http_json(url, headers=headers)
            except Exception:
                continue
            docs = data.get("documents") or []
            if docs:
                doc = docs[0]
                try:
                    return (float(doc["y"]), float(doc["x"]))
                except (KeyError, TypeError, ValueError):
                    continue
        return None

    def _nominatim(self, query: str) -> Optional[tuple[float, float]]:
        """OpenStreetMap Nominatim. 키가 필요 없지만 이용 정책상 초당 1회로 제한."""
        elapsed = _time.time() - self._last_nominatim_call
        if elapsed < 1.1:
            _time.sleep(1.1 - elapsed)
        self._last_nominatim_call = _time.time()

        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
            {"q": query, "format": "json", "limit": 1, "countrycodes": "kr"}
        )
        try:
            data = _http_json(url)
        except Exception:
            return None
        if not data:
            return None
        try:
            return (float(data[0]["lat"]), float(data[0]["lon"]))
        except (KeyError, IndexError, TypeError, ValueError):
            return None


# 시·군 이름. 골프장 이름 뒤 괄호에 붙는 말이 **지역 표시**인지
# **코스 구분**인지 가리는 데 쓴다.
#
#     그랜드(청주)   청주는 지명   → 충북 청주의 그랜드만 가리킨다
#     떼제베(동북)   동북은 지명 아님 → 코스 이름이므로 지역을 따지지 않는다
#
# 좌표 DB 에 나타나는 지명만으로 가리면, DB 에 그 지역 골프장이 하나도 없을 때
# 판별이 안 된다. 그때 엉뚱한 지역에 붙는 것을 막으려면 목록이 필요하다.
PLACE_NAMES = {
    # 시도
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
    "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
    # 경기
    "수원", "성남", "의정부", "안양", "부천", "광명", "평택", "동두천", "안산",
    "고양", "과천", "구리", "남양주", "오산", "시흥", "군포", "의왕", "하남",
    "용인", "파주", "이천", "안성", "김포", "화성", "광주시", "양주", "포천",
    "여주", "연천", "가평", "양평",
    # 강원
    "춘천", "원주", "강릉", "동해", "태백", "속초", "삼척", "홍천", "횡성",
    "영월", "평창", "정선", "철원", "화천", "양구", "인제", "고성", "양양",
    # 충북·충남
    "청주", "충주", "제천", "보은", "옥천", "영동", "증평", "진천", "괴산",
    "음성", "단양", "천안", "공주", "보령", "아산", "서산", "논산", "계룡",
    "당진", "금산", "부여", "서천", "청양", "홍성", "예산", "태안",
    # 전북·전남
    "전주", "군산", "익산", "정읍", "남원", "김제", "완주", "진안", "무주",
    "장수", "임실", "순창", "고창", "부안", "목포", "여수", "순천", "나주",
    "광양", "담양", "곡성", "구례", "고흥", "보성", "화순", "장흥", "강진",
    "해남", "영암", "무안", "함평", "영광", "장성", "완도", "진도", "신안",
    # 경북·경남
    "포항", "경주", "김천", "안동", "구미", "영주", "영천", "상주", "문경",
    "경산", "군위", "의성", "청송", "영양", "영덕", "청도", "고령", "성주",
    "칠곡", "예천", "봉화", "울진", "울릉", "창원", "진주", "통영", "사천",
    "김해", "밀양", "거제", "양산", "의령", "함안", "창녕", "남해", "하동",
    "산청", "함양", "거창", "합천",
    # 제주
    "제주시", "서귀포",
}
