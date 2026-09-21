#!/usr/bin/env python3
"""golf/collect.py — 터미널 수집과 웹 대시보드 즉석 수집이 함께 쓰는
공통 로직 테스트.

    python3 tests/test_collect_module.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_v, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import golf.collect as collect                                 # noqa: E402
from golf import snapshot                                      # noqa: E402
from golf.models import TeeTime, parse_time                    # noqa: E402
from tests.fake_paged_site import start                        # noqa: E402

DAY = date(2026, 9, 19)


def make_config(path: str, base: str) -> dict:
    cfg = {"sources": [{
        "id": "faky3", "name": "가짜", "enabled": True, "format": "html",
        "request": {
            "url": base + "/list", "method": "POST",
            "body": {"pageNum": "{page}", "rd_date": "{date:%Y-%m-%d}"},
            "delay_seconds": 0.01,
            "pages": {"start": 1, "max": 30, "stop_when_empty": True,
                      "stop_when_repeated": True},
        },
        "list_selector": "table.type2 tr",
        "fields": {
            "row_id": {"attr": "id"},
            "course_name": {"selector": "td:nth-child(5)"},
            "play_date": {"selector": "td:nth-child(2)"},
            "tee_time": {"selector": "td:nth-child(3)"},
            "green_fee": {"selector": "td:nth-child(8) span.price"},
        },
    }]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    return cfg


class TestLoadSourceConfig(unittest.TestCase):
    def test_not_found_raises_with_tried_paths(self):
        with self.assertRaises(collect.ConfigNotFound) as ctx:
            collect.load_source_config("없는소스이름", "/no/such/path.json")
        self.assertGreaterEqual(len(ctx.exception.tried), 1)

    def test_found_by_explicit_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sources.json")
            make_config(path, "http://x")
            cfg = collect.load_source_config("faky3", path)
            self.assertEqual(cfg["id"], "faky3")


class TestBuildSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "sources.json")
        make_config(self.path, "http://x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_min_delay_only_raises_never_lowers(self):
        """--delay 는 설정보다 짧게는 못 준다."""
        src = collect.build_source("faky3", config_path=self.path, min_delay=5.0)
        self.assertEqual(src.request_cfg["delay_seconds"], 5.0)

        src2 = collect.build_source("faky3", config_path=self.path, min_delay=0.001)
        self.assertEqual(src2.request_cfg["delay_seconds"], 0.01)  # 원래 값 유지

    def test_max_pages_overrides(self):
        src = collect.build_source("faky3", config_path=self.path, max_pages=3)
        self.assertEqual(src.request_cfg["pages"]["max"], 3)


class TestMergeIntoSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _tee(self, name, d, hhmm, fee):
        return TeeTime(course_name=name, play_date=d, tee_time=parse_time(hhmm),
                      green_fee=fee, source="faky3")

    def test_replaces_only_same_source(self):
        """다른 소스가 모아 둔 결과는 건드리지 않는다."""
        other = self._tee("남남", DAY, "07:00", 100000)
        other = TeeTime(**{**other.__dict__, "source": "other"})
        snapshot.save([other], directory=self.tmp)

        rows = [self._tee("가나CC", DAY, "08:00", 90000)]
        path, total = collect.merge_into_snapshot(
            "faky3", rows, [DAY], directory=self.tmp)

        loaded, _ = snapshot.load(snapshot.latest_path(self.tmp))
        sources = {t.source for t in loaded}
        self.assertEqual(sources, {"other", "faky3"})
        self.assertEqual(total, 2)

    def test_second_call_replaces_first(self):
        rows1 = [self._tee("가나CC", DAY, "08:00", 90000)]
        collect.merge_into_snapshot("faky3", rows1, [DAY], directory=self.tmp)
        rows2 = [self._tee("나다CC", DAY, "09:00", 95000)]
        _, total = collect.merge_into_snapshot(
            "faky3", rows2, [DAY], directory=self.tmp)

        self.assertEqual(total, 1)  # 이전 것으로 덮이고 새 것만 남음

    def test_marks_requested_dates_attempted_even_with_zero_rows(self):
        """0건이어도 시도한 날짜는 기록돼야 한다 — 계속 다시 권하지 않기 위해서다."""
        collect.merge_into_snapshot("faky3", [], [DAY], directory=self.tmp)
        self.assertTrue(collect.was_attempted("faky3", DAY, directory=self.tmp))
        self.assertFalse(collect.snapshot_has_date("faky3", DAY, directory=self.tmp))


class TestNeedsCollect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_true_when_never_tried(self):
        self.assertTrue(collect.needs_collect("faky3", DAY, directory=self.tmp))

    def test_false_once_collected(self):
        rows = [TeeTime(course_name="가나CC", play_date=DAY,
                        tee_time=parse_time("08:00"), green_fee=90000,
                        source="faky3")]
        collect.merge_into_snapshot("faky3", rows, [DAY], directory=self.tmp)
        self.assertFalse(collect.needs_collect("faky3", DAY, directory=self.tmp))

    def test_false_when_tried_and_empty(self):
        """매물이 진짜 0건인 날은 계속 다시 권하지 않는다."""
        collect.merge_into_snapshot("faky3", [], [DAY], directory=self.tmp)
        self.assertFalse(collect.needs_collect("faky3", DAY, directory=self.tmp))

    def test_true_for_a_different_untried_date(self):
        rows = [TeeTime(course_name="가나CC", play_date=DAY,
                        tee_time=parse_time("08:00"), green_fee=90000,
                        source="faky3")]
        collect.merge_into_snapshot("faky3", rows, [DAY], directory=self.tmp)
        self.assertTrue(collect.needs_collect(
            "faky3", date(2026, 9, 20), directory=self.tmp))


class TestEndToEndAgainstFakeSite(unittest.TestCase):
    """실제 build_source → WebSource.fetch → merge_into_snapshot 흐름."""

    @classmethod
    def setUpClass(cls):
        cls.srv, cls.base = start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.tmp, "sources.json")
        make_config(self.cfg_path, self.base)

    def test_collect_then_search_finds_it(self):
        src = collect.build_source("faky3", config_path=self.cfg_path)
        rows = src.fetch([DAY])
        self.assertGreater(len(rows), 0)

        self.assertTrue(collect.needs_collect("faky3", DAY, directory=self.tmp))
        collect.merge_into_snapshot("faky3", rows, [DAY], directory=self.tmp,
                                    requests=src.last_stats.get("requests", 0))
        self.assertFalse(collect.needs_collect("faky3", DAY, directory=self.tmp))
        self.assertTrue(collect.snapshot_has_date("faky3", DAY, directory=self.tmp))


if __name__ == "__main__":
    unittest.main(verbosity=2)
