"""표준 라이브러리만 쓰는 미니 HTML DOM + CSS 셀렉터.

BeautifulSoup/lxml을 쓰면 Lambda 배포 시 의존성 패키징이 필요해지므로,
게시판 목록 파싱에 필요한 최소 기능만 직접 구현한다.

지원하는 셀렉터 범위 (실제 게시판 목록에 필요한 수준):
  tag, .class, #id, [attr], [attr=value], [attr^=value], [attr*=value],
  자손(공백) / 자식(>) 결합자, 콤마로 여러 셀렉터 나열
예) "table.board_list tbody tr", "td.subject > a", "ul.list li[class*=item]"
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

# 닫는 태그가 없는 요소
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

# 새 태그가 열릴 때 자동으로 닫히는 요소 (게시판 HTML은 닫는 태그 누락이 흔하다)
IMPLIED_END = {
    "li": {"li"},
    "tr": {"tr", "td", "th"},
    "td": {"td", "th"},
    "th": {"td", "th"},
    "p": {"p"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
    "option": {"option"},
    "thead": {"tr", "td", "th"},
    "tbody": {"tr", "td", "th"},
}

# 텍스트로 취급하지 않을 요소
SKIP_TEXT_TAGS = {"script", "style"}

_WS = re.compile(r"\s+")


class Node:
    """HTML 요소 하나. tag가 None이면 문서 루트."""

    __slots__ = ("tag", "attrs", "children", "parent", "_content")

    def __init__(self, tag: str | None, attrs: dict[str, str] | None = None,
                 parent: "Node | None" = None) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[Node] = []
        self.parent = parent
        # 텍스트와 자식 요소를 문서에 나온 순서 그대로 담는다.
        # children만 보고 텍스트를 뒤에 몰아 붙이면
        # <td>앞<b>가운데</b>뒤</td> 가 "앞 뒤 가운데"로 뒤집힌다.
        self._content: list[str | Node] = []

    # --- 기본 접근자 ---------------------------------------------------
    def get(self, name: str, default: str = "") -> str:
        return self.attrs.get(name.lower(), default)

    @property
    def classes(self) -> list[str]:
        return self.get("class").split()

    def text(self, separator: str = " ") -> str:
        """자신과 모든 자손의 텍스트를 이어붙여 공백 정규화한 문자열."""
        parts: list[str] = []
        self._collect_text(parts)
        return _WS.sub(" ", separator.join(parts)).strip()

    def direct_text(self) -> str:
        """자식 요소를 뺀, 이 노드에 직접 붙어있는 텍스트.

        <li><strong>등록일</strong> 2026.08.13</li> 처럼 라벨과 값이 한 칸에
        들어있는 게시판에서 값만 골라내는 데 쓴다.
        """
        parts = [p for p in self._content if isinstance(p, str)]
        return _WS.sub(" ", " ".join(parts)).strip()

    def _collect_text(self, parts: list[str]) -> None:
        if self.tag in SKIP_TEXT_TAGS:
            return
        for piece in self._content:
            if isinstance(piece, str):
                parts.append(piece)
            else:
                piece._collect_text(parts)

    def iter_descendants(self):
        for child in self.children:
            yield child
            yield from child.iter_descendants()

    def signature(self) -> str:
        """루트부터 이어지는 tag.class 경로. 자동탐지에서 같은 종류의 행을 묶는 데 쓴다."""
        parts: list[str] = []
        node: Node | None = self
        while node is not None and node.tag is not None:
            cls = ".".join(sorted(node.classes))
            parts.append(f"{node.tag}.{cls}" if cls else node.tag)
            node = node.parent
        return ">".join(reversed(parts))

    # --- 셀렉터 --------------------------------------------------------
    def select(self, selector: str) -> list["Node"]:
        """셀렉터에 맞는 자손 노드를 문서 순서대로 반환."""
        groups = [parse_selector(s) for s in split_selector_list(selector)]
        found: list[Node] = []
        for node in self.iter_descendants():
            if any(_matches_chain(node, chain) for chain in groups):
                found.append(node)
        return found

    def select_one(self, selector: str) -> "Node | None":
        matches = self.select(selector)
        return matches[0] if matches else None

    def __repr__(self) -> str:  # pragma: no cover - 디버깅용
        cls = " ".join(self.classes)
        label = self.tag or "#root"
        return f"<Node {label}{'.' + cls if cls else ''} children={len(self.children)}>"


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.root = Node(None)
        self._stack: list[Node] = [self.root]

    @property
    def _current(self) -> Node:
        return self._stack[-1]

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        closes = IMPLIED_END.get(tag)
        if closes:
            while len(self._stack) > 1 and self._current.tag in closes:
                self._stack.pop()
        node = Node(tag, {k.lower(): (v or "") for k, v in attrs}, self._current)
        self._current.children.append(node)
        self._current._content.append(node)
        if tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        node = Node(tag, {k.lower(): (v or "") for k, v in attrs}, self._current)
        self._current.children.append(node)
        self._current._content.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in VOID_TAGS:
            return
        # 스택에 해당 태그가 있으면 거기까지 닫고, 없으면(짝 안 맞는 닫는 태그) 무시
        for depth in range(len(self._stack) - 1, 0, -1):
            if self._stack[depth].tag == tag:
                del self._stack[depth:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._current._content.append(data)

    def handle_entityref(self, name: str) -> None:
        self._current._content.append(unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self._current._content.append(unescape(f"&#{name};"))


def parse_html(html: str) -> Node:
    builder = _Builder()
    builder.feed(html)
    builder.close()
    return builder.root


# --- 셀렉터 파싱 --------------------------------------------------------

_ATTR_RE = re.compile(r"\[\s*([\w:-]+)\s*(?:([~^*$|]?=)\s*(\"[^\"]*\"|'[^']*'|[^\]]*))?\s*\]")
_SIMPLE_RE = re.compile(r"([\w*-]+)?((?:[.#][\w-]+)*)((?:\[[^\]]*\])*)$")


class _Simple:
    __slots__ = ("tag", "id", "classes", "attrs")

    def __init__(self, tag, id_, classes, attrs):
        self.tag = tag
        self.id = id_
        self.classes = classes
        self.attrs = attrs


def split_selector_list(selector: str) -> list[str]:
    parts = [p.strip() for p in selector.split(",")]
    return [p for p in parts if p]


def parse_selector(selector: str) -> list[tuple[str, _Simple]]:
    """셀렉터를 [(결합자, 단순셀렉터)] 리스트로 변환. 결합자는 ' '(자손) 또는 '>'(자식)."""
    tokens = re.split(r"\s*(>)\s*|\s+", selector.strip())
    tokens = [t for t in tokens if t]
    chain: list[tuple[str, _Simple]] = []
    combinator = " "
    for token in tokens:
        if token == ">":
            combinator = ">"
            continue
        chain.append((combinator, _parse_simple(token)))
        combinator = " "
    if not chain:
        raise ValueError(f"셀렉터를 해석할 수 없습니다: {selector!r}")
    return chain


def _parse_simple(token: str) -> _Simple:
    attrs: list[tuple[str, str, str]] = []
    for match in _ATTR_RE.finditer(token):
        name, op, value = match.group(1), match.group(2) or "", match.group(3) or ""
        value = value.strip().strip("\"'")
        attrs.append((name.lower(), op, value))
    stripped = _ATTR_RE.sub("", token)

    tag = None
    id_ = None
    classes: list[str] = []
    match = re.match(r"^([\w*-]+)?((?:[.#][\w-]+)*)$", stripped)
    if not match:
        raise ValueError(f"셀렉터를 해석할 수 없습니다: {token!r}")
    if match.group(1) and match.group(1) != "*":
        tag = match.group(1).lower()
    for piece in re.findall(r"[.#][\w-]+", match.group(2) or ""):
        if piece[0] == "#":
            id_ = piece[1:]
        else:
            classes.append(piece[1:])
    return _Simple(tag, id_, classes, attrs)


def _matches_simple(node: Node, simple: _Simple) -> bool:
    if node.tag is None:
        return False
    if simple.tag and node.tag != simple.tag:
        return False
    if simple.id and node.get("id") != simple.id:
        return False
    if simple.classes:
        have = set(node.classes)
        if not set(simple.classes).issubset(have):
            return False
    for name, op, value in simple.attrs:
        if name not in node.attrs:
            return False
        actual = node.attrs[name]
        if op == "" or value == "":
            continue
        if op == "=" and actual != value:
            return False
        if op == "^=" and not actual.startswith(value):
            return False
        if op == "*=" and value not in actual:
            return False
        if op == "$=" and not actual.endswith(value):
            return False
        if op == "~=" and value not in actual.split():
            return False
    return True


def _matches_chain(node: Node, chain: list[tuple[str, _Simple]]) -> bool:
    """체인의 마지막 단순셀렉터가 node에 맞는지 확인하고 조상 방향으로 거슬러 올라간다."""
    _, last = chain[-1]
    if not _matches_simple(node, last):
        return False
    return _match_ancestors(node, chain, len(chain) - 1)


def _match_ancestors(node: Node, chain, index: int) -> bool:
    if index == 0:
        return True
    combinator, simple = chain[index]
    parent = node.parent
    if combinator == ">":
        if parent is None or not _matches_simple(parent, chain[index - 1][1]):
            return False
        return _match_ancestors(parent, chain, index - 1)
    # 자손 결합자: 조상 중 하나라도 맞으면 됨 (백트래킹)
    while parent is not None:
        if _matches_simple(parent, chain[index - 1][1]) and _match_ancestors(parent, chain, index - 1):
            return True
        parent = parent.parent
    return False
