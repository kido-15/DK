"""검색 엔진: 조건에 맞는 티타임을 골라 순위를 매긴다.

흐름
  1. 소스들에서 티타임을 모은다
  2. 골프장 이름을 마스터 DB와 대조해 좌표를 붙인다
  3. 가격·시간·날짜로 먼저 거른다 (싼 연산)
  4. 직선거리로 한 번 더 거른다 (길찾기 API 호출을 줄이기 위한 핵심 단계)
  5. 남은 것만 길찾기 API로 실제 이동시간을 구한다
  6. 점수를 매겨 정렬한다
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Optional

from .courses import CourseBook
from .geo import haversine_km
from .models import SearchQuery, SearchResult, TeeTime
from .routing import Router

# 4단계 프리필터에서 쓰는 가정 최고 평균속도(km/h).
# 이보다 빠를 수는 없다고 보고 "이동시간 상한 → 직선거리 상한"을 계산한다.
# 넉넉하게 잡아야 실제로 갈 수 있는 곳을 잘라내지 않는다.
MAX_PLAUSIBLE_KMH = 110.0
# 직선거리를 도로거리로 볼 때의 최소 우회계수 (가장 곧게 뚫린 경우)
MIN_DETOUR_FACTOR = 1.05


@dataclass
class SearchStats:
    """검색 과정에서 무엇이 얼마나 걸러졌는지. 대시보드에 그대로 보여 준다."""

    fetched: int = 0            # 소스에서 받은 원본 티타임 수
    matched: int = 0            # 골프장 좌표 매칭 성공
    unmatched: int = 0          # 매칭 실패 (이동시간 계산 불가)
    after_basic: int = 0        # 날짜/시간/가격 필터 통과
    after_prefilter: int = 0    # 직선거리 프리필터 통과
    routed: int = 0             # 길찾기 API를 실제로 호출한 수
    duplicates: int = 0         # 똑같아서 걸러낸 중복 수
    final: int = 0              # 최종 결과 수
    source_errors: dict = field(default_factory=dict)
    unmatched_names: list = field(default_factory=list)
    route_providers: dict = field(default_factory=dict)
    elapsed_sec: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetched": self.fetched,
            "matched": self.matched,
            "unmatched": self.unmatched,
            "after_basic": self.after_basic,
            "after_prefilter": self.after_prefilter,
            "routed": self.routed,
            "duplicates": self.duplicates,
            "final": self.final,
            "source_errors": self.source_errors,
            "unmatched_names": self.unmatched_names[:20],
            "route_providers": self.route_providers,
            "elapsed_sec": round(self.elapsed_sec, 2),
        }


class GolfSearch:
    def __init__(self, book: CourseBook, sources: list, router: Optional[Router] = None):
        self.book = book
        self.sources = sources
        self.router = router or Router()

    # -- 수집 ---------------------------------------------------------------

    def collect(self, dates: list[date], stats: SearchStats) -> list[TeeTime]:
        """모든 소스에서 티타임을 모은다. 한 소스가 실패해도 나머지는 계속한다."""
        out: list[TeeTime] = []
        for src in self.sources:
            try:
                rows = src.fetch(dates)
            except Exception as exc:
                stats.source_errors[getattr(src, "id", "?")] = f"예외: {exc}"
                continue
            err = getattr(src, "last_error", "")
            if err:
                stats.source_errors[src.id] = err
            out.extend(rows)
        return out

    # -- 검색 ---------------------------------------------------------------

    def search(self, q: SearchQuery) -> tuple[list[SearchResult], SearchStats]:
        started = datetime.now()
        stats = SearchStats()

        dates = [q.play_date] if q.play_date else []
        tee_times = self.collect(dates, stats)
        stats.fetched = len(tee_times)

        # 완전히 같은 티타임은 하나만 남긴다. 설정이 잘못돼 같은 페이지를
        # 여러 번 받아 오는 경우가 있고, 그대로 두면 결과가 부풀려진다.
        # 예약처가 다르면(source) 각각 보여 준다.
        tee_times = _dedupe(tee_times)
        stats.duplicates = stats.fetched - len(tee_times)

        # 2단계: 골프장 매칭
        for t in tee_times:
            t.course = self.book.match(t.course_name)
        stats.matched = sum(1 for t in tee_times if t.matched)
        # 중복을 걸러낸 뒤의 목록으로 센다. fetched(거르기 전) 에서 빼면
        # 중복으로 지운 것까지 "매칭 실패" 로 잡혀, 실패가 실제보다 몇 배로
        # 부풀려진다. 대시보드에 그대로 보이는 숫자다.
        stats.unmatched = len(tee_times) - stats.matched
        stats.unmatched_names = [n for n, _ in self.book.unmatched_report()]

        # 3단계: 값싼 필터 먼저
        candidates = [t for t in tee_times if self._passes_basic(t, q)]
        stats.after_basic = len(candidates)

        # 4단계: 직선거리 프리필터
        origin = self._origin(q)
        if origin and q.max_drive_minutes:
            max_straight_km = (
                q.max_drive_minutes / 60.0 * MAX_PLAUSIBLE_KMH / MIN_DETOUR_FACTOR
            )
            kept = []
            for t in candidates:
                if not t.course:
                    continue
                d = haversine_km(origin[0], origin[1], t.course.lat, t.course.lon)
                if d <= max_straight_km:
                    kept.append(t)
            candidates = kept
        stats.after_prefilter = len(candidates)

        # 5단계: 실제 이동시간
        results: list[SearchResult] = []
        for t in candidates:
            r = SearchResult(tee_time=t)
            if origin and t.course:
                before = len(self.router.cache)
                info = self.router.route(origin[0], origin[1], t.course.lat, t.course.lon)
                if len(self.router.cache) > before:
                    stats.routed += 1
                r.drive_minutes = info.duration_min
                r.distance_km = info.distance_km
                r.route_provider = info.provider
                stats.route_providers[info.provider] = (
                    stats.route_providers.get(info.provider, 0) + 1
                )
                if q.max_drive_minutes and info.duration_min > q.max_drive_minutes:
                    continue
            results.append(r)

        # 6단계: 점수와 정렬
        self._score(results, q)
        results = self._sort(results, q.sort)
        stats.final = len(results)
        stats.elapsed_sec = (datetime.now() - started).total_seconds()
        return results[: q.limit], stats

    # -- 내부 ---------------------------------------------------------------

    def _origin(self, q: SearchQuery) -> Optional[tuple[float, float]]:
        if q.origin_lat is not None and q.origin_lon is not None:
            return (q.origin_lat, q.origin_lon)
        return None

    def _passes_basic(self, t: TeeTime, q: SearchQuery) -> bool:
        if q.play_date and t.play_date != q.play_date:
            return False
        if q.tee_from and t.tee_time < q.tee_from:
            return False
        if q.tee_to and t.tee_time > q.tee_to:
            return False
        # 가격을 모르는 슬롯(-1)은 상한 조건을 확인할 수 없다.
        # 기본적으로는 제외하고, 원하면 포함할 수 있게 한다.
        if q.max_price is not None:
            if t.green_fee < 0:
                if not q.include_unknown_price:
                    return False
            elif t.green_fee > q.max_price:
                return False
        if q.min_price is not None and 0 <= t.green_fee < q.min_price:
            return False
        if q.regions:
            region = t.course.region if t.course else ""
            if not any(r in region for r in q.regions if r):
                return False
        # 이동시간 조건이 있는데 좌표를 모르면 판단할 수 없으므로 제외한다.
        if q.max_drive_minutes and not t.matched:
            return False
        return True

    def _score(self, results: list[SearchResult], q: SearchQuery) -> None:
        """0~1 점수. 가격이 싸고, 가깝고, 희망 시간대 한가운데일수록 높다.

        세 요소를 각각 0~1로 정규화한 뒤 가중합한다. 비교 대상이 하나뿐이면
        정규화가 무의미하므로 해당 요소는 만점으로 둔다.
        """
        if not results:
            return

        fees = [r.tee_time.green_fee for r in results if r.tee_time.green_fee >= 0]
        drives = [r.drive_minutes for r in results if r.drive_minutes is not None]
        fee_lo, fee_hi = (min(fees), max(fees)) if fees else (0, 0)
        drv_lo, drv_hi = (min(drives), max(drives)) if drives else (0, 0)

        target = self._target_time(q)

        for r in results:
            fee = r.tee_time.green_fee
            if fee < 0 or fee_hi == fee_lo:
                fee_score = 1.0
            else:
                fee_score = 1.0 - (fee - fee_lo) / (fee_hi - fee_lo)

            if r.drive_minutes is None or drv_hi == drv_lo:
                drive_score = 1.0
            else:
                drive_score = 1.0 - (r.drive_minutes - drv_lo) / (drv_hi - drv_lo)

            if target is None:
                time_score = 1.0
            else:
                minutes = abs(_to_min(r.tee_time.tee_time) - target)
                # 희망 시각에서 3시간 벗어나면 0점
                time_score = max(0.0, 1.0 - minutes / 180.0)

            r.score = 0.4 * fee_score + 0.4 * drive_score + 0.2 * time_score

    @staticmethod
    def _target_time(q: SearchQuery) -> Optional[float]:
        if q.tee_from and q.tee_to:
            return (_to_min(q.tee_from) + _to_min(q.tee_to)) / 2
        if q.tee_from:
            return _to_min(q.tee_from)
        if q.tee_to:
            return _to_min(q.tee_to)
        return None

    @staticmethod
    def _sort(results: list[SearchResult], how: str) -> list[SearchResult]:
        if how == "price":
            return sorted(
                results,
                key=lambda r: (r.tee_time.green_fee if r.tee_time.green_fee >= 0 else 10**9),
            )
        if how == "drive":
            return sorted(
                results,
                key=lambda r: (r.drive_minutes if r.drive_minutes is not None else 10**9),
            )
        if how == "tee_time":
            return sorted(results, key=lambda r: (r.tee_time.play_date, r.tee_time.tee_time))
        return sorted(results, key=lambda r: r.score, reverse=True)


def _to_min(t: time) -> float:
    return t.hour * 60 + t.minute


def _dedupe(tee_times: list[TeeTime]) -> list[TeeTime]:
    """골프장·날짜·시각·가격·예약처가 모두 같으면 같은 티타임으로 본다."""
    seen: set[tuple] = set()
    out: list[TeeTime] = []
    for t in tee_times:
        key = (t.course_name, t.play_date, t.tee_time, t.green_fee, t.source)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out
