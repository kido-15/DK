#!/usr/bin/env python3
"""/api/consecutive 엔드투엔드 테스트 — 실제 서버를 띄운다.

    python3 tests/test_server_consecutive.py

kd님이 실제로 요청한 시나리오("10월 18일 13시 경 3타임이 연속으로 되어
있는 구장 찾기")를 그대로 흉내 낸다: CSV 소스로 티타임을 주고, 검색
API가 아니라 /api/consecutive 가 골프장별로 묶어 연속된 것만 돌려주는지
확인한다.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from datetime import date
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_v, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import golf.server as server                                    # noqa: E402
from golf.courses import Course, CourseBook                      # noqa: E402
from golf.geo import Geocoder                                    # noqa: E402
from golf.routing import Router                                  # noqa: E402
from golf.sources import CsvSource                                # noqa: E402

DAY = date(2026, 10, 18)

ROWS = [
    # 가나CC: 12:50~13:10 이 카카오에서 3개 연속 (kd님이 찾던 모양)
    ("가나CC", "12:50", 150000, "kakao"),
    ("가나CC", "13:00", 150000, "kakao"),
    ("가나CC", "13:10", 150000, "kakao"),
    # 다라CC: 13:00, 13:30 — 30분 차이라 연속이 아니다
    ("다라CC", "13:00", 140000, "golfpang"),
    ("다라CC", "13:30", 140000, "golfpang"),
]


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


class TestConsecutiveOverHttp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        csv_path = os.path.join(self.tmp, "teetimes.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["course_name", "play_date", "tee_time", "green_fee", "source"])
            for name, hhmm, fee, source in ROWS:
                w.writerow([name, DAY.isoformat(), hhmm, fee, source])

        book = CourseBook([
            Course(course_id="ga", name="가나CC", lat=37.50, lon=127.00, region="경기"),
            Course(course_id="da", name="다라CC", lat=37.52, lon=127.02, region="경기"),
        ])
        src = CsvSource(csv_path, source_id="csv")
        state = server.AppState(book, [src], Router(providers=["estimate"]), Geocoder())

        self.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), type("H", (server.Handler,), {"state": state}))
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _url(self, **params) -> str:
        params.setdefault("origin", "37.5,127.0")
        params.setdefault("date", DAY.isoformat())
        return "/api/consecutive?" + urllib.parse.urlencode(params)

    def test_finds_the_three_consecutive_slots(self):
        resp = _get(self.base, self._url(tee_from="12:00", tee_to="14:00"))
        self.assertEqual(len(resp["groups"]), 1)
        g = resp["groups"][0]
        self.assertEqual(g["course_name"], "가나CC")
        self.assertEqual(g["count"], 3)
        self.assertEqual(g["first_tee"], "12:50")
        self.assertEqual(g["last_tee"], "13:10")

    def test_two_slots_thirty_minutes_apart_do_not_qualify(self):
        resp = _get(self.base, self._url(tee_from="12:00", tee_to="14:00"))
        names = {g["course_name"] for g in resp["groups"]}
        self.assertNotIn("다라CC", names)

    def test_min_count_can_be_relaxed(self):
        """min_count=2 로 낮추면 다라CC도(30분 차이는 여전히 걸러지지만) 규칙을
        바꾼 만큼 반영돼야 한다 — max_gap_minutes 를 넉넉히 주면 잡힌다."""
        resp = _get(self.base, self._url(
            tee_from="12:00", tee_to="14:00", min_count="2", max_gap_minutes="40"))
        names = {g["course_name"] for g in resp["groups"]}
        self.assertIn("다라CC", names)

    def test_time_window_excludes_slots_outside_it(self):
        """13:10 을 창 밖으로 밀어내면(13:05 까지) 가나CC는 2개만 남아 안 잡힌다."""
        resp = _get(self.base, self._url(tee_from="12:00", tee_to="13:05"))
        names = {g["course_name"] for g in resp["groups"]}
        self.assertNotIn("가나CC", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
