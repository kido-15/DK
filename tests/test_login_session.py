#!/usr/bin/env python3
"""로그인이 필요한 사이트 수집 테스트.

    python3 tests/test_login_session.py

엑스골프·카카오골프예약처럼 회원만 티타임을 볼 수 있는 사이트를 위한 기능이다.
사용자가 직접 로그인한 브라우저 세션을 저장해 두고, 이후 수집에 재사용한다.
아이디와 비밀번호는 다루지 않으며 쿠키만 저장한다.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_v, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import golf.sources.browser_source as BS                        # noqa: E402
from golf.sources.web_source import WebSource                   # noqa: E402
from tests.fake_login_site import start                         # noqa: E402

D = date(2026, 9, 20)
SITE = "testlogin"


class TestCookieStore(unittest.TestCase):
    """쿠키 저장과 읽기. 네트워크 없이 도는 부분."""

    def setUp(self):
        self._orig = BS.SESSION_ROOT
        self.tmp = tempfile.mkdtemp()
        BS.SESSION_ROOT = self.tmp

    def tearDown(self):
        BS.SESSION_ROOT = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_and_read_back(self):
        BS.save_cookies(SITE, [{"name": "sid", "value": "abc", "domain": "x.com"},
                               {"name": "tok", "value": "123", "domain": "x.com"}])
        header = BS.cookie_header(SITE, "https://x.com/api")
        self.assertIn("sid=abc", header)
        self.assertIn("tok=123", header)

    def test_only_matching_domain_is_sent(self):
        """다른 사이트 쿠키를 함께 보내면 안 된다."""
        BS.save_cookies(SITE, [{"name": "mine", "value": "1", "domain": "x.com"},
                               {"name": "other", "value": "2", "domain": "y.com"}])
        header = BS.cookie_header(SITE, "https://x.com/api")
        self.assertIn("mine=1", header)
        self.assertNotIn("other=2", header)

    def test_subdomain_matches(self):
        BS.save_cookies(SITE, [{"name": "sid", "value": "a", "domain": ".x.com"}])
        self.assertIn("sid=a", BS.cookie_header(SITE, "https://api.x.com/list"))

    def test_no_session_returns_empty(self):
        self.assertEqual(BS.cookie_header("없는사이트", "https://x.com"), "")

    def test_file_permissions_are_restricted(self):
        """로그인 상태가 담기므로 남이 읽을 수 있으면 안 된다."""
        path = BS.save_cookies(SITE, [{"name": "sid", "value": "a", "domain": "x.com"}])
        mode = oct(os.stat(path).st_mode)[-3:]
        self.assertEqual(mode, "600")

    def test_only_cookies_are_stored(self):
        """아이디·비밀번호가 섞여 들어가지 않아야 한다."""
        path = BS.save_cookies(SITE, [{"name": "sid", "value": "a", "domain": "x.com",
                                       "httpOnly": True, "secure": True}])
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        keys = set(data["cookies"][0])
        self.assertEqual(keys, {"name", "value", "domain", "path"})

    def test_clear_removes_session(self):
        BS.save_cookies(SITE, [{"name": "sid", "value": "a", "domain": "x.com"}])
        self.assertTrue(BS.has_session(SITE))
        self.assertTrue(BS.clear_session(SITE))
        self.assertFalse(BS.has_session(SITE))
        self.assertEqual(BS.cookie_header(SITE, "https://x.com"), "")

    def test_site_id_is_sanitized(self):
        """사이트 이름이 경로를 벗어나지 않아야 한다."""
        d = BS.session_dir("../../etc/passwd")
        self.assertTrue(os.path.abspath(d).startswith(os.path.abspath(self.tmp)))


class TestLoginRequiredSource(unittest.TestCase):
    """로그인이 필요한 사이트에 대고 실제 HTTP 요청을 보낸다."""

    @classmethod
    def setUpClass(cls):
        cls.srv, cls.base = start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self._orig = BS.SESSION_ROOT
        self.tmp = tempfile.mkdtemp()
        BS.SESSION_ROOT = self.tmp

    def tearDown(self):
        BS.SESSION_ROOT = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg(self, use_session: bool) -> dict:
        return {
            "id": SITE, "name": "로그인필요", "enabled": True, "format": "json",
            "use_session": use_session,
            "request": {"url": self.base + "/api/list", "delay_seconds": 0,
                        "pages": {"start": 1, "max": 1}},
            "records_path": "data.list",
            "fields": {"course_name": {"path": "ccName"},
                       "tee_time": {"path": "teeTime"},
                       "green_fee": {"path": "greenFee"},
                       "play_date": {"from_request": "date"}},
        }

    def test_without_session_explains_what_to_do(self):
        src = WebSource(self._cfg(True))
        self.assertEqual(src.fetch([D]), [])
        self.assertIn("login.py", src.last_error)

    def test_with_cookie_collects_without_browser(self):
        """로그인 쿠키만 있으면 브라우저 없이 목록을 받아 온다."""
        BS.save_cookies(SITE, [{"name": "fake_session", "value": "ok",
                                "domain": "127.0.0.1", "path": "/"}])
        src = WebSource(self._cfg(True))
        rows = src.fetch([D])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].course_name, "남서울컨트리클럽")

    def test_session_not_used_when_not_requested(self):
        """use_session 이 없으면 쿠키가 있어도 보내지 않는다."""
        BS.save_cookies(SITE, [{"name": "fake_session", "value": "ok",
                                "domain": "127.0.0.1", "path": "/"}])
        self.assertEqual(WebSource(self._cfg(False)).fetch([D]), [])

    def test_expired_session_is_detected_as_login_wall(self):
        """세션이 만료되면 목록 대신 로그인 화면이 온다."""
        BS.save_cookies(SITE, [{"name": "wrong_cookie", "value": "x",
                                "domain": "127.0.0.1", "path": "/"}])
        src = WebSource(self._cfg(True))
        self.assertEqual(src.fetch([D]), [])


@unittest.skipUnless(BS.playwright_available(), "Playwright 가 설치되지 않음")
class TestBrowserSessionReuse(unittest.TestCase):
    """브라우저가 로그인 상태를 기억하는지."""

    @classmethod
    def setUpClass(cls):
        cls.srv, cls.base = start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self._orig = BS.SESSION_ROOT
        self.tmp = tempfile.mkdtemp()
        BS.SESSION_ROOT = self.tmp

    def tearDown(self):
        BS.SESSION_ROOT = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _login(self):
        """사용자가 브라우저 창에서 직접 하는 로그인을 대신한다."""
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser, ctx = BS.open_context(p, site_id=SITE, headless=True,
                                           use_session=True)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(self.base + "/do-login", wait_until="domcontentloaded")
            page.wait_for_timeout(300)
            BS.save_cookies(SITE, ctx.cookies())
            BS.close_context(browser, ctx)

    def test_blocked_before_login(self):
        src = BS.BrowserSource(self.base + "/list", source_id=SITE,
                               wait_ms=1200, scrolls=0)
        r = src.open_and_capture(D)
        self.assertEqual(len(r.tee_times), 0)
        self.assertIn("로그인", r.reason)

    def test_works_after_login_in_new_browser(self):
        """로그인한 브라우저를 닫았다 새로 열어도 유지돼야 한다."""
        self._login()
        src = BS.BrowserSource(self.base + "/list", source_id=SITE,
                               wait_ms=1200, scrolls=0)
        r = src.open_and_capture(D)
        self.assertEqual(len(r.tee_times), 3)
        self.assertEqual(r.tee_times[0].course_name, "남서울컨트리클럽")

    def test_blocked_again_after_clearing(self):
        self._login()
        BS.clear_session(SITE)
        src = BS.BrowserSource(self.base + "/list", source_id=SITE,
                               wait_ms=1200, scrolls=0)
        self.assertEqual(len(src.open_and_capture(D).tee_times), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
