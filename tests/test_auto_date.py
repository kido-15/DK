#!/usr/bin/env python3
"""날짜를 눌러 가며 자동으로 수집하는 기능 테스트.

    python3 tests/test_auto_date.py

카카오골프예약처럼 날짜를 클릭해야 목록이 바뀌는 사이트를 위한 것이다.
사이트마다 날짜가 속성으로 박혀 있기도 하고 글자로만 있기도 해서, 여러
방식으로 찾아 눌러 보고 목록이 실제로 바뀌었는지 확인한다.
"""

from __future__ import annotations

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

import golf.sources.browser_source as BS                       # noqa: E402
from golf import interact                                      # noqa: E402
from golf.extract import auto_extract                          # noqa: E402
from tests.fake_datepick_site import start                     # noqa: E402

DATES = [date(2026, 9, 20), date(2026, 9, 21), date(2026, 9, 22)]

# 가짜 사이트가 날짜별로 내주는 골프장. 날짜를 제대로 눌렀는지 이걸로 가린다.
EXPECTED = {
    DATES[0]: {"가나컨트리클럽", "나다컨트리클럽", "다라컨트리클럽"},
    DATES[1]: {"마바컨트리클럽", "바사컨트리클럽"},
    DATES[2]: {"사아컨트리클럽"},
}


class TestDateTokens(unittest.TestCase):
    def test_covers_common_notations(self):
        tokens = interact.date_tokens(date(2026, 9, 20))
        for expected in ("2026-09-20", "20260920", "9월 20일", "9/20"):
            self.assertIn(expected, tokens)

    def test_specific_first(self):
        """구체적인 표기를 먼저 시도해야 엉뚱한 것을 덜 누른다."""
        tokens = interact.date_tokens(date(2026, 9, 20))
        self.assertLess(tokens.index("2026-09-20"), tokens.index("9/20"))


class TestSingleItemList(unittest.TestCase):
    """티타임이 한 건뿐인 날은 '반복' 으로 보이지 않는다."""

    ONE = ('<table class="list"><tbody><tr class="item">'
           '<td>사아컨트리클럽</td><td>08:30</td><td>145,000원</td>'
           '</tr></tbody></table>')

    def test_not_found_without_hint(self):
        r = auto_extract(self.ONE, source_id="x", play_date=DATES[2])
        self.assertEqual(len(r.tee_times), 0)

    def test_found_with_known_selector(self):
        """앞선 날짜에서 확인한 구조를 주면 한 건도 읽어낸다."""
        r = auto_extract(self.ONE, source_id="x", play_date=DATES[2],
                         known_selector="tr.item")
        self.assertEqual(len(r.tee_times), 1)
        self.assertEqual(r.tee_times[0].course_name, "사아컨트리클럽")
        self.assertEqual(r.tee_times[0].green_fee, 145000)


@unittest.skipUnless(BS.playwright_available() and BS.find_chromium(),
                     "Playwright 또는 크로미움이 없음")
class TestCollectDates(unittest.TestCase):
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

    def _collect(self, path: str) -> dict:
        src = BS.BrowserSource(self.base + path, source_id="fake",
                               wait_ms=1500, scrolls=1, click_more=False)
        return src.collect_dates(DATES)

    def test_date_attribute_tabs(self):
        """날짜가 data-date 속성에 박혀 있는 형태."""
        results = self._collect("/tabs")
        for d in DATES:
            names = {t.course_name for t in results[d].tee_times}
            self.assertEqual(names, EXPECTED[d], f"{d}: {results[d].reason}")

    def test_text_only_tabs(self):
        """'9/20' 같은 글자만 있고 속성이 없는 형태.

        같은 글자가 감싸는 <li> 와 실제 핸들러가 달린 <a> 양쪽에 걸린다.
        <li> 만 눌러 보고 포기하면 날짜를 못 고른다.
        """
        results = self._collect("/text")
        for d in DATES:
            names = {t.course_name for t in results[d].tee_times}
            self.assertEqual(names, EXPECTED[d], f"{d}: {results[d].reason}")

    def test_single_item_day_is_collected(self):
        """마지막 날짜는 티타임이 한 건뿐이다. 놓치면 안 된다."""
        results = self._collect("/tabs")
        self.assertEqual(len(results[DATES[2]].tee_times), 1)

    def test_static_page_is_not_duplicated(self):
        """날짜를 눌러도 목록이 안 바뀌는 화면.

        확인 없이 모으면 같은 목록이 날짜만 바뀌어 여러 번 저장된다.
        """
        results = self._collect("/static")
        total = sum(len(results[d].tee_times) for d in DATES)
        self.assertEqual(total, 3, "같은 목록을 여러 날짜로 저장하면 안 된다")
        self.assertGreater(len(results[DATES[0]].tee_times), 0)
        for d in DATES[1:]:
            self.assertEqual(len(results[d].tee_times), 0)

    def test_failure_explains_date_controls(self):
        """날짜를 못 골랐으면 화면의 날짜 부분을 알려 줘야 한다."""
        results = self._collect("/static")
        later = results[DATES[1]]
        self.assertEqual(len(later.tee_times), 0)
        self.assertTrue(later.reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
