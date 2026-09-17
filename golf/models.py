"""핵심 데이터 모델.

모든 모듈이 공유하는 자료구조를 한곳에 모아 둔다.
외부 의존성 없이 표준 라이브러리만 사용한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, time
from typing import Any, Optional


# ---------------------------------------------------------------------------
# 골프장 (마스터 데이터)
# ---------------------------------------------------------------------------


@dataclass
class Course:
    """골프장 한 곳. 예약 정보가 아니라 변하지 않는 기본 정보다."""

    course_id: str          # 내부 고유 키 (예: "nam-seoul-cc")
    name: str               # 표시 이름 (예: "남서울컨트리클럽")
    lat: float              # 위도
    lon: float              # 경도
    region: str = ""        # 시도 단위 (예: "경기")
    address: str = ""       # 전체 주소
    holes: Optional[int] = None   # 홀 수 (18, 27, 36 ...)
    phone: str = ""
    homepage: str = ""      # 공식 홈페이지 (개별 사이트 직접 수집에 쓴다)
    source: str = ""        # 이 레코드의 출처 (예: "osm", "manual")
    aliases: list[str] = field(default_factory=list)  # 예약 사이트별 표기 차이 흡수

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Course":
        aliases = d.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [a.strip() for a in aliases.split("|") if a.strip()]
        return cls(
            course_id=str(d["course_id"]).strip(),
            name=str(d["name"]).strip(),
            lat=float(d["lat"]),
            lon=float(d["lon"]),
            region=str(d.get("region") or "").strip(),
            address=str(d.get("address") or "").strip(),
            holes=int(d["holes"]) if str(d.get("holes") or "").strip().isdigit() else None,
            phone=str(d.get("phone") or "").strip(),
            homepage=str(d.get("homepage") or "").strip(),
            source=str(d.get("source") or "").strip(),
            aliases=list(aliases),
        )

    def match_keys(self) -> list[str]:
        """예약 사이트의 골프장 이름과 대조할 때 쓰는 정규화된 키 목록."""
        return [normalize_course_name(n) for n in [self.name, *self.aliases] if n]


_NAME_NOISE = re.compile(
    r"(컨트리클럽|컨트리\s*클럽|골프클럽|골프장|골프&리조트|리조트|CC|GC|G\.C|C\.C"
    r"|club|country|golf|resort|\(.*?\)|\[.*?\])",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^0-9a-z가-힣]+")


def normalize_course_name(name: str) -> str:
    """'남서울 컨트리클럽 (레이크)' → '남서울레이크' 처럼 비교 가능한 형태로 만든다.

    예약 사이트마다 같은 골프장을 다르게 적기 때문에, 이름을 맞대볼 때는
    항상 이 함수를 거친 값끼리 비교한다.
    """
    if not name:
        return ""
    s = name.strip().lower()
    # 괄호 안의 코스명(레이크/밸리 등)은 살려두되 괄호 기호만 제거한다.
    s = s.replace("(", " ").replace(")", " ").replace("[", " ").replace("]", " ")
    s = _NAME_NOISE.sub(" ", s)
    s = _NON_WORD.sub("", s)
    return s


# ---------------------------------------------------------------------------
# 티타임 (예약 가능 슬롯)
# ---------------------------------------------------------------------------


@dataclass
class TeeTime:
    """특정 날짜·시각에 예약 가능한 슬롯 하나."""

    course_name: str        # 소스가 알려준 원본 골프장 이름
    play_date: date         # 플레이 날짜
    tee_time: time          # 티오프 시각
    green_fee: int          # 1인 그린피 (원). 알 수 없으면 -1
    source: str             # 어느 소스에서 왔는지 (예: "xgolf")
    booking_url: str = ""   # 예약 페이지 링크
    slots: Optional[int] = None    # 남은 자리 수
    hole_info: str = ""     # "18홀", "아웃 9홀" 등
    raw: dict[str, Any] = field(default_factory=dict)  # 원본 데이터 보존 (디버깅용)

    # 매칭 단계에서 채워지는 값
    course: Optional[Course] = None

    @property
    def matched(self) -> bool:
        return self.course is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "course_name": self.course_name,
            "course_id": self.course.course_id if self.course else None,
            "play_date": self.play_date.isoformat(),
            "tee_time": self.tee_time.strftime("%H:%M"),
            "green_fee": self.green_fee,
            "source": self.source,
            "booking_url": self.booking_url,
            "slots": self.slots,
            "hole_info": self.hole_info,
        }


# ---------------------------------------------------------------------------
# 검색 조건과 결과
# ---------------------------------------------------------------------------


@dataclass
class SearchQuery:
    """사용자가 입력하는 세 가지 조건: 출발 위치, 이동 희망 시간, 가격."""

    origin: str                       # 출발지 (주소 또는 "위도,경도")
    origin_lat: Optional[float] = None
    origin_lon: Optional[float] = None

    play_date: Optional[date] = None  # 플레이 날짜 (없으면 전체)
    tee_from: Optional[time] = None   # 희망 티오프 시작 시각
    tee_to: Optional[time] = None     # 희망 티오프 종료 시각

    max_drive_minutes: Optional[int] = None  # 편도 이동 허용 시간 (분)
    max_price: Optional[int] = None          # 1인 그린피 상한 (원)
    min_price: Optional[int] = None

    regions: list[str] = field(default_factory=list)  # 지역 필터 (예: ["경기", "충북"])
    sort: str = "score"               # score | price | drive | tee_time
    limit: int = 50

    def describe(self) -> str:
        """사람이 읽을 수 있는 조건 요약."""
        parts = [f"출발: {self.origin or '미지정'}"]
        if self.play_date:
            parts.append(f"날짜: {self.play_date.isoformat()}")
        if self.tee_from or self.tee_to:
            a = self.tee_from.strftime("%H:%M") if self.tee_from else ""
            b = self.tee_to.strftime("%H:%M") if self.tee_to else ""
            parts.append(f"티오프: {a}~{b}")
        if self.max_drive_minutes:
            parts.append(f"이동: {self.max_drive_minutes}분 이내")
        if self.max_price:
            parts.append(f"그린피: {self.max_price:,}원 이하")
        return " / ".join(parts)


@dataclass
class SearchResult:
    """검색 결과 한 줄. 티타임 + 이동 정보 + 점수."""

    tee_time: TeeTime
    drive_minutes: Optional[float] = None
    distance_km: Optional[float] = None
    route_provider: str = ""     # 이동시간을 어떻게 구했는지 (osrm / kakao / estimate)
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = self.tee_time.to_dict()
        course = self.tee_time.course
        d.update(
            {
                "display_name": course.name if course else self.tee_time.course_name,
                "region": course.region if course else "",
                "address": course.address if course else "",
                "lat": course.lat if course else None,
                "lon": course.lon if course else None,
                "drive_minutes": round(self.drive_minutes) if self.drive_minutes is not None else None,
                "distance_km": round(self.distance_km, 1) if self.distance_km is not None else None,
                "route_provider": self.route_provider,
                "score": round(self.score, 3),
            }
        )
        return d


# ---------------------------------------------------------------------------
# 파싱 도우미 — 크롤링한 문자열을 위 모델로 바꿀 때 쓴다
# ---------------------------------------------------------------------------


def parse_price(value: Any) -> int:
    """'168,000원', '16.8만', '168000' 등을 정수 원 단위로 바꾼다.

    해석할 수 없으면 -1을 돌려준다 (0원과 구분하기 위해).
    """
    if value is None:
        return -1
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if not s:
        return -1
    # "16.8만원" 형태
    m = re.search(r"(\d+(?:\.\d+)?)\s*만", s)
    if m:
        return int(float(m.group(1)) * 10_000)
    digits = re.sub(r"[^\d]", "", s)
    if not digits:
        return -1
    return int(digits)


def parse_time(value: Any) -> Optional[time]:
    """'07:30', '0730', '오전 7시 30분', '7:30 AM' 등을 time으로 바꾼다."""
    if value is None:
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time()
    s = str(value).strip()
    if not s:
        return None

    is_pm = bool(re.search(r"오후|PM", s, re.IGNORECASE))
    is_am = bool(re.search(r"오전|AM", s, re.IGNORECASE))

    m = re.search(r"(\d{1,2})\s*[:시]\s*(\d{1,2})", s)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"\b(\d{3,4})\b", s)
        if m:
            raw = m.group(1).zfill(4)
            hh, mm = int(raw[:2]), int(raw[2:])
        else:
            m = re.search(r"(\d{1,2})\s*시", s)
            if not m:
                return None
            hh, mm = int(m.group(1)), 0

    if is_pm and hh < 12:
        hh += 12
    if is_am and hh == 12:
        hh = 0
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return time(hour=hh, minute=mm)


def parse_date(value: Any, *, today: Optional[date] = None) -> Optional[date]:
    """'2026-09-20', '2026.09.20', '09/20', '9월 20일' 등을 date로 바꾼다.

    연도가 없으면 오늘을 기준으로 가장 가까운 미래 날짜로 해석한다.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    today = today or date.today()

    m = re.search(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    m = re.search(r"(\d{1,2})\s*[-./월]\s*(\d{1,2})", s)
    if m:
        mo, dd = int(m.group(1)), int(m.group(2))
        for year in (today.year, today.year + 1):
            try:
                cand = date(year, mo, dd)
            except ValueError:
                return None
            if cand >= today:
                return cand
        return None

    m = re.search(r"(\d{8})", s)
    if m:
        raw = m.group(1)
        try:
            return date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
        except ValueError:
            return None
    return None
