#!/usr/bin/env python3
"""웹 대시보드의 "지금 모으기" 백그라운드 작업 관리자 테스트.

    python3 tests/test_collect_job.py

검색한 날짜가 없을 때 화면에서 바로 수집을 시작할 수 있게 하는 부분이다.
여러 탭에서 동시에 눌러도 사이트를 중복으로 두드리지 않아야 하고, 진행
상황이 실시간으로 보여야 하며, 실패해도 서버 전체가 죽지 않아야 한다.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_v, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

from golf.collect_job import CollectJobManager, JobStatus  # noqa: E402

DAY = date(2026, 9, 19)


class TestJobStatusDict(unittest.TestCase):
    def test_idle_job_when_never_started(self):
        mgr = CollectJobManager(runner=lambda *a: (0, 0))
        s = mgr.status("golfpang")
        self.assertEqual(s["status"], "idle")
        self.assertEqual(s["source"], "golfpang")


class TestSingleFlight(unittest.TestCase):
    """여러 탭에서 동시에 눌러도 한 번만 돈다."""

    def test_second_start_while_running_does_not_start_a_new_thread(self):
        started = threading.Event()
        release = threading.Event()
        calls = []

        def slow_runner(source_id, dates, config_path, on_progress):
            calls.append(1)
            started.set()
            release.wait(timeout=5)
            return 10, 3

        mgr = CollectJobManager(runner=slow_runner)
        r1 = mgr.start("golfpang", [DAY])
        self.assertTrue(r1["started"])
        started.wait(timeout=2)

        r2 = mgr.start("golfpang", [DAY])
        self.assertFalse(r2["started"])
        self.assertTrue(r2["already_running"])

        release.set()
        time.sleep(0.2)
        self.assertEqual(len(calls), 1, "이미 도는데 또 시작했다")

    def test_different_sources_run_independently(self):
        events = {"a": threading.Event(), "b": threading.Event()}

        def runner(source_id, dates, config_path, on_progress):
            events[source_id].set()
            time.sleep(0.1)
            return 1, 1

        mgr = CollectJobManager(runner=runner)
        mgr.start("a", [DAY])
        mgr.start("b", [DAY])
        self.assertTrue(events["a"].wait(timeout=1))
        self.assertTrue(events["b"].wait(timeout=1))


class TestProgressAndCompletion(unittest.TestCase):
    def test_progress_updates_are_visible_during_run(self):
        started = threading.Event()
        release = threading.Event()

        def runner(source_id, dates, config_path, on_progress):
            on_progress(DAY, 1, 100, 100, "한강이남")
            started.set()
            release.wait(timeout=5)
            return 100, 1

        mgr = CollectJobManager(runner=runner)
        mgr.start("golfpang", [DAY])
        started.wait(timeout=2)

        status = mgr.status("golfpang")
        self.assertEqual(status["status"], "running")
        self.assertEqual(status["current_bucket"], "한강이남")
        self.assertEqual(status["rows_so_far"], 100)

        release.set()

    def test_done_status_has_result_rows(self):
        mgr = CollectJobManager(runner=lambda *a: (42, 5))
        mgr.start("golfpang", [DAY])
        for _ in range(50):
            if mgr.status("golfpang")["status"] != "running":
                break
            time.sleep(0.02)
        status = mgr.status("golfpang")
        self.assertEqual(status["status"], "done")
        self.assertEqual(status["result_rows"], 42)
        self.assertEqual(status["requests_so_far"], 5)

    def test_can_restart_after_done(self):
        mgr = CollectJobManager(runner=lambda *a: (1, 1))
        mgr.start("golfpang", [DAY])
        for _ in range(50):
            if mgr.status("golfpang")["status"] != "running":
                break
            time.sleep(0.02)
        r = mgr.start("golfpang", [DAY])
        self.assertTrue(r["started"], "끝난 뒤에는 다시 시작할 수 있어야 한다")


class TestFailureIsContained(unittest.TestCase):
    """수집 중 오류가 나도 서버(관리자)가 죽으면 안 된다."""

    def test_exception_becomes_error_status_not_a_crash(self):
        def boom(*a):
            raise RuntimeError("사이트가 응답하지 않습니다")

        mgr = CollectJobManager(runner=boom)
        mgr.start("golfpang", [DAY])
        for _ in range(50):
            if mgr.status("golfpang")["status"] != "running":
                break
            time.sleep(0.02)
        status = mgr.status("golfpang")
        self.assertEqual(status["status"], "error")
        self.assertIn("응답하지 않습니다", status["error"])


class TestRealCollectionAgainstFakeSite(unittest.TestCase):
    """기본 runner(golf.collect 사용)로 실제 가짜 사이트를 상대로 끝까지."""

    @classmethod
    def setUpClass(cls):
        from tests.fake_paged_site import start
        cls.srv, cls.base = start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_full_flow_updates_snapshot(self):
        import json
        import tempfile

        import golf.collect as collect

        tmp = tempfile.mkdtemp()
        cfg_path = os.path.join(tmp, "sources.json")
        cfg = {"sources": [{
            "id": "jobfake", "name": "가짜", "enabled": True, "format": "html",
            "request": {
                "url": self.base + "/list", "method": "POST",
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
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)

        orig_dir = collect.snapshot.SNAPSHOT_DIR
        snap_dir = os.path.join(tmp, "snapshots")

        def runner(source_id, dates, config_path, on_progress):
            src = collect.build_source(source_id, config_path=config_path)
            rows = src.fetch(dates, on_progress=on_progress)
            stats = src.last_stats
            collect.merge_into_snapshot(source_id, rows, dates,
                                        requests=stats.get("requests", 0),
                                        directory=snap_dir)
            return len(rows), stats.get("requests", 0)

        mgr = CollectJobManager(runner=runner)
        self.assertTrue(collect.needs_collect("jobfake", DAY, directory=snap_dir))

        mgr.start("jobfake", [DAY], config_path=cfg_path)
        for _ in range(200):
            if mgr.status("jobfake")["status"] != "running":
                break
            time.sleep(0.05)

        status = mgr.status("jobfake")
        self.assertEqual(status["status"], "done", status)
        self.assertGreater(status["result_rows"], 0)
        self.assertFalse(collect.needs_collect("jobfake", DAY, directory=snap_dir))


if __name__ == "__main__":
    unittest.main(verbosity=2)
