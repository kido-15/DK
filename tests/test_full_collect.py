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


class TestBrokenRequestIsReported(unittest.TestCase):
    """요청이 중간에 실패하면 그 날짜는 덜 받은 것이다. 조용히 넘어가면 안 된다.

    실제로 3일치를 받다가 2026-09-19 에서 연결이 한 번 끊겼는데, 그 날짜만
    700건(7페이지)에서 끊긴 채 전체는 정상 종료한 것처럼 끝났다. 건수만 보면
    그럴듯해 보여서 알아채기 어렵다.
    """

    def test_failed_page_marks_the_date_incomplete(self):
        cfg = config_for("http://127.0.0.1:1", "/list")   # 아무도 없는 포트
        cfg["request"]["retries"] = 0
        src = WebSource(cfg)
        rows = src.fetch([DAY])
        self.assertEqual(rows, [])
        incomplete = src.last_stats.get("incomplete")
        self.assertTrue(incomplete, "끝까지 못 받았다는 사실이 남아야 한다")
        self.assertIn(str(DAY), incomplete[0])

    def test_good_run_is_not_marked_incomplete(self):
        cfg = config_for(self.base, "/list")
        src = WebSource(cfg)
        rows = src.fetch([DAY])
        self.assertEqual(len(rows), TOTAL)
        self.assertFalse(src.last_stats.get("incomplete"))

    @classmethod
    def setUpClass(cls):
        cls.httpd, cls.base = start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()


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

    def test_pages_are_actually_turned(self):
        pages = self.cfg["request"]["pages"]
        self.assertGreater(pages["max"], 50, "한 페이지 100건이라 전량이 안 된다")
        self.assertTrue(self.cfg["request"].get("max_requests"))

    def test_end_of_list_is_found_by_the_empty_page(self):
        """골팡에서 끝을 알려 주는 것은 빈 표다. 같은 내용 검사가 아니다.

        실제로 확인한 것(2026-09-18):
          - 마지막 페이지(101)를 넘긴 102·103·200 페이지는 머리글만 있는 빈 표였고
            응답이 md5까지 같았다. 마지막 페이지를 반복해 주지 않는다.
          - 반면 목록은 매물이 실시간으로 드나들어 페이지 경계가 밀린다. 60페이지를
            연속으로 받아 보면 6,000행 중 612행이 앞뒤 페이지와 겹쳤다.
            그래서 서로 다른 페이지가 우연히 같은 내용으로 보일 수 있고,
            2026-09-20 수집이 104페이지 중 48페이지에서 그렇게 멈춰 절반을 놓쳤다.

        stop_when_repeated 는 이 사이트에서 얻는 것이 없고 조용히 절반을 버린다.
        """
        pages = self.cfg["request"]["pages"]
        self.assertTrue(pages["stop_when_empty"],
                        "빈 페이지로 멈추지 않으면 끝을 알 수 없다")
        self.assertFalse(pages["stop_when_repeated"],
                         "골팡에서는 목록 한가운데서 멈추게 만든다")

    def test_page_and_date_are_templated(self):
        body = self.cfg["request"]["body"]
        self.assertIn("{page}", body["pageNum"])
        self.assertIn("{date", body["rd_date"])

    # 2026-09-19 응답에서 그대로 떼어 온 행 하나. 실물은 12칸이고,
    # 그린피 뒤에 닉네임·캐디유무·구분·조회 네 칸이 더 붙는다.
    REAL_ROW = (
        '<table class="type2"><tbody>'
        '<tr id="tr_220219723" style="background-color:#EEF9F0">'
        '<td onclick="showCon(\'220219723\')" style="cursor:pointer">충청</td>'
        '<td onclick="showCon(\'220219723\')" style="cursor:pointer">09월19일 (토)</td>'
        '<td onclick="showCon(\'220219723\')" style="cursor:pointer">18:51</td>'
        '<td onclick="showCon(\'220219723\')" style="cursor:pointer"></td>'
        '<td onclick="showCon(\'220219723\')" style="cursor:pointer" align="left">'
        "대영베이스</td>"
        '<td onclick="showCon(\'220219723\')" style="cursor:pointer">18홀</td>'
        "<td></td>"
        '<td onclick="showCon(\'220219723\')" style="cursor:pointer">'
        '<span class="price">79,000</span>원</td>'
        '<td><img src="/images/ico/i_pang.png" class="btn_ico" alt="팡">골팡 강과장</td>'
        '<td class="state">운전캐디</td>'
        "<td>양도</td><td>2</td>"
        "</tr></tbody></table>")

    def test_real_row_has_twelve_columns(self):
        """그린피 뒤에도 칸이 네 개 더 있다. 뒤에서부터 세면 어긋난다."""
        row = htmlsel.parse(self.REAL_ROW).select_one("tbody tr")
        self.assertEqual(len(row.select("td")), 12)
        self.assertEqual(row.select_one("td:last-child").text, "2")

    def test_selectors_hit_the_right_columns_on_a_real_row(self):
        """실물 행에서 각 칸이 제자리에 들어오는지.

        골프장 자리에 '충청' 같은 지역 이름이 오면 칸 번호가 틀린 것이다.
        """
        row = htmlsel.parse(self.REAL_ROW).select_one("tbody tr")
        fields = self.cfg["fields"]
        self.assertEqual(row.select_one(fields["course_name"]["selector"]).text,
                         "대영베이스")
        self.assertEqual(row.select_one(fields["tee_time"]["selector"]).text, "18:51")
        self.assertEqual(row.select_one(fields["green_fee"]["selector"]).text, "79,000")
        self.assertEqual(row.select_one(fields["hole_info"]["selector"]).text, "18홀")
        self.assertEqual(row.select_one(fields["play_date"]["selector"]).text,
                         "09월19일 (토)")

    def test_row_without_year_falls_back_to_the_requested_date(self):
        """부킹일 칸에는 연도가 없다. 요청한 날짜로 풀려야 한다."""
        src = WebSource(self.cfg)
        rows = src._parse(self.REAL_ROW, {"date": date(2026, 9, 19)})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].play_date, date(2026, 9, 19))
        self.assertEqual(rows[0].course_name, "대영베이스")
        self.assertEqual(rows[0].green_fee, 79000)

    def test_nickname_column_is_not_collected(self):
        """목록 9번 칸에는 판매자 닉네임이 들어 있다. 수집하지 않는다."""
        for f in self.cfg["fields"].values():
            self.assertNotIn("nth-child(9)", str(f.get("selector", "")))
        src = WebSource(self.cfg)
        tee = src._parse(self.REAL_ROW, {"date": date(2026, 9, 19)})[0]
        blob = " ".join(str(v) for v in tee.to_dict().values())
        self.assertNotIn("강과장", blob)

    def test_broken_closing_tag_is_tolerated(self):
        """실물 조각은 </div> 가 아니라 </di> 로 닫힌다. 그래도 행이 나와야 한다."""
        html = ('<div class="table_box_list">'
                + self.REAL_ROW.replace("</table>", "</table></di></div>"))
        src = WebSource(self.cfg)
        self.assertEqual(len(src._parse(html, {"date": date(2026, 9, 19)})), 1)

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
        """마지막 페이지를 넘기면 실물은 이 한 줄짜리 표를 준다."""
        html = ('<table class="type2"><tbody><tr>'
                '<td colspan="12">리스트가 없습니다.</td></tr></tbody></table>')
        src = WebSource(self.cfg)
        self.assertEqual(src._parse(html, {"date": DAY}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
