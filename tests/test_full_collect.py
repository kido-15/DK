#!/usr/bin/env python3
"""전량 수집(페이지 넘기기) 테스트.

    python3 tests/test_full_collect.py

골팡은 하루에만 9천 건이 넘는데 한 페이지가 100건이다. 전량을 받으려면
페이지를 끝까지 넘겨야 하고, 그 과정에서 조용히 틀리기 쉬운 것들이 있다.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_v, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

from golf import htmlsel                                        # noqa: E402
from golf.sources.web_source import WebSource                   # noqa: E402
from tests.fake_paged_site import PER_PAGE, TOTAL, start        # noqa: E402

DAY = date(2026, 9, 19)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLFPANG_CONFIG = os.path.join(ROOT, "config", "sources.golfpang.json")


def config_for(base: str, path: str, **request) -> dict:
    """골팡 설정과 같은 모양의 시험용 설정."""
    req = {
        "url": base + path,
        "method": "POST",
        "body": {"pageNum": "{page}", "rd_date": "{date:%Y-%m-%d}"},
        "delay_seconds": 0,
        "pages": {"start": 1, "max": 20, "stop_when_empty": True,
                  "stop_when_repeated": True},
    }
    req.update(request)
    return {
        "id": "fake", "name": "가짜", "enabled": True, "format": "html",
        "request": req,
        "list_selector": "table.type2 tr",
        "fields": {
            "course_name": {"selector": "td:nth-child(5)"},
            "play_date": {"selector": "td:nth-child(2)"},
            "tee_time": {"selector": "td:nth-child(3)"},
            "green_fee": {"selector": "td:nth-child(8) span.price"},
            "hole_info": {"selector": "td:nth-child(6)"},
        },
    }


class TestNthChildSelector(unittest.TestCase):
    """표에서 n번째 칸을 집는 선택자.

    이게 조용히 무시되면 골프장 이름 자리에 지역 이름이 들어온다.
    """

    ROW = ('<table><tbody><tr>'
           '<td>한강이남</td><td>09월20일 (일)</td><td>17:27</td><td>x</td>'
           '<td>필로스</td><td>18홀</td><td></td>'
           '<td><span class="price">120,000</span>원</td>'
           "</tr></tbody></table>")

    def setUp(self):
        self.row = htmlsel.parse(self.ROW).select_one("tbody tr")

    def test_picks_the_right_column(self):
        self.assertEqual(self.row.select_one("td:nth-child(5)").text, "필로스")
        self.assertEqual(self.row.select_one("td:nth-child(3)").text, "17:27")
        self.assertEqual(
            self.row.select_one("td:nth-child(8) span.price").text, "120,000")

    def test_first_and_last(self):
        self.assertEqual(self.row.select_one("td:first-child").text, "한강이남")
        self.assertIn("120,000", self.row.select_one("td:last-child").text)

    def test_unknown_syntax_is_refused_not_ignored(self):
        """모르는 문법을 조용히 무시하면 조건 없이 다 잡힌다.

        예전에는 "td:nth-child(5)" 가 그냥 "td" 가 되어 첫 칸이 나왔다.
        """
        with self.assertRaises(ValueError):
            self.row.select("td:has(span)")


class TestPaging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.base = start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_collects_every_page(self):
        """한 페이지만 받고 끝내면 안 된다."""
        src = WebSource(config_for(self.base, "/list"))
        rows = src.fetch([DAY])
        self.assertEqual(len(rows), TOTAL)
        self.assertEqual(len(set(t.course_name for t in rows)), TOTAL)

    def test_stops_at_empty_page(self):
        """빈 페이지가 나오면 남은 페이지를 더 두드리지 않는다."""
        src = WebSource(config_for(self.base, "/list"))
        src.fetch([DAY])
        pages = (TOTAL + PER_PAGE - 1) // PER_PAGE
        self.assertEqual(src.last_stats["requests"], pages + 1)

    def test_repeated_page_stops_collection(self):
        """범위를 넘겨도 마지막 페이지를 계속 주는 사이트.

        확인하지 않으면 같은 매물이 페이지 상한만큼 쌓인다.
        """
        src = WebSource(config_for(self.base, "/clamp"))
        rows = src.fetch([DAY])
        self.assertEqual(len(rows), TOTAL, "같은 페이지를 다시 받아 쌓으면 안 된다")
        self.assertTrue(src.last_stats["stopped"])

    def test_request_cap_is_honored(self):
        """설정을 잘못 적어도 사이트를 끝없이 두드리지 않는다."""
        src = WebSource(config_for(self.base, "/clamp", max_requests=2))
        src.fetch([DAY])
        self.assertLessEqual(src.last_stats["requests"], 2)

    def test_progress_is_reported_per_page(self):
        """수천 건을 받는 동안 진행 상황을 알 수 있어야 한다."""
        seen = []
        src = WebSource(config_for(self.base, "/list"))
        src.fetch([DAY], on_progress=lambda d, p, n, total: seen.append((p, n, total)))
        self.assertGreaterEqual(len(seen), 3)
        self.assertEqual(seen[0][0], 1)
        self.assertEqual([n for _, n, _ in seen[:3]], [PER_PAGE, PER_PAGE, TOTAL % PER_PAGE])
        self.assertEqual(seen[2][2], TOTAL)

    def test_row_dates_are_kept_not_assumed(self):
        """행에 적힌 날짜를 그대로 쓴다. 요청 날짜로 덮으면 잘못 받은 걸 못 본다."""
        src = WebSource(config_for(self.base, "/wrongdate"))
        rows = src.fetch([DAY])
        self.assertTrue(any(t.play_date != DAY for t in rows),
                        "다른 날짜 행이 요청 날짜로 덮여 버렸다")

    def test_multiple_dates(self):
        src = WebSource(config_for(self.base, "/list"))
        rows = src.fetch([DAY, date(2026, 9, 20)])
        self.assertEqual(len(rows), TOTAL * 2)


class TestGolfpangConfig(unittest.TestCase):
    """저장소에 넣어 둔 골팡 설정이 실제로 쓸 수 있는 모양인지."""

    @classmethod
    def setUpClass(cls):
        with open(GOLFPANG_CONFIG, encoding="utf-8") as f:
            cls.cfg = json.load(f)["sources"][0]

    def test_uses_pc_site_only(self):
        """m.golfpang.com 은 robots.txt 가 /m/ 를 막고 있다.

        설명문이 아니라 **실제로 요청하는 주소** 를 본다.
        """
        req = self.cfg["request"]
        addresses = [req["url"]] + list((req.get("headers") or {}).values())
        for value in addresses:
            self.assertNotIn("m.golfpang.com", value)
            self.assertNotIn("/m/", value)
        self.assertTrue(req["url"].startswith("https://www.golfpang.com/"))

    def test_respects_robots_and_delays(self):
        self.assertTrue(self.cfg.get("respect_robots", True))
        self.assertGreaterEqual(self.cfg["request"]["delay_seconds"], 1.0)

    # 실측(2026-09-18): 2026-09-19 하루가 10,087건 = 101페이지.
    # 여기에 "더 없음"을 확인하는 빈 페이지 1회가 붙는다.
    PAGES_PER_DATE = 102

    def test_pages_are_actually_turned(self):
        pages = self.cfg["request"]["pages"]
        self.assertGreater(pages["max"], self.PAGES_PER_DATE,
                           "하루 101페이지라 이보다 작으면 전량이 안 된다")
        self.assertTrue(pages["stop_when_repeated"])
        self.assertTrue(self.cfg["request"].get("max_requests"))

    def test_request_cap_covers_the_documented_week(self):
        """max_requests 는 날짜를 합친 전체 요청 수다.

        README 가 `--days 7` 을 예로 드는데 상한이 그보다 작으면 뒷 날짜가
        통째로 잘린다. 멈춘 이유는 찍히지만, 예로 든 명령이 반쪽짜리가 된다.
        """
        need = 7 * self.PAGES_PER_DATE
        self.assertGreaterEqual(
            self.cfg["request"]["max_requests"], need,
            f"--days 7 에는 요청 {need}회가 필요하다")

    def test_page_and_date_are_templated(self):
        body = self.cfg["request"]["body"]
        self.assertIn("{page}", body["pageNum"])
        self.assertIn("{date", body["rd_date"])

    def test_selectors_parse_and_hit_the_right_columns(self):
        """설정의 선택자가 골팡 목록 구조에서 제 칸을 집는지."""
        html = ('<table class="type2"><tbody><tr>'
                '<td>강북/경춘</td><td>09월20일 (일)</td><td>17:27</td>'
                '<td><img alt="식사"></td><td>필로스</td><td>18홀</td><td></td>'
                '<td><span class="price">120,000</span>원</td><td>7</td>'
                "</tr></tbody></table>")
        row = htmlsel.parse(html).select_one(self.cfg["list_selector"])
        fields = self.cfg["fields"]
        self.assertEqual(row.select_one(fields["course_name"]["selector"]).text, "필로스")
        self.assertEqual(row.select_one(fields["tee_time"]["selector"]).text, "17:27")
        self.assertEqual(row.select_one(fields["green_fee"]["selector"]).text, "120,000")
        self.assertEqual(row.select_one(fields["play_date"]["selector"]).text,
                         "09월20일 (일)")

    def test_header_row_is_skipped(self):
        """list_selector 가 thead 의 머리글 행까지 잡아도 티타임으로 새면 안 된다."""
        html = ('<table class="type2"><thead><tr><th>지역</th><th>부킹일</th>'
                "<th>티타임</th></tr></thead><tbody></tbody></table>")
        src = WebSource(self.cfg)
        rows = src._parse(html, {"date": DAY})
        self.assertEqual(rows, [])

    def test_empty_result_row_is_skipped(self):
        html = ('<table class="type2"><tbody><tr>'
                '<td colspan="9">검색된 티타임이 없습니다.</td></tr></tbody></table>')
        src = WebSource(self.cfg)
        self.assertEqual(src._parse(html, {"date": DAY}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
