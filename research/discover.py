#!/usr/bin/env python3
"""
sources.json에 적힌 수집 주소가 실제로 살아 있는지 점검하고, 깨진 소스는
RSS 피드를 자동으로 찾아 고쳐주는 도구.

국내 기관 사이트 대부분은 해외 IP를 차단하므로 **국내 PC 또는 서울 리전**에서
실행해야 한다.

사용법:
    python3 research/discover.py              # 점검만 (아무것도 바꾸지 않음)
    python3 research/discover.py --fix        # 찾아낸 RSS 주소로 sources.json 갱신
    python3 research/discover.py --only spri,kisdi
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import collector  # noqa: E402

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources.json")

# 국내 기관 CMS(나라장터 계열, 자체 개발 게시판)에서 흔히 쓰이는 RSS 경로들
COMMON_FEED_PATHS = [
    "/rss", "/rss.xml", "/rss/", "/feed", "/feed/", "/index.xml",
    "/rss/allArticle.xml", "/rss/board.xml", "/bbs/rss.do", "/rssList.do",
    "/kor/rss.do", "/board/rss", "/rss/news.xml", "/atom.xml",
]


class _FeedLinkFinder(HTMLParser):
    """<link rel="alternate" type="application/rss+xml" href="..."> 를 찾는다."""

    def __init__(self) -> None:
        super().__init__()
        self.feeds: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in ("link", "a"):
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        href = a.get("href", "").strip()
        if not href:
            return
        type_attr = a.get("type", "").lower()
        if "rss" in type_attr or "atom" in type_attr:
            self.feeds.append(href)
        elif tag == "a" and re.search(r"(rss|feed)(\.xml|/|$)", href, re.I):
            self.feeds.append(href)


def looks_like_feed(body: str) -> bool:
    head = body.lstrip()[:400].lower()
    return "<rss" in head or "<feed" in head or "<rdf" in head


def probe(source: dict, timeout: int) -> tuple[bool, int, str]:
    """현재 url을 실제로 긁어보고 (성공여부, 항목수, 메시지)를 돌려준다."""
    items, error = collector.collect_source(source, timeout=timeout)
    if error:
        return False, 0, error.split(": ", 1)[-1]
    if not items:
        return False, 0, "응답은 받았으나 항목을 하나도 뽑지 못함 (link_pattern 확인 필요)"
    return True, len(items), f"{len(items)}건 추출 (예: {items[0].title[:40]})"


def find_feeds(base_url: str, timeout: int) -> list[str]:
    """홈페이지의 <link rel=alternate>와 흔한 경로 후보에서 살아 있는 피드를 찾는다."""
    found: list[str] = []
    candidates: list[str] = []

    try:
        home = collector.fetch(base_url, timeout=timeout)
        finder = _FeedLinkFinder()
        finder.feed(home)
        candidates += [urljoin(base_url, h) for h in finder.feeds]
    except Exception:  # noqa: BLE001 - 홈페이지가 막혀도 경로 후보는 계속 시도한다
        pass

    root = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    candidates += [root + p for p in COMMON_FEED_PATHS]

    for url in dict.fromkeys(candidates):
        try:
            body = collector.fetch(url, timeout=timeout)
        except Exception:  # noqa: BLE001
            continue
        if not looks_like_feed(body):
            continue
        probe_source = {"id": "probe", "name": "probe", "type": "rss", "base_url": root}
        try:
            items = collector.parse_feed(body, probe_source)
        except Exception:  # noqa: BLE001
            continue
        if items:
            found.append(url)
        if len(found) >= 3:
            break
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="찾아낸 RSS 주소로 sources.json을 갱신한다")
    ap.add_argument("--only", default="", help="점검할 소스 id 목록 (콤마 구분)")
    ap.add_argument("--timeout", type=int, default=15)
    args = ap.parse_args()

    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    changed = False
    ok_count = 0
    checked = 0

    for source in config["sources"]:
        if only and source["id"] not in only:
            continue
        if not source.get("url"):
            print(f"[  건너뜀 ] {source['id']:<20} url이 비어 있음 ({source.get('notes','')})")
            continue

        checked += 1
        success, _, message = probe(source, args.timeout)
        if success:
            ok_count += 1
            print(f"[   정상  ] {source['id']:<20} {message}")
            if not source.get("verified"):
                source["verified"] = True
                changed = True
            continue

        print(f"[  실패   ] {source['id']:<20} {message}")
        if source.get("verified"):
            source["verified"] = False
            changed = True

        base = source.get("base_url") or source["url"]
        feeds = find_feeds(base, args.timeout)
        if not feeds:
            print(f"             └ 대체 RSS를 찾지 못함. 브라우저에서 목록 페이지를 열고 "
                  f"url/link_pattern을 직접 수정하세요.")
            continue

        print(f"             └ RSS 후보 발견: {', '.join(feeds)}")
        if args.fix:
            source["type"] = "rss"
            source["url"] = feeds[0]
            source.pop("link_pattern", None)
            source["verified"] = True
            changed = True
            print(f"             └ sources.json 갱신: type=rss, url={feeds[0]}")

    print(f"\n점검 {checked}건 중 정상 {ok_count}건")

    if changed and args.fix:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"sources.json 을 갱신했습니다: {CONFIG_PATH}")
    elif changed:
        print("(--fix 를 붙이면 위 내용을 sources.json에 반영합니다)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
