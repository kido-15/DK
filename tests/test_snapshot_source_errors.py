#!/usr/bin/env python3
"""스냅샷 소스가 비었을 때 안내가 실제로 도움이 되는지 테스트.

    python3 tests/test_snapshot_source_errors.py

검색 결과 0건일 때 화면 기본 메시지는 "소스 설정이나 네트워크를 확인해
주세요" 처럼 뭉뚱그려져 있고, 진짜 이유(수집 결과 파일이 없음)는
"진단 정보 보기" 뒤에야 보였다. 그 이유가 담기는 자리(source.last_error)
자체가 실제로 맞는 명령을 가리키는지 여기서 확인한다.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from golf.sources.snapshot_source import SnapshotSource  # noqa: E402


class TestMissingSnapshotMessage(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.missing_path = os.path.join(tmp, "없는파일.json")

    def test_points_to_the_command_actually_used(self):
        """golfpang 등 플랫폼 수집은 collect_full.py 를 쓴다.

        예전엔 crawl_all.py(홈페이지 직접 수집) 만 안내해서, 정작 쓰는
        명령이 아니었다.
        """
        src = SnapshotSource(path=self.missing_path)
        rows = src.fetch([])
        self.assertEqual(rows, [])
        self.assertIn("collect_full.py", src.last_error)

    def test_still_mentions_the_direct_crawl_option(self):
        """홈페이지 직접 수집 경로도 여전히 안내한다 — 없앤 게 아니라 더한 것."""
        src = SnapshotSource(path=self.missing_path)
        src.fetch([])
        self.assertIn("crawl_all.py", src.last_error)

    def test_error_is_reported_through_search_stats(self):
        """검색 결과 화면의 source_errors 에 이 메시지가 그대로 올라가야
        화면에서 진짜 이유를 보여줄 수 있다."""
        from datetime import time

        from golf.courses import CourseBook
        from golf.models import SearchQuery
        from golf.routing import Router
        from golf.search import GolfSearch

        src = SnapshotSource(path=self.missing_path)
        book = CourseBook([])
        _, stats = GolfSearch(book, [src], Router()).search(
            SearchQuery(origin="", tee_from=time(6, 0), tee_to=time(10, 0)))

        self.assertEqual(stats.fetched, 0)
        self.assertIn(src.id, stats.source_errors)
        self.assertIn("collect_full.py", stats.source_errors[src.id])


if __name__ == "__main__":
    unittest.main(verbosity=2)
