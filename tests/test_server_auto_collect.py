#!/usr/bin/env python3
"""웹 대시보드에서 "지금 모으기" 흐름 전체 테스트 — 실제 서버를 띄운다.

    python3 tests/test_server_auto_collect.py

검색한 날짜가 없으면 API 가 needs_collect 를 알려주고, /api/collect/start
로 시작해 /api/collect/status 로 진행을 보다가, 끝나면 그 날짜로 다시
검색하면 결과가 나오는 것까지 확인한다.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import date
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_v, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import golf.collect as collect                                 # noqa: E402
import golf.server as server                                   # noqa: E402
from golf.courses import Course, CourseBook                    # noqa: E402
from golf.geo import Geocoder                                  # noqa: E402
from golf.routing import Router                                # noqa: E402
from golf.sources import SnapshotSource                        # noqa: E402
from tests.fake_paged_site import start as start_fake_site     # noqa: E402

DAY = date(2026, 9, 19)


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(base, path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


class TestAutoCollectOverHttp(unittest.TestCase):
    """실제 http.server 를 띄우고 진짜 요청으로 확인한다."""

    @classmethod
    def setUpClass(cls):
        cls.fake_srv, cls.fake_base = start_fake_site()

    @classmethod
    def tearDownClass(cls):
        cls.fake_srv.shutdown()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.snap_dir = os.path.join(self.tmp, "snapshots")
        os.makedirs(self.snap_dir, exist_ok=True)
        # golf.snapshot.SNAPSHOT_DIR 는 기본값이라 못 바꾸므로, 이 소스가
        # 쓸 경로를 명시적으로 준다(SnapshotSource 는 path 인자를 받는다).
        self.cfg_path = os.path.join(self.tmp, "sources.json")
        cfg = {"sources": [{
            "id": "webfake", "name": "가짜", "enabled": True, "format": "html",
            "request": {
                "url": self.fake_base + "/list", "method": "POST",
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
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)

        # 이번 시험에서만 "webfake" 를 자동 수집 대상으로 쓴다.
        self._orig_auto = server.AUTO_COLLECT_SOURCE
        server.AUTO_COLLECT_SOURCE = "webfake"

        # jobs.start 가 이 설정 경로를 쓰도록, 실제 CollectJobManager 의
        # 기본 러너를 그대로 쓰되 config_path 를 넘겨야 한다 — 서버는
        # config_path 없이 부르므로, 여기서는 golf.collect.CONFIG_DIR 를
        # 임시로 바꿔 "sources.webfake.json" 위치에서 찾게 한다.
        import shutil
        cfg_dir = os.path.join(self.tmp, "config")
        os.makedirs(cfg_dir, exist_ok=True)
        shutil.copy(self.cfg_path, os.path.join(cfg_dir, "sources.webfake.json"))
        self._orig_config_dir = collect.CONFIG_DIR
        collect.CONFIG_DIR = cfg_dir

        book = CourseBook([Course(course_id="x", name="아무개", lat=37.5, lon=127.0)])
        snap_src = SnapshotSource(path=os.path.join(self.snap_dir, "latest.json"),
                                  source_id="webfake")
        state = server.AppState(book, [snap_src], Router(providers=["estimate"]),
                                Geocoder(), auto_collect_source="webfake")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                         type("H", (server.Handler,), {"state": state}))
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        import threading
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

        # merge_into_snapshot/needs_collect 가 이 스냅샷 디렉터리를 보게
        # golf.snapshot 자체의 SNAPSHOT_DIR 도 이 프로세스 안에서 바꾼다.
        # (server.py 는 collect.merge_into_snapshot 을 directory 없이
        # 부르므로, 실제로는 snapshot.SNAPSHOT_DIR 이 그대로 쓰인다 —
        # 그래서 여기서 바꿔 둔다. 테스트가 끝나면 되돌린다.)
        self._orig_snap_dir = collect.snapshot.SNAPSHOT_DIR
        collect.snapshot.SNAPSHOT_DIR = self.snap_dir

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        server.AUTO_COLLECT_SOURCE = self._orig_auto
        collect.CONFIG_DIR = self._orig_config_dir
        collect.snapshot.SNAPSHOT_DIR = self._orig_snap_dir

    def test_search_for_missing_date_offers_collect(self):
        resp = _get(self.base,
                    f"/api/search?origin=37.5,127.0&date={DAY.isoformat()}")
        self.assertEqual(resp["stats"]["fetched"], 0)
        self.assertIn("needs_collect", resp)
        self.assertEqual(resp["needs_collect"]["date"], DAY.isoformat())

    def test_start_then_status_then_search_succeeds(self):
        started = _post(self.base, "/api/collect/start", {"date": DAY.isoformat()})
        self.assertTrue(started["started"])

        status = None
        for _ in range(200):
            status = _get(self.base, "/api/collect/status")
            if status["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(status["status"], "done", status)
        self.assertGreater(status["result_rows"], 0)

        resp = _get(self.base,
                    f"/api/search?origin=37.5,127.0&date={DAY.isoformat()}")
        self.assertGreater(resp["stats"]["fetched"], 0)
        self.assertNotIn("needs_collect", resp)

    def test_double_start_reports_already_running(self):
        r1 = _post(self.base, "/api/collect/start", {"date": DAY.isoformat()})
        self.assertTrue(r1["started"])
        r2 = _post(self.base, "/api/collect/start", {"date": DAY.isoformat()})
        self.assertFalse(r2["started"])
        self.assertTrue(r2["already_running"])
        # 정리: 끝날 때까지 기다려 스레드가 남지 않게 한다
        for _ in range(200):
            if _get(self.base, "/api/collect/status")["status"] != "running":
                break
            time.sleep(0.05)

    def test_invalid_date_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            _post(self.base, "/api/collect/start", {"date": "이상한날짜"})
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
