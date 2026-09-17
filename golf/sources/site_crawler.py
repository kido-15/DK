"""개별 골프장 홈페이지를 직접 돌며 티타임을 수집한다.

플랫폼(엑스골프 등)을 거치지 않고 골프장 공식 홈페이지에서 바로 읽어 온다.
골프장마다 홈페이지 구조가 다르므로 설정을 받지 않고 스스로 판단한다.

    홈페이지 → 예약 링크 찾기 → 예약 페이지 열기 → 티타임 자동 추출 → 프로필 저장

한 번 성공하면 그 주소를 프로필에 저장해 다음부터는 바로 그리로 간다.
로그인이 필요한 곳은 기록해 두고 다시 두드리지 않는다.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from datetime import date
from typing import Any, Iterable, Optional

from .. import htmlsel
from ..extract import auto_extract, auto_extract_json, detect_login_wall
from ..models import Course, TeeTime
from ..profiles import (STATUS_EMPTY, STATUS_ERROR, STATUS_JS, STATUS_LOGIN,
                        STATUS_NO_LINK, STATUS_NO_SITE, STATUS_OK,
                        ProfileStore, SiteProfile, extract_tech)
from .web_source import HttpClient

# 예약 페이지 링크를 고를 때 쓰는 단서
LINK_TEXT_STRONG = ["실시간예약", "실시간 예약", "온라인예약", "온라인 예약",
                    "부킹", "티타임", "tee time", "실시간부킹"]
LINK_TEXT_WEAK = ["예약", "예약하기", "booking", "reservation", "reserve", "rsv"]
# 예약과 무관한 "예약" 링크를 걸러낸다
LINK_TEXT_BLOCK = ["예약확인", "예약조회", "예약취소", "예약안내", "예약규정",
                   "단체예약", "레슨예약", "식당예약", "숙박예약", "연습장"]

URL_HINT_STRONG = ["realtime", "realtm", "teetime", "tee_time", "booking", "bookinglist"]
URL_HINT_WEAK = ["reserve", "reservation", "rsv", "resv", "book"]
URL_BLOCK = ["cancel", "confirm", "guide", "info", "notice", "member/join", "login"]


def _score_link(text: str, href: str) -> int:
    """예약 목록 페이지일 가능성을 점수로. 높을수록 먼저 시도한다."""
    t = re.sub(r"\s+", "", text.lower())
    h = href.lower()

    if any(b in t for b in LINK_TEXT_BLOCK):
        return -1
    if any(b in h for b in URL_BLOCK):
        return -1

    score = 0
    if any(w.replace(" ", "") in t for w in LINK_TEXT_STRONG):
        score += 10
    elif any(w in t for w in LINK_TEXT_WEAK):
        score += 4
    if any(w in h for w in URL_HINT_STRONG):
        score += 8
    elif any(w in h for w in URL_HINT_WEAK):
        score += 3
    return score


def find_booking_links(html: str, base_url: str, limit: int = 6) -> list[str]:
    """홈페이지에서 예약 페이지로 보이는 링크를 점수순으로 추린다."""
    root = htmlsel.parse(html)
    scored: dict[str, int] = {}

    for a in root.select("a[href]"):
        href = a.get("href", "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        text = a.text
        # 이미지 링크라 텍스트가 없으면 alt/title 을 본다
        if not text:
            img = a.select_one("img")
            if img:
                text = img.get("alt", "") or img.get("title", "")
        score = _score_link(text, href)
        if score <= 0:
            continue
        full = urllib.parse.urljoin(base_url, href)
        if not full.startswith(("http://", "https://")):
            continue
        scored[full] = max(scored.get(full, 0), score)

    return [u for u, _ in sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:limit]]


def looks_js_rendered(html: str) -> bool:
    """내용이 비어 있고 스크립트만 있는 페이지인지.

    이런 페이지는 브라우저 없이는 목록을 볼 수 없다.
    """
    root = htmlsel.parse(html)
    body_text = root.text
    if len(body_text) > 600:
        return False
    has_app_root = bool(root.select("#root, #app, [data-reactroot], [id=__next], [data-vue]"))
    script_count = len(root.select("script"))
    return has_app_root or (len(body_text) < 300 and script_count >= 3)


class SiteCrawler:
    """골프장 공식 홈페이지를 도는 소스.

    검색 엔진 입장에서는 다른 소스와 똑같이 fetch(dates) 를 부르면 된다.
    """

    id = "site"
    name = "골프장 홈페이지 직접 수집"

    def __init__(
        self,
        courses: Iterable[Course],
        store: Optional[ProfileStore] = None,
        *,
        delay_seconds: float = 1.5,
        timeout: int = 15,
        max_courses: Optional[int] = None,
        retry_failed: bool = False,
        on_progress=None,
    ):
        self.courses = list(courses)
        self.store = store if store is not None else ProfileStore.load()
        self.delay = delay_seconds
        self.timeout = timeout
        self.max_courses = max_courses
        self.retry_failed = retry_failed
        self.on_progress = on_progress
        self.last_error = ""
        self.last_stats: dict[str, Any] = {}

    # -- 한 곳 처리 ---------------------------------------------------------

    def crawl_course(self, course: Course, dates: list[date]) -> list[TeeTime]:
        """골프장 한 곳에서 티타임을 가져온다. 예외를 밖으로 내보내지 않는다."""
        profile = self.store.get(course.course_id, course.name)
        homepage = (profile.homepage or getattr(course, "homepage", "") or "").strip()

        if not homepage:
            profile.record_failure(STATUS_NO_SITE, "홈페이지 주소가 없습니다")
            return []
        profile.homepage = homepage

        client = HttpClient(timeout=self.timeout, retries=1)
        play_date = dates[0] if dates else None

        # 1) 지난번에 성공한 주소부터
        candidates: list[str] = []
        if profile.booking_url:
            candidates.append(profile.booking_url)
        candidates.extend(u for u in profile.candidates if u not in candidates)

        # 2) 없으면 홈페이지에서 예약 링크를 찾는다
        if not candidates:
            try:
                home_html = client.get(homepage)
            except Exception as exc:
                profile.record_failure(STATUS_ERROR, f"홈페이지 열기 실패: {exc}")
                return []

            profile.tech = extract_tech(home_html, homepage)
            links = find_booking_links(home_html, homepage)
            if not links:
                # 홈페이지 자체가 예약 화면인 경우도 있다
                links = [homepage]
            profile.candidates = links
            candidates = links

        # 3) 후보를 순서대로 시도
        last_status = STATUS_NO_LINK
        last_note = "예약 페이지를 찾지 못했습니다"

        for url in candidates[:5]:
            url_dated = self._apply_date(url, play_date)
            try:
                text = client.get(url_dated)
            except Exception as exc:
                last_status, last_note = STATUS_ERROR, f"{url_dated} → {exc}"
                continue

            if not profile.tech:
                profile.tech = extract_tech(text, url_dated)

            stripped = text.lstrip()
            if stripped.startswith(("{", "[")):
                try:
                    result = auto_extract_json(
                        json.loads(text), course_name=course.name,
                        source_id=self.id, play_date=play_date, base_url=url_dated)
                except json.JSONDecodeError:
                    last_status, last_note = STATUS_ERROR, "JSON 해석 실패"
                    continue
                fmt = "json"
            else:
                result = auto_extract(
                    text, course_name=course.name, source_id=self.id,
                    play_date=play_date, base_url=url_dated)
                fmt = "html"

            if result.needs_login:
                profile.record_failure(STATUS_LOGIN, "로그인 후에만 티타임이 보입니다")
                return []

            if result.tee_times:
                profile.record_success(url, len(result.tee_times), result.confidence,
                                       result.block_selector, fmt)
                return result.tee_times

            if fmt == "html" and looks_js_rendered(text):
                last_status, last_note = STATUS_JS, "자바스크립트로 그려지는 화면입니다"
            else:
                last_status, last_note = STATUS_EMPTY, result.reason

            if self.delay:
                time.sleep(self.delay)

        profile.record_failure(last_status, last_note)
        return []

    @staticmethod
    def _apply_date(url: str, play_date: Optional[date]) -> str:
        """주소에 날짜 치환자가 있으면 채운다."""
        if play_date is None or "{date" not in url:
            return url
        def repl(m):
            fmt = m.group(1) or "%Y-%m-%d"
            return play_date.strftime(fmt)
        return re.sub(r"\{date(?::([^}]+))?\}", repl, url)

    # -- 전체 --------------------------------------------------------------

    def fetch(self, dates: list[date]) -> list[TeeTime]:
        self.last_error = ""
        targets = [c for c in self.courses
                   if self.retry_failed
                   or not self.store.get(c.course_id, c.name).should_skip()]
        if self.max_courses:
            targets = targets[: self.max_courses]

        out: list[TeeTime] = []
        counts = {"ok": 0, "empty": 0, "login": 0, "js": 0, "error": 0, "nolink": 0, "nosite": 0}

        for i, course in enumerate(targets, 1):
            try:
                rows = self.crawl_course(course, dates)
            except Exception as exc:
                self.store.get(course.course_id, course.name).record_failure(
                    STATUS_ERROR, f"예외: {exc}")
                rows = []

            out.extend(rows)
            status = self.store.get(course.course_id).status
            key = {STATUS_OK: "ok", STATUS_EMPTY: "empty", STATUS_LOGIN: "login",
                   STATUS_JS: "js", STATUS_ERROR: "error", STATUS_NO_LINK: "nolink",
                   STATUS_NO_SITE: "nosite"}.get(status)
            if key:
                counts[key] += 1

            if self.on_progress:
                self.on_progress(i, len(targets), course, len(rows), status)
            if self.delay and i < len(targets):
                time.sleep(self.delay)

        self.last_stats = {"requests": len(targets), "rows": len(out),
                           "errors": [], "counts": counts}
        if not out and targets:
            self.last_error = (
                f"{len(targets)}곳을 돌았지만 티타임을 찾지 못했습니다 "
                f"(로그인 필요 {counts['login']}곳, 자바스크립트 화면 {counts['js']}곳, "
                f"매물 없음 {counts['empty']}곳)"
            )
        return out
