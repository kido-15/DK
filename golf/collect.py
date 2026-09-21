"""플랫폼 목록에서 티타임을 모아 스냅샷에 저장하는 공통 로직.

scripts/collect_full.py(터미널에서 미리 모아 두는 방식)와 golf/server.py
(웹 대시보드에서 검색한 날짜가 없을 때 그 자리에서 모으는 방식)가 이
모듈을 함께 쓴다. 둘이 따로 구현하면 언젠가 동작이 어긋난다 — 예를 들어
한쪽만 페이지 상한을 다르게 적용한다든지.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from . import snapshot
from .models import TeeTime
from .sources.web_source import WebSource

CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")


class ConfigNotFound(Exception):
    """그 사이트 설정을 어디서도 찾지 못했다."""

    def __init__(self, source_id: str, tried: list[str]):
        self.source_id = source_id
        self.tried = tried
        super().__init__(f"'{source_id}' 설정을 찾지 못했습니다.")


def load_source_config(source_id: str, explicit: str = "") -> dict:
    """그 사이트의 설정 하나를 찾아 온다.

    config/sources.json 을 먼저 보고, 없으면 config/sources.<이름>.json 을 본다.
    """
    candidates = [explicit] if explicit else [
        os.path.join(CONFIG_DIR, "sources.json"),
        os.path.join(CONFIG_DIR, f"sources.{source_id}.json"),
    ]
    tried: list[str] = []
    for path in candidates:
        if not path or not os.path.exists(path):
            tried.append(path)
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        configs = data.get("sources") if isinstance(data, dict) else data
        for c in configs or []:
            if isinstance(c, dict) and c.get("id") == source_id:
                return c
        tried.append(f"{path} (그 안에 '{source_id}' 가 없음)")
    raise ConfigNotFound(source_id, tried)


def build_source(source_id: str, *, config_path: str = "", max_pages: int = 0,
                 no_split: bool = False, min_delay: float = 0.0) -> WebSource:
    """설정을 읽어 오고, 명령줄/호출자 쪽 조정을 얹어 WebSource 를 만든다.

    min_delay 는 요청 간격을 **늘리기만** 한다. 설정에 적어 둔 예의를
    호출자가 줄이지 못하게 하기 위해서다.
    """
    cfg = load_source_config(source_id, config_path)
    req = cfg.setdefault("request", {})

    if no_split:
        req.pop("split", None)
    if max_pages:
        req.setdefault("pages", {})["max"] = max_pages
    if min_delay:
        req["delay_seconds"] = max(min_delay, float(req.get("delay_seconds", 0)))

    return WebSource(cfg)


# ---------------------------------------------------------------------------
# 스냅샷 읽고 쓰기
#
# golf.snapshot 의 save()/latest_path() 는 directory 를 "기본값이 있는
# 매개변수" 로 받는데, 그 기본값은 golf.snapshot 이 처음 import 될 때
# 한 번만 정해진다(파이썬 함수 기본값의 흔한 함정). 그래서 나중에
# `snapshot.SNAPSHOT_DIR = 다른경로` 처럼 바꿔치기해도 소용없다 — 반드시
# 매번 명시적으로 directory 를 넘겨야 한다. 아래 함수들은 그래서 directory
# 인자를 받고, 안 주면 **호출 시점의** snapshot.SNAPSHOT_DIR 값을 쓴다
# (함수 기본값이 아니라 함수 안에서 읽으므로 매번 최신 값이다).
# ---------------------------------------------------------------------------


def merge_into_snapshot(source_id: str, rows: list[TeeTime], dates: list, *,
                        requests: int = 0,
                        directory: Optional[str] = None) -> tuple[str, int]:
    """방금 모은 결과를 스냅샷에 합쳐 저장한다.

    같은 소스의 예전 결과는 새 결과로 통째로 바꾸고, 다른 소스의 결과는
    그대로 둔다 — 예를 들어 골팡을 다시 모으면 골팡 것만 바뀌고 다른
    플랫폼에서 모아 둔 것은 남는다.

    dates 는 이번에 실제로 요청한 날짜들이다. rows 에 들어 있는 날짜만으로는
    "그 날짜를 시도했지만 0건이었다" 를 알 수 없어(0건이면 rows 에 아예
    안 나온다) 따로 받는다. mark_attempted 에 쓰인다.

    (저장 경로, 병합 후 전체 건수) 를 돌려준다.
    """
    directory = directory or snapshot.SNAPSHOT_DIR
    existing, _ = snapshot.load(snapshot.latest_path(directory))
    keep = [t for t in existing if t.source != source_id]
    merged = keep + rows
    path = snapshot.save(merged, directory=directory,
                         stats={"source": source_id, "collected": len(rows),
                                "requests": requests})
    for d in dates:
        mark_attempted(source_id, d, directory=directory)
    return path, len(merged)


def snapshot_has_date(source_id: str, play_date, *,
                      directory: Optional[str] = None) -> bool:
    """그 소스가 이미 그 날짜의 티타임을 갖고 있는지."""
    directory = directory or snapshot.SNAPSHOT_DIR
    rows, _ = snapshot.load(snapshot.latest_path(directory))
    return any(t.source == source_id and t.play_date == play_date for t in rows)


# ---------------------------------------------------------------------------
# 이미 시도해 본 날짜 기록 — 빈 날짜를 계속 다시 모으자고 권하지 않기 위해서다
# ---------------------------------------------------------------------------

_ATTEMPTED_FILENAME = "attempted.json"


def _attempted_path(directory: Optional[str] = None) -> str:
    directory = directory or snapshot.SNAPSHOT_DIR
    return os.path.join(directory, _ATTEMPTED_FILENAME)


def _load_attempted(directory: Optional[str] = None) -> dict:
    path = _attempted_path(directory)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def mark_attempted(source_id: str, play_date, *,
                   directory: Optional[str] = None) -> None:
    """이 날짜를 한 번 모아 봤다고 기록한다.

    모아 봤는데 0건이었던 날짜를 "지금 모아 보자" 고 계속 다시 권하지
    않기 위해서다. 매물이 진짜 없는 날인지, 아직 한 번도 안 모아 본
    날인지 구분해야 한다.
    """
    directory = directory or snapshot.SNAPSHOT_DIR
    data = _load_attempted(directory)
    dates = set(data.get(source_id, []))
    dates.add(play_date.isoformat())
    data[source_id] = sorted(dates)
    os.makedirs(directory, exist_ok=True)
    with open(_attempted_path(directory), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def was_attempted(source_id: str, play_date, *,
                  directory: Optional[str] = None) -> bool:
    data = _load_attempted(directory)
    return play_date.isoformat() in set(data.get(source_id, []))


def needs_collect(source_id: str, play_date, *,
                  directory: Optional[str] = None) -> bool:
    """그 날짜를 지금 모아 보자고 권해야 하는지.

    이미 갖고 있거나, 이미 시도해서 빈 날짜로 확인됐으면 권하지 않는다.
    (수동으로는 언제든 다시 모을 수 있다 — 이건 자동 권유만 막는다.)
    """
    if snapshot_has_date(source_id, play_date, directory=directory):
        return False
    return not was_attempted(source_id, play_date, directory=directory)
