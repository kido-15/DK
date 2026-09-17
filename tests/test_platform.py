#!/usr/bin/env python3
"""플랫폼(가격 모음 사이트) 수집 테스트.

    python3 tests/test_platform.py

브라우저 모드는 Playwright 가 있을 때만 돈다.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_v, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

from golf.courses import CourseBook                              # noqa: E402
from golf.extract import auto_extract, extract_course_name       # noqa: E402
from golf.models import Course, SearchQuery, TeeTime             # noqa: E402
from golf.routing import Router                                  # noqa: E402
from golf.search import GolfSearch                               # noqa: E402
from golf.sources.browser_source import (BrowserSource, CapturedApi,  # noqa: E402
                                         playwright_available)
from golf import htmlsel                                         # noqa: E402

D = date(2026, 9, 20)


class TestMultiCourseListing(unittest.TestCase):
    """플랫폼은 여러 골프장을 한 목록에 섞어 보여 준다. 행마다 이름을 뽑아야 한다."""

    HTML = """<table><tbody>
      <tr><td>남서울컨트리클럽</td><td>06:30</td><td>168,000원</td><td>2자리</td></tr>
      <tr><td>레이크사이드CC</td><td>11:20</td><td>132,000원</td><td>4자리</td></tr>
      <tr><td>블루원용인</td><td>13:40</td><td>98,000원</td><td>3자리</td></tr>
    </tbody></table>"""

    def test_each_row_gets_its_own_name(self):
        r = auto_extract(self.HTML, source_id="plat", play_date=D)
        names = [t.course_name for t in r.tee_times]
        self.assertEqual(names, ["남서울컨트리클럽", "레이크사이드CC", "블루원용인"])

    def test_given_name_overrides_detection(self):
        """개별 골프장 페이지면 호출자가 준 이름을 그대로 쓴다."""
        r = auto_extract(self.HTML, course_name="가나CC", source_id="site", play_date=D)
        self.assertTrue(all(t.course_name == "가나CC" for t in r.tee_times))

    def test_status_words_are_not_names(self):
        block = htmlsel.parse(
            '<tr><td>예약</td><td>06:30</td><td>168,000원</td>'
            '<td>남서울CC</td><td>2자리</td></tr>').select_one("tr")
        self.assertEqual(extract_course_name(block), "남서울CC")

    def test_out_in_labels_are_not_names(self):
        block = htmlsel.parse(
            '<tr><td>07:00</td><td>OUT</td><td>레이크사이드CC</td>'
            '<td>168,000원</td></tr>').select_one("tr")
        self.assertEqual(extract_course_name(block), "레이크사이드CC")


class TestCapturedApiConfig(unittest.TestCase):
    """브라우저가 가로챈 API 를 설정으로 남길 때, 그 설정만으로 수집이 돼야 한다."""

    KEYS = {"course_name": "ccName", "tee_time": "teeTime", "green_fee": "greenFee"}

    def test_fields_are_written(self):
        """필드 매핑이 없으면 저장된 설정으로 아무것도 못 뽑는다."""
        api = CapturedApi("https://x.com/api?d=20260920", "GET", {}, 4,
                          "data.list", self.KEYS)
        cfg = api.to_source_config("xgolf", "엑스골프")
        self.assertEqual(cfg["fields"]["course_name"]["path"], "ccName")
        self.assertEqual(cfg["fields"]["tee_time"]["path"], "teeTime")
        self.assertEqual(cfg["fields"]["green_fee"]["path"], "greenFee")
        self.assertEqual(cfg["records_path"], "data.list")

    def test_date_becomes_template(self):
        api = CapturedApi("https://x.com/api?d=20260920", "GET", {}, 4, "l", self.KEYS)
        self.assertIn("{date:%Y%m%d}", api.to_source_config("x", "X")["request"]["url"])

        api2 = CapturedApi("https://x.com/api?d=2026-09-20", "GET", {}, 4, "l", self.KEYS)
        self.assertIn("{date:%Y-%m-%d}", api2.to_source_config("x", "X")["request"]["url"])

    def test_page_templating_prevents_duplicates(self):
        """page=1 을 그대로 두고 3페이지를 부르면 같은 자료를 3번 받는다."""
        with_page = CapturedApi("https://x.com/api?page=1", "GET", {}, 4, "l", self.KEYS)
        cfg = with_page.to_source_config("x", "X")
        self.assertIn("page={page}", cfg["request"]["url"])
        self.assertEqual(cfg["request"]["pages"]["max"], 3)

        no_page = CapturedApi("https://x.com/api?d=1", "GET", {}, 4, "l", self.KEYS)
        cfg2 = no_page.to_source_config("x", "X")
        self.assertEqual(cfg2["request"]["pages"]["max"], 1)

    def test_missing_date_key_falls_back_to_request(self):
        api = CapturedApi("https://x.com/api", "GET", {}, 4, "l", {"tee_time": "t"})
        cfg = api.to_source_config("x", "X")
        self.assertEqual(cfg["fields"]["play_date"]["from_request"], "date")


class TestFilters(unittest.TestCase):
    """시간대 범위와 가격 상한."""

    def setUp(self):
        self.book = CourseBook([
            Course("1", "가나컨트리클럽", 37.3862, 127.0966, "경기"),
            Course("2", "다라컨트리클럽", 37.2405, 127.1100, "경기"),
        ])
        self.rows = [
            TeeTime("가나컨트리클럽", D, time(6, 30), 168000, "plat"),
            TeeTime("다라컨트리클럽", D, time(11, 20), 132000, "plat"),
            TeeTime("가나컨트리클럽", D, time(13, 40), 98000, "plat"),
            TeeTime("다라컨트리클럽", D, time(14, 50), 210000, "plat"),
            TeeTime("가나컨트리클럽", D, time(16, 10), -1, "plat"),   # 가격 미기재
        ]
        source = type("S", (), {"id": "plat", "name": "테스트",
                                "fetch": lambda _s, _d: list(self.rows)})()
        self.engine = GolfSearch(self.book, [source], Router(providers=["estimate"]))

    def _q(self, **kw):
        base = dict(origin="x", origin_lat=37.4979, origin_lon=127.0276, play_date=D)
        base.update(kw)
        return SearchQuery(**base)

    def test_time_range_is_inclusive_window(self):
        res, _ = self.engine.search(self._q(tee_from=time(11, 0), tee_to=time(15, 0)))
        times = sorted(r.tee_time.tee_time for r in res)
        self.assertEqual(times, [time(11, 20), time(13, 40), time(14, 50)])

    def test_price_is_upper_bound(self):
        res, _ = self.engine.search(self._q(max_price=150000))
        fees = [r.tee_time.green_fee for r in res]
        self.assertTrue(all(0 <= f <= 150000 for f in fees))
        self.assertNotIn(210000, fees)

    def test_unknown_price_excluded_by_default(self):
        res, _ = self.engine.search(self._q(max_price=150000))
        self.assertNotIn(-1, [r.tee_time.green_fee for r in res])

    def test_unknown_price_can_be_included(self):
        res, _ = self.engine.search(
            self._q(max_price=150000, include_unknown_price=True))
        self.assertIn(-1, [r.tee_time.green_fee for r in res])

    def test_time_and_price_combined(self):
        res, _ = self.engine.search(
            self._q(tee_from=time(11, 0), tee_to=time(15, 0), max_price=150000))
        self.assertEqual(len(res), 2)
        for r in res:
            self.assertTrue(time(11, 0) <= r.tee_time.tee_time <= time(15, 0))
            self.assertLessEqual(r.tee_time.green_fee, 150000)

    def test_duplicates_removed(self):
        """같은 페이지를 여러 번 받아 와도 결과가 부풀지 않아야 한다."""
        dup = type("S", (), {"id": "plat", "name": "테스트",
                             "fetch": lambda _s, _d: self.rows * 3})()
        engine = GolfSearch(self.book, [dup], Router(providers=["estimate"]))
        res, stats = engine.search(self._q())
        self.assertEqual(stats.fetched, 15)
        self.assertEqual(stats.duplicates, 10)
        self.assertEqual(len(res), 5)


@unittest.skipUnless(playwright_available(), "Playwright 가 설치되지 않음")
class TestBrowserSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.fake_spa import start
        cls.srv, cls.base = start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_captures_api_from_spa(self):
        """목록이 자바스크립트로 그려져도 오가는 API 를 가로채야 한다."""
        src = BrowserSource(self.base + "/spa", source_id="plat",
                            wait_ms=3000, scrolls=1)
        r = src.open_and_capture(D)
        self.assertTrue(r.from_api, r.reason)
        self.assertEqual(len(r.tee_times), 4)
        self.assertEqual(r.tee_times[0].course_name, "가나컨트리클럽")
        self.assertIn("/api/v2/teetimes", r.apis[0].url)

    def test_reads_rendered_dom_without_api(self):
        src = BrowserSource(self.base + "/render", source_id="plat",
                            wait_ms=2500, scrolls=1)
        r = src.open_and_capture(D)
        self.assertFalse(r.from_api)
        self.assertEqual(len(r.tee_times), 3)
        self.assertEqual(r.tee_times[0].course_name, "가나CC")

    def test_plain_http_cannot_see_spa_list(self):
        """일반 요청으로는 못 본다는 것이 브라우저 모드의 존재 이유다."""
        from golf.sources.web_source import HttpClient
        html = HttpClient().get(self.base + "/spa")
        r = auto_extract(html, source_id="x", play_date=D)
        self.assertEqual(len(r.tee_times), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
