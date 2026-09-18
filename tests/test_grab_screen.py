#!/usr/bin/env python3
"""보고 있는 화면을 그대로 읽는 기능 테스트.

    python3 tests/test_grab_screen.py

예약 사이트는 날짜·시간을 드롭다운으로 고르고 검색을 눌러야 목록이 나오는
경우가 많다. 그런 화면은 주소만으로는 재현되지 않으므로, 사람이 만들어 둔
화면을 그대로 읽어야 한다.
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
from datetime import date, time as dtime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_v, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import golf.sources.browser_source as BS                       # noqa: E402
from golf.extract import auto_extract                          # noqa: E402
from golf import htmlsel                                       # noqa: E402
from golf.sources.web_source import HttpClient                 # noqa: E402
from tests.fake_dropdown_site import start                     # noqa: E402

D = date(2026, 9, 20)


class TestDropdownHandling(unittest.TestCase):
    """드롭다운은 고르지 않은 값까지 다 들고 있어 그대로 읽으면 안 된다."""

    def test_unselected_options_are_ignored(self):
        html = """<table><tr class="r">
            <td>남서울CC</td>
            <td><select name="t">
                <option value="0600">06:00</option>
                <option value="1100" selected>11:20</option>
                <option value="1500">15:00</option>
            </select></td>
            <td>168,000원</td></tr>
          <tr class="r">
            <td>레이크사이드</td>
            <td><select name="t">
                <option value="0600">06:00</option>
                <option value="1240" selected>12:40</option>
            </select></td>
            <td>132,000원</td></tr>
          <tr class="r">
            <td>블루원</td>
            <td><select name="t">
                <option value="1320" selected>13:20</option>
                <option value="1500">15:00</option>
            </select></td>
            <td>98,000원</td></tr></table>"""
        r = auto_extract(html, source_id="x", play_date=D)
        times = sorted(t.tee_time for t in r.tee_times)
        self.assertEqual(times, [dtime(11, 20), dtime(12, 40), dtime(13, 20)])

    def test_option_is_never_a_list_candidate(self):
        """드롭다운 항목이 여러 개라고 티타임 목록으로 보면 안 된다."""
        from golf.extract import find_repeating_blocks
        html = """<select>
            <option>06:00</option><option>07:00</option>
            <option>08:00</option><option>09:00</option></select>"""
        blocks = find_repeating_blocks(htmlsel.parse(html))
        self.assertEqual([sig for sig, _ in blocks if sig.startswith("option")], [])


@unittest.skipUnless(BS.playwright_available() and BS.find_chromium(),
                     "Playwright 또는 크로미움이 없음")
class TestGrabOpenTab(unittest.TestCase):
    PORT = 9377

    @classmethod
    def setUpClass(cls):
        cls.srv, cls.base = start()
        cls.profile = tempfile.mkdtemp()
        cls.proc = subprocess.Popen(
            [BS.find_chromium(), f"--remote-debugging-port={cls.PORT}",
             f"--user-data-dir={cls.profile}", "--headless=new", "--no-sandbox",
             "--no-first-run", cls.base + "/booking"],
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

    def _search_in_browser(self):
        """사용자가 드롭다운을 고르고 검색을 누르는 상황."""
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.connect_over_cdp(self.cdp)
            pg = [x for c in b.contexts for x in c.pages][0]
            pg.wait_for_load_state("domcontentloaded")
            pg.select_option("#tee", "1100")
            pg.click("#go")
            pg.wait_for_timeout(500)
            b.close()

    def test_plain_request_cannot_see_the_list(self):
        """주소만으로는 목록을 못 본다는 것이 이 기능의 존재 이유다."""
        html = HttpClient().get(self.base + "/booking")
        r = auto_extract(html, source_id="x", play_date=D)
        self.assertEqual(len(r.tee_times), 0)

    def test_lists_open_tabs(self):
        tabs = BS.list_open_tabs(self.cdp)
        self.assertTrue(tabs)
        self.assertTrue(any("/booking" in t["url"] for t in tabs))

    def test_reads_the_screen_the_user_made(self):
        self._search_in_browser()
        res = BS.capture_open_tab(self.cdp, tab_index=0, source_id="grab",
                                  play_date=D)
        self.assertEqual(len(res.tee_times), 4, res.reason)
        names = [t.course_name for t in res.tee_times]
        self.assertIn("남서울컨트리클럽", names)
        self.assertEqual(res.tee_times[0].green_fee, 168000)

    def test_dropdown_values_do_not_leak_in(self):
        """폼의 06:00·15:00 가 결과에 섞이면 안 된다."""
        self._search_in_browser()
        res = BS.capture_open_tab(self.cdp, tab_index=0, source_id="grab",
                                  play_date=D)
        times = {f"{t.tee_time:%H:%M}" for t in res.tee_times}
        self.assertNotIn("06:00", times)
        self.assertNotIn("15:00", times)

    def test_browser_and_tabs_survive(self):
        """읽기만 할 뿐, 사용자의 브라우저를 건드리지 않는다."""
        before = len(BS.list_open_tabs(self.cdp))
        BS.capture_open_tab(self.cdp, tab_index=0, source_id="grab", play_date=D)
        self.assertIsNone(self.proc.poll())
        self.assertEqual(len(BS.list_open_tabs(self.cdp)), before)

    def test_bad_cdp_url_is_reported(self):
        res = BS.capture_open_tab("http://127.0.0.1:1", source_id="grab")
        self.assertEqual(len(res.tee_times), 0)
        self.assertTrue(res.reason)


@unittest.skipUnless(BS.playwright_available() and BS.find_chromium(),
                     "Playwright 또는 크로미움이 없음")
class TestGrabMultipleDates(unittest.TestCase):
    """열린 탭에서 날짜만 눌러 가며 여러 날을 모은다.

    사람이 로그인하고 조건을 골라 만들어 둔 화면을 그대로 쓰므로, 주소를
    복사해 오거나 매번 조건을 다시 고를 필요가 없다.
    """

    PORT = 9399
    DATES = [date(2026, 9, 20), date(2026, 9, 21), date(2026, 9, 22)]
    EXPECTED = {
        DATES[0]: {"가나컨트리클럽", "나다컨트리클럽", "다라컨트리클럽"},
        DATES[1]: {"마바컨트리클럽", "바사컨트리클럽"},
        DATES[2]: {"사아컨트리클럽"},
    }

    @classmethod
    def setUpClass(cls):
        from tests.fake_datepick_site import start as start_datepick
        cls.srv, cls.base = start_datepick()
        cls.profile = tempfile.mkdtemp()
        cls.proc = subprocess.Popen(
            [BS.find_chromium(), f"--remote-debugging-port={cls.PORT}",
             f"--user-data-dir={cls.profile}", "--headless=new", "--no-sandbox",
             "--no-first-run", cls.base + "/tabs"],
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

    def test_collects_each_date_separately(self):
        results = BS.capture_open_tab_dates(self.cdp, self.DATES, tab_index=0,
                                            source_id="grab")
        for d in self.DATES:
            names = {t.course_name for t in results[d].tee_times}
            self.assertEqual(names, self.EXPECTED[d], f"{d}: {results[d].reason}")

    def test_does_not_open_extra_tabs(self):
        before = len(BS.list_open_tabs(self.cdp))
        BS.capture_open_tab_dates(self.cdp, self.DATES, tab_index=0,
                                  source_id="grab")
        self.assertEqual(len(BS.list_open_tabs(self.cdp)), before)
        self.assertIsNone(self.proc.poll())


if __name__ == "__main__":
    unittest.main(verbosity=2)
