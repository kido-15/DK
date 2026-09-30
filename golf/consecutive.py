"""같은 골프장·같은 예약처에서 연속으로 잡히는 티타임 묶음 찾기.

여러 팀(조)이 한 골프장에서 이어서 치려면, 티오프 시각이 조금씩(보통
7~10분) 차이 나는 티타임 여러 개가 같은 예약처에 나란히 나와 있어야
한다. golf.search.GolfSearch.search() 는 티타임 한 줄 한 줄을 개별
결과로 돌려주므로, 그 결과를 골프장·예약처별로 묶어서 "연속된 몇 개"를
찾아내는 후처리 단계가 이 모듈이다.

검색 자체(날짜·시간대·이동시간·가격 필터, 골프장 매칭)는 그대로
GolfSearch 를 쓴다 — 이 모듈은 그 결과 위에서만 동작한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from typing import Any, Optional

from .models import SearchResult

# 티타임 사이 간격이 이보다 벌어지면 "연속"으로 안 본다. 국내 골프장 대부분이
# 조를 7~10분 간격으로 배정하므로, 그 다음 조까지 놓치지 않을 만큼 넉넉히 잡되
# 반나절 뒤 티타임까지 한 묶음으로 엮이지 않을 만큼은 좁게 잡은 값이다.
DEFAULT_MAX_GAP_MINUTES = 10


def _to_min(t: time) -> int:
    return t.hour * 60 + t.minute


@dataclass
class ConsecutiveGroup:
    """한 골프장(한 예약처)에서 이어지는 티타임 묶음 하나."""

    course_id: str
    course_name: str
    region: str
    source: str
    play_date: date
    results: list[SearchResult] = field(default_factory=list)  # 티오프 시각 순 정렬

    @property
    def count(self) -> int:
        return len(self.results)

    @property
    def first_tee(self) -> time:
        return self.results[0].tee_time.tee_time

    @property
    def last_tee(self) -> time:
        return self.results[-1].tee_time.tee_time

    @property
    def drive_minutes(self) -> Optional[float]:
        return self.results[0].drive_minutes if self.results else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "course_id": self.course_id,
            "course_name": self.course_name,
            "region": self.region,
            "source": self.source,
            "play_date": self.play_date.isoformat(),
            "count": self.count,
            "first_tee": self.first_tee.strftime("%H:%M"),
            "last_tee": self.last_tee.strftime("%H:%M"),
            "drive_minutes": (
                round(self.drive_minutes) if self.drive_minutes is not None else None
            ),
            "slots": [r.to_dict() for r in self.results],
        }


def find_consecutive_groups(
    results: list[SearchResult],
    *,
    min_count: int = 3,
    max_gap_minutes: int = DEFAULT_MAX_GAP_MINUTES,
) -> list[ConsecutiveGroup]:
    """검색 결과를 골프장·예약처별로 묶어, 연속된 티타임이 min_count개
    이상인 묶음만 골라낸다.

    다른 예약처(source)끼리는 한 화면에서 같이 나와도 실제로는 따로
    예약해야 하는 별개의 창구라 하나로 묶지 않는다. 같은 시각에 가격이
    다른 중복 매물이 여러 개 있으면(예: 같은 슬롯을 두 소스가 각각 다시
    올린 경우) 더 싼 쪽 하나만 대표로 남긴다.

    반환값은 묶음 안의 개수(count) 내림차순 → 이동시간 오름차순으로
    정렬한다 — 조가 더 많이 이어지는 곳, 그중 가까운 곳을 먼저 보여 준다.
    """
    by_key: dict[tuple, dict[time, SearchResult]] = {}
    for r in results:
        t = r.tee_time
        if not t.course:
            continue  # 골프장을 특정할 수 없으면 어느 묶음인지도 알 수 없다
        key = (t.course.course_id, t.source, t.play_date)
        by_time = by_key.setdefault(key, {})
        existing = by_time.get(t.tee_time)
        if existing is None or _is_cheaper(r, existing):
            by_time[t.tee_time] = r

    groups: list[ConsecutiveGroup] = []
    for (course_id, source, play_date), by_time in by_key.items():
        ordered = sorted(by_time.values(), key=lambda r: r.tee_time.tee_time)
        run: list[SearchResult] = []
        for r in ordered:
            if run and _to_min(r.tee_time.tee_time) - _to_min(run[-1].tee_time.tee_time) > max_gap_minutes:
                _flush(groups, run, course_id, source, play_date, min_count)
                run = []
            run.append(r)
        _flush(groups, run, course_id, source, play_date, min_count)

    groups.sort(key=lambda g: (-g.count, g.drive_minutes if g.drive_minutes is not None else 10**9))
    return groups


def _is_cheaper(a: SearchResult, b: SearchResult) -> bool:
    fee_a, fee_b = a.tee_time.green_fee, b.tee_time.green_fee
    if fee_a < 0:
        return False
    if fee_b < 0:
        return True
    return fee_a < fee_b


def _flush(groups: list[ConsecutiveGroup], run: list[SearchResult],
          course_id: str, source: str, play_date: date, min_count: int) -> None:
    if len(run) < min_count:
        return
    course = run[0].tee_time.course
    groups.append(ConsecutiveGroup(
        course_id=course_id,
        course_name=course.name if course else run[0].tee_time.course_name,
        region=course.region if course else "",
        source=source,
        play_date=play_date,
        results=list(run),
    ))
