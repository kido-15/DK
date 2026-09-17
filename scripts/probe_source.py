#!/usr/bin/env python3
"""예약 사이트의 응답 구조를 분석해 크롤러 설정 초안을 만들어 준다.

사이트마다 HTML 구조가 다르기 때문에 셀렉터는 직접 확인해야 한다.
이 스크립트는 그 작업을 대신해 준다. 실제 응답을 받아서
  - 반복되는 목록 구조를 찾고
  - 그 안에서 시간·가격·골프장 이름처럼 보이는 칸을 짚어
  - config/sources.json 에 붙여 넣을 수 있는 설정 초안을 출력한다.

사용법:
    python3 scripts/probe_source.py "https://예약사이트/목록?date=20260920"
    python3 scripts/probe_source.py URL --id mysite --name "내 사이트"
    python3 scripts/probe_source.py URL --save-body out.html   # 응답을 파일로 저장
    python3 scripts/probe_source.py --from-file out.html       # 저장한 파일로 분석만

브라우저에서 개발자도구 → Network 탭을 열고 티타임 목록이 뜰 때 호출되는
주소를 복사해 넣으면 가장 잘 동작한다. 목록이 JSON API로 내려오는 사이트라면
그 API 주소를 넣는 편이 HTML보다 훨씬 안정적이다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from golf import htmlsel                                    # noqa: E402
from golf.extract import block_score as extract_block_score  # noqa: E402
from golf.sources.web_source import HttpClient              # noqa: E402

# 값의 성격을 알아보는 패턴들
TIME_RE = re.compile(r"(?:[01]?\d|2[0-3])\s*[:시]\s*[0-5]\d|오[전후]\s*\d{1,2}")
PRICE_RE = re.compile(r"\d{1,3}(?:,\d{3})+\s*원?|\d+(?:\.\d+)?\s*만\s*원?|\b\d{5,7}\b")
DATE_RE = re.compile(r"\d{4}\s*[-./년]\s*\d{1,2}\s*[-./월]\s*\d{1,2}|\d{1,2}\s*[-./월]\s*\d{1,2}")
COURSE_HINT = re.compile(r"CC|GC|컨트리|골프|클럽|리조트", re.IGNORECASE)


# ---------------------------------------------------------------------------
# HTML 분석
# ---------------------------------------------------------------------------


def node_signature(node: htmlsel.Node) -> str:
    """요소를 'tag.class1.class2' 형태의 서명으로 만든다."""
    classes = ".".join(sorted(node.classes))
    return f"{node.tag}.{classes}" if classes else node.tag


def find_repeating_blocks(root: htmlsel.Node, min_count: int = 3) -> list[tuple[str, list]]:
    """같은 부모 밑에서 같은 서명으로 여러 번 반복되는 요소 묶음을 찾는다.

    예약 목록은 거의 항상 이런 형태(tr이 여러 개, li가 여러 개)로 되어 있다.
    """
    groups: dict[tuple[int, str], list] = defaultdict(list)
    for node in root.descendants():
        if node.parent is None:
            continue
        groups[(id(node.parent), node_signature(node))].append(node)

    candidates = []
    for (_, sig), nodes in groups.items():
        if len(nodes) < min_count:
            continue
        # 텍스트가 거의 없는 묶음(내비게이션 등)은 제외
        texts = [n.text for n in nodes]
        if sum(1 for t in texts if len(t) > 4) < min_count:
            continue
        # 0점은 시각이 없다는 뜻이다. 티타임 목록이 아니므로 후보에서 뺀다.
        score = _block_score(nodes)
        if score <= 0:
            continue
        candidates.append((score, sig, nodes))

    candidates.sort(key=lambda c: c[0], reverse=True)
    return [(sig, nodes) for _, sig, nodes in candidates]


def _block_score(nodes: list) -> float:
    """티타임 목록다운 정도를 점수로.

    판정은 golf/extract.py 의 것을 그대로 쓴다. 거기서는 시각이 없으면 0점이라,
    회원등급 안내표처럼 시각 없는 표를 티타임 목록으로 잘못 고르지 않는다.
    """
    return extract_block_score(nodes)


def selector_for(node: htmlsel.Node, root: htmlsel.Node) -> str:
    """요소를 가리키는 짧고 안정적인 선택자를 만든다."""
    parts = []
    cur = node
    while cur is not None and cur.tag != "[document]":
        sig = cur.tag
        if cur.attrs.get("id"):
            sig = f"{cur.tag}#{cur.attrs['id']}"
            parts.append(sig)
            break
        if cur.classes:
            # 숫자가 섞인 클래스는 동적으로 생성된 것일 가능성이 커서 피한다
            stable = [c for c in cur.classes if not re.search(r"\d{2,}", c)]
            if stable:
                sig = f"{cur.tag}.{stable[0]}"
        parts.append(sig)
        cur = cur.parent
        if len(parts) >= 3:
            break
    return " ".join(reversed(parts))


def describe_fields(block: htmlsel.Node, root: htmlsel.Node) -> dict:
    """목록 항목 하나를 뜯어보고 각 칸이 무엇인지 추측한다."""
    guesses: dict[str, dict] = {}
    seen_selectors = set()

    children = [n for n in block.descendants() if n.text and len(n.text) < 60]
    for node in children:
        text = node.text
        # 가장 안쪽 요소만 본다 (부모는 자식 텍스트를 다 물고 있어 중복)
        if any(c.text == text for c in node.children):
            continue

        rel = _relative_selector(node, block)
        if not rel or rel in seen_selectors:
            continue

        kind = None
        if TIME_RE.search(text) and not DATE_RE.search(text):
            kind = "tee_time"
        elif PRICE_RE.search(text):
            kind = "green_fee"
        elif DATE_RE.search(text):
            kind = "play_date"
        elif COURSE_HINT.search(text):
            kind = "course_name"

        if kind and kind not in guesses:
            guesses[kind] = {"selector": rel, "_sample": text}
            seen_selectors.add(rel)

    link = block.select_one("a[href]")
    if link:
        rel = _relative_selector(link, block)
        guesses["booking_url"] = {
            "selector": rel or "a",
            "attr": "href",
            "_sample": link.get("href")[:80],
        }

    # 골프장 이름을 못 찾았다면 첫 번째 긴 텍스트 칸을 후보로 제시한다
    if "course_name" not in guesses:
        for node in children:
            if len(node.text) >= 3 and not TIME_RE.search(node.text) and not PRICE_RE.search(node.text):
                rel = _relative_selector(node, block)
                if rel:
                    guesses["course_name"] = {"selector": rel, "_sample": node.text}
                    break
    return guesses


def _relative_selector(node: htmlsel.Node, block: htmlsel.Node) -> str:
    """목록 항목을 기준으로 한 상대 선택자."""
    if node is block:
        return ""
    chain = []
    cur = node
    while cur is not None and cur is not block:
        sig = cur.tag
        stable = [c for c in cur.classes if not re.search(r"\d{2,}", c)]
        if stable:
            sig = f"{cur.tag}.{stable[0]}"
        elif cur.parent is not None:
            same = [c for c in cur.parent.children if c.tag == cur.tag]
            if len(same) > 1:
                sig = f"{cur.tag}:nth-of-type({same.index(cur) + 1})"
        chain.append(sig)
        cur = cur.parent
    if cur is not block:
        return ""
    return " ".join(reversed(chain))


def analyze_html(text: str, source_id: str, name: str, url: str) -> dict:
    root = htmlsel.parse(text)
    blocks = find_repeating_blocks(root)

    print(f"\n반복 구조 후보 {len(blocks)}개 (티타임 목록일 가능성이 높은 순)")
    if not blocks:
        print("  반복 구조를 찾지 못했습니다.")
        print("  → 목록이 자바스크립트로 그려지는 사이트일 수 있습니다.")
        print("     브라우저 개발자도구 Network 탭에서 XHR/Fetch 요청을 찾아")
        print("     그 JSON 주소를 다시 넣어 보세요.")
        return {}

    best_config = {}
    for i, (sig, nodes) in enumerate(blocks[:5], 1):
        sel = selector_for(nodes[0], root)
        print(f"\n[{i}] {sig}  —  {len(nodes)}개 반복")
        print(f"    선택자: {sel}")
        print(f"    첫 항목 텍스트: {nodes[0].text[:100]}")

        fields = describe_fields(nodes[0], root)
        if fields:
            print("    추측한 필드:")
            for key, spec in fields.items():
                print(f"      {key:14s} {spec['selector']!r:40s} 예: {spec['_sample']}")

        if i == 1:
            clean_fields = {
                k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
                for k, v in fields.items()
            }
            best_config = {
                "id": source_id,
                "name": name,
                "enabled": True,
                "format": "html",
                "request": {
                    "url": url,
                    "method": "GET",
                    "delay_seconds": 1.5,
                    "pages": {"start": 1, "max": 3, "stop_when_empty": True},
                },
                "list_selector": sel,
                "fields": clean_fields,
            }
    return best_config


# ---------------------------------------------------------------------------
# JSON 분석
# ---------------------------------------------------------------------------


def find_record_arrays(obj, path="", out=None, depth=0):
    """딕셔너리들이 들어 있는 배열의 경로를 모두 찾는다."""
    if out is None:
        out = []
    if depth > 8:
        return out
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict):
            out.append((path, obj))
        for i, v in enumerate(obj[:1]):
            find_record_arrays(v, f"{path}.{i}" if path else str(i), out, depth + 1)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            find_record_arrays(v, f"{path}.{k}" if path else k, out, depth + 1)
    return out


def analyze_json(text: str, source_id: str, name: str, url: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"JSON으로 읽을 수 없습니다: {exc}")
        return {}

    arrays = find_record_arrays(data)
    arrays.sort(key=lambda pa: len(pa[1]), reverse=True)

    print(f"\n레코드 배열 후보 {len(arrays)}개")
    if not arrays:
        print("  객체 배열을 찾지 못했습니다. 응답 최상위 구조:")
        print("  " + json.dumps(data, ensure_ascii=False)[:400])
        return {}

    best = {}
    for i, (path, arr) in enumerate(arrays[:5], 1):
        print(f"\n[{i}] 경로 {path!r} — {len(arr)}건")
        sample = arr[0]
        print("    첫 레코드의 키:")
        guesses = {}
        for k, v in list(sample.items())[:30]:
            sval = str(v)[:40]
            tag = ""
            if TIME_RE.search(str(v)) or (str(v).isdigit() and len(str(v)) == 4):
                tag = "← 시간?"
                guesses.setdefault("tee_time", {"path": k})
            elif isinstance(v, (int, float)) and 10000 <= float(v) <= 2000000:
                tag = "← 가격?"
                guesses.setdefault("green_fee", {"path": k})
            elif PRICE_RE.search(str(v)):
                tag = "← 가격?"
                guesses.setdefault("green_fee", {"path": k})
            elif DATE_RE.search(str(v)):
                tag = "← 날짜?"
                guesses.setdefault("play_date", {"path": k})
            elif isinstance(v, str) and COURSE_HINT.search(v):
                tag = "← 골프장명?"
                guesses.setdefault("course_name", {"path": k})
            print(f"      {k:24s} = {sval:42s} {tag}")

        if i == 1:
            if "course_name" not in guesses:
                for k, v in sample.items():
                    if isinstance(v, str) and 2 <= len(v) <= 30:
                        guesses["course_name"] = {"path": k}
                        break
            best = {
                "id": source_id,
                "name": name,
                "enabled": True,
                "format": "json",
                "request": {
                    "url": url,
                    "method": "GET",
                    "delay_seconds": 1.5,
                    "pages": {"start": 1, "max": 3, "stop_when_empty": True},
                },
                "records_path": path,
                "fields": guesses,
            }
    return best


# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="예약 사이트 구조 분석기")
    ap.add_argument("url", nargs="?", help="분석할 주소")
    ap.add_argument("--from-file", help="저장해 둔 응답 파일로 분석")
    ap.add_argument("--save-body", help="받은 응답을 이 경로에 저장")
    ap.add_argument("--id", default="newsite", help="소스 id")
    ap.add_argument("--name", default="새 사이트", help="소스 표시 이름")
    ap.add_argument("--header", action="append", default=[],
                    help="추가 헤더. 예: --header 'Referer: https://...'")
    ap.add_argument("--format", choices=["auto", "html", "json"], default="auto")
    args = ap.parse_args()

    if not args.url and not args.from_file:
        ap.error("url 또는 --from-file 중 하나는 있어야 합니다")

    if args.from_file:
        with open(args.from_file, encoding="utf-8", errors="replace") as f:
            text = f.read()
        url = args.url or "(파일에서 읽음)"
        print(f"파일 분석: {args.from_file} ({len(text):,}자)")
    else:
        headers = {}
        for h in args.header:
            if ":" in h:
                k, _, v = h.partition(":")
                headers[k.strip()] = v.strip()
        client = HttpClient(headers=headers)
        print(f"요청: {args.url}")
        try:
            text = client.get(args.url)
        except Exception as exc:
            print(f"\n요청 실패: {exc}")
            print("  - 403/401이면 로그인이나 Referer 헤더가 필요한 주소입니다")
            print("  - 타임아웃이면 해외 IP 차단일 수 있습니다 (국내망에서 실행해 보세요)")
            return 1
        url = args.url
        print(f"  {len(text):,}자 수신")
        if args.save_body:
            with open(args.save_body, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"  응답 저장: {args.save_body}")

    fmt = args.format
    if fmt == "auto":
        stripped = text.lstrip()
        fmt = "json" if stripped.startswith(("{", "[")) else "html"
    print(f"형식 판정: {fmt}")

    config = analyze_json(text, args.id, args.name, url) if fmt == "json" \
        else analyze_html(text, args.id, args.name, url)

    if config:
        print("\n" + "=" * 70)
        print("config/sources.json 의 \"sources\" 배열에 붙여 넣으세요:")
        print("=" * 70)
        print(json.dumps(config, ensure_ascii=False, indent=2))
        print("=" * 70)
        print("붙여 넣은 뒤 아래로 확인:")
        print(f"  python3 golf_cli.py --test-source {args.id}")
        print("\n주의: 위 필드는 자동 추측입니다. 샘플 값을 보고 틀린 것은 고쳐야 합니다.")
        print("      날짜가 페이지마다 고정이면 play_date를 지우고")
        print("      request.url 에 {date:%Y%m%d} 를 넣는 편이 낫습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
