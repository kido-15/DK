#!/usr/bin/env python3
"""예약 사이트 이름 ↔ 골프장 좌표 DB 매칭 테스트.

    python3 tests/test_matching.py

좌표를 못 찾은 골프장의 티타임은 **검색 결과에서 통째로 빠진다.** 출발지에서
몇 분 걸리는지 잴 수 없기 때문이다. 조용히 사라지는 쪽이라 여기서 막는다.

여기 쓰인 이름들은 골팡에서 실제로 수집한 239종에서 가져왔다.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from golf.courses import CourseBook                  # noqa: E402
from golf.models import Course, name_variants        # noqa: E402

# 좌표 DB 쪽 이름(공식 이름)을 흉내 낸 것
OFFICIAL = [
    ("gapyeong", "가평컨트리클럽", 37.83, 127.51),
    ("glenross", "글렌로스골프클럽", 37.29, 127.62),
    ("sindo", "신안컨트리클럽", 35.02, 126.38),
    ("mongbert", "몽베르컨트리클럽", 37.77, 127.29),
    ("songdo", "오렌지듄스골프클럽", 37.38, 126.64),
    ("roseviang", "큐로컨트리클럽", 36.81, 127.15),
    ("namseoul", "남서울컨트리클럽", 37.35, 127.06),
    ("victoria", "빅토리아컨트리클럽", 37.55, 127.68),
]


def book() -> CourseBook:
    return CourseBook([
        Course(course_id=i, name=n, lat=la, lon=lo) for i, n, la, lo in OFFICIAL
    ])


class TestBookingQualifiers(unittest.TestCase):
    """예약 사이트가 이름 뒤에 붙이는 판매 조건.

    골프장 이름의 일부가 아니라 "이 매물이 어떤 건인지" 를 말하는 꼬리표다.
    골프장 DB 에는 이런 말이 없으므로 떼어내지 않으면 영영 못 찾는다.
    """

    def setUp(self):
        self.book = book()

    def test_private_tag(self):
        """골팡 239종 중 24종이 (비공개) 를 달고 있다."""
        self.assertEqual(self.book.match("가평(비공개)").course_id, "gapyeong")

    def test_nine_hole_tag(self):
        """14종이 -퍼9 / (퍼9) 를 달고 있다."""
        self.assertEqual(self.book.match("글렌로스-퍼9").course_id, "glenross")

    def test_membership_tag(self):
        self.assertEqual(self.book.match("신안(병행)").course_id, "sindo")
        self.assertEqual(self.book.match("몽베르(대중제)").course_id, "mongbert")

    def test_former_name_in_parens(self):
        """옛 이름으로만 DB 에 있는 경우. 골팡은 새 이름에 괄호로 적어 준다."""
        self.assertEqual(
            self.book.match("골프존 송도(구.오렌지듄스)").course_id, "songdo")
        self.assertEqual(
            self.book.match("로제비앙(구.큐로cc)").course_id, "roseviang")


class TestNameVariants(unittest.TestCase):
    def test_specific_first(self):
        """있는 그대로를 먼저 본다. 꼬리표를 뗀 이름이 먼저 걸리면 안 된다."""
        v = name_variants("골프존 송도(구.오렌지듄스)")
        self.assertEqual(v[0], "골프존송도구오렌지듄스")

    def test_course_subdivision_is_kept(self):
        """(레이크)·(밸리) 는 골프장이 실제로 나눠 부르는 코스 이름이다.

        판매 조건처럼 떼어내면 서로 다른 코스가 한 곳으로 합쳐진다.
        """
        self.assertEqual(name_variants("남서울 컨트리클럽 (레이크)"), ["남서울레이크"])

    def test_region_qualifier_is_kept(self):
        """(청주) 같은 지역 표시는 같은 이름을 가리는 데 쓰인다. 남겨야 한다."""
        self.assertIn("그랜드청주", name_variants("그랜드(청주)"))

    def test_empty(self):
        self.assertEqual(name_variants(""), [])
        self.assertEqual(name_variants("   "), [])


class TestRegionHint(unittest.TestCase):
    """이름 괄호 안의 지역 표시.

    같은 이름이 여러 곳에 있을 때 가르는 말이다. 무시하고 붙이면 **엉뚱한
    지역의 좌표**가 박힌다. 좌표가 없는 것보다 나쁘다 — 없으면 결과에서
    빠지지만, 틀리면 "강남역에서 90분" 자리에 경남 골프장이 올라온다.
    """

    def setUp(self):
        self.book = CourseBook([
            Course(course_id="gn", name="그랜드 골프클럽", lat=35.2, lon=128.6,
                   region="경남", address="경남 김해시"),
            Course(course_id="powell", name="포웰CC", lat=35.3, lon=128.4,
                   region="경남", address="경남 양산시"),
            Course(course_id="tgv", name="떼제베 골프장(TGV CC)", lat=36.9,
                   lon=127.5, region="충북", address="충북 음성군"),
            Course(course_id="ns", name="남서울cc", lat=37.35, lon=127.06,
                   region="경기", address="경기 성남시"),
        ])

    def test_wrong_region_is_refused(self):
        """충북 청주의 그랜드를 경남 그랜드에 붙이면 안 된다. 못 찾는 편이 낫다."""
        self.assertIsNone(self.book.match("그랜드(청주)"))
        self.assertIsNone(self.book.match("포웰(안성)cc"))

    def test_course_subdivision_is_not_a_region(self):
        """(동북)·(레이크) 는 코스 구분이다. 지역으로 오해해 막으면 안 된다.

        글자만으로는 가를 수 없으므로, 좌표 DB 에 실제 지명으로 나타나는
        말일 때만 지역으로 본다. '동북'·'레이크' 는 나타나지 않는다.
        """
        self.assertEqual(self.book.match("떼제베(동북)").course_id, "tgv")
        self.assertEqual(self.book.match("남서울 컨트리클럽 (레이크)").course_id, "ns")

    def test_right_region_still_matches(self):
        b = CourseBook([
            Course(course_id="cj", name="그랜드 골프클럽", lat=36.6, lon=127.5,
                   region="충북", address="충북 청주시"),
        ])
        self.assertEqual(b.match("그랜드(청주)").course_id, "cj")


class TestParenPrefixIsIndexed(unittest.TestCase):
    """지도 데이터는 괄호에 영문명을 같이 적어 둔다.

    '떼제베 골프장(TGV CC)' 를 통째로만 색인하면 '떼제베' 로는 못 찾는다.
    """

    def test_matches_by_prefix_before_paren(self):
        b = CourseBook([
            Course(course_id="tgv", name="떼제베 골프장(TGV CC)", lat=36.9,
                   lon=127.5, region="충북"),
        ])
        self.assertEqual(b.match("떼제베").course_id, "tgv")


class TestExactBeatsFuzzy(unittest.TestCase):
    """확실한 방법을 모든 후보에 먼저 써 본 뒤 어림짐작으로 간다."""

    def test_exact_on_later_variant_wins(self):
        b = CourseBook([
            Course(course_id="right", name="신안", lat=35.0, lon=126.4),
            Course(course_id="wrong", name="신안산", lat=37.3, lon=126.8),
        ])
        self.assertEqual(b.match("신안(병행)").course_id, "right")


class TestUnmatchedIsReported(unittest.TestCase):
    """못 찾은 이름은 조용히 넘어가지 않고 보고되어야 한다."""

    def test_reported(self):
        b = book()
        b.match("없는골프장이름")
        b.match("없는골프장이름")
        report = dict(b.unmatched_report())
        self.assertEqual(report.get("없는골프장이름"), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
