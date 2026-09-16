"""sites.json 로드 및 검증.

설정 파일 구조 (sites.json)
{
  "defaults": { "lookback_days": 1, "pages": 1, "timeout": 20 },
  "include_keywords": [],          // 비우면 제목 필터 없음
  "exclude_keywords": ["채용"],
  "sites": [
    {
      "id": "kisdi_report",             // 필수, 상태 저장 키로 쓰이므로 바꾸지 말 것
      "name": "KISDI 연구보고서",         // 필수, 메일에 표시되는 이름
      "list_url": "https://.../list.do",// 필수, 게시판 '목록' 페이지 URL
      "enabled": true,
      "type": "html",                   // "html" | "rss"
      "row_selector": "table.board tbody tr",   // 생략하면 자동탐지
      "title_selector": "td.subject a",
      "link_selector": "td.subject a",  // 생략하면 title_selector 사용
      "date_selector": "td.date",
      "category_selector": "td.cate",
      "date_format": "%Y.%m.%d",        // 생략하면 자동 인식
      "detail_url_template": "https://.../view.do?no={arg0}",  // javascript 링크용
      "pages": 1,                       // 2 이상이면 page_param으로 여러 페이지 조회
      "page_param": "pageIndex",
      "page_start": 1,
      "params": {"searchCnd": "0"},      // 목록 URL에 붙일 쿼리스트링
      "data": {},                        // 값이 있으면 POST로 요청
      "headers": {"Referer": "https://..."},
      "encoding": "utf-8",               // EUC-KR 사이트는 "cp949"
      "skip_row_classes": ["notice"],    // 상단 고정 공지 제외
      "include_keywords": [], "exclude_keywords": []   // 사이트별 제목 필터
    }
  ]
}
"""

from __future__ import annotations

import json
import os
from pathlib import Path

REQUIRED_FIELDS = ("id", "name", "list_url")
DEFAULT_CONFIG_NAME = "sites.json"

BASE_DEFAULTS: dict[str, object] = {
    "enabled": True,
    "type": "html",
    "lookback_days": 1,
    "pages": 1,
    "page_param": "page",
    "page_start": 1,
    "timeout": 20,
    "retries": 3,
}


class ConfigError(RuntimeError):
    pass


def default_config_path() -> Path:
    """환경변수 SITES_CONFIG > 저장소 루트 sites.json 순으로 찾는다."""
    env = os.environ.get("SITES_CONFIG")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / DEFAULT_CONFIG_NAME


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path) if path else default_config_path()
    if not path.exists():
        raise ConfigError(f"설정 파일이 없습니다: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"설정 파일 JSON 문법 오류 ({path}): {e}") from e
    return normalize(raw, source=str(path))


def load_config_text(text: str, source: str = "<inline>") -> dict:
    """Lambda 환경변수 등으로 설정을 문자열로 받을 때 사용."""
    try:
        return normalize(json.loads(text), source=source)
    except json.JSONDecodeError as e:
        raise ConfigError(f"설정 JSON 문법 오류 ({source}): {e}") from e


def normalize(raw: dict | list, source: str = "") -> dict:
    if isinstance(raw, list):  # sites 배열만 준 경우도 받아준다
        raw = {"sites": raw}
    if not isinstance(raw, dict):
        raise ConfigError(f"설정 최상위는 객체 또는 배열이어야 합니다 ({source})")

    defaults = dict(BASE_DEFAULTS)
    defaults.update(raw.get("defaults") or {})
    global_include = list(raw.get("include_keywords") or [])
    global_exclude = list(raw.get("exclude_keywords") or [])

    sites: list[dict] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(raw.get("sites") or []):
        if not isinstance(entry, dict):
            raise ConfigError(f"sites[{index}]는 객체여야 합니다 ({source})")
        site = dict(defaults)
        site.update(entry)
        missing = [f for f in REQUIRED_FIELDS if not str(site.get(f) or "").strip()]
        if missing:
            raise ConfigError(
                f"sites[{index}]에 필수 항목이 없습니다: {', '.join(missing)} ({source})"
            )
        if site["id"] in seen_ids:
            raise ConfigError(f"사이트 id가 중복되었습니다: {site['id']} ({source})")
        seen_ids.add(site["id"])
        site["include_keywords"] = list(site.get("include_keywords") or global_include)
        site["exclude_keywords"] = list(site.get("exclude_keywords") or []) + global_exclude
        try:
            site["pages"] = max(1, int(site.get("pages", 1)))
            site["lookback_days"] = max(1, int(site.get("lookback_days", 1)))
        except (TypeError, ValueError) as e:
            raise ConfigError(f"sites[{index}] pages/lookback_days는 정수여야 합니다 ({source})") from e
        sites.append(site)

    return {
        "defaults": defaults,
        "sites": sites,
        "include_keywords": global_include,
        "exclude_keywords": global_exclude,
        "source": source,
    }


def enabled_sites(config: dict, only: list[str] | None = None) -> list[dict]:
    sites = [s for s in config["sites"] if s.get("enabled", True)]
    if only:
        wanted = set(only)
        unknown = wanted - {s["id"] for s in config["sites"]}
        if unknown:
            raise ConfigError(f"설정에 없는 사이트 id: {', '.join(sorted(unknown))}")
        sites = [s for s in config["sites"] if s["id"] in wanted]
    return sites
