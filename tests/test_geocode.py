#!/usr/bin/env python3
"""주소 → 좌표 자동 검색 테스트.

    python3 tests/test_geocode.py

건물 번호까지 있는 정확한 주소는 지오코딩 서비스가 못 찾는 경우가 잦다.
그대로 실패하면 사용자가 결국 좌표를 직접 찾아 넣어야 하는데, 그건 이
기능이 없애려는 수고 그 자체다. 세부 단위를 떼어가며 다시 찾아보는
동작을 실제 네트워크 없이 검증한다.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from golf.geo import USER_AGENT, Geocoder, _address_variants  # noqa: E402


class TestAddressVariants(unittest.TestCase):
    def test_strips_building_number(self):
        v = _address_variants("서울시 강남구 테헤란로 152")
        self.assertEqual(v, [
            "서울시 강남구 테헤란로 152",
            "서울시 강남구 테헤란로",
            "서울시 강남구",
        ])

    def test_strips_multiple_detail_levels(self):
        """번지-호수처럼 숫자 토큰이 여러 개 붙어도 하나씩 뗀다."""
        v = _address_variants("경기도 성남시 분당구 판교역로 235 3층 301호")
        self.assertIn("경기도 성남시 분당구 판교역로 235 3층", v)
        self.assertIn("경기도 성남시 분당구 판교역로", v)
        self.assertIn("경기도 성남시", v)

    def test_landmark_without_numbers_is_unchanged(self):
        """숫자가 없으면 뗄 것도 없다. 그대로 한 번만 시도한다."""
        self.assertEqual(_address_variants("강남역"), ["강남역"])

    def test_two_tokens_or_fewer_has_no_coarse_fallback(self):
        """이미 앞 두 단어뿐이면 그보다 더 뭉뚱그릴 게 없다."""
        self.assertEqual(_address_variants("강남구 역삼동"), ["강남구 역삼동"])

    def test_specific_first(self):
        """자세한 표기를 먼저 시도해야 한다. 뭉뚱그린 것이 먼저면 엉뚱한
        위치(구 전체 중심)를 정확한 주소보다 먼저 받아들이게 된다."""
        v = _address_variants("서울시 강남구 테헤란로 152")
        self.assertEqual(v[0], "서울시 강남구 테헤란로 152")


class TestGeocodeFallback(unittest.TestCase):
    """실제 네트워크 없이, 지오코딩 호출만 흉내 낸다."""

    def test_falls_back_when_exact_address_fails(self):
        """건물 번호까지 있는 주소가 실패하면, 뗀 표기로 다시 시도해 찾는다."""
        calls = []

        def fake_nominatim(self, query):
            calls.append(query)
            if query == "서울시 강남구 테헤란로":
                return (37.501, 127.039)
            return None

        with patch.object(Geocoder, "_nominatim", fake_nominatim):
            g = Geocoder()
            coords = g.geocode("서울시 강남구 테헤란로 152")

        self.assertEqual(coords, (37.501, 127.039))
        self.assertEqual(calls, ["서울시 강남구 테헤란로 152", "서울시 강남구 테헤란로"])

    def test_first_variant_succeeding_makes_no_extra_calls(self):
        """대부분은 첫 시도에서 찾아지므로, 그때는 추가 요청이 없어야 한다."""
        calls = []

        def fake_nominatim(self, query):
            calls.append(query)
            return (37.5, 127.0)

        with patch.object(Geocoder, "_nominatim", fake_nominatim):
            g = Geocoder()
            g.geocode("강남역")

        self.assertEqual(calls, ["강남역"])

    def test_all_variants_fail_returns_none(self):
        with patch.object(Geocoder, "_nominatim", lambda self, q: None):
            g = Geocoder()
            self.assertIsNone(g.geocode("존재하지않는주소12345"))

    def test_coordinates_bypass_network_entirely(self):
        """'37.4979,127.0276' 처럼 좌표를 직접 넣으면 네트워크를 타지 않는다."""
        called = []
        with patch.object(Geocoder, "_nominatim", lambda self, q: called.append(q)):
            g = Geocoder()
            coords = g.geocode("37.4979,127.0276")
        self.assertEqual(coords, (37.4979, 127.0276))
        self.assertEqual(called, [])

    def test_kakao_tried_before_nominatim_per_variant(self):
        """카카오 키가 있으면 각 표기마다 카카오를 먼저 본다."""
        order = []
        with patch.object(Geocoder, "_kakao", lambda self, q: order.append(("kakao", q)) or None), \
             patch.object(Geocoder, "_nominatim", lambda self, q: order.append(("nominatim", q)) or (37.5, 127.0)):
            g = Geocoder(kakao_key="test-key")
            g.geocode("강남역")
        self.assertEqual(order, [("kakao", "강남역"), ("nominatim", "강남역")])

    def test_result_cached_under_original_query(self):
        """중간에 뗀 표기가 아니라 사용자가 입력한 원래 문자열로 캐시된다."""
        calls = []

        def fake_nominatim(self, query):
            calls.append(query)
            return (37.501, 127.039) if query == "서울시 강남구" else None

        with patch.object(Geocoder, "_nominatim", fake_nominatim):
            g = Geocoder()
            g.geocode("서울시 강남구 테헤란로 152")
            g.geocode("서울시 강남구 테헤란로 152")   # 두 번째는 캐시에서

        self.assertEqual(calls, ["서울시 강남구 테헤란로 152", "서울시 강남구 테헤란로",
                                 "서울시 강남구"])   # 첫 호출에서만 3번, 두 번째는 0번


class TestUserAgentIsPolicyCompliant(unittest.TestCase):
    """Nominatim 이용 정책은 요청자를 식별할 수 있는 User-Agent를 요구한다.

    식별 정보가 없으면 조용히 차단되거나 빈 결과만 온다 — 겉보기엔 그냥
    "못 찾았다" 로만 보여서 원인을 알기 어렵다.
    """

    def test_identifies_the_application(self):
        self.assertIn("golf-finder", USER_AGENT)
        self.assertTrue(
            "http" in USER_AGENT or "@" in USER_AGENT,
            f"연락처나 URL이 없다: {USER_AGENT!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
