"""표준 라이브러리만으로 동작하는 간이 HTML 파서와 CSS 선택자.

BeautifulSoup 없이도 크롤링이 되도록 하기 위한 것이다.
지원하는 선택자는 크롤링에 실제로 필요한 범위로 한정했다.

    div                    태그
    .price                 클래스
    #list                  아이디
    div.item               태그 + 클래스
    table tr td            자손 (공백)
    ul > li                자식 (>)
    a[href]                속성 존재
    td[class="fee"]        속성 값 일치
    li:nth-of-type(2)      형제 중 같은 태그 n번째
    td:nth-child(5)        부모의 n번째 자식 (표의 n번째 칸)
    td:first-child         첫 칸 / td:last-child 마지막 칸

BeautifulSoup이 설치돼 있으면 그쪽을 쓰는 편이 빠르고 견고하지만,
이 모듈만으로도 일반적인 예약 목록 페이지는 충분히 다룰 수 있다.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Optional

# 닫는 태그가 없는 요소들
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
# 내용을 텍스트로 취급하면 안 되는 요소들
SKIP_TEXT_TAGS = {"script", "style", "noscript"}


class Node:
    """HTML 요소 하나."""

    __slots__ = ("tag", "attrs", "children", "parent", "_text_parts")

    def __init__(self, tag: str, attrs: Optional[dict] = None, parent: Optional["Node"] = None):
        self.tag = tag
        self.attrs: dict[str, str] = attrs or {}
        self.children: list["Node"] = []
        self.parent = parent
        self._text_parts: list[str] = []

    # -- 속성 접근 ---------------------------------------------------------

    def get(self, name: str, default: str = "") -> str:
        return self.attrs.get(name, default)

    @property
    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()

    @property
    def text(self) -> str:
        """이 요소와 하위 요소의 텍스트를 공백 하나로 이어 붙인다."""
        out: list[str] = []
        self._collect_text(out)
        return re.sub(r"\s+", " ", " ".join(out)).strip()

    def _collect_text(self, out: list[str]) -> None:
        if self.tag in SKIP_TEXT_TAGS:
            return
        for part in self._text_parts:
            if part.strip():
                out.append(part.strip())
        for child in self.children:
            child._collect_text(out)

    # -- 순회 --------------------------------------------------------------

    def descendants(self):
        for child in self.children:
            yield child
            yield from child.descendants()

    def select(self, selector: str) -> list["Node"]:
        """선택자에 맞는 하위 요소를 문서 순서대로 반환한다."""
        return select(self, selector)

    def select_one(self, selector: str) -> Optional["Node"]:
        hits = self.select(selector)
        return hits[0] if hits else None

    def __repr__(self) -> str:
        cls = ".".join(self.classes)
        ident = self.attrs.get("id", "")
        label = self.tag + (f"#{ident}" if ident else "") + (f".{cls}" if cls else "")
        return f"<{label}>"


class _Builder(HTMLParser):
    """HTML 문자열을 Node 트리로 만든다. 닫히지 않은 태그도 견딘다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("[document]")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        # 스택을 거꾸로 훑어 짝이 맞는 지점까지만 닫는다.
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1]._text_parts.append(data)


def parse(html: str) -> Node:
    """HTML 문자열을 파싱해 루트 Node를 돌려준다."""
    builder = _Builder()
    try:
        builder.feed(html)
    except Exception:
        pass   # 깨진 HTML이어도 그때까지 만들어진 트리를 쓴다
    return builder.root


# ---------------------------------------------------------------------------
# 선택자
# ---------------------------------------------------------------------------

_SIMPLE_RE = re.compile(
    r"""
    (?P<tag>[a-zA-Z][\w-]*|\*)?
    (?P<rest>(?:
        \#[\w-]+
      | \.[\w-]+
      | \[[^\]]+\]
      | :nth-of-type\(\d+\)
      | :nth-child\(\d+\)
      | :first-child
      | :last-child
    )*)
    """,
    re.VERBOSE,
)
_ATTR_RE = re.compile(r"""\[\s*([\w:-]+)\s*(?:([~^$*|]?=)\s*["']?([^"'\]]*)["']?\s*)?\]""")


class _Simple:
    """결합자 없는 단일 조건 (예: div.item[data-id]:nth-of-type(2))."""

    def __init__(self, token: str):
        m = _SIMPLE_RE.match(token)
        if not m or (not m.group("tag") and not m.group("rest")):
            raise ValueError(f"해석할 수 없는 선택자: {token!r}")
        # 끝까지 읽지 못했으면 모르는 문법이다. 여기서 조용히 넘기면 그 조건이
        # 통째로 무시되어 엉뚱한 요소가 잡힌다. 예를 들어 :nth-child 를 모르던
        # 때 "td:nth-child(5)" 는 그냥 "td" 가 되어 모든 칸이 걸렸다.
        if m.end() != len(token):
            raise ValueError(
                f"해석할 수 없는 선택자: {token!r} "
                f"(모르는 부분: {token[m.end():]!r})")
        self.tag = m.group("tag") or "*"
        rest = m.group("rest") or ""
        self.ids: list[str] = re.findall(r"#([\w-]+)", rest)
        self.classes: list[str] = re.findall(r"\.([\w-]+)", rest)
        self.attrs: list[tuple[str, str, str]] = [
            (a, op or "", val or "") for a, op, val in _ATTR_RE.findall(rest)
        ]
        nth = re.search(r":nth-of-type\((\d+)\)", rest)
        self.nth = int(nth.group(1)) if nth else None
        # :nth-child 는 태그와 무관하게 부모의 몇 번째 자식인지를 센다.
        # 표에서 "n번째 칸" 을 가리킬 때 쓴다.
        child = re.search(r":nth-child\((\d+)\)", rest)
        self.nth_child: Optional[int] = int(child.group(1)) if child else None
        if ":first-child" in rest:
            self.nth_child = 1
        self.last_child = ":last-child" in rest

    def matches(self, node: Node) -> bool:
        if self.tag != "*" and node.tag != self.tag:
            return False
        if self.ids and node.get("id") not in self.ids:
            return False
        if self.classes:
            have = set(node.classes)
            if not set(self.classes).issubset(have):
                return False
        for name, op, val in self.attrs:
            if name not in node.attrs:
                return False
            if not op:
                continue
            actual = node.attrs[name]
            if op == "=" and actual != val:
                return False
            if op == "*=" and val not in actual:
                return False
            if op == "^=" and not actual.startswith(val):
                return False
            if op == "$=" and not actual.endswith(val):
                return False
            if op == "~=" and val not in actual.split():
                return False
        if self.nth is not None:
            if node.parent is None:
                return False
            same = [c for c in node.parent.children if c.tag == node.tag]
            try:
                if same.index(node) + 1 != self.nth:
                    return False
            except ValueError:
                return False
        if self.nth_child is not None or self.last_child:
            if node.parent is None:
                return False
            sibs = list(node.parent.children)
            try:
                pos = sibs.index(node) + 1
            except ValueError:
                return False
            if self.nth_child is not None and pos != self.nth_child:
                return False
            if self.last_child and pos != len(sibs):
                return False
        return True


def _tokenize(selector: str) -> list:
    """'ul > li a' → [_Simple(ul), '>', _Simple(li), ' ', _Simple(a)]"""
    parts = re.split(r"\s*(>)\s*|\s+", selector.strip())
    tokens: list = []
    pending_combinator = " "
    for part in parts:
        if part is None or part == "":
            continue
        if part == ">":
            pending_combinator = ">"
            continue
        if tokens:
            tokens.append(pending_combinator)
        tokens.append(_Simple(part))
        pending_combinator = " "
    return tokens


def select(root: Node, selector: str) -> list[Node]:
    """선택자에 맞는 요소 목록. 쉼표로 여러 선택자를 넘길 수 있다."""
    results: list[Node] = []
    seen: set[int] = set()
    for one in selector.split(","):
        one = one.strip()
        if not one:
            continue
        for node in _select_single(root, one):
            if id(node) not in seen:
                seen.add(id(node))
                results.append(node)
    return results


def _select_single(root: Node, selector: str) -> list[Node]:
    # 해석 못 한 선택자를 빈 결과로 덮으면, 설정이 틀렸는데도 "매물 0건" 으로만
    # 보여서 원인을 알 수 없다. 그대로 올려 보내 어디가 틀렸는지 알린다.
    tokens = _tokenize(selector)
    if not tokens:
        return []

    current = [n for n in root.descendants() if tokens[0].matches(n)]
    i = 1
    while i < len(tokens) - 1:
        combinator = tokens[i]
        simple = tokens[i + 1]
        nxt: list[Node] = []
        seen: set[int] = set()
        for node in current:
            pool = node.children if combinator == ">" else node.descendants()
            for cand in pool:
                if simple.matches(cand) and id(cand) not in seen:
                    seen.add(id(cand))
                    nxt.append(cand)
        current = nxt
        i += 2
    return current
