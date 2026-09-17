#!/usr/bin/env python3
"""개별 골프장 홈페이지 직접 수집 테스트.

    python3 tests/test_site_crawl.py

네트워크 없이 로컬에 띄운 가짜 골프장 사이트를 상대로 실제 HTTP 요청을 보낸다.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_v, None)

from golf import snapshot                                    # noqa: E402
from golf.extract import auto_extract, auto_extract_json, detect_login_wall  # noqa: E402
from golf.models import Course, TeeTime                      # noqa: E402
from golf.profiles import (STATUS_JS, STATUS_LOGIN, STATUS_NO_SITE,  # noqa: E402
                           STATUS_OK, ProfileStore, extract_tech, root_domain)
from golf.sources.site_crawler import (SiteCrawler, find_booking_links,  # noqa: E402
                                       looks_js_rendered)
from tests.fake_courses import start_all, stop_all           # noqa: E402

D = date(2026, 9, 20)


class TestAutoExtract(unittest.TestCase):
    def test_table_layout(self):
        html = """<table><tr><td>06:30</td><td>168,000원</td><td>4명</td>
                  <td><a href="/r/1">예약</a></td></tr>
                  <tr><td>06:48</td><td>175,000원</td><td>2명</td>
                  <td><a href="/r/2">예약</a></td></tr>
                  <tr><td>07:06</td><td>175,000원</td><td>4명</td>
                  <td><a href="/r/3">예약</a></td></tr></table>"""
        r = auto_extract(html, course_name="가나CC", source_id="s", play_date=D,
                         base_url="https://cc.example.com/list")
        self.assertEqual(len(r.tee_times), 3)
        self.assertGreater(r.confidence, 0.5)
        self.assertEqual(r.tee_times[0].tee_time, time(6, 30))
        self.assertEqual(r.tee_times[0].green_fee, 168000)
        self.assertEqual(r.tee_times[0].slots, 4)
        self.assertTrue(r.tee_times[0].booking_url.startswith("https://cc.example.com/r/1"))

    def test_pm_time_is_not_lost(self):
        """'오후 1:20' 을 01:20 으로 읽으면 12시간이 틀어진다."""
        html = """<ul><li><b>오후 1:20</b><i>128,000원</i></li>
                  <li><b>오후 2:40</b><i>118,000원</i></li>
                  <li><b>오전 7:00</b><i>198,000원</i></li></ul>"""
        r = auto_extract(html, course_name="가나CC", source_id="s", play_date=D)
        times = sorted(t.tee_time for t in r.tee_times)
        self.assertEqual(times, [time(7, 0), time(13, 20), time(14, 40)])

    def test_unavailable_rows_excluded(self):
        html = """<table><tr><td>06:30</td><td>168,000원</td><td>예약가능</td></tr>
                  <tr><td>06:48</td><td>168,000원</td><td>마감</td></tr>
                  <tr><td>07:06</td><td>168,000원</td><td>예약완료</td></tr></table>"""
        r = auto_extract(html, course_name="가나CC", source_id="s", play_date=D)
        self.assertEqual(len(r.tee_times), 1)
        self.assertEqual(r.tee_times[0].tee_time, time(6, 30))

    def test_notice_board_is_not_mistaken(self):
        """공지사항 목록을 티타임으로 착각하면 안 된다."""
        html = """<table><tr><td>2026-09-01</td><td>추석 운영 안내</td><td>조회 152</td></tr>
                  <tr><td>2026-08-20</td><td>그린 보수 공지</td><td>조회 87</td></tr>
                  <tr><td>2026-08-01</td><td>회원권 안내</td><td>조회 203</td></tr></table>"""
        r = auto_extract(html, course_name="가나CC", source_id="s", play_date=D)
        self.assertEqual(len(r.tee_times), 0)

    def test_menu_is_not_mistaken(self):
        html = """<ul><li><a href="/a">골프장 소개</a></li><li><a href="/b">코스 안내</a></li>
                  <li><a href="/c">이용 요금</a></li><li><a href="/d">오시는 길</a></li></ul>"""
        r = auto_extract(html, course_name="가나CC", source_id="s", play_date=D)
        self.assertEqual(len(r.tee_times), 0)

    def test_phone_number_is_not_a_price(self):
        """전화번호나 회원번호를 그린피로 읽으면 안 된다."""
        html = """<table><tr><td>06:30</td><td>031-123-4567</td><td>예약</td></tr>
                  <tr><td>06:48</td><td>031-123-4567</td><td>예약</td></tr>
                  <tr><td>07:06</td><td>031-123-4567</td><td>예약</td></tr></table>"""
        r = auto_extract(html, course_name="가나CC", source_id="s", play_date=D)
        for t in r.tee_times:
            self.assertEqual(t.green_fee, -1)

    def test_login_wall(self):
        html = """<div>회원 로그인 후 이용하실 수 있습니다.</div>
                  <form><input type="password" name="pw"></form>"""
        self.assertTrue(detect_login_wall(html))
        r = auto_extract(html, course_name="가나CC", source_id="s", play_date=D)
        self.assertTrue(r.needs_login)
        self.assertEqual(len(r.tee_times), 0)

    def test_login_link_in_header_is_not_a_wall(self):
        """헤더에 로그인 링크만 있는 정상 목록 페이지를 막으면 안 된다."""
        html = """<header><a href="/login">로그인</a></header>
                  <table><tr><td>06:30</td><td>168,000원</td></tr>
                  <tr><td>06:48</td><td>168,000원</td></tr>
                  <tr><td>07:06</td><td>175,000원</td></tr></table>"""
        self.assertFalse(detect_login_wall(html))
        r = auto_extract(html, course_name="가나CC", source_id="s", play_date=D)
        self.assertEqual(len(r.tee_times), 3)

    def test_json_extraction(self):
        data = {"result": {"list": [
            {"teeTime": "0640", "greenFee": 198000, "state": "Y"},
            {"teeTime": "0658", "greenFee": 198000, "state": "Y"},
            {"teeTime": "1330", "greenFee": 128000, "state": "마감"}]}}
        r = auto_extract_json(data, course_name="가나CC", source_id="s", play_date=D)
        self.assertEqual(len(r.tee_times), 2)
        self.assertEqual(r.tee_times[0].tee_time, time(6, 40))
        self.assertEqual(r.tee_times[0].green_fee, 198000)


class TestBookingLinkFinding(unittest.TestCase):
    BASE = "https://cc.example.com/"

    def test_prefers_realtime_booking(self):
        html = """<a href="/intro">소개</a><a href="/rsv/realtime">실시간예약</a>
                  <a href="/notice">공지사항</a>"""
        links = find_booking_links(html, self.BASE)
        self.assertEqual(links[0], "https://cc.example.com/rsv/realtime")

    def test_excludes_confirm_and_cancel(self):
        """'예약확인', '예약취소' 는 목록 페이지가 아니다."""
        html = """<a href="/rsv/confirm">예약확인</a><a href="/rsv/cancel">예약취소</a>
                  <a href="/rsv/guide">예약안내</a>"""
        self.assertEqual(find_booking_links(html, self.BASE), [])

    def test_image_link_alt_text(self):
        html = '<a href="/booking/list"><img src="/i.png" alt="온라인 예약"></a>'
        links = find_booking_links(html, self.BASE)
        self.assertIn("https://cc.example.com/booking/list", links)

    def test_js_render_detection(self):
        self.assertTrue(looks_js_rendered(
            '<body><div id="root"></div><script src="/a.js"></script></body>'))
        self.assertFalse(looks_js_rendered(
            "<body>" + "<p>실제 내용이 충분히 있는 페이지입니다.</p>" * 40 + "</body>"))


class TestProfiles(unittest.TestCase):
    def test_korean_two_level_tld(self):
        """namseoul-cc.co.kr 의 루트를 co.kr 로 보면 모든 .co.kr 이 자기 도메인이 된다."""
        self.assertEqual(root_domain("www.namseoul-cc.co.kr"), "namseoul-cc.co.kr")
        self.assertEqual(root_domain("booking.vendor.co.kr"), "vendor.co.kr")
        self.assertEqual(root_domain("a.b.example.com"), "example.com")

    def test_tech_excludes_own_and_analytics(self):
        html = """<script src="https://www.googletagmanager.com/gtm.js"></script>
                  <script src="https://cdn.mycc.co.kr/site.js"></script>
                  <script src="https://booking.vendor.co.kr/app.js"></script>"""
        tech = extract_tech(html, "https://www.mycc.co.kr/")
        self.assertIn("script:booking.vendor.co.kr", tech)
        self.assertNotIn("script:cdn.mycc.co.kr", tech)
        self.assertFalse(any("googletagmanager" in t for t in tech))

    def test_tech_grouping(self):
        store = ProfileStore()
        for cid, name in [("1", "가나CC"), ("2", "다라CC")]:
            store.get(cid, name).tech = ["script:booking.vendor.co.kr"]
        groups = store.tech_groups()
        self.assertEqual(groups[0][1], 2)

    def test_login_wall_is_terminal(self):
        store = ProfileStore()
        p = store.get("1", "가나CC")
        p.record_failure(STATUS_LOGIN)
        self.assertTrue(p.should_skip())

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.json")
            store = ProfileStore(path=path)
            p = store.get("1", "가나CC")
            p.record_success("https://x/rsv", 5, 0.9, "tr.item")
            store.save()
            again = ProfileStore.load(path)
            self.assertEqual(again.get("1").booking_url, "https://x/rsv")
            self.assertEqual(again.get("1").status, STATUS_OK)


class TestSnapshot(unittest.TestCase):
    def test_save_load_roundtrip(self):
        rows = [TeeTime("가나CC", D, time(6, 30), 168000, "site",
                        booking_url="https://x/1", slots=2)]
        with tempfile.TemporaryDirectory() as d:
            snapshot.save(rows, directory=d)
            back, meta = snapshot.load(os.path.join(d, "latest.json"))
            self.assertEqual(len(back), 1)
            self.assertEqual(back[0].green_fee, 168000)
            self.assertEqual(back[0].tee_time, time(6, 30))
            self.assertIn("collected_at", meta)

    def test_diff_detects_price_drop_and_new(self):
        old = [TeeTime("가나CC", D, time(6, 30), 180000, "site"),
               TeeTime("다라CC", D, time(8, 0), 150000, "site")]
        new = [TeeTime("가나CC", D, time(6, 30), 150000, "site"),
               TeeTime("마바CC", D, time(9, 0), 99000, "site")]
        ch = snapshot.diff(old, new)
        self.assertEqual(len(ch["price_down"]), 1)
        self.assertEqual(ch["price_down"][0].diff, -30000)
        self.assertEqual(len(ch["new"]), 1)
        self.assertEqual(len(ch["gone"]), 1)

    def test_small_change_is_ignored(self):
        old = [TeeTime("가나CC", D, time(6, 30), 168000, "site")]
        new = [TeeTime("가나CC", D, time(6, 30), 165000, "site")]
        ch = snapshot.diff(old, new, min_drop=10000)
        self.assertEqual(len(ch["price_down"]), 0)


class TestSiteCrawler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servers, cls.urls = start_all()

    @classmethod
    def tearDownClass(cls):
        stop_all(cls.servers)

    def _courses(self):
        U = self.urls
        return [
            Course("a", "가나컨트리클럽", 37.3, 127.1, "경기", homepage=U["a"]),
            Course("b", "다라컨트리클럽", 37.2, 127.2, "경기", homepage=U["b"]),
            Course("c", "마바컨트리클럽", 37.4, 127.3, "경기", homepage=U["c"]),
            Course("d", "사아컨트리클럽", 37.5, 127.4, "경기", homepage=U["d"]),
            Course("f", "카타컨트리클럽", 37.7, 127.6, "경기", homepage=U["f"]),
            Course("g", "홈페이지없는CC", 37.8, 127.7, "경기"),
        ]

    def test_finds_booking_page_and_extracts(self):
        store = ProfileStore(path=os.devnull)
        crawler = SiteCrawler(self._courses(), store, delay_seconds=0)
        rows = crawler.fetch([D])

        names = {t.course_name for t in rows}
        self.assertIn("가나컨트리클럽", names)      # 표 형식
        self.assertIn("다라컨트리클럽", names)      # 카드 형식 + 이미지 링크
        self.assertIn("카타컨트리클럽", names)      # JSON API
        self.assertEqual(len(rows), 8)

        self.assertEqual(store.get("c").status, STATUS_LOGIN)
        self.assertEqual(store.get("d").status, STATUS_JS)
        self.assertEqual(store.get("g").status, STATUS_NO_SITE)

    def test_profile_remembers_booking_url(self):
        store = ProfileStore(path=os.devnull)
        SiteCrawler(self._courses(), store, delay_seconds=0).fetch([D])
        self.assertTrue(store.get("a").booking_url.endswith("/rsv/realtime"))
        self.assertEqual(store.get("a").status, STATUS_OK)

    def test_login_wall_skipped_on_next_run(self):
        """로그인 필요한 곳을 매번 다시 두드리지 않아야 한다."""
        store = ProfileStore(path=os.devnull)
        courses = self._courses()
        SiteCrawler(courses, store, delay_seconds=0).fetch([D])

        second = SiteCrawler(courses, store, delay_seconds=0)
        second.fetch([D])
        self.assertEqual(second.last_stats["requests"], len(courses) - 1)

    def test_results_are_stable_across_runs(self):
        store = ProfileStore(path=os.devnull)
        courses = self._courses()
        first = SiteCrawler(courses, store, delay_seconds=0).fetch([D])
        second = SiteCrawler(courses, store, delay_seconds=0).fetch([D])
        self.assertEqual(len(first), len(second))


if __name__ == "__main__":
    unittest.main(verbosity=2)
