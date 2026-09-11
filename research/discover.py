#!/usr/bin/env python3
"""
sources.json의 수집 주소가 살아 있는지 점검하고, 깨진 소스는 **자동으로 고쳐주는** 도구.

기관 사이트가 개편되면 목록 페이지 주소나 상세보기 링크 형태가 바뀐다.
그때마다 사람이 HTML을 뒤지는 대신, 이 스크립트가 아래 순서로 스스로 찾는다.

  1) 현재 주소로 자료가 뽑히면   -> 그대로 통과
  2) RSS 피드가 있으면          -> RSS로 교체 (가장 안정적)
  3) 목록 페이지는 열리는데 링크 패턴이 안 맞으면
                               -> 페이지의 링크를 형태별로 묶어 '자료 목록'답게 생긴 것을 고름
  4) 주소 자체가 404면          -> 홈에서 '발간물/보고서/자료실' 류 메뉴를 따라가 3)을 반복

국내 기관 사이트는 해외 IP를 차단하므로 **국내 PC 또는 서울 리전**에서 실행해야 한다.

사용법:
    python3 research/discover.py                 # 점검·후보 제시만 (파일 수정 없음)
    python3 research/discover.py --fix           # 가장 점수 높은 후보로 sources.json 갱신
    python3 research/discover.py --only kisdi,nars --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import collector  # noqa: E402

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources.json")

COMMON_FEED_PATHS = [
    "/rss", "/rss.xml", "/rss/", "/feed", "/feed/", "/index.xml",
    "/rss/allArticle.xml", "/rss/board.xml", "/bbs/rss.do", "/rssList.do",
    "/kor/rss.do", "/board/rss", "/rss/news.xml", "/atom.xml",
]

# 홈에서 자료 목록 페이지로 가는 메뉴를 찾을 때 쓰는 단서
MENU_HINTS = [
    "발간물", "발간자료", "연구보고서", "보고서", "자료실", "간행물", "정기간행물",
    "보도자료", "연구자료", "출판물", "이슈페이퍼", "리포트", "브리프", "동향",
    "연구실적", "정책자료", "공지사항", "알림", "뉴스",
]

# 상세보기 링크에서 흔히 보이는 흔적
DETAIL_HINTS = re.compile(
    r"(view|detail|read|show|artid|nttid|bbsseq|boardseq|seq=|idx=|no=|id=|masterid)", re.I
)
# 자료 목록으로 오해하기 쉬운 링크
NOISE_HINTS = re.compile(
    r"(login|join|member|sitemap|privacy|search|download|file|popup|print|share|"
    r"facebook|twitter|instagram|youtube|blog\.naver|javascript)", re.I
)


class _AnchorParser(HTMLParser):
    """모든 <a>의 href와 텍스트, 그리고 뒤따르는 텍스트(날짜용)를 순서대로 모은다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.anchors: list[dict] = []
        self._stack: list[dict] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
            return
        if tag != "a":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        anchor = {
            "href": a.get("href", "").strip(),
            "title_attr": collector.normalize_space(a.get("title", "")),
            "chunk_index": len(self.chunks),
            "parts": [],
            "type_attr": a.get("type", "").lower(),
        }
        self._stack.append(anchor)
        self.anchors.append(anchor)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._stack:
            self._stack.pop()

    def handle_data(self, data):
        if self._skip:
            return
        text = collector.normalize_space(data)
        if not text:
            return
        self.chunks.append(text)
        for anchor in self._stack:
            anchor["parts"].append(text)

    def handle_startendtag(self, tag, attrs):
        if tag == "link":
            a = {k.lower(): (v or "") for k, v in attrs}
            if "rss" in a.get("type", "").lower() or "atom" in a.get("type", "").lower():
                self.anchors.append({
                    "href": a.get("href", "").strip(), "title_attr": "feed",
                    "chunk_index": len(self.chunks), "parts": ["feed"],
                    "type_attr": a.get("type", "").lower(),
                })


def parse_anchors(html: str) -> _AnchorParser:
    parser = _AnchorParser()
    parser.feed(html)
    return parser


def link_shape(href: str) -> str | None:
    """링크를 '형태'로 요약한다. 같은 게시판의 상세보기들은 같은 형태로 묶인다.

    /report/view.do?key=m21&artId=1779316  ->  report/view.do?artId,key
    """
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return None
    parsed = urlparse(href)
    segments = [s for s in parsed.path.split("/") if s]
    if not segments:
        return None
    # 마지막 두 세그먼트까지만 보면 게시판별 구분에 충분하다
    tail = "/".join(segments[-2:])
    keys = ",".join(sorted(parse_qs(parsed.query).keys()))
    return f"{tail}?{keys}" if keys else tail


def shape_to_pattern(shape: str) -> str:
    """형태 요약을 sources.json에 넣을 link_pattern 정규식으로 바꾼다."""
    path_part = shape.split("?")[0]
    return re.escape(path_part)


def score_group(anchors: list[dict], chunks: list[str]) -> tuple[int, list[str]]:
    """이 링크 묶음이 '자료 목록'일 가능성을 점수로 매긴다."""
    titles = []
    dated = 0
    for anchor in anchors:
        title = collector.normalize_space(" ".join(anchor["parts"])) or anchor["title_attr"]
        if len(title) >= 6:
            titles.append(title)
        start = anchor["chunk_index"]
        if collector.parse_date(" ".join(chunks[start:start + 6])):
            dated += 1

    count = len(anchors)
    if not titles:
        return -100, []

    score = 0
    # 게시판 한 페이지는 보통 10~30건이다. 2건 이하나 200건 이상은 목록이 아닐 가능성이 높다
    if 5 <= count <= 60:
        score += 30
    elif 3 <= count < 5:
        score += 12
    elif count > 100:
        score -= 25

    avg_len = sum(len(t) for t in titles) / len(titles)
    if avg_len >= 15:
        score += 25
    elif avg_len >= 10:
        score += 15
    elif avg_len < 6:
        score -= 20

    score += int(30 * dated / count)                    # 날짜가 붙어 있으면 게시판일 확률이 높다
    score += int(20 * len(titles) / count)              # 제목다운 텍스트 비율

    sample_href = anchors[0]["href"]
    if DETAIL_HINTS.search(sample_href):
        score += 20
    if NOISE_HINTS.search(sample_href):
        score -= 40
    # 제목이 죄다 같으면(더보기/다운로드 등) 목록이 아니다
    if len(set(titles)) <= max(1, len(titles) // 4):
        score -= 40

    return score, titles[:3]


def suggest_patterns(html: str, limit: int = 4) -> list[dict]:
    """목록 페이지 HTML에서 link_pattern 후보를 점수순으로 뽑는다."""
    parser = parse_anchors(html)
    groups: dict[str, list[dict]] = defaultdict(list)
    for anchor in parser.anchors:
        shape = link_shape(anchor["href"])
        if shape:
            groups[shape].append(anchor)

    results = []
    for shape, anchors in groups.items():
        score, samples = score_group(anchors, parser.chunks)
        if score <= 0:
            continue
        results.append({
            "pattern": shape_to_pattern(shape),
            "shape": shape,
            "count": len(anchors),
            "score": score,
            "samples": samples,
        })

    # 같은 정규식으로 수렴하는 후보는 하나로 합친다
    merged: dict[str, dict] = {}
    for r in sorted(results, key=lambda x: -x["score"]):
        merged.setdefault(r["pattern"], r)
    return sorted(merged.values(), key=lambda x: -x["score"])[:limit]


def find_list_pages(base_url: str, timeout: int, verbose: bool = False) -> list[tuple[str, str]]:
    """홈에서 '발간물/보고서/자료실' 류 메뉴를 찾아 (링크텍스트, URL) 목록으로 돌려준다."""
    try:
        home = collector.fetch(base_url, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        if verbose:
            print(f"             (홈 접속 실패: {e})")
        return []

    parser = parse_anchors(home)
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in parser.anchors:
        text = collector.normalize_space(" ".join(anchor["parts"])) or anchor["title_attr"]
        href = anchor["href"]
        if not href or not text or NOISE_HINTS.search(href):
            continue
        if not any(hint in text for hint in MENU_HINTS):
            continue
        url = urljoin(base_url, href)
        if url in seen or url.rstrip("/") == base_url.rstrip("/"):
            continue
        seen.add(url)
        found.append((text, url))
    return found[:12]


def looks_like_feed(body: str) -> bool:
    head = body.lstrip()[:400].lower()
    return "<rss" in head or "<feed" in head or "<rdf" in head


def find_feeds(base_url: str, timeout: int) -> list[str]:
    candidates: list[str] = []
    try:
        home = collector.fetch(base_url, timeout=timeout)
        for anchor in parse_anchors(home).anchors:
            href = anchor["href"]
            if not href:
                continue
            if "rss" in anchor["type_attr"] or "atom" in anchor["type_attr"]:
                candidates.append(urljoin(base_url, href))
            elif re.search(r"(rss|feed)(\.xml|/|$)", href, re.I):
                candidates.append(urljoin(base_url, href))
    except Exception:  # noqa: BLE001
        pass

    root = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    candidates += [root + p for p in COMMON_FEED_PATHS]

    found: list[str] = []
    for url in dict.fromkeys(candidates):
        try:
            body = collector.fetch(url, timeout=timeout)
        except Exception:  # noqa: BLE001
            continue
        if not looks_like_feed(body):
            continue
        try:
            if collector.parse_feed(body, {"id": "p", "name": "p", "base_url": root}):
                found.append(url)
        except Exception:  # noqa: BLE001
            continue
        if len(found) >= 3:
            break
    return found


def find_feed_in_index(index_url: str, match: str, timeout: int) -> str:
    """RSS 안내 페이지(예: 정책브리핑 RSS 목록)에서 기관명에 해당하는 .xml 주소를 찾는다."""
    try:
        html = collector.fetch(index_url, timeout=timeout)
    except Exception:  # noqa: BLE001
        return ""
    parser = parse_anchors(html)
    xml_links = [(i, a) for i, a in enumerate(parser.anchors)
                 if a["href"].lower().endswith(".xml")]
    for _, anchor in xml_links:
        text = collector.normalize_space(" ".join(anchor["parts"])) or anchor["title_attr"]
        if match in text:
            return urljoin(index_url, anchor["href"])
    # 링크 텍스트가 '바로가기' 같은 경우: 앵커 앞쪽 텍스트에서 기관명을 찾는다
    for _, anchor in xml_links:
        start = max(0, anchor["chunk_index"] - 4)
        context = " ".join(parser.chunks[start:anchor["chunk_index"] + 1])
        if match in context:
            return urljoin(index_url, anchor["href"])
    return ""


def probe(source: dict, timeout: int) -> tuple[bool, str]:
    items, error = collector.collect_source(source, timeout=timeout)
    if error:
        return False, error.split(": ", 1)[-1]
    if not items:
        return False, "응답은 받았으나 항목을 하나도 뽑지 못함"
    return True, f"{len(items)}건 추출 (예: {items[0].title[:42]})"


def try_patterns_on(url: str, source: dict, timeout: int, verbose: bool) -> list[dict]:
    """주어진 목록 페이지에서 link_pattern 후보를 뽑아 실제 추출 결과까지 확인한다."""
    try:
        html = collector.fetch(url, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        if verbose:
            print(f"             ({url} 접속 실패: {e})")
        return []
    if looks_like_feed(html):
        return [{"pattern": None, "url": url, "score": 999, "count": 0,
                 "samples": ["(RSS 피드)"], "type": "rss"}]

    candidates = suggest_patterns(html)
    verified = []
    for cand in candidates:
        trial = dict(source, url=url, type="html", link_pattern=cand["pattern"])
        items = collector.parse_html_list(html, trial)
        keep = [i for i in items if collector.matches_keywords(i, collector.DEFAULT_KEYWORDS)]
        cand.update({
            "url": url,
            "type": "html",
            "extracted": len(items),
            "ai_hits": len(keep),
            "samples": [i.title[:42] for i in items[:3]] or cand["samples"],
        })
        if items:
            verified.append(cand)
    return verified


def repair(source: dict, timeout: int, verbose: bool) -> list[dict]:
    """실패한 소스를 고칠 후보들을 점수순으로 찾아낸다."""
    base = source.get("base_url") or source["url"]
    root = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
    candidates: list[dict] = []

    # 0) RSS 안내 페이지가 지정된 소스 (정책브리핑 등)
    if source.get("rss_index") and source.get("rss_match"):
        index_urls = source["rss_index"]
        if isinstance(index_urls, str):
            index_urls = [index_urls]
        for index_url in index_urls:
            feed = find_feed_in_index(index_url, source["rss_match"], timeout)
            if feed:
                candidates.append({"pattern": None, "url": feed, "type": "rss",
                                   "score": 1000, "samples": ["(RSS 안내 페이지에서 확인)"]})
                break

    # 1) RSS 우선
    for feed in find_feeds(base, timeout):
        candidates.append({"pattern": None, "url": feed, "type": "rss",
                           "score": 900, "samples": ["(RSS 피드)"]})

    # 2) 현재 주소가 열리면 그 페이지에서 패턴 추론
    candidates += try_patterns_on(source["url"], source, timeout, verbose)

    # 3) 홈에서 자료실 메뉴를 따라가 본다
    if not [c for c in candidates if c["score"] > 60]:
        for text, url in find_list_pages(root, timeout, verbose):
            if verbose:
                print(f"             (메뉴 후보: {text} -> {url})")
            for cand in try_patterns_on(url, source, timeout, verbose):
                cand["menu"] = text
                # 메뉴 이름이 자료다울수록 가산점
                if any(h in text for h in ("발간", "보고서", "간행물", "자료", "리포트", "브리프")):
                    cand["score"] += 15
                candidates.append(cand)

    return sorted(candidates, key=lambda c: -c["score"])[:5]


def apply_candidate(source: dict, cand: dict) -> None:
    source["url"] = cand["url"]
    source["type"] = cand.get("type", "html")
    if source["type"] == "rss":
        source.pop("link_pattern", None)
    else:
        source["link_pattern"] = cand["pattern"]
    source["verified"] = True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="가장 점수 높은 후보로 sources.json을 갱신한다")
    ap.add_argument("--only", default="", help="점검할 소스 id (콤마 구분)")
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--verbose", action="store_true", help="탐색 과정을 자세히 출력한다")
    args = ap.parse_args()

    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    changed = False
    ok, fixed, failed = [], [], []

    for source in config["sources"]:
        if only and source["id"] not in only:
            continue
        if not source.get("url") and not source.get("rss_index"):
            print(f"[ 건너뜀 ] {source['id']:<20} url 없음 ({source.get('notes', '')[:50]})")
            continue

        if source.get("url"):
            success, message = probe(source, args.timeout)
            if success:
                ok.append(source["id"])
                print(f"[  정상  ] {source['id']:<20} {message}")
                if not source.get("verified"):
                    source["verified"] = True
                    changed = True
                continue
            print(f"[  실패  ] {source['id']:<20} {message}")
        else:
            print(f"[  탐색  ] {source['id']:<20} 주소 없음 — 자동 탐색 시도")

        if source.get("verified"):
            source["verified"] = False
            changed = True

        candidates = repair(source, args.timeout, args.verbose)
        if not candidates:
            failed.append(source["id"])
            print("            └ 후보를 찾지 못했습니다. 브라우저에서 목록 페이지를 열고 "
                  "url/link_pattern을 직접 넣어주세요.")
            continue

        for rank, cand in enumerate(candidates):
            mark = "★" if rank == 0 else " "
            kind = "RSS" if cand.get("type") == "rss" else f"패턴 {cand['pattern']}"
            menu = f" [{cand['menu']}]" if cand.get("menu") else ""
            print(f"            {mark} {kind}{menu}")
            print(f"              {cand['url']}")
            for sample in cand.get("samples", [])[:2]:
                print(f"              · {sample}")

        if args.fix:
            apply_candidate(source, candidates[0])
            changed = True
            fixed.append(source["id"])
            success, message = probe(source, args.timeout)
            print(f"            └ 적용 후 재확인: {'성공 — ' + message if success else '실패 — ' + message}")
            if not success:
                source["verified"] = False

    print(f"\n정상 {len(ok)}건 / 자동수정 {len(fixed)}건 / 미해결 {len(failed)}건")
    if failed:
        print(f"미해결: {', '.join(failed)}")

    if changed and args.fix:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"sources.json 갱신 완료")
    elif changed:
        print("(--fix 를 붙이면 ★ 후보를 sources.json에 반영합니다)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
