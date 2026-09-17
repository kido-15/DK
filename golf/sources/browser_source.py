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
import sys
import urllib.parse
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

# 로그인 세션(쿠키)을 담아 두는 곳. 사이트마다 폴더를 따로 쓴다.
# 여기에는 쿠키와 브라우저 프로필만 들어가며, 아이디나 비밀번호는 저장하지 않는다.
SESSION_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "golf", "sessions",
)


def session_dir(site_id: str) -> str:
    """사이트별 로그인 세션 폴더 경로."""
    safe = re.sub(r"[^\w.-]", "_", site_id or "default")
    return os.path.join(SESSION_ROOT, safe)


def has_session(site_id: str) -> bool:
    d = session_dir(site_id)
    return os.path.isdir(d) and bool(os.listdir(d))


def cookie_file(site_id: str) -> str:
    """브라우저 없이 쓸 수 있도록 꺼내 둔 쿠키 파일."""
    return os.path.join(session_dir(site_id), "cookies.json")


def save_cookies(site_id: str, cookies: list) -> str:
    """로그인 쿠키를 파일로 남긴다.

    목록 API 를 찾은 뒤에는 브라우저 없이 일반 요청으로 부르는 편이 훨씬 빠른데,
    로그인이 필요한 사이트라면 그 요청에도 쿠키가 있어야 한다.
    """
    d = session_dir(site_id)
    os.makedirs(d, exist_ok=True)
    path = cookie_file(site_id)
    keep = [{"name": c.get("name"), "value": c.get("value"),
             "domain": c.get("domain", ""), "path": c.get("path", "/")}
            for c in cookies if c.get("name")]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"cookies": keep}, f, ensure_ascii=False)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)      # 로그인 상태가 담기므로 본인만 읽게
    except OSError:
        pass
    return path


def cookie_header(site_id: str, url: str = "") -> str:
    """저장해 둔 쿠키를 'a=1; b=2' 형태로. 없으면 빈 문자열.

    url 을 주면 그 도메인에 해당하는 쿠키만 고른다.
    """
    path = cookie_file(site_id)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return ""

    host = ""
    if url:
        try:
            host = urllib.parse.urlsplit(url).netloc.lower().split(":")[0]
        except ValueError:
            host = ""

    parts = []
    for c in data.get("cookies") or []:
        domain = (c.get("domain") or "").lstrip(".").lower()
        if host and domain and not (host == domain or host.endswith("." + domain)):
            continue
        if c.get("name") and c.get("value") is not None:
            parts.append(f"{c['name']}={c['value']}")
    return "; ".join(parts)


def clear_session(site_id: str) -> bool:
    """저장된 로그인 세션을 지운다."""
    import shutil
    d = session_dir(site_id)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False


INSTALL_HINT = (
    "브라우저 모드에는 Playwright 가 필요합니다. 아래를 실행하세요:\n"
    "    pip3 install playwright\n"
    "    python3 -m playwright install chromium"
)


# ---------------------------------------------------------------------------
# 어떤 브라우저를 쓸지 고르기
#
# Playwright 가 제어할 수 있는 것은 크로미움 계열, 파이어폭스, 웹킷뿐이다.
# 크로미움 계열이라면 평소 쓰는 브라우저를 그대로 지정할 수 있다.
# ---------------------------------------------------------------------------

# 앱 이름으로 찾아볼 후보들. 여기에 없는 브라우저도 경로를 직접 주면 된다.
MAC_APP_DIRS = ["/Applications", os.path.expanduser("~/Applications")]
LINUX_BIN_NAMES = [
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "microsoft-edge", "brave-browser", "vivaldi", "opera",
]
WIN_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
]


def _mac_app_executable(app_path: str) -> str:
    """맥 앱 번들에서 실행 파일 경로를 꺼낸다."""
    macos_dir = os.path.join(app_path, "Contents", "MacOS")
    if not os.path.isdir(macos_dir):
        return ""

    # Info.plist 에 적힌 실행 파일 이름을 먼저 본다 (앱 이름과 다를 수 있다)
    plist = os.path.join(app_path, "Contents", "Info.plist")
    if os.path.exists(plist):
        try:
            import plistlib
            with open(plist, "rb") as f:
                name = (plistlib.load(f) or {}).get("CFBundleExecutable", "")
            cand = os.path.join(macos_dir, name) if name else ""
            if cand and os.path.exists(cand):
                return cand
        except Exception:
            pass

    entries = [os.path.join(macos_dir, n) for n in os.listdir(macos_dir)]
    files = [e for e in entries if os.path.isfile(e) and os.access(e, os.X_OK)]
    return files[0] if files else ""


def _is_chromium_bundle(app_path: str) -> bool:
    """맥 앱이 크로미움 기반인지 짐작한다.

    크로미움 기반 앱은 'XXX Framework.framework' 를 함께 담고 있다.
    확실한 판별은 아니지만, 실행해 보기 전에 거르는 데는 쓸 만하다.
    """
    fw = os.path.join(app_path, "Contents", "Frameworks")
    if not os.path.isdir(fw):
        return False
    try:
        return any(n.endswith("Framework.framework") for n in os.listdir(fw))
    except OSError:
        return False


def discover_browsers() -> list[dict]:
    """이 컴퓨터에 설치된 브라우저를 찾는다.

    [{"name": "Arc", "path": "...", "chromium": True}, ...] 형태로 돌려준다.
    chromium 이 False 인 것은 Playwright 로 제어되지 않을 가능성이 크다.
    """
    found: list[dict] = []
    seen: set[str] = set()

    def add(name: str, path: str, chromium: bool):
        if not path or path in seen or not os.path.exists(path):
            return
        seen.add(path)
        found.append({"name": name, "path": path, "chromium": chromium})

    if sys.platform == "darwin":
        for d in MAC_APP_DIRS:
            if not os.path.isdir(d):
                continue
            try:
                entries = sorted(os.listdir(d))
            except OSError:
                continue
            for entry in entries:
                if not entry.endswith(".app"):
                    continue
                app = os.path.join(d, entry)
                exe = _mac_app_executable(app)
                if not exe:
                    continue
                chromium = _is_chromium_bundle(app)
                # 브라우저로 보이는 앱만 목록에 올린다
                low = entry.lower()
                looks_browser = chromium or any(
                    k in low for k in ("browser", "chrome", "chromium", "edge",
                                       "brave", "arc", "safari", "firefox",
                                       "vivaldi", "opera", "whale", "aside"))
                if looks_browser:
                    add(entry[:-4], exe, chromium)
    elif sys.platform.startswith("win"):
        for path in WIN_CANDIDATES:
            add(os.path.basename(path), path, True)
    else:
        import shutil as _shutil
        for name in LINUX_BIN_NAMES:
            add(name, _shutil.which(name) or "", True)

    # Playwright 가 내려받아 둔 크로미움도 후보에 넣는다
    bundled = find_chromium()
    if bundled:
        add("Playwright 내장 크로미움", bundled, True)
    return found


def resolve_browser(name_or_path: str) -> str:
    """브라우저 이름이나 경로를 실행 파일 경로로 바꾼다. 못 찾으면 빈 문자열."""
    if not name_or_path:
        return ""

    # 경로를 직접 준 경우
    if os.path.exists(name_or_path):
        if name_or_path.endswith(".app"):
            return _mac_app_executable(name_or_path)
        return name_or_path

    want = name_or_path.strip().lower()
    browsers = discover_browsers()
    for b in browsers:                      # 이름이 정확히 같은 것 먼저
        if b["name"].lower() == want:
            return b["path"]
    for b in browsers:                      # 그다음 부분 일치
        if want in b["name"].lower():
            return b["path"]
    return ""


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


CONTEXT_OPTIONS = {
    "locale": "ko-KR",
    "viewport": {"width": 1400, "height": 1000},
    "user_agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/122.0.0.0 Safari/537.36"),
}


def open_context(p, *, site_id: str = "", headless: bool = True,
                 executable_path: str = "", use_session: bool = True,
                 cdp_url: str = ""):
    """브라우저를 연다. 저장된 로그인 세션이 있으면 그대로 이어서 쓴다.

    cdp_url 을 주면 새로 띄우지 않고 **이미 열려 있는 브라우저에 붙는다.**
    평소 쓰는 브라우저를 원격 디버깅 포트와 함께 켜 두었다면, 거기 로그인된
    상태를 그대로 쓸 수 있다.

    (browser, context) 를 돌려준다. 세션을 쓰는 경우 browser 는 None 이다
    (persistent context 는 브라우저 객체를 따로 주지 않는다).
    """
    exe = executable_path or os.environ.get("GOLF_BROWSER_PATH", "")
    cdp_url = cdp_url or os.environ.get("GOLF_BROWSER_CDP", "")

    if cdp_url:
        # 이미 떠 있는 브라우저에 붙는다. 그 브라우저의 쿠키와 로그인 상태를
        # 그대로 쓰므로, 따로 로그인할 필요가 없다.
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context(
            **CONTEXT_OPTIONS)
        return browser, context

    def _with_fallback(fn, args: dict):
        """설치된 크로미움 경로가 어긋나면 찾아서 다시 시도한다."""
        try:
            return fn(**args)
        except Exception:
            found = find_chromium()
            if not found or args.get("executable_path") == found:
                raise
            args["executable_path"] = found
            return fn(**args)

    if use_session and site_id:
        d = session_dir(site_id)
        os.makedirs(d, exist_ok=True)
        try:
            os.chmod(d, 0o700)      # 로그인 쿠키가 들어가므로 본인만 읽게
        except OSError:
            pass
        args = {"user_data_dir": d, "headless": headless, **CONTEXT_OPTIONS}
        if exe:
            args["executable_path"] = exe
        context = _with_fallback(p.chromium.launch_persistent_context, args)
        return None, context

    args = {"headless": headless}
    if exe:
        args["executable_path"] = exe
    browser = _with_fallback(p.chromium.launch, args)
    return browser, browser.new_context(**CONTEXT_OPTIONS)


def close_context(browser, context, *, attached: bool = False, page=None) -> None:
    """열었던 것을 정리한다.

    attached 는 이미 떠 있던 브라우저에 붙은 경우다. 그 브라우저는 사용자의
    것이므로 닫지 않는다. 우리가 연 탭만 닫고 연결을 끊는다.
    """
    if attached:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()      # CDP 연결만 끊는다. 브라우저는 계속 떠 있다
            except Exception:
                pass
        return

    try:
        context.close()
    except Exception:
        pass
    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass


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
        use_session: bool = True,
        browser: str = "",
        cdp_url: str = "",
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
        # 쓸 브라우저를 정한다. 이름(예: "Arc")을 주면 설치된 것을 찾아 쓴다.
        self.executable_path = (
            executable_path
            or (resolve_browser(browser) if browser else "")
            or os.environ.get("GOLF_BROWSER_PATH", "")
            or os.environ.get("GOLF_CHROMIUM_PATH", "")
        )
        # 이미 열려 있는 브라우저에 붙을 주소 (예: http://localhost:9222)
        self.cdp_url = cdp_url or os.environ.get("GOLF_BROWSER_CDP", "")
        # 저장된 로그인 세션이 있으면 쓴다. 없으면 평소처럼 익명으로 연다.
        self.use_session = use_session
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
                browser, context = open_context(
                    p, site_id=self.id, headless=self.headless,
                    executable_path=self.executable_path,
                    use_session=self.use_session and not self.cdp_url,
                    cdp_url=self.cdp_url)
                # 붙은 브라우저라면 사용자가 보던 탭을 건드리지 않도록 새 탭을 연다
                if self.cdp_url:
                    page = context.new_page()
                else:
                    page = context.pages[0] if context.pages else context.new_page()

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
                if self.use_session and self.id:
                    try:
                        save_cookies(self.id, context.cookies())
                    except Exception:
                        pass       # 쿠키를 못 꺼내도 수집 자체는 계속한다
                close_context(browser, context,
                              attached=bool(self.cdp_url), page=page)
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
