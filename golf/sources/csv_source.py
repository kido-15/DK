"""CSV 파일에서 티타임을 읽는 소스.

크롤링이 막히거나 특정 사이트를 아직 설정하지 못했을 때, 손으로 정리한 표를
그대로 검색에 태울 수 있다. 크롤러가 만든 결과를 저장해 두고 재사용할 때도 쓴다.

필요한 열: course_name, play_date, tee_time, green_fee
선택 열:  booking_url, slots, hole_info, source
"""

from __future__ import annotations

import csv
import os
from datetime import date
from typing import Optional

from ..models import TeeTime, parse_date, parse_price, parse_time


class CsvSource:
    def __init__(self, path: str, source_id: str = "csv", name: str = "CSV 파일"):
        self.path = path
        self.id = source_id
        self.name = name
        self.last_error: str = ""

    def fetch(self, dates: Optional[list[date]] = None) -> list[TeeTime]:
        self.last_error = ""
        if not os.path.exists(self.path):
            self.last_error = f"파일이 없습니다: {self.path}"
            return []

        wanted = set(dates) if dates else None
        out: list[TeeTime] = []
        try:
            with open(self.path, newline="", encoding="utf-8-sig") as f:
                for lineno, row in enumerate(csv.DictReader(f), start=2):
                    tee = self._row_to_teetime(row)
                    if tee is None:
                        continue
                    if wanted and tee.play_date not in wanted:
                        continue
                    out.append(tee)
        except Exception as exc:
            self.last_error = f"읽기 실패: {exc}"
            return out
        return out

    def _row_to_teetime(self, row: dict) -> Optional[TeeTime]:
        name = (row.get("course_name") or row.get("골프장") or "").strip()
        if not name:
            return None
        d = parse_date(row.get("play_date") or row.get("날짜"))
        t = parse_time(row.get("tee_time") or row.get("시간"))
        if d is None or t is None:
            return None
        slots_raw = str(row.get("slots") or "").strip()
        return TeeTime(
            course_name=name,
            play_date=d,
            tee_time=t,
            green_fee=parse_price(row.get("green_fee") or row.get("그린피")),
            source=(row.get("source") or self.id).strip(),
            booking_url=(row.get("booking_url") or "").strip(),
            slots=int(slots_raw) if slots_raw.isdigit() else None,
            hole_info=(row.get("hole_info") or "").strip(),
            raw=dict(row),
        )

    @staticmethod
    def write(path: str, tee_times: list[TeeTime]) -> int:
        """티타임 목록을 CSV로 저장한다 (크롤링 결과 보관용)."""
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        cols = ["course_name", "play_date", "tee_time", "green_fee",
                "source", "booking_url", "slots", "hole_info"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for t in tee_times:
                w.writerow({
                    "course_name": t.course_name,
                    "play_date": t.play_date.isoformat(),
                    "tee_time": t.tee_time.strftime("%H:%M"),
                    "green_fee": t.green_fee,
                    "source": t.source,
                    "booking_url": t.booking_url,
                    "slots": t.slots if t.slots is not None else "",
                    "hole_info": t.hole_info,
                })
        return len(tee_times)
