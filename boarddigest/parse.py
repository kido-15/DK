"""게시판 목록 HTML/RSS에서 (제목, 링크, 등록일)을 뽑아낸다.

사이트마다 마크업이 다르므로 두 가지 경로를 제공한다.
  1) 설정에 셀렉터를 적어주면 그대로 사용 (정확, 권장)
  2) 셀렉터가 없으면 자동탐지: '날짜가 들어있는 반복 블록'을 행으로 간주
     -> scripts/check_boards.py --probe 로 결과를 확인하고 셀렉터를 확정하면 된다
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin
from xml.etree import ElementTree

from .dates import KST, parse_date, today_kst
from .dom import Node, parse_html

# 목록 페이지에 늘 붙어있어 제목으로 오인하기 쉬운 링크 텍스트
_NOISE_TITLES = {
    "다음", "이전", "처음", "맨끝", "목록", "더보기", "상세보기", "보기", "다운로드",
    "첨부파일", "바로가기", "검색", "닫기", "인쇄", "공유", "top", "next", "prev",
    "more", "list", "download", "search",
}
_PAGE_NUMBER = re.compile(r"^\s*\d{1,3}\s*$")
# javascript:goView('1234') 처럼 링크가 함수 호출인 경우 인자를 뽑는다
_JS_ARGS = re.compile(r"""['"]([\w-]{1,64})['"]""")
# onclick="goView('1234', '')" 에서 함수 이름 (probe가 설정 힌트를 만들 때 쓴다)
_JS_FUNC = re.compile(r"([A-Za-z_$][\w$.]{0,63})\s*\(")


@dataclass
class Item:
    """게시판 글 한 건."""

    site_id: str
    site_name: str
    title: str
    url: str
    posted: date | None
    category: str = ""
    raw_date: str = ""

    def key(self) -> str:
        """중복 발송 방지용 식별자. 링크가 없으면 제목으로 대체한다."""
        return f"{self.site_id}|{self.url or self.title}"


@dataclass
class ParseResult:
    items: list[Item] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    detected: dict[str, str] = field(default_factory=dict)


def _text_of(node: Node | None) -> str:
    return node.text() if node is not None else ""


def _select_field(row: Node, selector: str | None) -> tuple[Node | None, str]:
    """'td.subject a@title' 형태(끝의 @속성)를 지원하는 필드 추출."""
    if not selector:
        return None, ""
    attr = None
    if "@" in selector:
        selector, attr = selector.rsplit("@", 1)
        selector = selector.strip()
    node = row if selector in ("", "self", ".") else row.select_one(selector)
    if node is None:
        return None, ""
    return node, (node.get(attr) if attr else node.text())


def js_call_args(href: str, anchor: Node | None = None) -> list[str]:
    """링크가 자바스크립트 함수 호출일 때 그 인자를 순서대로 뽑는다.

    국내 기관 게시판(전자정부 프레임워크 계열)은 실제 주소 대신
    href="#none" onclick="goView('115095', '')" 형태를 쓰는 경우가 매우 흔하다.
    href의 javascript: 스킴만 보면 이런 게시판은 링크를 하나도 만들지 못한다.
    """
    sources = []
    if href.lower().startswith("javascript:"):
        sources.append(href)
    if anchor is not None:
        sources.append(anchor.get("onclick"))
        sources.append(anchor.get("href"))
    for source in sources:
        args = _JS_ARGS.findall(source or "")
        if args:
            return args
    return []


def js_call_name(anchor: Node | None) -> str:
    """onclick(없으면 href)에서 호출하는 함수 이름. probe 안내 문구용."""
    if anchor is None:
        return ""
    for source in (anchor.get("onclick"), anchor.get("href")):
        source = (source or "").strip()
        if source.lower().startswith("javascript:"):
            source = source[len("javascript:"):]
        match = _JS_FUNC.search(source)
        if match:
            return match.group(1)
    return ""


def _resolve_link(base_url: str, href: str, cfg: dict, anchor: Node | None = None) -> str:
    """상대경로 / javascript: / onclick 링크를 최종 URL로 만든다."""
    href = (href or "").strip()
    if href.startswith("#"):
        href = ""  # "#", "#none" 처럼 자리만 채운 href
    template = cfg.get("detail_url_template")
    if href.lower().startswith("javascript:") or not href:
        args = js_call_args(href, anchor)
        if not template or not args:
            return ""
        try:
            return template.format(id=args[0], **{f"arg{i}": v for i, v in enumerate(args)})
        except (IndexError, KeyError):
            # 템플릿이 없는 자리(arg3 등)를 참조하면 링크만 비우고 계속 진행한다
            return ""
    return urljoin(base_url, href)


def _is_noise_anchor(node: Node) -> bool:
    text = node.text().strip()
    if not text or len(text) < 2:
        return True
    if text.lower() in _NOISE_TITLES or _PAGE_NUMBER.match(text):
        return True
    href = node.get("href").strip().lower()
    return href.startswith(("mailto:", "tel:"))


def _row_date_node(row: Node, exclude: Node | None) -> tuple[Node | None, str]:
    """행 안에서 '날짜만 들어있는 가장 작은 칸'을 찾는다 (제목 노드는 제외)."""
    excluded = set()
    if exclude is not None:
        excluded.add(id(exclude))
        for descendant in exclude.iter_descendants():
            excluded.add(id(descendant))

    best: tuple[Node, str] | None = None
    for node in row.iter_descendants():
        if id(node) in excluded or node.children:
            continue  # 잎 노드만 본다
        text = node.text().strip()
        if not text or parse_date(text) is None:
            continue
        if best is None or len(text) < len(best[1]):
            best = (node, text)
    if best:
        return best

    # 제목 링크 '안쪽'에 등록일이 들어있는 구조 (KISDI 등 국내 기관에 흔하다)
    #   <a ...><strong>제목</strong><ul><li><strong>등록일</strong> 2026.08.13</li></ul></a>
    # 이때는 위 잎 노드 훑기가 제목 링크를 통째로 건너뛰므로 아무것도 찾지 못한다.
    # 라벨을 뺀 '직접 텍스트'만 보고, 제목을 날짜로 오인하지 않도록 연도가 있는 표기만 받는다.
    for node in row.iter_descendants():
        if node is exclude:
            continue
        own = node.direct_text()
        if own and parse_date(own, require_year=True) is not None:
            if best is None or len(own) < len(best[1]):
                best = (node, own)
    if best:
        return best

    # 그래도 없으면 행 전체 텍스트에서 찾는다 (제목 텍스트는 뺀다)
    whole = row.text()
    if exclude is not None:
        whole = whole.replace(exclude.text(), " ")
    return (None, whole) if parse_date(whole) is not None else (None, "")


def autodetect_rows(doc: Node) -> tuple[list[tuple[Node, Node]], dict[str, str]]:
    """(행, 제목앵커) 목록과 추천 셀렉터를 돌려준다."""
    candidates: dict[str, list[tuple[Node, Node]]] = {}
    for anchor in doc.select("a[href]"):
        if _is_noise_anchor(anchor):
            continue
        node: Node | None = anchor.parent
        depth = 0
        while node is not None and node.tag is not None and depth < 6:
            date_node, raw = _row_date_node(node, anchor)
            if raw and parse_date(raw) is not None:
                candidates.setdefault(node.signature(), []).append((node, anchor))
                break
            node = node.parent
            depth += 1

    if not candidates:
        return [], {}

    # 목록의 '행'은 다른 행을 품지 않는다. 페이지 어딘가에 날짜가 있기만 하면
    # 바깥 컨테이너(footer, wrapper 등)도 후보로 잡히는데, 이들은 행 수가 많아
    # 실제 목록을 이겨버린다. 다른 후보 행을 감싸는 시그니처를 먼저 걸러낸다.
    owner = {id(node): sig for sig, rows in candidates.items() for node, _ in rows}
    containers: set[str] = set()
    for signature, rows in candidates.items():
        if len(rows) < 2:
            continue  # 한 번뿐인 구조는 목록이 아니므로 기준으로 삼지 않는다
        for node, _ in rows:
            parent = node.parent
            while parent is not None:
                outer = owner.get(id(parent))
                if outer is not None and outer != signature:
                    containers.add(outer)
                parent = parent.parent
    usable = {s: r for s, r in candidates.items() if s not in containers} or candidates

    # 같은 구조가 여러 번 반복되는 것이 목록이다. 동률이면 더 깊은(구체적인) 구조를 택한다.
    signature, rows = max(
        usable.items(), key=lambda kv: (len(kv[1]), kv[0].count(">"))
    )
    if len(rows) < 2:
        return [], {}

    # 같은 행이 앵커 여러 개로 중복 수집될 수 있어, 행별로 가장 긴 앵커만 남긴다
    by_row: dict[int, tuple[Node, Node]] = {}
    for row, anchor in rows:
        current = by_row.get(id(row))
        if current is None or len(anchor.text()) > len(current[1].text()):
            by_row[id(row)] = (row, anchor)
    rows = list(by_row.values())

    suggestion = {
        "row_selector": _short_selector(signature),
        "title_selector": _relative_selector(rows[0][0], rows[0][1]) or "a",
    }
    date_node, _ = _row_date_node(rows[0][0], rows[0][1])
    if date_node is not None:
        suggestion["date_selector"] = _relative_selector(rows[0][0], date_node) or ""
    return rows, suggestion


def _short_selector(signature: str) -> str:
    """전체 경로 시그니처에서 뒤쪽 2~3단계만 남겨 사람이 읽을 수 있게 만든다."""
    parts = signature.split(">")
    tail = parts[-3:] if len(parts) >= 3 else parts
    return " ".join(tail)


def _relative_selector(row: Node, target: Node) -> str:
    parts: list[str] = []
    node: Node | None = target
    while node is not None and node is not row:
        cls = ".".join(node.classes[:1])
        parts.append(f"{node.tag}.{cls}" if cls else str(node.tag))
        node = node.parent
    return " ".join(reversed(parts))


def parse_board(html: str, base_url: str, cfg: dict) -> ParseResult:
    """게시판 목록 HTML을 Item 리스트로 변환."""
    result = ParseResult()
    doc = parse_html(html)
    site_id = cfg.get("id", "")
    site_name = cfg.get("name", site_id)
    date_format = cfg.get("date_format")
    today = today_kst()

    row_selector = cfg.get("row_selector")
    pairs: list[tuple[Node, Node | None]] = []
    if row_selector:
        rows = doc.select(row_selector)
        if not rows:
            result.warnings.append(
                f"row_selector '{row_selector}'에 맞는 행이 없습니다. "
                "--probe로 구조를 다시 확인하세요."
            )
        pairs = [(row, None) for row in rows]
    else:
        detected, suggestion = autodetect_rows(doc)
        result.detected = suggestion
        if not detected:
            result.warnings.append(
                "자동탐지 실패: 날짜가 포함된 반복 목록을 찾지 못했습니다. "
                "자바스크립트로 목록을 그리는 게시판일 수 있습니다."
            )
        pairs = [(row, anchor) for row, anchor in detected]

    skip_classes = {c.lower() for c in cfg.get("skip_row_classes", ["notice", "notice_top", "fixed"])}
    seen_keys: set[str] = set()
    js_function = ""          # 링크가 함수 호출일 때 그 이름/인자를 기억해 두었다가
    js_sample: list[str] = []  # 아래 경고에서 바로 쓸 수 있는 템플릿 예시로 보여준다

    for row, auto_anchor in pairs:
        if skip_classes and {c.lower() for c in row.classes} & skip_classes:
            continue  # 상단 고정 공지는 매일 올라온 자료가 아니므로 제외

        title_node, title = _select_field(row, cfg.get("title_selector"))
        if title_node is None and auto_anchor is not None:
            title_node, title = auto_anchor, auto_anchor.text()
        title = re.sub(r"\s+", " ", (title or "")).strip()
        title = re.sub(r"^(?:NEW|N|HOT|공지|새글)\s+", "", title, flags=re.IGNORECASE).strip()
        if not title:
            continue

        link_selector = cfg.get("link_selector") or cfg.get("title_selector")
        anchor: Node | None = None
        href = ""
        if link_selector:
            if "@" not in link_selector:
                link_selector = f"{link_selector}@href"
            anchor, href = _select_field(row, link_selector)
        if anchor is None or anchor.tag != "a":
            # onclick을 읽어야 하므로 href 문자열이 아니라 a 노드 자체가 필요하다
            anchor = (title_node if title_node is not None and title_node.tag == "a" else None)
            # href 있는 앵커를 먼저 찾고, onclick만 있는 <a>는 그다음에 본다
            anchor = anchor or row.select_one("a[href]") or row.select_one("a")
            if not href:
                href = anchor.get("href") if anchor is not None else ""
        url = _resolve_link(base_url, href, cfg, anchor)
        if anchor is not None and not js_function:
            js_function = js_call_name(anchor)
            js_sample = js_call_args(href, anchor)

        date_node, raw_date = _select_field(row, cfg.get("date_selector"))
        if not raw_date:
            date_node, raw_date = _row_date_node(row, title_node)
        posted = parse_date(raw_date, today=today, explicit_format=date_format)

        category = ""
        if cfg.get("category_selector"):
            category = _text_of(row.select_one(cfg["category_selector"])).strip()

        item = Item(
            site_id=site_id,
            site_name=site_name,
            title=title,
            url=url,
            posted=posted,
            category=category,
            raw_date=raw_date.strip()[:40],
        )
        if item.key() in seen_keys:
            continue  # 같은 페이지 안의 중복 행
        seen_keys.add(item.key())
        result.items.append(item)

    if pairs and not result.items:
        result.warnings.append("행은 찾았지만 제목을 추출하지 못했습니다. title_selector를 확인하세요.")
    if result.items and not any(i.url for i in result.items):
        if js_function and js_sample:
            args = ", ".join(f"{{arg{i}}}={v}" for i, v in enumerate(js_sample))
            result.warnings.append(
                f"링크를 만들지 못했습니다. 목록이 {js_function}({args}) 형태의 함수 호출입니다. "
                "상세 페이지를 한 번 열어 주소를 확인한 뒤 detail_url_template에 "
                "그 주소를 {arg0} 자리와 함께 적어주세요."
            )
        else:
            result.warnings.append(
                "링크를 만들지 못했습니다. 목록이 javascript: 함수 호출이면 "
                "detail_url_template(예: \"https://.../view.do?no={arg0}\")을 지정하세요."
            )
    undated = [i for i in result.items if i.posted is None]
    if result.items and len(undated) == len(result.items):
        result.warnings.append(
            "모든 행의 날짜를 읽지 못했습니다. date_selector 또는 date_format을 지정하세요."
        )
    return result


def parse_feed(xml_text: str, cfg: dict) -> ParseResult:
    """RSS 2.0 / Atom 피드를 Item 리스트로 변환."""
    result = ParseResult()
    site_id = cfg.get("id", "")
    site_name = cfg.get("name", site_id)
    try:
        root = ElementTree.fromstring(xml_text.strip())
    except ElementTree.ParseError as e:
        result.warnings.append(f"피드 파싱 실패: {e}")
        return result

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    entries = [el for el in root.iter() if local(el.tag) in ("item", "entry")]
    for entry in entries:
        title = ""
        url = ""
        raw_date = ""
        for child in entry:
            name = local(child.tag)
            if name == "title" and not title:
                title = (child.text or "").strip()
            elif name == "link" and not url:
                url = (child.get("href") or child.text or "").strip()
            elif name in ("pubdate", "published", "updated", "date") and not raw_date:
                raw_date = (child.text or "").strip()
        if not title:
            continue
        result.items.append(
            Item(
                site_id=site_id,
                site_name=site_name,
                title=re.sub(r"\s+", " ", title),
                url=urljoin(cfg.get("list_url", ""), url),
                posted=_parse_feed_date(raw_date),
                raw_date=raw_date[:40],
            )
        )
    if not result.items:
        result.warnings.append("피드에서 항목을 찾지 못했습니다.")
    return result


def _parse_feed_date(raw: str) -> date | None:
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)  # RFC 822 (RSS)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))  # ISO 8601 (Atom)
        except ValueError:
            return parse_date(raw)
    if parsed.tzinfo is None:
        return parsed.date()  # 표기가 없으면 현지(KST) 시각으로 본다
    return parsed.astimezone(KST).date()
