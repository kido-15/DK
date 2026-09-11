"""
국내외 기관 사이트에서 AI 관련 신규 공개자료를 수집하는 엔진.

설계 원칙
  - 표준 라이브러리만 사용한다. AWS Lambda 기본 런타임(python3.12)에
    추가 패키지 설치 없이 zip 하나로 배포하기 위해서다.
  - 사이트별 파서를 코드에 하드코딩하지 않고 sources.json 설정으로 기술한다.
    국내 기관 게시판은 개편이 잦아, 링크 패턴만 바꿔 대응할 수 있어야 한다.
  - RSS/Atom이 있으면 RSS를 쓰고, 없으면 목록 페이지의 <a> 링크를 훑는다.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

KST = timezone(timedelta(hours=9))

# 제목에 이 중 하나라도 있으면 "AI 관련"으로 본다.
# keyword_filter=false인 소스(AI 전문 보고서 등)에는 적용하지 않는다.
DEFAULT_KEYWORDS = [
    "인공지능", "AI", "A.I.", "생성형", "생성 AI", "초거대", "거대언어",
    "LLM", "머신러닝", "기계학습", "딥러닝", "심층학습", "알고리즘",
    "챗GPT", "ChatGPT", "파운데이션 모델", "foundation model",
    "AI기본법", "인공지능기본법", "AI Act", "artificial intelligence",
    "generative", "machine learning", "deep learning", "algorithmic",
]

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

# 2026.09.11 / 2026-09-11 / 2026/09/11 / 2026년 9월 11일 / 26.09.11
DATE_PATTERNS = [
    re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})"),
    re.compile(r"\b(\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})\b"),
]

# RSS pubDate: Thu, 11 Sep 2026 09:00:00 +0900
_RFC822_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}


@dataclass
class Item:
    source_id: str
    source_name: str
    category: str
    title: str
    url: str
    published: str = ""          # YYYY-MM-DD, 알 수 없으면 ""
    summary: str = ""

    def key(self) -> str:
        """중복 판정 키. 같은 자료가 URL 파라미터 순서만 달라 두 번 오지 않게 정규화한다."""
        parsed = urlparse(self.url)
        query = "&".join(sorted(p for p in parsed.query.split("&") if p))
        path = parsed.path.rstrip("/")
        if parsed.netloc and (path or query):
            return f"{parsed.netloc}{path}?{query}" if query else f"{parsed.netloc}{path}"
        return f"{self.source_id}:{normalize_space(self.title)}"

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "category": self.category,
            "title": self.title,
            "url": self.url,
            "published": self.published,
            "summary": self.summary,
        }


@dataclass
class CollectResult:
    items: list[Item] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text or "")).strip()


def parse_date(text: str) -> str:
    """임의의 텍스트에서 날짜를 찾아 YYYY-MM-DD로 돌려준다. 못 찾으면 ""."""
    if not text:
        return ""
    rfc = parse_rfc822(text)
    if rfc:
        return rfc
    iso = re.search(r"(20\d{2})-(\d{2})-(\d{2})T", text)
    if iso:
        return f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)}"
    for idx, pattern in enumerate(DATE_PATTERNS):
        m = pattern.search(text)
        if not m:
            continue
        year, month, day = m.group(1), int(m.group(2)), int(m.group(3))
        year = int(year) if len(year) == 4 else 2000 + int(year)
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        if idx == 1 and not (2000 <= year <= 2099):
            continue
        return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


def parse_rfc822(text: str) -> str:
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})", text)
    if not m:
        return ""
    month = _RFC822_MONTHS.get(m.group(2).lower())
    if not month:
        return ""
    return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(1)):02d}"


def fetch(url: str, timeout: int = 20) -> str:
    """페이지 본문을 문자열로 받아온다. 국내 기관 사이트는 EUC-KR인 경우가 아직 있다."""
    request = Request(url, headers=REQUEST_HEADERS)
    context = ssl.create_default_context()
    with urlopen(request, timeout=timeout, context=context) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset()
    return decode_body(raw, charset)


def decode_body(raw: bytes, charset: str | None = None) -> str:
    candidates = [charset] if charset else []
    head = raw[:2048].decode("ascii", errors="ignore").lower()
    declared = re.search(r'charset=["\']?([\w\-]+)', head)
    if declared:
        candidates.append(declared.group(1))
    candidates += ["utf-8-sig", "utf-8", "euc-kr", "cp949"]
    for enc in candidates:
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# RSS / Atom
# --------------------------------------------------------------------------

def _tag(element: ET.Element) -> str:
    return element.tag.split("}")[-1].lower()


def _find_text(parent: ET.Element, names: tuple[str, ...]) -> str:
    for child in parent:
        if _tag(child) in names:
            return normalize_space(child.text or "")
    return ""


def _find_link(parent: ET.Element) -> str:
    """RSS는 <link>텍스트</link>, Atom은 <link href="..."/>. 둘 다 처리한다."""
    fallback = ""
    for child in parent:
        if _tag(child) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            rel = child.attrib.get("rel", "alternate")
            if rel == "alternate":
                return href.strip()
            fallback = fallback or href.strip()
        elif child.text and child.text.strip():
            return child.text.strip()
    if not fallback:
        for child in parent:
            if _tag(child) in ("guid", "id") and child.text and child.text.strip().startswith("http"):
                return child.text.strip()
    return fallback


def parse_feed(xml_text: str, source: dict) -> list[Item]:
    root = ET.fromstring(xml_text.strip())
    entries = [e for e in root.iter() if _tag(e) in ("item", "entry")]
    base_url = source.get("base_url") or source.get("url", "")
    items: list[Item] = []
    for entry in entries:
        title = _find_text(entry, ("title",))
        link = _find_link(entry)
        if not title or not link:
            continue
        published = parse_date(
            _find_text(entry, ("pubdate", "published", "updated", "date"))
        )
        summary = _find_text(entry, ("description", "summary", "content"))
        items.append(
            Item(
                source_id=source["id"],
                source_name=source["name"],
                category=source.get("category", ""),
                title=title,
                url=urljoin(base_url, link),
                published=published,
                summary=summary[:300],
            )
        )
    return items


# --------------------------------------------------------------------------
# HTML 목록 페이지
# --------------------------------------------------------------------------

class _ListParser(HTMLParser):
    """목록 페이지에서 <a> 링크와 그 주변 텍스트를 순서대로 수집한다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.anchors: list[dict] = []
        self._stack: list[dict] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip_depth += 1
            return
        if tag != "a":
            return
        attrs_dict = dict(attrs)
        href = (attrs_dict.get("href") or "").strip()
        anchor = {
            "href": href,
            "title_attr": normalize_space(attrs_dict.get("title") or ""),
            "chunk_index": len(self.chunks),
            "parts": [],
        }
        self._stack.append(anchor)
        self.anchors.append(anchor)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "a" and self._stack:
            self._stack.pop()

    def handle_data(self, data):
        if self._skip_depth:
            return
        text = normalize_space(data)
        if not text:
            return
        self.chunks.append(text)
        for anchor in self._stack:
            anchor["parts"].append(text)


def parse_html_list(html_text: str, source: dict) -> list[Item]:
    parser = _ListParser()
    parser.feed(html_text)

    link_pattern = source.get("link_pattern")
    regex = re.compile(link_pattern) if link_pattern else None
    exclude = source.get("exclude_pattern")
    exclude_regex = re.compile(exclude) if exclude else None
    min_len = int(source.get("title_min_length", 6))
    base_url = source.get("base_url") or source.get("url", "")
    # 날짜는 링크 자체에 없고 같은 행의 옆 칸에 있는 경우가 많아, 뒤따르는 텍스트도 본다.
    lookahead = int(source.get("date_lookahead", 6))

    items: list[Item] = []
    seen: set[str] = set()
    for anchor in parser.anchors:
        href = anchor["href"]
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        if regex and not regex.search(href):
            continue
        if exclude_regex and exclude_regex.search(href):
            continue
        title = normalize_space(" ".join(anchor["parts"])) or anchor["title_attr"]
        if len(title) < min_len:
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)

        start = anchor["chunk_index"]
        context = " ".join(parser.chunks[start : start + lookahead])
        published = parse_date(title) or parse_date(context)
        items.append(
            Item(
                source_id=source["id"],
                source_name=source["name"],
                category=source.get("category", ""),
                title=title,
                url=url,
                published=published,
            )
        )
    return items


# --------------------------------------------------------------------------
# 필터 / 수집
# --------------------------------------------------------------------------

def matches_keywords(item: Item, keywords: list[str]) -> bool:
    haystack = f"{item.title} {item.summary}".lower()
    for kw in keywords:
        k = kw.lower()
        # 한글 키워드는 부분일치로 충분하지만, 짧은 영문 약어(AI, LLM)는
        # 'chain', 'mail' 같은 단어에 걸리지 않도록 단어 경계를 요구한다.
        if k.isascii() and len(k) <= 4:
            if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", haystack):
                return True
        elif k in haystack:
            return True
    return False


def within_max_age(item: Item, max_age_days: int, today: datetime | None = None) -> bool:
    """날짜를 모르는 항목은 버리지 않는다. 신규 여부는 상태 파일이 따로 판정한다."""
    if not item.published or max_age_days <= 0:
        return True
    try:
        published = datetime.strptime(item.published, "%Y-%m-%d").replace(tzinfo=KST)
    except ValueError:
        return True
    now = today or datetime.now(KST)
    if published > now + timedelta(days=2):   # 미래 날짜는 파싱 오류로 본다
        return True
    return (now - published).days <= max_age_days


def collect_source(source: dict, timeout: int = 20) -> tuple[list[Item], str]:
    try:
        body = fetch(source["url"], timeout=timeout)
    except HTTPError as e:
        return [], f"{source['id']}: HTTP {e.code} {e.reason}"
    except URLError as e:
        return [], f"{source['id']}: 접속 실패 ({e.reason})"
    except Exception as e:  # noqa: BLE001 - 소스 하나가 죽어도 전체는 계속 돌아야 한다
        return [], f"{source['id']}: {type(e).__name__} {e}"

    try:
        if source.get("type") == "rss":
            return parse_feed(body, source), ""
        return parse_html_list(body, source), ""
    except ET.ParseError as e:
        return [], f"{source['id']}: RSS 파싱 실패 ({e}). sources.json의 url을 확인하세요"
    except Exception as e:  # noqa: BLE001
        return [], f"{source['id']}: 파싱 실패 {type(e).__name__} {e}"


def collect(config: dict, timeout: int = 20, workers: int = 6) -> CollectResult:
    keywords = config.get("keywords") or DEFAULT_KEYWORDS
    max_age_days = int(config.get("max_age_days", 21))
    result = CollectResult()
    seen_keys: set[str] = set()

    sources = [s for s in config.get("sources", []) if s.get("enabled", True)]
    # 소스가 수십 개로 늘어도 Lambda 타임아웃 안에 끝나도록 병렬로 받아온다.
    # executor.map은 입력 순서대로 결과를 돌려주므로 결과 순서는 그대로 유지된다.
    if workers > 1 and len(sources) > 1:
        with ThreadPoolExecutor(max_workers=min(workers, len(sources))) as pool:
            fetched = list(pool.map(lambda s: collect_source(s, timeout=timeout), sources))
    else:
        fetched = [collect_source(s, timeout=timeout) for s in sources]

    for source, (items, error) in zip(sources, fetched):
        if error:
            result.errors.append(error)
            continue

        kept = 0
        for item in items:
            if source.get("keyword_filter", True) and not matches_keywords(item, keywords):
                continue
            if not within_max_age(item, max_age_days):
                continue
            key = item.key()
            if key in seen_keys:
                continue
            seen_keys.add(key)
            result.items.append(item)
            kept += 1

        limit = int(source.get("max_items", 0))
        if limit and kept > limit:
            # 목록 페이지 전체가 걸린 경우를 대비한 안전장치
            del result.items[len(result.items) - (kept - limit):]

    result.items.sort(key=lambda i: (i.published or "0000-00-00", i.source_name), reverse=True)
    return result


def load_config(path: str | None = None) -> dict:
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)
