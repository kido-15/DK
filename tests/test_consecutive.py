#!/usr/bin/env python3
"""연속 티타임 묶음 찾기 (golf/consecutive.py) 테스트.

    python3 tests/test_consecutive.py

여러 팀이 한 골프장에서 이어서 치려면, 티오프 시각이 조금씩 차이 나는
티타임 여러 개가 같은 예약처에 나란히 있어야 한다. 실제 kd님이 "10월
18일 13시 경 3타임이 연속으로 되어 있는 구장을 찾아 달라"고 요청한
시나리오를 그대로 반영한 시험이다.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from golf.consecutive import find_consecutive_groups              # noqa: E402
from golf.models import Course, SearchResult, TeeTime, parse_time  # noqa: E402

DAY = date(2026, 10, 18)


def _course(course_id: str, name: str, region: str = "경기") -> Course:
    return Course(course_id=course_id, name=name, lat=37.5, lon=127.0, region=region)


def _result(course: Course, hhmm: str, *, fee: int = 150000, source: str = "kakao",
           drive: float = 40.0) -> SearchResult:
    tee = TeeTime(course_name=course.name, play_date=DAY, tee_time=parse_time(hhmm),
                 green_fee=fee, source=source, course=course)
    return SearchResult(tee_time=tee, drive_minutes=drive)


class TestFindsConsecutiveRuns(unittest.TestCase):
    def test_three_ten_minute_apart_slots_form_one_group(self):
        """kd님이 실제로 찾던 모양: 12:50, 13:00, 13:10 이 한 골프장에 나란히."""
        c = _course("a", "가나CC")
        results = [_result(c, "12:50"), _result(c, "13:00"), _result(c, "13:10")]
        groups = find_consecutive_groups(results, min_count=3)
        self.assertEqual(len(groups), 1)
        g = groups[0]
        self.assertEqual(g.course_name, "가나CC")
        self.assertEqual(g.count, 3)
        self.assertEqual(g.first_tee.strftime("%H:%M"), "12:50")
        self.assertEqual(g.last_tee.strftime("%H:%M"), "13:10")

    def test_gap_over_threshold_breaks_the_run(self):
        """간격이 너무 벌어지면 같은 묶음으로 보면 안 된다."""
        c = _course("a", "가나CC")
        results = [_result(c, "13:00"), _result(c, "13:10"), _result(c, "13:25")]
        groups = find_consecutive_groups(results, min_count=3, max_gap_minutes=10)
        self.assertEqual(groups, [])

    def test_only_two_slots_is_not_enough_for_three(self):
        c = _course("a", "가나CC")
        results = [_result(c, "13:00"), _result(c, "13:10")]
        groups = find_consecutive_groups(results, min_count=3)
        self.assertEqual(groups, [])

    def test_run_longer_than_requested_is_reported_in_full(self):
        """5개가 이어져 있으면 3개만 잘라내지 않고 5개 그대로 보여 준다 —
        더 많이 이어질수록 좋은 정보다."""
        c = _course("a", "가나CC")
        results = [_result(c, hhmm) for hhmm in
                  ["12:40", "12:50", "13:00", "13:10", "13:20"]]
        groups = find_consecutive_groups(results, min_count=3)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].count, 5)

    def test_different_sources_are_not_combined(self):
        """골팡에서 잡힌 자리와 카카오에서 잡힌 자리는 실제로는 서로 다른
        예약 창구라 하나로 묶으면 안 된다 — 한쪽만 3개 이상이어야 잡힌다."""
        c = _course("a", "가나CC")
        results = [
            _result(c, "13:00", source="golfpang"),
            _result(c, "13:10", source="kakao"),
            _result(c, "13:20", source="golfpang"),
        ]
        groups = find_consecutive_groups(results, min_count=3)
        self.assertEqual(groups, [])

    def test_different_courses_are_separate_groups(self):
        a, b = _course("a", "가나CC"), _course("b", "다라CC")
        results = [
            _result(a, "13:00"), _result(a, "13:10"), _result(a, "13:20"),
            _result(b, "13:00"), _result(b, "13:08"), _result(b, "13:16"),
        ]
        groups = find_consecutive_groups(results, min_count=3)
        names = {g.course_name for g in groups}
        self.assertEqual(names, {"가나CC", "다라CC"})

    def test_unmatched_course_is_skipped_not_crashed(self):
        """골프장 매칭이 안 된 티타임은 어느 묶음인지 알 수 없으니 건너뛴다."""
        tee = TeeTime(course_name="이상한이름", play_date=DAY,
                     tee_time=parse_time("13:00"), green_fee=100000, source="kakao")
        results = [SearchResult(tee_time=tee)]
        groups = find_consecutive_groups(results, min_count=3)
        self.assertEqual(groups, [])

    def test_duplicate_same_time_keeps_cheaper_one(self):
        """같은 시각이 두 번(가격만 다르게) 들어와도 하나의 슬롯으로 본다."""
        c = _course("a", "가나CC")
        results = [
            _result(c, "13:00", fee=200000), _result(c, "13:00", fee=150000),
            _result(c, "13:10"), _result(c, "13:20"),
        ]
        groups = find_consecutive_groups(results, min_count=3)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].count, 3)
        self.assertEqual(groups[0].results[0].tee_time.green_fee, 150000)

    def test_groups_sorted_by_count_then_drive_time(self):
        near = _course("near", "가까운CC")
        far = _course("far", "먼CC")
        results = [
            _result(far, "13:00", drive=20.0), _result(far, "13:10", drive=20.0),
            _result(far, "13:20", drive=20.0),
            _result(near, "13:00", drive=60.0), _result(near, "13:10", drive=60.0),
            _result(near, "13:20", drive=60.0), _result(near, "13:30", drive=60.0),
        ]
        groups = find_consecutive_groups(results, min_count=3)
        # 4개 이어진 쪽(near, count=4)이 3개짜리(far)보다 먼저 나와야 한다 —
        # 이동시간보다 "몇 개가 이어지는지"를 먼저 본다. 이름과는 반대로
        # near 가 이동시간이 더 길게(drive=60) 설정돼 있어, 만약 정렬이
        # 이동시간부터 본다면 순서가 뒤집혀 이 시험이 잡아낸다.
        self.assertEqual([g.course_name for g in groups], ["가까운CC", "먼CC"])

    def test_to_dict_has_expected_shape(self):
        c = _course("a", "가나CC", region="경기")
        results = [_result(c, "12:50"), _result(c, "13:00"), _result(c, "13:10")]
        groups = find_consecutive_groups(results, min_count=3)
        d = groups[0].to_dict()
        self.assertEqual(d["course_name"], "가나CC")
        self.assertEqual(d["region"], "경기")
        self.assertEqual(d["count"], 3)
        self.assertEqual(d["first_tee"], "12:50")
        self.assertEqual(d["last_tee"], "13:10")
        self.assertEqual(len(d["slots"]), 3)
        self.assertIn("drive_minutes", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
