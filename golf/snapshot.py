"""일별 크롤링 결과 저장과 변동 추적.

특가는 매일 바뀐다. 어제보다 싸졌는지, 오늘 새로 뜬 자리인지 알려면
매번의 결과를 남겨 두고 비교해야 한다.

    data/golf/snapshots/2026-09-17.json   그날 수집한 전체 티타임
    data/golf/snapshots/latest.json       가장 최근 것 (검색이 이 파일을 읽는다)

크롤링은 오래 걸리므로 검색할 때마다 돌 수 없다. 하루 몇 번 crawl_all.py 로
모아 두고, 검색은 저장된 결과를 읽는 방식이다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Optional

from .models import TeeTime, parse_date, parse_time

SNAPSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "golf", "snapshots",
)


def _key(t: TeeTime) -> str:
    """같은 티타임인지 가리는 기준. 골프장+날짜+시각+소스."""
    return f"{t.course_name}|{t.play_date}|{t.tee_time:%H:%M}|{t.source}"


def save(tee_times: list[TeeTime], *, directory: str = SNAPSHOT_DIR,
         stats: Optional[dict] = None, when: Optional[datetime] = None) -> str:
    """수집 결과를 그날 파일과 latest.json 에 저장한다."""
    when = when or datetime.now()
    os.makedirs(directory, exist_ok=True)

    payload = {
        "collected_at": when.isoformat(timespec="seconds"),
        "count": len(tee_times),
        "stats": stats or {},
        "tee_times": [t.to_dict() for t in tee_times],
    }

    day_path = os.path.join(directory, f"{when.date().isoformat()}.json")
    for path in (day_path, os.path.join(directory, "latest.json")):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    return day_path


def load(path: str) -> tuple[list[TeeTime], dict]:
    """저장된 결과를 TeeTime 목록으로 되살린다."""
    if not os.path.exists(path):
        return [], {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return [], {}

    out: list[TeeTime] = []
    for d in data.get("tee_times") or []:
        play_date = parse_date(d.get("play_date"))
        tee_time = parse_time(d.get("tee_time"))
        if play_date is None or tee_time is None:
            continue
        slots = d.get("slots")
        out.append(TeeTime(
            course_name=d.get("course_name", ""),
            play_date=play_date,
            tee_time=tee_time,
            green_fee=int(d.get("green_fee", -1)),
            source=d.get("source", ""),
            booking_url=d.get("booking_url", "") or "",
            slots=int(slots) if isinstance(slots, int) else None,
            hole_info=d.get("hole_info", "") or "",
        ))
    meta = {k: v for k, v in data.items() if k != "tee_times"}
    return out, meta


def latest_path(directory: str = SNAPSHOT_DIR) -> str:
    return os.path.join(directory, "latest.json")


def list_snapshots(directory: str = SNAPSHOT_DIR) -> list[str]:
    """날짜순 스냅샷 파일 목록 (latest.json 제외)."""
    if not os.path.isdir(directory):
        return []
    names = [n for n in os.listdir(directory)
             if n.endswith(".json") and n != "latest.json"]
    return [os.path.join(directory, n) for n in sorted(names)]


def previous_path(directory: str = SNAPSHOT_DIR,
                  before: Optional[date] = None) -> Optional[str]:
    """가장 최근 것 바로 앞의 스냅샷. 비교 대상으로 쓴다."""
    paths = list_snapshots(directory)
    if before is not None:
        stamp = before.isoformat()
        paths = [p for p in paths if os.path.basename(p)[:-5] < stamp]
    if len(paths) < 1:
        return None
    return paths[-1]


# ---------------------------------------------------------------------------
# 변동 비교
# ---------------------------------------------------------------------------


@dataclass
class Change:
    """전 스냅샷 대비 달라진 점."""

    tee_time: TeeTime
    kind: str              # new | price_down | price_up | gone
    old_fee: int = -1

    @property
    def diff(self) -> int:
        if self.old_fee < 0 or self.tee_time.green_fee < 0:
            return 0
        return self.tee_time.green_fee - self.old_fee

    def to_dict(self) -> dict[str, Any]:
        d = self.tee_time.to_dict()
        d.update({"kind": self.kind, "old_fee": self.old_fee, "diff": self.diff})
        return d


def diff(old: list[TeeTime], new: list[TeeTime], *,
         min_drop: int = 10_000) -> dict[str, list[Change]]:
    """두 스냅샷을 비교해 새 자리와 가격 변동을 찾는다.

    min_drop 이상 싸진 것만 price_down 으로 본다. 몇 천 원 차이까지 알림을
    보내면 쓸모가 없기 때문이다.
    """
    old_map = {_key(t): t for t in old}
    new_map = {_key(t): t for t in new}

    out: dict[str, list[Change]] = {"new": [], "price_down": [], "price_up": [], "gone": []}

    for key, t in new_map.items():
        prev = old_map.get(key)
        if prev is None:
            out["new"].append(Change(t, "new"))
            continue
        if t.green_fee < 0 or prev.green_fee < 0:
            continue
        delta = t.green_fee - prev.green_fee
        if delta <= -min_drop:
            out["price_down"].append(Change(t, "price_down", prev.green_fee))
        elif delta >= min_drop:
            out["price_up"].append(Change(t, "price_up", prev.green_fee))

    for key, t in old_map.items():
        if key not in new_map:
            out["gone"].append(Change(t, "gone"))

    out["price_down"].sort(key=lambda c: c.diff)          # 많이 내린 순
    out["new"].sort(key=lambda c: (c.tee_time.green_fee
                                   if c.tee_time.green_fee >= 0 else 10**9))
    return out


def format_changes(changes: dict[str, list[Change]], *, limit: int = 10) -> str:
    """변동 내역을 사람이 읽을 수 있게. 알림 메일 본문으로도 쓴다."""
    lines: list[str] = []

    downs = changes.get("price_down") or []
    if downs:
        lines.append(f"■ 가격이 내려간 티타임 {len(downs)}건")
        for c in downs[:limit]:
            t = c.tee_time
            lines.append(
                f"   {t.course_name} {t.play_date} {t.tee_time:%H:%M}  "
                f"{c.old_fee:,}원 → {t.green_fee:,}원 ({c.diff:+,}원)"
            )
        if len(downs) > limit:
            lines.append(f"   ... 외 {len(downs) - limit}건")

    news = changes.get("new") or []
    if news:
        if lines:
            lines.append("")
        lines.append(f"■ 새로 올라온 티타임 {len(news)}건")
        for c in news[:limit]:
            t = c.tee_time
            fee = f"{t.green_fee:,}원" if t.green_fee >= 0 else "가격미상"
            lines.append(f"   {t.course_name} {t.play_date} {t.tee_time:%H:%M}  {fee}")
        if len(news) > limit:
            lines.append(f"   ... 외 {len(news) - limit}건")

    gone = changes.get("gone") or []
    if gone:
        if lines:
            lines.append("")
        lines.append(f"■ 사라진 티타임 {len(gone)}건 (예약 완료되었거나 내려감)")

    return "\n".join(lines) if lines else "변동 없음"
