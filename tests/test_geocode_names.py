#!/usr/bin/env python3
"""이름으로 골프장 좌표를 찾을 때의 안전장치 테스트.

    python3 tests/test_geocode_names.py

격자 훑기는 지도에 golf_course 로 찍힌 곳만 가져온다. 그렇게 안 찍힌
골프장은 이름을 직접 대고 찾아야 하는데, **엉뚱한 곳이 잡히기 쉽다.**
좌표가 틀리면 없는 것보다 나쁘다 — 없으면 결과에서 빠지지만, 틀리면
엉뚱한 지역이 이동시간 안에 들어온다.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from golf.models import Course                                # noqa: E402

_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "scripts", "fetch_golf_courses.py")
_spec = importlib.util.spec_from_file_location("fetch_golf_courses", _SCRIPT)
fetch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch)


def course(name, region="", address="", aliases=None):
    return Course(course_id="x", name=name, lat=37.5, lon=127.5,
                  region=region, address=address, aliases=aliases or [])


class TestSearchName(unittest.TestCase):
    def test_booking_tags_are_dropped(self):
        self.assertEqual(fetch._search_name("스프링베일-퍼9"), "스프링베일")
        self.assertEqual(fetch._search_name("가평(비공개)"), "가평")

    def test_region_is_kept_in_the_query(self):
        """괄호를 통째로 지우면 '어느 포웰인지' 가 사라진다.

        떼고 찾으면 경남 포웰이 나와 경기 골프장에 경남 좌표가 박힌다.
        """
        self.assertIn("안성", fetch._search_name("포웰(안성)cc"))
        self.assertIn("청주", fetch._search_name("그랜드(청주)"))


class TestSamePlace(unittest.TestCase):
    def test_name_must_actually_match(self):
        self.assertTrue(fetch._same_place("라싸", course("라싸골프클럽")))
        self.assertTrue(fetch._same_place("라싸", course("라싸CC")))
        self.assertFalse(fetch._same_place("라싸", course("전혀다른골프장")))

    def test_wrong_region_is_refused(self):
        """이름이 같아도 지역이 다르면 다른 골프장이다."""
        self.assertFalse(fetch._same_place(
            "포웰 cc", course("포웰CC", region="경남", address="경남 양산시"), "안성"))
        self.assertTrue(fetch._same_place(
            "포웰 cc", course("포웰CC", region="경기", address="경기 안성시"), "안성"))

    def test_alias_counts(self):
        self.assertTrue(fetch._same_place(
            "오렌지듄스", course("송도GC", aliases=["오렌지듄스GC"])))

    def test_empty_query(self):
        self.assertFalse(fetch._same_place("", course("아무골프장")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
