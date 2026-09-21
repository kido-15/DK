"""저장된 크롤링 결과(스냅샷)를 읽는 소스.

개별 골프장 수백 곳을 도는 데는 몇 분이 걸린다. 검색할 때마다 돌 수 없으므로
crawl_all.py 가 모아 둔 결과를 읽는다.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Optional

from .. import snapshot
from ..models import TeeTime


class SnapshotSource:
    def __init__(self, path: Optional[str] = None, source_id: str = "snapshot",
                 name: str = "저장된 수집 결과"):
        self.path = path or snapshot.latest_path()
        self.id = source_id
        self.name = name
        self.last_error = ""
        self.last_stats: dict = {}
        self.collected_at: str = ""

    def fetch(self, dates: Optional[list[date]] = None) -> list[TeeTime]:
        self.last_error = ""
        if not os.path.exists(self.path):
            self.last_error = (
                f"수집 결과가 없습니다: {self.path}\n"
                "  아래 중 하나를 먼저 실행하세요.\n"
                "  python3 scripts/collect_full.py golfpang --days 3   (예약 사이트)\n"
                "  python3 scripts/crawl_all.py                       (골프장 홈페이지 직접 수집)"
            )
            return []

        rows, meta = snapshot.load(self.path)
        self.collected_at = meta.get("collected_at", "")

        if not rows:
            self.last_error = "저장된 결과가 비어 있습니다"
            return []

        # 수집한 지 오래된 결과는 예약 상황이 달라졌을 수 있으므로 알려 준다
        if self.collected_at:
            try:
                age = datetime.now() - datetime.fromisoformat(self.collected_at)
                hours = age.total_seconds() / 3600
                if hours > 24:
                    self.last_error = (
                        f"수집한 지 {hours/24:.0f}일 지난 결과입니다. "
                        "python3 scripts/crawl_all.py 로 갱신하세요."
                    )
            except ValueError:
                pass

        if dates:
            wanted = set(dates)
            rows = [t for t in rows if t.play_date in wanted]

        self.last_stats = {"requests": 0, "rows": len(rows), "errors": [],
                           "collected_at": self.collected_at}
        return rows
