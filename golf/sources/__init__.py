"""티타임 데이터 소스.

소스는 "어디선가 예약 가능한 티타임 목록을 가져오는 것"이면 무엇이든 될 수 있다.
CSV 파일일 수도 있고, 예약 사이트 크롤링일 수도 있다.
검색 엔진은 소스가 무엇인지 모르고 TeeTime 목록만 받는다.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from ..models import TeeTime


class TeeTimeSource(Protocol):
    """모든 소스가 지켜야 하는 최소 약속."""

    id: str
    name: str

    def fetch(self, dates: list[date]) -> list[TeeTime]:
        """주어진 날짜들의 예약 가능한 티타임을 가져온다.

        실패하더라도 예외를 밖으로 던지지 않고 빈 목록을 반환해야 한다.
        한 소스가 죽어도 나머지 소스로 검색이 계속되어야 하기 때문이다.
        """
        ...


from .csv_source import CsvSource          # noqa: E402,F401
from .web_source import WebSource, load_sources   # noqa: E402,F401

__all__ = ["TeeTimeSource", "CsvSource", "WebSource", "load_sources"]
