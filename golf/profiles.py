"""골프장별 크롤링 프로필 — 한 번 성공한 방법을 기억한다.

개별 골프장 홈페이지를 매번 처음부터 뒤지면 느리고 부담도 크다.
한 번 예약 페이지를 찾아 티타임을 뽑는 데 성공하면 그 경로를 저장해 두고,
다음부터는 거기로 바로 간다.

실패도 기록한다. 로그인이 필요한 곳을 매일 다시 두드릴 이유가 없다.

또 각 사이트가 쓰는 예약 솔루션의 흔적(외부 스크립트 도메인 등)을 함께
남긴다. 골프장들은 자체 개발 대신 소수의 솔루션을 공유해 쓰는 경우가 많아,
같은 흔적을 가진 곳들은 같은 방법으로 풀린다. 어떤 솔루션이 많이 쓰이는지는
미리 적어 두지 않고 크롤링하면서 쌓인 것으로 파악한다.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "golf", "site_profiles.json",
)

# 실패 상태 — 다시 시도할 가치가 있는지 판단하는 데 쓴다
STATUS_OK = "ok"
STATUS_LOGIN = "login_required"      # 로그인 벽. 재시도해도 소용없다
STATUS_JS = "js_rendered"            # 자바스크립트로 그려짐. 브라우저 모드가 필요
STATUS_NO_LINK = "no_booking_link"   # 예약 페이지를 못 찾음
STATUS_NO_SITE = "no_homepage"       # 홈페이지 주소를 모름
STATUS_ERROR = "error"               # 네트워크/HTTP 오류. 일시적일 수 있다
STATUS_EMPTY = "empty"               # 페이지는 읽었으나 티타임이 없음 (매물 없음일 수도)

# 재시도해도 결과가 달라지지 않을 상태
TERMINAL_STATUSES = {STATUS_LOGIN}


@dataclass
class SiteProfile:
    """골프장 한 곳의 크롤링 이력."""

    course_id: str
    course_name: str = ""
    homepage: str = ""
    booking_url: str = ""          # 티타임이 실제로 나온 주소
    format: str = "html"           # html | json
    status: str = ""
    last_success: str = ""         # ISO 시각
    last_attempt: str = ""
    last_count: int = 0            # 마지막으로 뽑은 티타임 수
    confidence: float = 0.0
    block_selector: str = ""
    note: str = ""
    fail_streak: int = 0
    tech: list[str] = field(default_factory=list)   # 예약 솔루션 흔적
    candidates: list[str] = field(default_factory=list)  # 시도해 볼 예약 페이지 후보

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SiteProfile":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def record_success(self, url: str, count: int, confidence: float,
                       selector: str, fmt: str = "html") -> None:
        now = datetime.now().isoformat(timespec="seconds")
        self.booking_url = url
        self.format = fmt
        self.status = STATUS_OK
        self.last_success = now
        self.last_attempt = now
        self.last_count = count
        self.confidence = confidence
        self.block_selector = selector
        self.fail_streak = 0
        self.note = ""

    def record_failure(self, status: str, note: str = "") -> None:
        self.last_attempt = datetime.now().isoformat(timespec="seconds")
        self.status = status
        self.note = note
        self.last_count = 0
        self.fail_streak += 1

    @property
    def is_terminal(self) -> bool:
        """더 시도해도 소용없는 상태인지."""
        return self.status in TERMINAL_STATUSES

    def should_skip(self, max_fail_streak: int = 5) -> bool:
        """이번 실행에서 건너뛸지. 로그인 벽이거나 계속 실패한 곳은 건너뛴다."""
        return self.is_terminal or self.fail_streak >= max_fail_streak


class ProfileStore:
    """프로필 묶음. 파일 한 장에 저장한다."""

    def __init__(self, profiles: Optional[dict[str, SiteProfile]] = None,
                 path: str = DEFAULT_PATH):
        self.profiles: dict[str, SiteProfile] = profiles or {}
        self.path = path

    @classmethod
    def load(cls, path: str = DEFAULT_PATH) -> "ProfileStore":
        if not os.path.exists(path):
            return cls(path=path)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return cls(path=path)
        profiles = {}
        for cid, d in (data.get("profiles") or {}).items():
            try:
                profiles[cid] = SiteProfile.from_dict(d)
            except TypeError:
                continue
        return cls(profiles, path=path)

    def save(self, path: Optional[str] = None) -> int:
        path = path or self.path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        payload = {
            "updated": datetime.now().isoformat(timespec="seconds"),
            "profiles": {cid: p.to_dict() for cid, p in self.profiles.items()},
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)     # 쓰다 말고 죽어도 기존 파일이 깨지지 않도록
        return len(self.profiles)

    def get(self, course_id: str, course_name: str = "") -> SiteProfile:
        p = self.profiles.get(course_id)
        if p is None:
            p = SiteProfile(course_id=course_id, course_name=course_name)
            self.profiles[course_id] = p
        elif course_name and not p.course_name:
            p.course_name = course_name
        return p

    def __len__(self) -> int:
        return len(self.profiles)

    # -- 통계 --------------------------------------------------------------

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.profiles.values():
            out[p.status or "(미시도)"] = out.get(p.status or "(미시도)", 0) + 1
        return out

    def tech_groups(self) -> list[tuple[str, int, list[str]]]:
        """같은 예약 솔루션을 쓰는 골프장 묶음. 많이 쓰이는 순.

        같은 솔루션을 쓰는 곳이 여럿이면, 한 곳을 풀면 나머지도 풀린다.
        """
        groups: dict[str, list[str]] = {}
        for p in self.profiles.values():
            for t in p.tech:
                groups.setdefault(t, []).append(p.course_name or p.course_id)
        rows = [(tech, len(names), sorted(names)) for tech, names in groups.items()]
        rows.sort(key=lambda r: r[1], reverse=True)
        return rows

    def working(self) -> list[SiteProfile]:
        return [p for p in self.profiles.values() if p.status == STATUS_OK]


# ---------------------------------------------------------------------------
# 예약 솔루션 흔적 뽑기
# ---------------------------------------------------------------------------

# 자기 도메인이 아닌 곳에서 불러오는 스크립트는 외부 솔루션일 가능성이 있다.
# 분석/광고 도메인은 솔루션과 무관하므로 제외한다.
_IGNORE_HOSTS = re.compile(
    r"(google|gstatic|googletagmanager|google-analytics|doubleclick|facebook|"
    r"kakao\.com/v1/cs|daum|naver\.com/analytics|jquery|bootstrapcdn|cloudflare|"
    r"jsdelivr|unpkg|fontawesome|youtube|channel\.io|wcs\.naver)",
    re.IGNORECASE,
)

# 한국 도메인은 co.kr, or.kr 처럼 2단계 TLD가 흔하다. 단순히 뒤 두 조각을
# 루트로 보면 namseoul-cc.co.kr 의 루트가 "co.kr" 이 되어 모든 .co.kr 사이트를
# 자기 도메인으로 착각한다.
_TWO_LEVEL_TLDS = {
    "co.kr", "or.kr", "ne.kr", "go.kr", "re.kr", "pe.kr", "sc.kr", "hs.kr", "ms.kr",
    "es.kr", "ac.kr", "co.jp", "ne.jp", "or.jp", "com.cn", "co.uk", "com.au",
}


def root_domain(host: str) -> str:
    """도메인에서 등록 가능한 최상위 이름을 뽑는다. namseoul-cc.co.kr → namseoul-cc.co.kr"""
    parts = [p for p in host.lower().split(".") if p]
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_LEVEL_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
_GENERATOR_RE = re.compile(
    r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE)
_FORM_ACTION_RE = re.compile(r'<form[^>]+action=["\']([^"\']+)["\']', re.IGNORECASE)


def extract_tech(html: str, page_url: str) -> list[str]:
    """페이지에서 예약 솔루션의 흔적을 뽑는다.

    정확한 솔루션 이름을 알아내는 것이 목적이 아니라, 같은 것을 쓰는 사이트끼리
    묶을 수 있는 표식이면 된다.
    """
    tech: set[str] = set()
    try:
        own_host = urllib.parse.urlsplit(page_url).netloc.lower()
    except ValueError:
        own_host = ""
    own_root = root_domain(own_host) if own_host else ""

    for src in _SCRIPT_SRC_RE.findall(html)[:60]:
        try:
            host = urllib.parse.urlsplit(urllib.parse.urljoin(page_url, src)).netloc.lower()
        except ValueError:
            continue
        if not host or _IGNORE_HOSTS.search(host):
            continue
        if own_root and root_domain(host) == own_root:
            continue
        tech.add(f"script:{host}")

    m = _GENERATOR_RE.search(html)
    if m:
        tech.add(f"generator:{m.group(1).strip()[:40]}")

    # 예약 폼이 다른 도메인으로 넘어가면 그쪽이 예약 솔루션이다
    for action in _FORM_ACTION_RE.findall(html)[:20]:
        if action.startswith(("http://", "https://")):
            try:
                host = urllib.parse.urlsplit(action).netloc.lower()
            except ValueError:
                continue
            if host and own_root and root_domain(host) != own_root:
                tech.add(f"form:{host}")

    return sorted(tech)[:6]
