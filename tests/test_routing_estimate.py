#!/usr/bin/env python3
"""이동시간 추정이 실제 도로와 얼마나 맞는지 테스트.

    python3 tests/test_routing_estimate.py

길찾기 API 를 못 쓰는 환경에서는 직선거리로 추정한다. 그 추정이 "90분 이내"
같은 조건을 가르는 데 쓰이므로, 어긋나면 **실제로는 더 가까운 곳이 잘리고
실제로는 90분을 넘는 곳이 통과한다.**

아래 실측값은 2026-09-19 에 강남역(37.4979,127.0276) 기준으로 골프장 35곳을
OSRM 실제 경로와 맞대어 잰 것이다(reports/route-estimate-vs-osrm.csv).
그중 거리대를 고루 덮는 것을 골라 두었다.
"""

from __future__ import annotations

import os
import statistics
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from golf.routing import _estimate, _speed_kmh                  # noqa: E402

GANGNAM = (37.4979, 127.0276)

# (골프장, 위도, 경도, OSRM 실측 분)
MEASURED = [
    ("아세코밸리골프클럽클럽하우스", 37.367828, 126.769678, 35.0),
    ("동서울골프클럽", 37.471645, 127.33676, 36.8),
    ("양지파인CC", 37.215259, 127.297381, 42.9),
    ("해솔리아CC", 37.208369, 127.184226, 45.2),
    ("블루원 용인CC", 37.129604, 127.322186, 50.5),
    ("골프존카운티 안성W", 37.07595, 127.194287, 54.8),
    ("비에이비스타CC", 37.179591, 127.419048, 56.4),
    ("샤인데일골프리조트", 37.674677, 127.561235, 61.5),
    ("에덴블루CC", 37.054631, 127.389042, 66.1),
    ("양평TPC골프클럽", 37.413379, 127.645106, 69.0),
    ("감곡CC", 37.129366, 127.696916, 76.5),
    ("이천실크밸리GC", 37.081305, 127.575783, 78.9),
    ("일레븐CC", 37.091431, 127.735668, 80.6),
    ("비콘힐스골프클럽", 37.62464, 127.867872, 85.8),
    ("센추리21CC", 37.282541, 127.849576, 87.3),
    ("센테리움", 37.028605, 127.786534, 87.3),
    ("골프존카운티 화랑", 36.821211, 127.468845, 89.1),
    ("골프존카운티 진천", 36.816274, 127.400943, 89.5),
]


def estimate_min(lat: float, lon: float) -> float:
    return _estimate(GANGNAM[0], GANGNAM[1], lat, lon).duration_min


class TestSpeedTable(unittest.TestCase):
    def test_speed_rises_with_distance(self):
        """멀수록 고속도로 비중이 높아 빨라진다. 뒤집히면 안 된다."""
        speeds = [_speed_kmh(km) for km in (5, 20, 40, 60, 80, 100, 150)]
        self.assertEqual(speeds, sorted(speeds))

    def test_speeds_are_plausible(self):
        for km in (5, 20, 40, 60, 80, 100, 150, 400):
            kmh = _speed_kmh(km)
            self.assertGreater(kmh, 20, f"{km}km 에서 너무 느리다")
            self.assertLess(kmh, 110, f"{km}km 에서 너무 빠르다")

    def test_beyond_the_table_does_not_explode(self):
        self.assertEqual(_speed_kmh(10_000), _speed_kmh(130))
        self.assertEqual(_speed_kmh(0), _speed_kmh(10))


class TestAgainstMeasured(unittest.TestCase):
    """실측과의 오차. 고치기 전에는 평균 6.1분이었다."""

    def setUp(self):
        self.errors = [abs(estimate_min(lat, lon) - real)
                       for _, lat, lon, real in MEASURED]

    def test_average_error_stays_small(self):
        avg = statistics.mean(self.errors)
        self.assertLess(avg, 5.0, f"평균 오차가 {avg:.1f}분으로 커졌다")

    def test_no_wild_outlier(self):
        worst = max(self.errors)
        self.assertLess(worst, 15.0, f"가장 큰 오차가 {worst:.1f}분이다")

    def test_direction_is_not_systematic(self):
        """한쪽으로만 치우치면 안 된다. 고치기 전에는 가까운 곳을 느리게,
        먼 곳을 빠르게 잡아 경계에 걸린 골프장이 뒤바뀌었다."""
        signed = [estimate_min(lat, lon) - real for _, lat, lon, real in MEASURED]
        self.assertLess(abs(statistics.mean(signed)), 4.0)


class TestEstimateAlwaysAnswers(unittest.TestCase):
    """길찾기가 안 되어도 검색은 멈추지 않아야 한다."""

    def test_same_point(self):
        info = _estimate(*GANGNAM, *GANGNAM)
        self.assertEqual(info.provider, "estimate")
        self.assertGreaterEqual(info.duration_min, 0)

    def test_far_point(self):
        info = _estimate(GANGNAM[0], GANGNAM[1], 33.4, 126.5)   # 제주
        self.assertGreater(info.duration_min, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
