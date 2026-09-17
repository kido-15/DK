#!/usr/bin/env python3
"""쓸 브라우저 고르기 / 이미 열린 브라우저에 붙기 테스트.

    python3 tests/test_browser_choice.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_v, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import golf.sources.browser_source as BS                       # noqa: E402
from tests.fake_login_site import start                        # noqa: E402

D = date(2026, 9, 20)


class TestBrowserDiscovery(unittest.TestCase):
    def test_discover_returns_shape(self):
        for b in BS.discover_browsers():
            self.assertIn("name", b)
            self.assertIn("path", b)
            self.assertIn("chromium", b)
            self.assertTrue(os.path.exists(b["path"]), b["path"])

    def test_bundled_chromium_is_listed(self):
        """Playwright 가 내려받은 크로미움은 언제나 후보에 있어야 한다."""
        if not BS.find_chromium():
            self.skipTest("내장 크로미움이 없음")
        names = [b["name"] for b in BS.discover_browsers()]
        self.assertTrue(any("크로미움" in n or "chromium" in n.lower() for n in names))

    def test_resolve_by_name(self):
        browsers = BS.discover_browsers()
        if not browsers:
            self.skipTest("설치된 브라우저 없음")
        target = browsers[0]
        self.assertEqual(BS.resolve_browser(target["name"]), target["path"])

    def test_resolve_by_path(self):
        exe = BS.find_chromium()
        if not exe:
            self.skipTest("내장 크로미움이 없음")
        self.assertEqual(BS.resolve_browser(exe), exe)

    def test_unknown_name_returns_empty(self):
        self.assertEqual(BS.resolve_browser("존재하지않는브라우저이름"), "")
        self.assertEqual(BS.resolve_browser(""), "")


@unittest.skipUnless(BS.playwright_available() and BS.find_chromium(),
                     "Playwright 또는 크로미움이 없음")
class TestAttachToRunningBrowser(unittest.TestCase):
    """이미 떠 있는 브라우저에 붙는 모드.

    평소 쓰는 브라우저에 로그인해 둔 상태를 그대로 쓰기 위한 기능이다.
    """

    PORT = 9411

    @classmethod
    def setUpClass(cls):
        cls.srv, cls.base = start()
        cls.profile = tempfile.mkdtemp()
        cls.chrome = BS.find_chromium()
        cls.proc = subprocess.Popen(
            [cls.chrome, f"--remote-debugging-port={cls.PORT}",
             f"--user-data-dir={cls.profile}", "--headless=new", "--no-sandbox",
             "--no-first-run", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.cdp = f"http://127.0.0.1:{cls.PORT}"
        for _ in range(50):
            try:
                urllib.request.urlopen(cls.cdp + "/json/version", timeout=1).read()
                break
            except Exception:
                time.sleep(0.3)
        else:
            raise unittest.SkipTest("브라우저를 띄우지 못함")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except Exception:
            cls.proc.kill()
        shutil.rmtree(cls.profile, ignore_errors=True)

    def setUp(self):
        self._orig = BS.SESSION_ROOT
        self.tmp = tempfile.mkdtemp()
        BS.SESSION_ROOT = self.tmp

    def tearDown(self):
        BS.SESSION_ROOT = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _login_in_running_browser(self):
        """사용자가 자기 브라우저에서 직접 로그인하는 상황."""
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.connect_over_cdp(self.cdp)
            ctx = b.contexts[0] if b.contexts else b.new_context()
            pg = ctx.new_page()
            pg.goto(self.base + "/do-login", wait_until="domcontentloaded")
            pg.wait_for_timeout(300)
            pg.close()
            b.close()

    def test_uses_existing_login(self):
        """붙은 브라우저에 로그인돼 있으면 따로 로그인할 필요가 없다."""
        self._login_in_running_browser()
        src = BS.BrowserSource(self.base + "/list", source_id="attached",
                               cdp_url=self.cdp, wait_ms=1200, scrolls=0)
        r = src.open_and_capture(D)
        self.assertEqual(len(r.tee_times), 3, r.reason)
        self.assertEqual(r.tee_times[0].course_name, "남서울컨트리클럽")

    def test_does_not_close_the_users_browser(self):
        """붙은 브라우저는 사용자 것이므로 닫으면 안 된다."""
        src = BS.BrowserSource(self.base + "/list", source_id="attached",
                               cdp_url=self.cdp, wait_ms=800, scrolls=0)
        src.open_and_capture(D)
        self.assertIsNone(self.proc.poll(), "브라우저 프로세스가 죽었다")
        urllib.request.urlopen(self.cdp + "/json/version", timeout=3).read()

    def test_leaves_no_extra_tabs(self):
        """우리가 연 탭은 우리가 닫아야 한다."""
        def tab_count() -> int:
            import json as _json
            raw = urllib.request.urlopen(self.cdp + "/json/list", timeout=3).read()
            return len([t for t in _json.loads(raw) if t.get("type") == "page"])

        before = tab_count()
        src = BS.BrowserSource(self.base + "/list", source_id="attached",
                               cdp_url=self.cdp, wait_ms=800, scrolls=0)
        src.open_and_capture(D)
        time.sleep(1.0)
        self.assertLessEqual(tab_count(), before)

    def test_cookies_saved_for_later_plain_requests(self):
        """붙어서 읽은 뒤에는 브라우저 없이도 쓸 수 있어야 한다."""
        self._login_in_running_browser()
        src = BS.BrowserSource(self.base + "/list", source_id="attached",
                               cdp_url=self.cdp, wait_ms=1000, scrolls=0)
        src.open_and_capture(D)
        self.assertIn("fake_session",
                      BS.cookie_header("attached", self.base + "/list"))

    def test_env_var_is_honored(self):
        os.environ["GOLF_BROWSER_CDP"] = self.cdp
        try:
            src = BS.BrowserSource(self.base + "/list", source_id="attached")
            self.assertEqual(src.cdp_url, self.cdp)
        finally:
            os.environ.pop("GOLF_BROWSER_CDP", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
