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
    python3 research/discover.py --add "<목록 URL>"   # 새 게시판 URL 하나를 설정으로 만들어 준다
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
from concurrent.futures import ThreadPoolExecutor
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
            # onclick으로만 상세를 여는 게시판이 많아, 무엇을 물어봐야 할지
            # 알려주려면 이 속성이 필요하다 (collector도 같은 값을 쓴다)
            "onclick": a.get("onclick", "").strip(),
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


def link_shape(href: str, base_netloc: str = "") -> str | None:
    """링크를 '형태'로 요약한다. 같은 게시판의 상세보기들은 같은 형태로 묶인다.

    /report/view.do?key=m21&artId=1779316  ->  report/view.do?artId,key
    /posts/view/24024                      ->  posts/view/<num>

    글 번호가 경로에 박히는 사이트(SPRi, 법제연구원 등)에서 같은 게시판이
    글마다 다른 형태로 쪼개지지 않도록 숫자 세그먼트를 <num>으로 묶는다.
    """
    if not href or href.startswith(("javascript:", "mailto:", "tel:")):
        return None
    parsed = urlparse(href)
    # 외부 사이트로 나가는 링크(광고·제휴 배너)는 자료 목록이 아니다
    if parsed.netloc and base_netloc and parsed.netloc.lstrip("www.") != base_netloc.lstrip("www."):
        return None
    segments = [s for s in parsed.path.split("/") if s]
    if not segments:
        return None
    normalized = []
    for seg in segments[-3:]:
        if re.fullmatch(r"\d+", seg):
            normalized.append("<num>")
        elif re.fullmatch(r"[0-9A-Fa-f]{16,}", seg):
            normalized.append("<hash>")
        else:
            normalized.append(seg)
    tail = "/".join(normalized)
    keys = ",".join(sorted(parse_qs(parsed.query).keys()))
    return f"{tail}?{keys}" if keys else tail


def shape_to_pattern(shape: str) -> str:
    """형태 요약을 sources.json에 넣을 link_pattern 정규식으로 바꾼다."""
    path_part = shape.split("?")[0]
    parts = []
    for seg in path_part.split("/"):
        if seg == "<num>":
            parts.append(r"\d+")
        elif seg == "<hash>":
            parts.append(r"[0-9A-Fa-f]+")
        else:
            parts.append(re.escape(seg))
    return "/".join(parts)


def passes_gate(items: list, strict: bool = True) -> tuple[bool, str]:
    """추출 결과가 '진짜 자료 목록'인지 판정한다.

    메뉴·배너·푸터 링크를 자료로 오인하면 알림 메일이 쓰레기로 채워진다.
    자동 탐색에서 가장 확실한 구분선은 **날짜**다. 게시판 목록에는 게시일이
    붙고 메뉴에는 없다.

    다만 날짜와 제목 길이는 '대개 그렇다'는 추측이라, 사람이 URL을 직접 지정한
    경우에는 오히려 방해가 된다. 실제로 목록에 날짜를 안 쓰는 진짜 자료 게시판이
    탈락하고, 날짜가 붙은 채용·입찰 공고 게시판이 뽑히는 역전이 일어났다
    (SPRi 연구자료 vs 공지사항). 제목이 "2026년09월호"처럼 짧은 간행물 목록도
    길이 규칙에 걸린다. 그래서 strict=False면 이 두 추측을 끈다.

    건수와 중복 제목 검사는 strict와 무관하게 늘 적용한다. 메뉴·더보기 링크를
    걸러내는 건 이 둘이고, 이건 추측이 아니라 확실한 신호이기 때문이다.
    """
    if len(items) < 5:
        return False, f"항목 {len(items)}건뿐 (게시판 한 페이지로 보기엔 너무 적음)"

    dated = sum(1 for i in items if i.published)
    if strict and dated / len(items) < 0.6:
        return False, f"날짜가 붙은 항목이 {dated}/{len(items)}건뿐 (메뉴 링크로 보임)"

    titles = [i.title for i in items]
    avg_len = sum(len(t) for t in titles) / len(titles)
    if strict and avg_len < 12:
        return False, f"제목 평균 {avg_len:.0f}자로 너무 짧음 (메뉴 링크로 보임)"

    if len(set(titles)) / len(titles) < 0.8:
        return False, "같은 제목이 반복됨 (더보기·다운로드 링크로 보임)"

    return True, ""


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


def suggest_patterns(html: str, limit: int = 4, base_netloc: str = "") -> list[dict]:
    """목록 페이지 HTML에서 link_pattern 후보를 점수순으로 뽑는다."""
    parser = parse_anchors(html)
    groups: dict[str, list[dict]] = defaultdict(list)
    for anchor in parser.anchors:
        shape = link_shape(anchor["href"], base_netloc)
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
        url = clean_url(urljoin(base_url, href))
        # 제휴·광고 배너로 외부 사이트에 나가면 그 사이트의 글이 자료로 잡힌다
        if urlparse(url).netloc.lstrip("www.") != urlparse(base_url).netloc.lstrip("www."):
            continue
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


def probe(source: dict, timeout: int, strict: bool = True) -> tuple[bool, str]:
    """현재 설정으로 실제 수집해 보고, 게이트까지 통과하는지 확인한다."""
    items, error = collector.collect_source(source, timeout=timeout)
    if error:
        return False, error.split(": ", 1)[-1]
    if not items:
        return False, "응답은 받았으나 항목을 하나도 뽑지 못함"
    if source.get("type") != "rss":
        passed, reason = passes_gate(items, strict=strict)
        if not passed:
            return False, reason
    dated = sum(1 for i in items if i.published)
    return True, f"{len(items)}건 추출 (날짜 {dated}건, 예: {items[0].title[:38]})"


def clean_url(url: str) -> str:
    """#앵커만 다른 주소는 같은 페이지다. 조각(fragment)을 떼어낸다."""
    parsed = urlparse(url)
    rebuilt = parsed._replace(fragment="")
    return rebuilt.geturl()


def try_patterns_on(url: str, source: dict, timeout: int, verbose: bool) -> list[dict]:
    """주어진 목록 페이지에서 link_pattern 후보를 뽑고, 게이트를 통과한 것만 돌려준다."""
    url = clean_url(url)
    try:
        html = collector.fetch(url, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        if verbose:
            print(f"              ({url} 접속 실패: {e})")
        return []
    if looks_like_feed(html):
        return [{"pattern": None, "url": url, "score": 999, "count": 0,
                 "samples": ["(RSS 피드)"], "type": "rss"}]

    base_netloc = urlparse(source.get("base_url") or url).netloc
    verified = []
    for cand in suggest_patterns(html, base_netloc=base_netloc):
        trial = dict(source, url=url, type="html", link_pattern=cand["pattern"])
        items = collector.parse_html_list(html, trial)
        passed, reason = passes_gate(items)
        if not passed:
            if verbose:
                print(f"              (기각: {cand['pattern']} — {reason})")
            continue
        keep = [i for i in items if collector.matches_keywords(i, collector.DEFAULT_KEYWORDS)]
        cand.update({
            "url": url,
            "type": "html",
            "extracted": len(items),
            "ai_hits": len(keep),
            "samples": [f"{i.title[:40]} ({i.published or '날짜없음'})" for i in items[:3]],
        })
        # 날짜가 잘 붙고 건수가 많을수록 확실한 게시판이다
        dated = sum(1 for i in items if i.published)
        cand["score"] += int(40 * dated / len(items)) + min(len(items), 20)
        # 글 상세 페이지에도 '관련 자료' 목록이 붙어 있어 목록처럼 보인다.
        # 목록 페이지를 놔두고 상세 페이지를 수집원으로 삼으면 그 글이 지워질 때 깨진다.
        if re.search(r"(/view/|view\.do|/detail|selectBoardArticle|mode=view)", url, re.I):
            cand["score"] -= 45
        verified.append(cand)
    return sorted(verified, key=lambda c: -c["score"])


def repair(source: dict, timeout: int, verbose: bool) -> list[dict]:
    """실패한 소스를 고칠 후보들을 점수순으로 찾아낸다.

    홈 -> 자료실 메뉴 -> (필요하면) 그 안의 하위 목록까지 2단계로 따라간다.
    게시판을 '발간물' 같은 중간 페이지 뒤에 숨겨둔 사이트가 많기 때문이다.
    """
    base = source.get("base_url") or source["url"]
    root = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
    candidates: list[dict] = []

    # 0) RSS 안내 페이지가 지정된 소스
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

    # 1) RSS 우선 — 가장 안정적이다
    for feed in find_feeds(base, timeout):
        candidates.append({"pattern": None, "url": feed, "type": "rss",
                           "score": 900, "samples": ["(RSS 피드)"]})

    # 2) 현재 주소가 열리면 그 페이지에서 패턴 추론
    candidates += try_patterns_on(source["url"], source, timeout, verbose) if source.get("url") else []

    def best_score() -> int:
        return max((c["score"] for c in candidates), default=0)

    # 3) 홈에서 자료실 메뉴를 따라간다
    if best_score() < 100:
        menus = find_list_pages(root, timeout, verbose)
        if verbose:
            print(f"              (1단계 메뉴 {len(menus)}개 탐색)")
        found = scan_pages(menus, source, timeout, verbose)
        candidates += found

        # 4) 그래도 없으면 메뉴 페이지 안쪽을 한 번 더 들어간다
        if not found and menus:
            deeper: list[tuple[str, str]] = []
            for text, url in menus[:6]:
                for sub_text, sub_url in find_list_pages(clean_url(url), timeout, False):
                    if sub_url not in {u for _, u in menus}:
                        deeper.append((f"{text}>{sub_text}", sub_url))
            if verbose:
                print(f"              (2단계 메뉴 {len(deeper)}개 탐색)")
            candidates += scan_pages(deeper[:16], source, timeout, verbose)

    return sorted(candidates, key=lambda c: -c["score"])[:5]


def scan_pages(pages: list[tuple[str, str]], source: dict, timeout: int,
               verbose: bool) -> list[dict]:
    """여러 목록 페이지 후보를 병렬로 훑어 게이트를 통과한 것만 모은다."""
    if not pages:
        return []

    def scan(entry: tuple[str, str]) -> list[dict]:
        text, url = entry
        found = try_patterns_on(url, source, timeout, verbose)
        for cand in found:
            cand["menu"] = text
            if any(h in text for h in ("발간", "보고서", "간행물", "자료", "리포트", "브리프", "연구")):
                cand["score"] += 15
            if "보도자료" in text:
                cand["score"] += 10
        return found

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(6, len(pages))) as pool:
        for found in pool.map(scan, pages):
            results += found
    return results


def apply_candidate(source: dict, cand: dict) -> None:
    source["url"] = cand["url"]
    source["type"] = cand.get("type", "html")
    if source["type"] == "rss":
        source.pop("link_pattern", None)
    else:
        source["link_pattern"] = cand["pattern"]
    source["verified"] = True


def inspect(source: dict, timeout: int) -> None:
    """자동 탐색이 실패한 소스의 페이지 구조를 그대로 보여준다.

    이 출력만 있으면 사이트를 직접 열어보지 않고도 link_pattern이나
    detail_url_template을 정할 수 있다.
    """
    url = source.get("url") or source.get("base_url", "")
    print(f"\n===== {source['id']} 진단: {url} =====")
    try:
        html = collector.fetch(url, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        print(f"접속 실패: {type(e).__name__} {e}")
        return

    print(f"HTML 길이: {len(html):,}자 / <table> {html.lower().count('<table')}개 "
          f"/ <iframe> {html.lower().count('<iframe')}개")

    parser = parse_anchors(html)
    with_href = [a for a in parser.anchors
                 if a["href"] and not a["href"].startswith(("#", "javascript:"))]
    onclick_only = [a for a in parser.anchors
                    if a.get("onclick") and (not a["href"] or a["href"].startswith(("#", "javascript:")))]
    print(f"링크 {len(parser.anchors)}개 = 주소 있음 {len(with_href)}개 / "
          f"onclick 방식 {len(onclick_only)}개")

    if onclick_only:
        print("\n[onclick 방식 링크 샘플]  <- detail_url_template 이 필요한 게시판입니다")
        for anchor in onclick_only[:4]:
            title = collector.normalize_space(" ".join(anchor["parts"]))[:45]
            print(f"  onclick: {anchor['onclick'][:95]}")
            print(f"  제목   : {title}")

    base_netloc = urlparse(source.get("base_url") or url).netloc
    groups: dict[str, list[dict]] = defaultdict(list)
    for anchor in with_href:
        shape = link_shape(anchor["href"], base_netloc)
        if shape:
            groups[shape].append(anchor)

    print("\n[링크 형태별 분포 (많은 순 10개)]")
    for shape, anchors in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:10]:
        titles = [collector.normalize_space(" ".join(a["parts"])) for a in anchors]
        titles = [t for t in titles if t]
        dated = sum(1 for a in anchors
                    if collector.parse_date(" ".join(parser.chunks[a["chunk_index"]:a["chunk_index"] + 6])))
        print(f"  {len(anchors):>3}건 날짜{dated:>3}건  {shape}")
        for title in titles[:2]:
            print(f"        · {title[:52]}")


DEFAULT_CATEGORY = "연구기관"
KNOWN_CATEGORIES = ("연구기관", "정부·부처", "해외 규제", "학술논문")


def slug_from_url(url: str) -> str:
    """주소에서 기본 id를 만든다. www/go/or/re/kr 같은 공통 조각은 뺀다."""
    host = urlparse(url).netloc.lower()
    parts = [p for p in host.split(".") if p not in ("www", "go", "or", "re", "kr", "com", "net", "co")]
    return (parts[0] if parts else "source").replace("-", "_")


def onclick_report(html: str) -> list[tuple[str, int, list[str]]]:
    """href 대신 onclick으로 상세 페이지를 여는 앵커를 함수별로 묶어 돌려준다.

    국내 기관 게시판은 href="#none" onclick="goView('115116','')" 형태가
    사실상 표준이다. 이런 페이지는 href만 보는 패턴 탐색으로는 후보가 아예
    잡히지 않아 '모든 후보 탈락'으로 끝난다. 무엇을 확인해야 하는지
    알려주려면 함수 이름과 실제 인자를 보여줘야 한다.
    """
    parser = parse_anchors(html)
    groups: dict[str, list[list[str]]] = defaultdict(list)
    for anchor in parser.anchors:
        href = (anchor.get("href") or "").strip()
        if href and not href.startswith(("#", "javascript:")):
            continue  # 진짜 주소가 있는 링크는 기존 경로가 처리한다
        source = (anchor.get("onclick") or "") or href
        func = collector.onclick_func(source)
        args = collector.onclick_args(source)
        if not func or not args:
            continue
        groups[func].append(args)
    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    return [(fn, len(calls), calls[0]) for fn, calls in ranked]


def pattern_from_template(template: str) -> str:
    """detail_url_template에서 link_pattern을 뽑는다 (view.do 등 경로 끝부분)."""
    path = urlparse(template.split("{")[0]).path
    tail = path.rsplit("/", 1)[-1] or path
    return re.escape(tail) if tail else ""


def add_source(url: str, *, source_id: str, name: str, category: str,
               timeout: int, keyword_filter: bool,
               detail_url_template: str = "", relaxed: bool = False,
               link_pattern: str = "", onclick_function: str = "") -> dict | None:
    """목록 URL 하나로 sources.json에 넣을 설정을 만들어 돌려준다.

    수집까지 실제로 해 보고 게이트를 통과한 설정만 내놓는다. 후보를 그냥
    출력하기만 하면 '되는 줄 알았는데 조용히 0건'이 되풀이되기 때문이다.
    """
    url = clean_url(url)
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    source_id = source_id or slug_from_url(url)
    base = {
        "id": source_id,
        "name": name or source_id,
        "category": category,
        "base_url": base_url,
        "keyword_filter": keyword_filter,
        "enabled": True,
    }

    print(f"[분석] {url}")

    # 1) RSS/Atom이면 link_pattern 자체가 필요 없다 — 가장 안정적이므로 먼저 본다.
    try:
        body = collector.fetch(url, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        print(f"  수집 실패: {type(e).__name__} {e}")
        print("  - 해외 IP를 차단하는 기관일 수 있습니다(국내 PC에서 실행해 보세요).")
        return None
    print(f"  수신 {len(body):,}자")

    if looks_like_feed(body):
        candidate = {**base, "type": "rss", "url": url}
        ok, message = probe(candidate, timeout, strict=not relaxed)
        print(f"  RSS 피드로 인식 -> {'정상' if ok else '실패'}: {message}")
        if ok:
            candidate["verified"] = True
            return candidate

    # 2) onclick 방식 게시판은 템플릿을 받아야 링크를 만들 수 있다.
    if detail_url_template:
        candidate = {
            **base, "type": "html", "url": url,
            "detail_url_template": detail_url_template,
            "link_pattern": pattern_from_template(detail_url_template),
        }
        # 목록에는 정렬·페이지 이동 링크도 onclick으로 섞여 있다. 가장 많이 나온
        # 함수만 상세보기로 보는데, 검색 조건 설정처럼 더 많이 나오는 함수가 있는
        # 목록도 있어(국회입법조사처는 setCmsCode가 15개, 실제 view는 10개)
        # --onclick-function 으로 직접 지정할 수 있게 둔다.
        report = onclick_report(body)
        if onclick_function:
            candidate["onclick_function"] = onclick_function
            found = next((c for f, c, _ in report if f == onclick_function), 0)
            print(f"  상세보기 함수(지정): {onclick_function}() {found}개")
        elif report:
            candidate["onclick_function"] = report[0][0]
            print(f"  상세보기 함수: {report[0][0]}() {report[0][1]}개"
                  + (f" / 무시: {', '.join(f + '()' for f, _, _ in report[1:4])}" if len(report) > 1 else ""))
        if report and not onclick_function and len(report) > 1:
            print(f"  (다른 함수를 쓰려면 --onclick-function "
                  f"{'|'.join(f for f, _, _ in report[:4])})")
        ok, message = probe(candidate, timeout, strict=not relaxed)
        print(f"  detail_url_template 적용 -> {'정상' if ok else '실패'}: {message}")
        if ok:
            candidate["verified"] = True
            return candidate
        print("  - 템플릿의 자리표시자가 맞는지 확인하세요 (첫 인자가 {0}, 둘째가 {1}).")
        return None

    # 3) link_pattern을 직접 받은 경우. 자동 제안이 놓치는 목록이 있어 탈출구를 둔다.
    #    직접 지정해도 실제 수집 검증은 똑같이 거친다.
    if link_pattern:
        candidate = {**base, "type": "html", "url": url, "link_pattern": link_pattern}
        ok, message = probe(candidate, timeout, strict=not relaxed)
        print(f"  지정한 link_pattern={link_pattern!r} -> {'정상' if ok else '실패'}: {message}")
        if ok:
            candidate["verified"] = True
            return candidate
        return None

    # 4) HTML 목록이면 링크를 형태별로 묶어 '자료 목록'답게 생긴 후보부터 실제로 돌려본다.
    suggestions = suggest_patterns(body, base_netloc=parsed.netloc)
    if not suggestions:
        print("  링크 패턴 후보를 찾지 못했습니다.")
        print("  - 목록을 자바스크립트로 그리는 게시판일 수 있습니다. 해당 기관 RSS가 있으면 그 주소를 주세요.")
        return None

    for rank, cand in enumerate(suggestions, 1):
        candidate = {**base, "type": "html", "url": url, "link_pattern": cand["pattern"]}
        ok, message = probe(candidate, timeout, strict=not relaxed)
        mark = "정상" if ok else "탈락"
        cand["_why"] = message
        print(f"  후보{rank} link_pattern={cand['pattern']!r} (링크 {cand['count']}개) -> {mark}: {message}")
        if ok:
            candidate["verified"] = True
            return candidate

    print("  모든 후보가 검증을 통과하지 못했습니다.")
    if not relaxed and any(
        "날짜가 붙은 항목이" in c.get("_why", "") or "제목 평균" in c.get("_why", "")
        for c in suggestions
    ):
        print()
        print("  자동탐색용 추측(등록일 표기·제목 길이)에만 걸렸습니다.")
        print("  이 주소가 맞다면 --relaxed 를 붙여 다시 실행하세요.")
        print("  (건수·중복 제목 검사는 그대로 적용되므로 메뉴 링크는 계속 걸러집니다.)")
        return None
    _print_onclick_hint(body, url)
    return None


def _print_onclick_hint(body: str, url: str) -> None:
    """onclick 방식 게시판이면 무엇을 확인하면 되는지 구체적으로 알려준다."""
    report = onclick_report(body)
    if not report:
        print("  - 목록을 자바스크립트로 그리는 게시판일 수 있습니다. 기관 RSS 주소가 있으면 그것을 주세요.")
        return
    func, count, sample = report[0]
    args = ", ".join(f"{{{i}}}={v}" for i, v in enumerate(sample))
    print()
    print(f"  이 게시판은 주소 대신 자바스크립트로 상세 페이지를 엽니다: {func}(...) 형태 {count}개")
    print(f"  첫 글의 인자: {args}")
    print("  브라우저에서 목록의 글 하나를 클릭해 주소창 주소를 확인한 뒤, 그 인자가 들어가는")
    print("  자리를 {0}으로 바꿔 아래처럼 다시 실행하세요.")
    print(f'    python3 research/discover.py --add "{url}" \\')
    print('        --detail-url-template "https://.../view.do?id={0}"')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="가장 점수 높은 후보로 sources.json을 갱신한다")
    ap.add_argument("--only", default="", help="점검할 소스 id (콤마 구분)")
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--verbose", action="store_true", help="탐색 과정을 자세히 출력한다")
    ap.add_argument("--inspect", default="",
                    help="해당 소스 페이지의 링크 구조를 그대로 출력한다 (id 콤마 구분, 'all' 가능)")
    ap.add_argument("--add", default="", metavar="URL",
                    help="새 게시판 목록 URL로 sources.json 설정을 만들어 출력한다")
    ap.add_argument("--id", default="", help="--add와 함께: 소스 id (기본: 주소에서 자동 생성)")
    ap.add_argument("--name", default="", help="--add와 함께: 메일에 표시될 이름")
    ap.add_argument("--category", default=DEFAULT_CATEGORY,
                    help=f"--add와 함께: 분류 {KNOWN_CATEGORIES}")
    ap.add_argument("--no-keyword-filter", action="store_true",
                    help="--add와 함께: AI 키워드 필터 없이 목록 전체를 수집한다")
    ap.add_argument("--detail-url-template", default="", metavar="URL",
                    help="--add와 함께: onclick 방식 게시판의 상세 주소 형식 (인자 자리는 {0}, {1})")
    ap.add_argument("--link-pattern", default="", metavar="REGEX",
                    help="--add와 함께: 링크 패턴을 직접 지정 (자동 제안이 놓칠 때)")
    ap.add_argument("--onclick-function", default="", metavar="NAME",
                    help="--add와 함께: 상세보기 함수를 직접 지정 (자동 감지가 틀릴 때)")
    ap.add_argument("--relaxed", action="store_true",
                    help="--add와 함께: 자동탐색용 추측(등록일 표기·제목 길이)을 끈다")
    ap.add_argument("--save", action="store_true",
                    help="--add와 함께: 검증에 성공하면 sources.json에 바로 추가한다")
    args = ap.parse_args()

    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)

    if args.add:
        entry = add_source(
            args.add,
            source_id=args.id,
            name=args.name,
            category=args.category,
            timeout=args.timeout,
            keyword_filter=not args.no_keyword_filter,
            detail_url_template=args.detail_url_template,
            relaxed=args.relaxed,
            link_pattern=args.link_pattern,
            onclick_function=args.onclick_function,
        )
        if entry is None:
            return 1
        print("\n[sources.json의 sources 배열에 넣을 설정]")
        print(json.dumps(entry, ensure_ascii=False, indent=2))
        if not args.save:
            print("\n--save를 붙이면 sources.json에 바로 추가합니다.")
            return 0
        if any(s["id"] == entry["id"] for s in config["sources"]):
            print(f"\nid '{entry['id']}'가 이미 있습니다. --id로 다른 이름을 지정하세요.")
            return 1
        config["sources"].append(entry)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"\nsources.json에 추가했습니다: {entry['id']}")
        return 0

    if args.inspect:
        targets = {s.strip() for s in args.inspect.split(",") if s.strip()}
        for source in config["sources"]:
            if "all" in targets and not source.get("enabled", True):
                inspect(source, args.timeout)
            elif source["id"] in targets:
                inspect(source, args.timeout)
        return 0

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
            print("            └ 쓸 만한 자료 목록을 찾지 못했습니다.")
            if args.fix and source.get("enabled", True):
                source["enabled"] = False
                source["notes"] = ("자동 탐색 실패. 목록 페이지 주소와 link_pattern을 "
                                   "직접 넣은 뒤 enabled를 true로 되돌리세요.")
                changed = True
                print("            └ 잘못된 자료가 섞이지 않도록 이 소스를 비활성화했습니다.")
            continue

        for rank, cand in enumerate(candidates):
            mark = "★" if rank == 0 else " "
            kind = "RSS" if cand.get("type") == "rss" else f"패턴 {cand['pattern']}"
            menu = f" [{cand['menu']}]" if cand.get("menu") else ""
            hits = f" / AI 관련 {cand['ai_hits']}건" if cand.get("ai_hits") is not None else ""
            print(f"            {mark} {kind}{menu}{hits}")
            print(f"              {cand['url']}")
            for sample in cand.get("samples", [])[:2]:
                print(f"              · {sample}")

        if args.fix:
            apply_candidate(source, candidates[0])
            changed = True
            success, message = probe(source, args.timeout)
            if success:
                fixed.append(source["id"])
                source["enabled"] = True
                source.pop("notes", None)
                print(f"            └ 적용 후 재확인: 성공 — {message}")
            else:
                source["verified"] = False
                source["enabled"] = False
                source["notes"] = f"자동 수정 후에도 검증 실패: {message}"
                failed.append(source["id"])
                print(f"            └ 적용 후 재확인: 실패 — {message} (비활성화)")

    disabled = [src["id"] for src in config["sources"] if not src.get("enabled", True)
                and src["id"] != "kci"]
    print(f"\n정상 {len(ok)}건 / 자동수정 {len(fixed)}건 / 미해결 {len(failed)}건")
    if fixed:
        print(f"수정됨: {', '.join(fixed)}")
    if disabled:
        print(f"비활성화(수동 확인 필요): {', '.join(disabled)}")
        print("  -> 잘못된 자료가 알림에 섞이는 것보다 빠지는 편이 낫다고 보고 꺼두었습니다.")

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
