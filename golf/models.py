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
        """예약 사이트의 골프장 이름과 대조할 때 쓰는 정규화된 키 목록.

        지도 데이터는 괄호 안에 영문명이나 운영 형태를 같이 적어 두는 일이 많다.
        그대로 두면 예약 사이트 표기와 글자가 어긋나 못 찾는다.

            떼제베 골프장(TGV CC)   → '떼제베tgv' 만으로는 '떼제베(동북)' 을 못 잡는다
                                   → 괄호 앞부분 '떼제베' 도 키로 넣는다

        괄호 앞부분이 너무 짧아지면(한두 글자) 엉뚱한 곳에 걸리므로 넣지 않는다.
        """
        keys: list[str] = []
        for raw in [self.name, *self.aliases]:
            if not raw:
                continue
            for candidate in (raw, raw.split("(")[0].split("[")[0]):
                key = normalize_course_name(candidate)
                if key and len(key) >= 2 and key not in keys:
                    keys.append(key)
                elif key:
                    # 이름이 거의 전부 "골프클럽" 같은 일반 낱말인 경우.
                    # 그 낱말을 지우면 "골프클럽Q" 가 "q" 한 글자만 남아
                    # 색인에서 빠진다. 그러면 그 골프장은 영영 못 찾고,
                    # 좌표를 다시 받을 때마다 같은 행이 또 쌓인다.
                    #
                    # 지우기 전 형태를 키로 넣는다. 남은 것이 아예 없는
                    # 이름("골프장", "CC")은 넣지 않는다 — 아무 데나 걸린다.
                    light = _NON_WORD.sub("", candidate.strip().lower())
                    if len(light) >= 3 and light not in keys:
                        keys.append(light)
        return keys


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


# 예약 사이트가 이름 뒤에 붙이는 **판매 조건** 표시들.
#
# 골프장 이름의 일부가 아니라 "이 매물이 어떤 건인지" 를 말하는 꼬리표다.
# 골프장 DB(공식 이름)에는 이런 말이 없으므로, 떼어내지 않으면 영영 못 찾는다.
#
#   가평(비공개)   → 가평          비공개는 골프장 이름이 아니다
#   신안(병행)     → 신안          회원제·대중제 병행 운영이라는 뜻
#   글렌로스-퍼9   → 글렌로스      9홀 코스라는 뜻
#
# 반대로 (레이크)·(밸리) 같은 **코스 구분**은 골프장이 실제로 나눠 부르는
# 이름이므로 남겨 둔다. 그래서 괄호를 통째로 지우지 않고 목록으로 가린다.
_BOOKING_QUALIFIERS = re.compile(
    r"(비공개|병행|대중제|회원제|퍼블릭|퍼9|9홀|조인|단체|특가|마감임박|당일)",
    re.IGNORECASE,
)

# 이름이나 홀 수 표시에 "9홀 코스"라는 뜻이 담겨 있는지.
#
#     빅토리아-퍼9    → 9홀을 두 바퀴 돌아 18홀을 채우는 코스
#     스프링베일-퍼9  → 마찬가지
#
# "19홀"·"29홀" 같은 걸 오인하지 않게 앞뒤로 다른 숫자가 붙어 있으면 뺀다.
_NINE_HOLE_TAG = re.compile(r"퍼\s*9(?!\d)|(?<!\d)9\s*홀")


def has_nine_hole_tag(text: str) -> bool:
    """이름이나 hole_info에 9홀(하프) 코스 표시가 있으면 True."""
    return bool(_NINE_HOLE_TAG.search(text or ""))

# "(구.큐로cc)" 처럼 괄호 안에 적어 주는 옛 이름.
# 골프장 DB 가 아직 옛 이름으로 들고 있을 수 있어, 따로 뽑아 후보로 쓴다.
_FORMER_NAME = re.compile(r"[(\[]\s*구[.\s]\s*([^)\]]+)[)\]]")

# 예약 사이트가 앞에 붙이는 운영사 이름.
_OPERATOR_PREFIX = re.compile(r"^(골프존카운티|골프존|sk|한화|대명|소노|아난티)\s*", re.IGNORECASE)


# 이름 뒤 괄호에 적히는 **지역 표시**. 같은 이름이 여러 곳에 있을 때 가른다.
#
#     그랜드(청주)   충북 청주의 그랜드   ≠   경남의 그랜드 골프클럽
#     포웰(안성)cc   경기 안성의 포웰     ≠   경남의 포웰CC
#
# 이걸 무시하고 이름만 보고 붙이면 **엉뚱한 지역의 좌표**가 박힌다. 좌표가
# 없는 것보다 나쁘다 — 없으면 결과에서 빠지지만, 틀리면 "강남역에서 90분"
# 자리에 경남 골프장이 자신 있게 올라온다.
_REGION_HINT = re.compile(r"[(\[]\s*([가-힣]{2,4})\s*[)\]]")


def region_hint(raw: str) -> str:
    """이름 괄호 안의 지역 표시. 없으면 빈 문자열."""
    for m in _REGION_HINT.finditer(raw or ""):
        token = m.group(1)
        if _BOOKING_QUALIFIERS.fullmatch(token) or token.startswith("구."):
            continue                      # 판매 조건이지 지역이 아니다
        return token
    return ""


# 같은 브랜드를 예약 사이트와 지도 데이터가 다르게 적는 경우.
#
#     골팡 "골프존 진천"   ↔  지도 "골프존카운티 진천"
#     골팡 "클럽D 보은"    ↔  지도 "클럽디보은CC"
#
# 글자가 겹치기는 해도 한쪽이 다른 쪽을 포함하지 않아 포함 규칙으로는 안 걸린다.
# 어림짐작에 맡기면 "스프링베일" 이 "스프링데일" 에 붙는 것 같은 사고가 난다.
# 그래서 **확인된 표기 차이만** 목록으로 둔다.
#
# 각 묶음은 같은 브랜드의 여러 표기다. 정규화된 키가 그중 하나로 시작하면
# 나머지 표기로 바꾼 키도 후보에 넣는다.
_BRAND_FORMS = [
    ("골프존카운티", "골프존"),
    ("클럽디", "클럽d"),
]


def _brand_swaps(key: str) -> list[str]:
    """브랜드 표기만 바꾼 키들."""
    out = []
    for forms in _BRAND_FORMS:
        # 긴 표기부터 본다. "골프존카운티진천" 이 "골프존" 으로도 시작하므로,
        # 짧은 쪽을 먼저 잡으면 "골프존카운티카운티진천" 이 된다.
        for form in sorted(forms, key=len, reverse=True):
            if key.startswith(form):
                rest = key[len(form):]
                out.extend(other + rest for other in forms if other != form)
                break
    return out


def name_variants(raw: str) -> list[str]:
    """그 이름을 가리킬 법한 정규화 키들. 확실한 것부터.

    예약 사이트 이름과 골프장 DB 이름은 같은 곳을 다르게 적는다. 하나만 보고
    포기하면 좌표를 못 찾고, 좌표가 없으면 이동시간을 못 재서 **검색 결과에서
    통째로 빠진다.** 조용히 사라지는 쪽이라 후보를 넉넉히 만들어 둔다.

        골프존 송도(구.오렌지듄스)
          → ['골프존송도구오렌지듄스', '골프존송도', '오렌지듄스', '송도']
    """
    out: list[str] = []

    def add(value: str) -> None:
        key = normalize_course_name(value)
        if key and len(key) < 2:
            # "골프클럽Q" 처럼 이름이 거의 전부 일반 낱말인 경우. 그 낱말을
            # 지우면 한 글자만 남아 아무것도 못 찾는다. 지우기 전 형태로 찾는다.
            light = _NON_WORD.sub("", (value or "").strip().lower())
            if len(light) >= 3 and light not in out:
                out.append(light)
        if key and key not in out:
            out.append(key)

    raw = (raw or "").strip()
    if not raw:
        return []

    add(raw)                                   # 있는 그대로

    former = _FORMER_NAME.search(raw)
    base = _FORMER_NAME.sub(" ", raw)          # 옛 이름 표기를 떼어낸 나머지

    stripped = _BOOKING_QUALIFIERS.sub(" ", base)
    add(stripped)                              # 판매 조건 꼬리표를 뗀 것

    if former:
        add(former.group(1))                   # 옛 이름 그 자체

    without_op = _OPERATOR_PREFIX.sub("", stripped)
    add(without_op)                            # 운영사 이름을 뗀 것

    for key in list(out):                      # 브랜드 표기만 다른 것
        for swapped in _brand_swaps(key):
            if swapped not in out:
                out.append(swapped)

    return out


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
    include_unknown_price: bool = False      # 가격이 안 적힌 티타임도 포함할지
    exclude_nine_holes: bool = False         # 9홀(하프) 코스 제외할지

    regions: list[str] = field(default_factory=list)  # 지역 필터 (예: ["경기", "충북"])
    sort: str = "score"               # score | price | drive | tee_time
    limit: Optional[int] = None       # 없으면 조건에 맞는 결과 전부

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
        if self.exclude_nine_holes:
            parts.append("9홀 코스 제외")
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
    # 가격을 모른다는 뜻으로 -1 을 쓴다. CSV 로 오간 뒤 다시 읽을 때 숫자만
    # 뽑으면 부호가 떨어져 "-1" 이 1원이 된다. 그러면 가격 미상인 매물이
    # **가장 싼 매물**로 둔갑해 검색 결과 맨 위에 올라온다.
    if re.match(r"-\s*\d", s):
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


def parse_date(value: Any, *, today: Optional[date] = None,
               near: Optional[date] = None) -> Optional[date]:
    """'2026-09-20', '2026.09.20', '09/20', '9월 20일' 등을 date로 바꾼다.

    연도가 없으면 오늘을 기준으로 가장 가까운 미래 날짜로 해석한다.

    near 를 주면 **그 날짜에 가장 가까운 연도**를 고른다. 어느 날짜를 요청해서
    받은 목록인지 아는 경우에 쓴다. 예약 사이트는 연도 없이 "09월18일" 처럼만
    적는 곳이 많은데, 오늘 기준으로만 풀면 미래 쪽으로만 밀린다.

        오늘 2026-09-19, 목록에 "09월18일" (어제 목록이 섞여 들어온 경우)
          today 기준 → 2027-09-18   내년으로 밀려 버린다
          near=2026-09-19 기준 → 2026-09-18   하루 전으로 제대로 잡힌다

    앞의 결과는 그럴듯해 보여서 더 나쁘다. 잘못 받아 온 목록이 1년 뒤 날짜로
    조용히 저장된다. 뒤의 결과라야 "요청한 날짜가 아니다" 로 걸러진다.
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
        if near is not None:
            # 요청한 날짜를 아는 경우. 앞뒤 연도까지 놓고 가장 가까운 것을 고른다.
            # 연말에 다음 해 날짜를 조회하는 경우(12월에 1월 티타임)도 이걸로 풀린다.
            best: Optional[date] = None
            for year in (near.year - 1, near.year, near.year + 1):
                try:
                    cand = date(year, mo, dd)
                except ValueError:
                    continue          # 윤년이 아닌 해의 2월 29일
                if best is None or abs((cand - near).days) < abs((best - near).days):
                    best = cand
            return best
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
