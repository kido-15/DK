#!/usr/bin/env python3
"""골프장 검색 기능 테스트.

    python3 tests/test_golf.py

외부 네트워크 없이 도는 테스트만 들어 있다. 크롤러는 로컬에 띄운
가짜 사이트(tests/fake_site.py)를 상대로 실제 HTTP 요청을 보내 검증한다.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 크롤러 테스트는 localhost로만 요청하므로 프록시 설정을 걷어낸다
for _v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_v, None)

from golf import htmlsel                                          # noqa: E402
from golf.courses import CourseBook                               # noqa: E402
from golf.geo import haversine_km, parse_coords                   # noqa: E402
from golf.models import (Course, SearchQuery, normalize_course_name,   # noqa: E402
                         parse_date, parse_price, parse_time)
from golf.routing import Router                                   # noqa: E402
from golf.search import GolfSearch                                # noqa: E402
from golf.sources import CsvSource, WebSource                     # noqa: E402
from tests.fake_site import start                                 # noqa: E402


class TestParsing(unittest.TestCase):
    def test_price(self):
        self.assertEqual(parse_price("168,000원"), 168000)
        self.assertEqual(parse_price("16.8만원"), 168000)
        self.assertEqual(parse_price("12만"), 120000)
        self.assertEqual(parse_price(195000), 195000)
        self.assertEqual(parse_price(""), -1)       # 모름은 0원과 구분된다
        self.assertEqual(parse_price("-"), -1)

    def test_time(self):
        self.assertEqual(parse_time("07:30"), time(7, 30))
        self.assertEqual(parse_time("0730"), time(7, 30))
        self.assertEqual(parse_time("오후 2시 30분"), time(14, 30))
        self.assertEqual(parse_time("7:30 AM"), time(7, 30))
        self.assertEqual(parse_time("오전 12시 10분"), time(0, 10))
        self.assertIsNone(parse_time("시간미정"))

    def test_date(self):
        self.assertEqual(parse_date("2026-09-20"), date(2026, 9, 20))
        self.assertEqual(parse_date("2026.09.20"), date(2026, 9, 20))
        self.assertEqual(parse_date("20260920"), date(2026, 9, 20))
        # 연도가 없으면 오늘 기준 가장 가까운 미래로 해석한다
        self.assertEqual(parse_date("9월 20일", today=date(2026, 1, 1)), date(2026, 9, 20))
        self.assertEqual(parse_date("1월 5일", today=date(2026, 6, 1)), date(2027, 1, 5))

    def test_course_name_normalize(self):
        self.assertEqual(normalize_course_name("남서울 컨트리클럽"), "남서울")
        self.assertEqual(normalize_course_name("남서울CC"), "남서울")
        self.assertEqual(normalize_course_name("레이크사이드 (남코스)"), "레이크사이드남코스")


class TestGeo(unittest.TestCase):
    def test_distance(self):
        d = haversine_km(37.4979, 127.0276, 37.2636, 127.0286)
        self.assertAlmostEqual(d, 26.1, delta=0.5)

    def test_coords(self):
        self.assertEqual(parse_coords("37.4979,127.0276"), (37.4979, 127.0276))
        # 경도,위도 순으로 들어와도 바로잡는다
        self.assertEqual(parse_coords("127.0276,37.4979"), (37.4979, 127.0276))
        self.assertIsNone(parse_coords("강남역"))


class TestCourseMatching(unittest.TestCase):
    def setUp(self):
        self.book = CourseBook([
            Course("a", "남서울컨트리클럽", 37.38, 127.09, "경기"),
            Course("b", "서울한양컨트리클럽", 37.60, 127.20, "경기"),
            Course("c", "레이크사이드컨트리클럽", 37.24, 127.11, "경기",
                   aliases=["레이크사이드"]),
        ])

    def test_exact_and_abbrev(self):
        self.assertEqual(self.book.match("남서울CC").course_id, "a")
        self.assertEqual(self.book.match("남서울 컨트리클럽").course_id, "a")

    def test_alias(self):
        self.assertEqual(self.book.match("레이크사이드").course_id, "c")

    def test_no_confusion(self):
        """'남서울'과 '서울한양'을 헷갈리면 안 된다."""
        self.assertEqual(self.book.match("서울한양").course_id, "b")

    def test_unmatched_is_reported(self):
        self.assertIsNone(self.book.match("존재하지않는골프장"))
        names = [n for n, _ in self.book.unmatched_report()]
        self.assertIn("존재하지않는골프장", names)


class TestHtmlSelector(unittest.TestCase):
    HTML = """<div id="wrap"><table class="t">
      <tr class="row"><td class="name">가나CC</td><td class="fee">100,000원</td>
        <td><a href="/b/1">예약</a></td></tr>
      <tr class="row"><td class="name">다라CC</td><td class="fee">200,000원</td>
        <td><a href="/b/2">예약</a></td></tr>
    </table><script>var s="<tr class='row'>가짜</tr>";</script></div>"""

    def setUp(self):
        self.root = htmlsel.parse(self.HTML)

    def test_descendant(self):
        self.assertEqual(len(self.root.select("table.t tr.row")), 2)

    def test_child_combinator(self):
        self.assertEqual(len(self.root.select("tr.row > td.name")), 2)

    def test_attr(self):
        self.assertEqual(len(self.root.select('a[href^="/b"]')), 2)

    def test_nth_of_type(self):
        self.assertEqual(self.root.select("tr:nth-of-type(2) td.name")[0].text, "다라CC")

    def test_script_text_excluded(self):
        self.assertNotIn("가짜", self.root.select_one("#wrap").text)


class TestCrawler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.base = start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_html_source_with_pagination(self):
        src = WebSource({
            "id": "t", "format": "html",
            "request": {"url": self.base + "/tee?date={date:%Y%m%d}&page={page}",
                        "delay_seconds": 0,
                        "pages": {"start": 1, "max": 5, "stop_when_empty": True}},
            "list_selector": "table.tee-list tr.row",
            "fields": {"course_name": {"selector": "td.name"},
                       "tee_time": {"selector": "td.time"},
                       "green_fee": {"selector": "td.fee"},
                       "slots": {"selector": "td.rest"},
                       "booking_url": {"selector": "a", "attr": "href",
                                       "prefix": self.base}},
        })
        rows = src.fetch([date(2026, 9, 20)])
        # 유효한 행만 3건. "시간미정" 행은 걸러진다
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].course_name, "남서울컨트리클럽")
        self.assertEqual(rows[0].tee_time, time(7, 12))
        self.assertEqual(rows[0].green_fee, 168000)
        self.assertEqual(rows[0].slots, 2)
        self.assertTrue(rows[0].booking_url.startswith("http"))
        # 빈 페이지에서 멈춘다 (1,2,3페이지만 요청)
        self.assertEqual(src.last_stats["requests"], 3)

    def test_json_source(self):
        src = WebSource({
            "id": "j", "format": "json",
            "request": {"url": self.base + "/api?d={date:%Y-%m-%d}", "delay_seconds": 0},
            "records_path": "result.list",
            "fields": {"course_name": {"path": "cc"}, "tee_time": {"path": "tm"},
                       "green_fee": {"path": "price"},
                       "play_date": {"from_request": "date"}},
        })
        rows = src.fetch([date(2026, 9, 21)])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].tee_time, time(6, 54))
        self.assertEqual(rows[0].play_date, date(2026, 9, 21))

    def test_robots_is_respected(self):
        src = WebSource({
            "id": "r", "format": "html",
            "request": {"url": self.base + "/private", "delay_seconds": 0},
            "list_selector": "tr", "fields": {"course_name": {"selector": "td"}},
        })
        src.fetch([date(2026, 9, 20)])
        self.assertTrue(any("robots" in e for e in src.last_stats["errors"]))

    def test_bad_url_does_not_raise(self):
        """소스 하나가 죽어도 예외가 밖으로 나가면 안 된다."""
        src = WebSource({
            "id": "x", "format": "html",
            "request": {"url": "http://127.0.0.1:1/nope", "delay_seconds": 0},
            "list_selector": "tr", "fields": {"course_name": {"selector": "td"}},
        })
        self.assertEqual(src.fetch([date(2026, 9, 20)]), [])
        self.assertTrue(src.last_error)


class TestSearch(unittest.TestCase):
    def setUp(self):
        self.book = CourseBook([
            Course("c1", "남서울컨트리클럽", 37.3862, 127.0966, "경기"),
            Course("c2", "레이크사이드컨트리클럽", 37.2405, 127.1100, "경기",
                   aliases=["레이크사이드"]),
            Course("c3", "제이드팰리스골프클럽", 37.8200, 127.6500, "강원",
                   aliases=["제이드팰리스"]),
        ])
        self.csv = os.path.join(os.path.dirname(__file__), "_tmp_teetimes.csv")
        with open(self.csv, "w", encoding="utf-8") as f:
            f.write("course_name,play_date,tee_time,green_fee,booking_url\n")
            f.write("남서울CC,2026-09-20,06:48,175000,https://x/1\n")
            f.write("레이크사이드,2026-09-20,07:12,158000,https://x/2\n")
            f.write("제이드팰리스,2026-09-20,07:50,250000,https://x/3\n")
            f.write("모르는골프장,2026-09-20,08:00,100000,https://x/4\n")
        self.engine = GolfSearch(self.book, [CsvSource(self.csv)],
                                 Router(providers=["estimate"]))

    def tearDown(self):
        if os.path.exists(self.csv):
            os.remove(self.csv)

    def _q(self, **kw):
        base = dict(origin="강남역", origin_lat=37.4979, origin_lon=127.0276,
                    play_date=date(2026, 9, 20))
        base.update(kw)
        return SearchQuery(**base)

    def test_price_filter(self):
        res, _ = self.engine.search(self._q(max_price=200000))
        fees = [r.tee_time.green_fee for r in res]
        self.assertTrue(all(f <= 200000 for f in fees))
        self.assertNotIn(250000, fees)

    def test_time_filter(self):
        res, _ = self.engine.search(self._q(tee_from=time(6, 0), tee_to=time(7, 0)))
        self.assertEqual([r.tee_time.tee_time for r in res], [time(6, 48)])

    def test_drive_filter_excludes_far_course(self):
        """강원 춘천은 40분 안에 갈 수 없다."""
        res, _ = self.engine.search(self._q(max_drive_minutes=40))
        names = [r.tee_time.course.name for r in res]
        self.assertNotIn("제이드팰리스골프클럽", names)

    def test_unmatched_course_reported(self):
        _, stats = self.engine.search(self._q())
        self.assertEqual(stats.unmatched, 1)
        self.assertIn("모르는골프장", stats.unmatched_names)

    def test_unmatched_excluded_when_drive_limit_set(self):
        """좌표를 모르면 이동시간을 판단할 수 없으므로 제외되어야 한다."""
        res, _ = self.engine.search(self._q(max_drive_minutes=300))
        self.assertTrue(all(r.tee_time.matched for r in res))

    def test_sorting(self):
        res, _ = self.engine.search(self._q(sort="price"))
        fees = [r.tee_time.green_fee for r in res]
        self.assertEqual(fees, sorted(fees))

        res, _ = self.engine.search(self._q(sort="drive"))
        drives = [r.drive_minutes for r in res if r.drive_minutes is not None]
        self.assertEqual(drives, sorted(drives))

    def test_prefilter_reduces_routing_calls(self):
        """거리 프리필터가 길찾기 호출 수를 줄여야 한다."""
        _, stats = self.engine.search(self._q(max_drive_minutes=30))
        self.assertLess(stats.after_prefilter, stats.after_basic)

    def test_region_filter(self):
        res, _ = self.engine.search(self._q(regions=["강원"]))
        self.assertTrue(all(r.tee_time.course.region == "강원" for r in res))


class TestRouting(unittest.TestCase):
    def test_estimate_always_succeeds(self):
        r = Router(providers=["estimate"])
        info = r.route(37.4979, 127.0276, 37.2636, 127.0286)
        self.assertEqual(info.provider, "estimate")
        self.assertGreater(info.duration_min, 0)

    def test_dead_provider_falls_back(self):
        """길찾기 API가 죽어도 estimate가 받아내야 한다."""
        r = Router(providers=["osrm"], osrm_base_url="http://127.0.0.1:1")
        info = r.route(37.4979, 127.0276, 37.2636, 127.0286)
        self.assertEqual(info.provider, "estimate")

    def test_estimate_is_never_removed(self):
        r = Router(providers=["osrm"])
        self.assertIn("estimate", r.providers)

    def test_unknown_provider_rejected(self):
        with self.assertRaises(ValueError):
            Router(providers=["없는제공자"])

    def test_cache(self):
        r = Router(providers=["estimate"])
        r.route(37.5, 127.0, 37.3, 127.1)
        self.assertEqual(len(r.cache), 1)
        r.route(37.5, 127.0, 37.3, 127.1)
        self.assertEqual(len(r.cache), 1)


class TestSetupWizard(unittest.TestCase):
    """마법사가 브라우저에서 복사한 주소를 올바로 다듬는지."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "scripts", "setup_sites.py")
        spec = importlib.util.spec_from_file_location("setup_sites", path)
        cls.wiz = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.wiz)

    def test_date_templating(self):
        """주소에 박힌 날짜가 치환자로 바뀌어야 매번 원하는 날짜로 조회된다."""
        cases = [
            ("https://x.com/list?playDate=20260920", "{date:%Y%m%d}"),
            ("https://x.com/api?d=2026-09-20", "{date:%Y-%m-%d}"),
            ("https://x.com/api?d=2026.09.20", "{date:%Y.%m.%d}"),
        ]
        for url, expected in cases:
            new_url, fmt = self.wiz.templatize_date(url)
            self.assertEqual(fmt, expected, url)
            self.assertIn(expected, new_url)

    def test_date_templating_ignores_non_dates(self):
        """상품번호 같은 숫자를 날짜로 오인하면 안 된다."""
        for url in ["https://x.com/list?id=12345678",
                    "https://x.com/list?no=20261332",   # 13월 32일
                    "https://x.com/list?v=19990101"]:   # 20xx 아님
            new_url, fmt = self.wiz.templatize_date(url)
            self.assertEqual(fmt, "", url)
            self.assertEqual(new_url, url)

    def test_page_templating(self):
        for url, expect in [
            ("https://x.com/l?page=3", "https://x.com/l?page={page}"),
            ("https://x.com/l?d=1&pageNo=12", "https://x.com/l?d=1&pageNo={page}"),
            ("https://x.com/l?d=1", "https://x.com/l?d=1"),
        ]:
            self.assertEqual(self.wiz.templatize_page(url), expect)

    def test_upsert_replaces_same_id(self):
        cfg = {"sources": [{"id": "xgolf", "name": "옛 설정"}]}
        action = self.wiz.upsert_source(cfg, {"id": "xgolf", "name": "새 설정"})
        self.assertEqual(action, "교체")
        self.assertEqual(len(cfg["sources"]), 1)
        self.assertEqual(cfg["sources"][0]["name"], "새 설정")

        action = self.wiz.upsert_source(cfg, {"id": "golfpang", "name": "골팡"})
        self.assertEqual(action, "추가")
        self.assertEqual(len(cfg["sources"]), 2)

    def test_three_sites_are_defined(self):
        self.assertEqual(set(self.wiz.SITES), {"xgolf", "kakao", "golfpang"})


class TestExampleConfig(unittest.TestCase):
    def test_example_config_is_valid_json(self):
        import json
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "config", "sources.example.json")
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        ids = [s["id"] for s in cfg["sources"]]
        self.assertEqual(ids, ["xgolf", "kakao", "golfpang"])
        # 셀렉터를 추측해 넣어 두지 않았는지 확인한다
        for s in cfg["sources"]:
            self.assertFalse(s["enabled"], f"{s['id']} 는 기본적으로 꺼져 있어야 한다")
            self.assertEqual(s["request"]["url"], "")

    def test_unconfigured_source_reports_reason(self):
        src = WebSource({"id": "x", "format": "html", "request": {"url": ""},
                         "list_selector": "tr", "fields": {"course_name": {"selector": "td"}}})
        self.assertEqual(src.fetch([date(2026, 9, 20)]), [])
        self.assertIn("setup_sites", src.last_error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
