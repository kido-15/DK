"""출발지 → 골프장 이동 시간 계산.

제공자를 여러 개 두고 순서대로 시도한다.
  kakao : 카카오모빌리티 길찾기 (키 필요, 국내 도로 기준 가장 정확)
  ors   : OpenRouteService (무료 키 필요)
  osrm  : 공개 OSRM 데모 서버 (키 불필요, 속도 제한 있음)
  estimate : 직선거리 × 우회계수 ÷ 평균속도 (네트워크 불필요, 항상 성공)

어떤 제공자도 응답하지 않으면 estimate가 받아내므로 검색은 절대 멈추지 않는다.
결과에는 어떤 방식으로 계산했는지가 항상 함께 담긴다.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from .geo import USER_AGENT, haversine_km

# 직선거리를 실제 도로거리로 바꿀 때 쓰는 우회계수.
#
# 2026-09-19 에 강남역 기준 골프장 35곳을 OSRM 실제 경로와 맞대어 재 본 값이다
# (도로거리/직선거리 평균 1.267, 중앙값 1.256). 측정 범위는 36~113km 이다.
DETOUR_FACTOR = 1.26

# 도로거리별 평균 주행속도 (km, km/h). 사이는 직선으로 이어 쓴다.
#
# 멀수록 고속도로 비중이 높아 빨라진다. 고정 속도를 쓰면 가까운 곳은 너무
# 느리게, 먼 곳은 너무 빠르게 잡힌다. "90분 이내" 로 거를 때 그 어긋남이
# 그대로 결과에 나온다 — 실제로는 더 가까운 곳이 잘리고, 실제로는 90분을
# 넘는 곳이 통과한다.
#
# 36km 아래는 재 보지 못했다. 시내 구간 비중이 크므로 낮게 두었으나 **측정값이
# 아니다.** 그 거리대의 결과는 더 어긋날 수 있다.
_SPEED_BY_KM = [
    (10, 30.0),      # 측정 안 됨 (시내)
    (25, 48.0),      # 측정 안 됨 (36km 측정값에서 내려 잡음)
    (39, 58.9),      # 이하 측정값
    (55, 64.7),
    (68, 65.9),
    (90, 65.9),
    (105, 73.3),
    (130, 77.3),
]


def _speed_kmh(road_km: float) -> float:
    """그 거리에서 기대할 만한 평균 주행속도."""
    if road_km <= _SPEED_BY_KM[0][0]:
        return _SPEED_BY_KM[0][1]
    if road_km >= _SPEED_BY_KM[-1][0]:
        return _SPEED_BY_KM[-1][1]
    for (k0, v0), (k1, v1) in zip(_SPEED_BY_KM, _SPEED_BY_KM[1:]):
        if k0 <= road_km <= k1:
            ratio = (road_km - k0) / (k1 - k0)
            return v0 + (v1 - v0) * ratio
    return _SPEED_BY_KM[-1][1]


@dataclass
class RouteInfo:
    distance_km: float
    duration_min: float
    provider: str


def _http_json(url: str, *, headers=None, data=None, timeout: int = 20):
    req = urllib.request.Request(
        url,
        data=data,
        headers={"User-Agent": USER_AGENT, **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# 제공자별 구현
# ---------------------------------------------------------------------------


def _estimate(o_lat, o_lon, d_lat, d_lon, **_) -> RouteInfo:
    """네트워크 없이 추정. 거리에 따라 평균 주행속도를 다르게 적용한다.

    가까울수록 시내 구간 비중이 높아 느리고, 멀수록 고속도로 비중이 높아 빠르다.
    속도표는 실제 경로와 맞대어 재 본 값이다(위 _SPEED_BY_KM 참고).

    어디까지나 추정이다. 실제 도로 기준이 필요하면 길찾기 API 를 쓰는 쪽이 맞고,
    결과에는 어느 방식으로 계산했는지가 늘 함께 담긴다(route_provider).
    """
    straight = haversine_km(o_lat, o_lon, d_lat, d_lon)
    road_km = straight * DETOUR_FACTOR
    kmh = _speed_kmh(road_km)

    return RouteInfo(
        distance_km=road_km,
        duration_min=road_km / kmh * 60.0,
        provider="estimate",
    )


def _osrm(o_lat, o_lon, d_lat, d_lon, *, base_url: str = "", **_) -> Optional[RouteInfo]:
    """OSRM. 좌표 순서가 경도,위도인 점에 주의."""
    base = (base_url or "https://router.project-osrm.org").rstrip("/")
    url = (
        f"{base}/route/v1/driving/"
        f"{o_lon:.6f},{o_lat:.6f};{d_lon:.6f},{d_lat:.6f}"
        "?overview=false&alternatives=false"
    )
    data = _http_json(url)
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    r = data["routes"][0]
    return RouteInfo(
        distance_km=r["distance"] / 1000.0,
        duration_min=r["duration"] / 60.0,
        provider="osrm",
    )


def _ors(o_lat, o_lon, d_lat, d_lon, *, api_key: str = "", **_) -> Optional[RouteInfo]:
    """OpenRouteService. 무료 키로 하루 2,000건 정도 호출할 수 있다."""
    if not api_key:
        return None
    url = "https://api.openrouteservice.org/v2/directions/driving-car"
    body = json.dumps({"coordinates": [[o_lon, o_lat], [d_lon, d_lat]]}).encode("utf-8")
    data = _http_json(
        url,
        headers={"Authorization": api_key, "Content-Type": "application/json"},
        data=body,
    )
    routes = data.get("routes") or []
    if not routes:
        return None
    summary = routes[0].get("summary") or {}
    if "distance" not in summary or "duration" not in summary:
        return None
    return RouteInfo(
        distance_km=summary["distance"] / 1000.0,
        duration_min=summary["duration"] / 60.0,
        provider="ors",
    )


def _kakao(o_lat, o_lon, d_lat, d_lon, *, api_key: str = "", **_) -> Optional[RouteInfo]:
    """카카오모빌리티 길찾기. origin/destination은 경도,위도 순서."""
    if not api_key:
        return None
    url = "https://apis-navi.kakaomobility.com/v1/directions?" + urllib.parse.urlencode(
        {
            "origin": f"{o_lon},{o_lat}",
            "destination": f"{d_lon},{d_lat}",
            "priority": "RECOMMEND",
            "car_fuel": "GASOLINE",
        }
    )
    data = _http_json(url, headers={"Authorization": f"KakaoAK {api_key}"})
    routes = data.get("routes") or []
    if not routes or routes[0].get("result_code") != 0:
        return None
    summary = routes[0].get("summary") or {}
    if "distance" not in summary or "duration" not in summary:
        return None
    return RouteInfo(
        distance_km=summary["distance"] / 1000.0,
        duration_min=summary["duration"] / 60.0,
        provider="kakao",
    )


_PROVIDERS = {
    "kakao": _kakao,
    "ors": _ors,
    "osrm": _osrm,
    "estimate": _estimate,
}


class Router:
    """이동시간 계산기. 제공자를 순서대로 시도하고 결과를 캐시한다.

    한 제공자가 연속으로 실패하면 그 제공자는 이번 실행에서 더 시도하지 않는다.
    (수백 개 골프장을 도는 동안 죽은 API를 계속 두드리지 않기 위해)
    """

    FAILURE_LIMIT = 3

    def __init__(
        self,
        providers: Optional[list[str]] = None,
        *,
        kakao_key: Optional[str] = None,
        ors_key: Optional[str] = None,
        osrm_base_url: str = "",
        cache: Optional[dict] = None,
    ):
        if providers is None:
            providers = ["kakao", "ors", "osrm", "estimate"]
        # estimate는 최후의 보루이므로 목록에 없으면 강제로 붙인다.
        if "estimate" not in providers:
            providers = [*providers, "estimate"]

        unknown = [p for p in providers if p not in _PROVIDERS]
        if unknown:
            raise ValueError(f"알 수 없는 길찾기 제공자: {', '.join(unknown)}")

        self.providers = providers
        self.kakao_key = kakao_key or os.environ.get("KAKAO_REST_API_KEY", "")
        self.ors_key = ors_key or os.environ.get("ORS_API_KEY", "")
        self.osrm_base_url = osrm_base_url or os.environ.get("OSRM_BASE_URL", "")
        self.cache: dict[tuple, RouteInfo] = cache if cache is not None else {}
        self._failures: dict[str, int] = {}

    def _disabled(self, name: str) -> bool:
        return self._failures.get(name, 0) >= self.FAILURE_LIMIT

    def route(self, o_lat: float, o_lon: float, d_lat: float, d_lon: float) -> RouteInfo:
        """이동 정보를 반환한다. 절대 예외를 던지지 않는다."""
        key = (round(o_lat, 4), round(o_lon, 4), round(d_lat, 4), round(d_lon, 4))
        if key in self.cache:
            return self.cache[key]

        result: Optional[RouteInfo] = None
        for name in self.providers:
            if name != "estimate" and self._disabled(name):
                continue
            fn = _PROVIDERS[name]
            try:
                result = fn(
                    o_lat,
                    o_lon,
                    d_lat,
                    d_lon,
                    api_key=self.kakao_key if name == "kakao" else self.ors_key,
                    base_url=self.osrm_base_url,
                )
            except Exception:
                result = None
            if result is not None:
                break
            self._failures[name] = self._failures.get(name, 0) + 1

        if result is None:                      # 모든 제공자가 실패한 경우
            result = _estimate(o_lat, o_lon, d_lat, d_lon)

        self.cache[key] = result
        return result

    def status(self) -> dict[str, str]:
        """제공자별 상태 요약 (대시보드에 표시하기 위한 것)."""
        out = {}
        for name in self.providers:
            if name == "estimate":
                out[name] = "사용 가능 (추정)"
            elif name == "kakao" and not self.kakao_key:
                out[name] = "키 없음"
            elif name == "ors" and not self.ors_key:
                out[name] = "키 없음"
            elif self._disabled(name):
                out[name] = "응답 없음 — 이번 실행에서 제외"
            else:
                out[name] = "사용 가능"
        return out
