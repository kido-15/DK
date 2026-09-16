"""게시판 목록에 나타나는 다양한 날짜 표기를 date로 변환한다.

국내 기관 게시판에서 실제로 쓰이는 표기를 폭넓게 받아들인다.
  2026-09-15 / 2026.09.15 / 2026.09.15. / 2026/09/15 / 2026년 9월 15일
  26.09.15 / 20260915 / 09-15(연도 생략) / 2026-09-15 14:30
  오늘 / 어제 / 3일 전 / 14:30 (= 오늘)
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), "KST")

# (정규식, 처리 함수) 순서대로 시도 — 더 구체적인 패턴을 먼저 둔다.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?<!\d)(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?"), "ymd"),
    (re.compile(r"(?<!\d)(\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)"), "ymd2"),
    (re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)"), "ymd"),
    (re.compile(r"(?<!\d)(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?(?!\d)"), "md"),
]

_RELATIVE = re.compile(r"(\d+)\s*일\s*전")
_HOURS_AGO = re.compile(r"(\d+)\s*(?:시간|분)\s*전")
_TIME_ONLY = re.compile(r"^\s*\d{1,2}:\d{2}(:\d{2})?\s*$")


def today_kst() -> date:
    return datetime.now(KST).date()


def yesterday_kst() -> date:
    return today_kst() - timedelta(days=1)


def parse_date(text: str, *, today: date | None = None, explicit_format: str | None = None) -> date | None:
    """문자열에서 날짜를 찾아 반환. 못 찾으면 None.

    explicit_format을 주면 strptime을 먼저 시도한다 (예: "%Y.%m.%d").
    """
    if not text:
        return None
    text = text.strip()
    today = today or today_kst()

    if explicit_format:
        try:
            return datetime.strptime(text, explicit_format).date()
        except ValueError:
            pass  # 형식이 안 맞으면 아래 일반 규칙으로 넘어간다

    if "오늘" in text or _TIME_ONLY.match(text) or _HOURS_AGO.search(text):
        return today
    if "어제" in text or "전일" in text:
        return today - timedelta(days=1)
    if "그제" in text or "그저께" in text:
        return today - timedelta(days=2)
    relative = _RELATIVE.search(text)
    if relative:
        return today - timedelta(days=int(relative.group(1)))

    for pattern, kind in _PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            if kind == "ymd":
                year, month, day = (int(g) for g in match.groups())
            elif kind == "ymd2":
                year, month, day = (int(g) for g in match.groups())
                year += 2000
            else:  # md — 연도 생략. 오늘 기준으로 가장 가까운 과거 연도를 택한다.
                month, day = (int(g) for g in match.groups())
                year = today.year
                try:
                    candidate = date(year, month, day)
                except ValueError:
                    continue
                if candidate > today + timedelta(days=1):
                    year -= 1
            return date(year, month, day)
        except ValueError:
            continue  # 13월 35일 같은 오탐은 건너뛴다
    return None


def looks_like_date(text: str) -> bool:
    """자동탐지에서 '이 칸은 날짜 칸 같다'를 판단할 때 쓴다."""
    return parse_date(text) is not None


def format_kr(value: date) -> str:
    return value.strftime("%Y-%m-%d")
