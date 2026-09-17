"""브라우저로 예약 목록을 읽는다.

플랫폼 예약 사이트는 대부분 목록을 자바스크립트로 그린다. 주소만 받아 오면
빈 껍데기 HTML뿐이라 아무것도 뽑을 수 없다.

두 가지를 한다.

  1. 실제 브라우저로 페이지를 열어 그려진 뒤의 화면을 읽는다
  2. 그 과정에서 오간 JSON 응답을 가로채 목록 API를 스스로 찾아낸다

2번이 핵심이다. 개발자도구를 열어 XHR을 뒤지는 일을 대신해 준다.
API를 찾으면 그 주소를 설정으로 남겨, 다음부터는 브라우저 없이 훨씬 빠르게
같은 목록을 받아 올 수 있다.

Playwright 가 필요하다. 없으면 설치 방법을 알려 준다.

    pip3 install playwright && python3 -m playwright install chromium
"""

from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from ..extract import (_find_record_arrays, _guess_json_keys, auto_extract,
                       auto_extract_json)
from ..models import TeeTime

# 목록과 무관한 잡다한 요청을 걸러낸다
_SKIP_URL = re.compile(
    r"(google|gstatic|doubleclick|facebook|analytics|gtm\.js|hotjar|"
    r"sentry|wcs\.naver|/log|/track|/beacon|\.(png|jpg|jpeg|gif|svg|webp|woff2?|css|ico)(\?|$))",
    re.IGNORECASE,
)

INSTALL_HINT = (
    "브라우저 모드에는 Playwright 가 필요합니다. 아래를 실행하세요:\n"
    "    pip3 install playwright\n"
    "    python3 -m playwright install chromium"
)


def find_chromium() -> str:
    """설치된 크로미움 실행 파일을 찾는다.

    Playwright 버전과 내려받은 브라우저 버전이 어긋나면 기본 경로로는 실행되지
    않는다. 그럴 때 실제로 있는 실행 파일을 찾아 쓴다.
    """
    env = os.environ.get("GOLF_CHROMIUM_PATH", "")
    if env and os.path.exists(env):
        return env

    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
             os.path.expanduser("~/Library/Caches/ms-playwright"),
             os.path.expanduser("~/.cache/ms-playwright"),
             "/opt/pw-browsers"]
    patterns = [
        "chromium-*/chrome-linux/chrome",
        "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
        "chromium-*/chrome-win/chrome.exe",
        "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
        "chromium_headless_shell-*/chrome-headless-shell-mac*/chrome-headless-shell",
    ]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for pat in patterns:
            hits = sorted(glob.glob(os.path.join(root, pat)), reverse=True)
            for hit in hits:
                if os.path.exists(hit):
                    return hit
    return ""


def playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass
class CapturedApi:
    """페이지가 부르는 것을 가로챈 JSON 응답 하나."""

    url: str
    method: str
    body: Any
    tee_count: int = 0          # 여기서 뽑을 수 있었던 티타임 수
    records_path: str = ""
    field_keys: dict = field(default_factory=dict)   # 어느 키가 시각/가격/이름인지

    def to_source_config(self, source_id: str, name: str) -> dict:
        """config/sources.json 에 넣을 수 있는 형태로.

        주소에 박힌 날짜를 치환자로 바꿔 매번 원하는 날짜를 조회하게 한다.
        """
        url = self.url
        for pat, repl in [
            (r"(?<!\d)20\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)", "{date:%Y%m%d}"),
            (r"(?<!\d)20\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])(?!\d)", "{date:%Y-%m-%d}"),
        ]:
            new = re.sub(pat, repl, url, count=1)
            if new != url:
                url = new
                break

        # 페이지 번호도 치환자로 바꾼다. 그대로 두면 같은 페이지를 반복해서
        # 받아 오게 되어 같은 티타임이 여러 번 쌓인다.
        paged = re.sub(r"([?&](?:page|pageNo|pageNum|pageIndex|p|currentPage)=)\d+",
                       r"\g<1>{page}", url, count=1, flags=re.IGNORECASE)
        has_page = paged != url
        url = paged
        # 어느 키에서 무엇을 읽을지 함께 적어 둔다.
        # 이것이 없으면 저장된 설정으로는 아무것도 뽑아내지 못한다.
        fields: dict[str, Any] = {}
        for name_key in ("course_name", "tee_time", "green_fee", "play_date"):
            k = self.field_keys.get(name_key)
            if k:
                fields[name_key] = {"path": k}
        if "play_date" not in fields:
            # 날짜 키가 없으면 요청에 쓴 날짜를 그대로 쓴다
            fields["play_date"] = {"from_request": "date"}

        return {
            "id": source_id,
            "name": name,
            "enabled": True,
            "format": "json",
            "respect_robots": True,
            "request": {
                "url": url,
                "method": self.method,
                "delay_seconds": 1.5,
                # 페이지 치환자가 없으면 여러 번 불러도 같은 결과가 오므로 1회만
                "pages": ({"start": 1, "max": 3, "stop_when_empty": True} if has_page
                          else {"start": 1, "max": 1, "stop_when_empty": True}),
            },
            "records_path": self.records_path,
            "fields": fields,
            "_note": "브라우저가 가로챈 API 입니다. 필드는 자동 추측이므로 확인하세요.",
        }


@dataclass
class BrowserResult:
    tee_times: list[TeeTime] = field(default_factory=list)
    from_api: bool = False           # API 응답에서 뽑았는지 (화면 읽기보다 안정적)
    apis: list[CapturedApi] = field(default_factory=list)
    reason: str = ""
    page_title: str = ""


class BrowserSource:
    """브라우저로 목록을 읽는 소스.

    느리다. 설정을 찾아내는 용도로 한 번 쓰고, 이후에는 찾아낸 API를
    WebSource 로 부르는 편이 낫다.
    """

    def __init__(
        self,
        url_template: str,
        *,
        source_id: str = "browser",
        name: str = "브라우저 수집",
        course_name: str = "",
        wait_ms: int = 3000,
        scrolls: int = 3,
        click_more: bool = True,
        headless: bool = True,
        timeout_ms: int = 30000,
        executable_path: str = "",
    ):
        self.url_template = url_template
        self.id = source_id
        self.name = name
        self.course_name = course_name
        self.wait_ms = wait_ms
        self.scrolls = scrolls
        self.click_more = click_more
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.executable_path = executable_path or os.environ.get("GOLF_CHROMIUM_PATH", "")
        self.last_error = ""
        self.last_stats: dict[str, Any] = {}
        self.last_result: Optional[BrowserResult] = None

    # -- 주소 만들기 --------------------------------------------------------

    def _render(self, play_date: Optional[date]) -> str:
        if play_date is None or "{date" not in self.url_template:
            return self.url_template
        def repl(m):
            return play_date.strftime(m.group(1) or "%Y-%m-%d")
        return re.sub(r"\{date(?::([^}]+))?\}", repl, self.url_template)

    # -- 수집 ---------------------------------------------------------------

    def open_and_capture(self, play_date: Optional[date] = None) -> BrowserResult:
        """페이지를 열고 화면과 오간 JSON을 모두 살펴본다."""
        result = BrowserResult()
        if not playwright_available():
            result.reason = INSTALL_HINT
            return result

        from playwright.sync_api import sync_playwright

        url = self._render(play_date)
        captured: list[tuple[str, str, Any]] = []

        try:
            with sync_playwright() as p:
                launch_args = {"headless": self.headless}
                if self.executable_path:
                    launch_args["executable_path"] = self.executable_path
                try:
                    browser = p.chromium.launch(**launch_args)
                except Exception:
                    # 기본 경로에 없으면 실제로 설치된 실행 파일을 찾아 다시 시도한다
                    found = find_chromium()
                    if not found:
                        raise
                    launch_args["executable_path"] = found
                    browser = p.chromium.launch(**launch_args)
                context = browser.new_context(
                    locale="ko-KR",
                    viewport={"width": 1400, "height": 1000},
                    user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/122.0.0.0 Safari/537.36"),
                )
                page = context.new_page()

                def on_response(resp):
                    try:
                        u = resp.url
                        if _SKIP_URL.search(u) or not resp.ok:
                            return
                        ctype = (resp.header_value("content-type") or "").lower()
                        if "json" not in ctype:
                            return
                        captured.append((u, resp.request.method, resp.json()))
                    except Exception:
                        pass       # 본문을 못 읽는 응답은 그냥 넘어간다

                page.on("response", on_response)
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                page.wait_for_timeout(self.wait_ms)

                # 목록이 스크롤로 채워지는 경우가 많다
                for _ in range(self.scrolls):
                    page.mouse.wheel(0, 3000)
                    page.wait_for_timeout(800)

                if self.click_more:
                    self._click_more_buttons(page)

                result.page_title = page.title()
                html = page.content()
                browser.close()
        except Exception as exc:
            result.reason = f"브라우저 실행 실패: {exc}"
            return result

        # 1) 가로챈 JSON 에서 티타임을 찾아본다 (화면 읽기보다 안정적)
        for u, method, body in captured:
            r = auto_extract_json(body, course_name=self.course_name or "",
                                  source_id=self.id, play_date=play_date, base_url=u)
            if r.tee_times:
                keys = {}
                for path, arr in _find_record_arrays(body):
                    if path == r.block_selector:
                        keys = _guess_json_keys(arr)
                        break
                result.apis.append(CapturedApi(u, method, body, len(r.tee_times),
                                               r.block_selector, keys))

        if result.apis:
            best = max(result.apis, key=lambda a: a.tee_count)
            r = auto_extract_json(best.body, course_name=self.course_name or "",
                                  source_id=self.id, play_date=play_date, base_url=best.url)
            result.tee_times = r.tee_times
            result.from_api = True
            result.reason = (f"목록 API 를 찾았습니다: {best.url[:80]} "
                             f"({best.records_path}, {best.tee_count}건)")
            return result

        # 2) API 로 못 찾으면 그려진 화면을 읽는다
        r = auto_extract(html, course_name=self.course_name or "", source_id=self.id,
                         play_date=play_date, base_url=url)
        result.tee_times = r.tee_times
        result.reason = r.reason if r.tee_times else (
            r.reason + " / JSON 응답 " + str(len(captured)) + "건을 살폈지만 목록이 없었습니다")
        if r.needs_login:
            result.reason = "로그인해야 목록이 보이는 화면입니다"
        return result

    @staticmethod
    def _click_more_buttons(page, rounds: int = 3) -> None:
        """'더보기' 류 버튼을 눌러 목록을 더 불러온다."""
        labels = ["더보기", "더 보기", "더불러오기", "다음", "more", "load more"]
        for _ in range(rounds):
            clicked = False
            for label in labels:
                try:
                    btn = page.get_by_text(label, exact=False).first
                    if btn.is_visible(timeout=600):
                        btn.click(timeout=1500)
                        page.wait_for_timeout(1200)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                break

    # -- 소스 인터페이스 ----------------------------------------------------

    def fetch(self, dates: list[date]) -> list[TeeTime]:
        self.last_error = ""
        out: list[TeeTime] = []
        for d in (dates or [None]):
            result = self.open_and_capture(d)
            self.last_result = result
            out.extend(result.tee_times)
            if not result.tee_times and not self.last_error:
                self.last_error = result.reason
        self.last_stats = {"requests": len(dates or [1]), "rows": len(out), "errors": []}
        return out
