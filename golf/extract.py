"""설정 없이 아무 예약 페이지에서나 티타임을 뽑아내는 자동 추출 엔진.

개별 골프장 홈페이지는 수백 곳이고 구조가 전부 다르다. 한 곳씩 셀렉터를
적어 주는 것은 불가능하므로, 페이지를 보고 스스로 판단해야 한다.

판단 근거는 단순하다. 예약 목록은 거의 항상 이런 모습이다.
  - 같은 모양의 덩어리가 여러 번 반복된다 (표의 행, 카드 목록)
  - 각 덩어리 안에 시각(07:12)과 금액(168,000원)이 함께 들어 있다

이 두 조건을 만족하는 덩어리를 찾아 티타임으로 읽는다.
확신도(confidence)를 함께 돌려주므로, 낮은 값은 버리거나 사람이 확인할 수 있다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from . import htmlsel
from .models import TeeTime, parse_date, parse_price, parse_time

# ---------------------------------------------------------------------------
# 값의 성격을 알아보는 패턴
# ---------------------------------------------------------------------------

# 시각: 07:12, 7시 30분, 오전 6:40. 날짜로 오인되지 않도록 뒤에 년/월/일이 붙으면 제외
TIME_RE = re.compile(r"(?<!\d)(?:[01]?\d|2[0-3])\s*[:시]\s*[0-5]\d(?!\s*[년월일])")
TIME_LOOSE_RE = re.compile(r"(?<!\d)(?:[01]?\d|2[0-3])\s*시(?!\s*간)")
# 금액: 168,000원 / 16.8만원 / 168000원. 단독 숫자는 위험해서 '원'이나 콤마를 요구한다
PRICE_RE = re.compile(r"\d{1,3}(?:,\d{3})+\s*원?|\d+(?:\.\d+)?\s*만\s*원?|\d{5,7}\s*원")
DATE_RE = re.compile(r"(?:20)?\d{2}\s*[-./년]\s*\d{1,2}\s*[-./월]\s*\d{1,2}|\d{1,2}\s*월\s*\d{1,2}\s*일")
# 잔여 좌석: 2자리, 3명, 잔여 1
SLOT_RE = re.compile(r"(\d)\s*(?:자리|명|팀|인)|잔여\s*(\d)")

# 예약이 불가능한 상태를 나타내는 말. 이런 덩어리는 결과에서 뺀다.
UNAVAILABLE_WORDS = [
    "마감", "매진", "예약불가", "완료", "종료", "불가", "대기", "품절",
    "soldout", "sold out", "closed", "full",
]
# 예약 가능을 나타내는 말
AVAILABLE_WORDS = ["예약", "부킹", "신청", "가능", "booking", "reserve"]

# 목록이 아니라 안내/광고인 덩어리를 걸러내기 위한 말
NOISE_WORDS = ["로그인", "회원가입", "고객센터", "이용약관", "개인정보", "공지사항"]


@dataclass
class ExtractResult:
    """추출 결과와 그 근거."""

    tee_times: list[TeeTime] = field(default_factory=list)
    confidence: float = 0.0        # 0~1. 이 페이지를 예약 목록으로 볼 수 있는 정도
    block_selector: str = ""       # 목록으로 판단한 선택자
    block_count: int = 0           # 반복 덩어리 수
    reason: str = ""               # 사람이 읽을 수 있는 판단 근거
    needs_login: bool = False      # 로그인이 필요해 보이는 페이지인지

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": len(self.tee_times),
            "confidence": round(self.confidence, 3),
            "block_selector": self.block_selector,
            "block_count": self.block_count,
            "reason": self.reason,
            "needs_login": self.needs_login,
        }


# ---------------------------------------------------------------------------
# 반복 구조 찾기
# ---------------------------------------------------------------------------


def node_signature(node: htmlsel.Node) -> str:
    """요소를 'tag.class1.class2' 형태의 서명으로. 같은 서명 = 같은 모양."""
    # 숫자가 섞인 클래스는 동적으로 붙는 것이 많아 서명에서 뺀다
    classes = sorted(c for c in node.classes if not re.search(r"\d{2,}", c))
    return f"{node.tag}.{'.'.join(classes)}" if classes else node.tag


def find_repeating_blocks(root: htmlsel.Node, min_count: int = 2) -> list[tuple[str, list]]:
    """같은 부모 밑에서 같은 서명으로 반복되는 요소 묶음을 점수순으로 반환한다."""
    groups: dict[tuple[int, str], list] = {}
    for node in root.descendants():
        if node.parent is None:
            continue
        key = (id(node.parent), node_signature(node))
        groups.setdefault(key, []).append(node)

    candidates = []
    for (_, sig), nodes in groups.items():
        if len(nodes) < min_count:
            continue
        if sum(1 for n in nodes if len(n.text) > 4) < min_count:
            continue
        score = block_score(nodes)
        if score > 0:
            candidates.append((sig, nodes, score))

    candidates.sort(key=lambda x: x[2], reverse=True)
    return [(sig, nodes) for sig, nodes, _ in candidates]


def block_score(nodes: list) -> float:
    """티타임 목록다운 정도. 시각과 금액이 함께 있으면 높다."""
    sample = nodes[: min(10, len(nodes))]
    n = len(sample) or 1

    has_time = sum(1 for x in sample if TIME_RE.search(x.text))
    has_price = sum(1 for x in sample if PRICE_RE.search(x.text))
    has_noise = sum(1 for x in sample if any(w in x.text for w in NOISE_WORDS))

    time_ratio = has_time / n
    price_ratio = has_price / n

    # 시각이 없으면 예약 목록으로 보지 않는다. 가장 중요한 조건이다.
    if time_ratio < 0.5:
        return 0.0

    score = time_ratio * 3.0 + price_ratio * 2.5
    score += min(len(nodes), 40) / 40.0        # 항목이 많을수록 목록일 가능성이 높다
    score -= (has_noise / n) * 2.0             # 내비게이션 등은 감점
    return max(score, 0.0)


# ---------------------------------------------------------------------------
# 덩어리 하나에서 값 뽑기
# ---------------------------------------------------------------------------


def _leaf_texts(block: htmlsel.Node) -> list[tuple[htmlsel.Node, str]]:
    """가장 안쪽 요소들의 텍스트. 부모는 자식 텍스트를 다 물고 있어 제외한다."""
    out = []
    for node in block.descendants():
        text = node.text
        if not text or len(text) > 80:
            continue
        if any(c.text == text for c in node.children):
            continue
        out.append((node, text))
    return out


def extract_time(block: htmlsel.Node):
    for _, text in _leaf_texts(block):
        if DATE_RE.search(text):
            continue
        if TIME_RE.search(text):
            # 잘라낸 부분이 아니라 원문 전체를 넘긴다.
            # "오후 1:20" 에서 시각만 떼면 오전/오후가 사라져 12시간이 틀어진다.
            t = parse_time(text)
            if t:
                return t
    # 엄격한 패턴으로 못 찾으면 '7시' 형태까지 본다
    for _, text in _leaf_texts(block):
        if TIME_LOOSE_RE.search(text) and not DATE_RE.search(text):
            t = parse_time(text)
            if t:
                return t
    return None


def extract_price(block: htmlsel.Node) -> int:
    """덩어리 안의 금액. 여러 개면 가장 큰 값을 그린피로 본다.

    할인 전/후 가격이 같이 적힌 경우가 많은데, 작은 쪽이 카트비나 캐디피인
    경우도 있어 판단이 애매하다. 가장 큰 값을 쓰고 원문을 함께 남긴다.
    """
    prices = []
    for _, text in _leaf_texts(block):
        for m in PRICE_RE.finditer(text):
            v = parse_price(m.group(0))
            # 그린피로 볼 수 있는 범위 밖은 버린다 (전화번호, 회원번호 등)
            if 10_000 <= v <= 2_000_000:
                prices.append(v)
    return max(prices) if prices else -1


def extract_slots(block: htmlsel.Node) -> Optional[int]:
    for _, text in _leaf_texts(block):
        m = SLOT_RE.search(text)
        if m:
            raw = m.group(1) or m.group(2)
            if raw and raw.isdigit():
                return int(raw)
    return None


def extract_date(block: htmlsel.Node):
    for _, text in _leaf_texts(block):
        if DATE_RE.search(text):
            d = parse_date(text)
            if d:
                return d
    return None


# 골프장 이름다운 텍스트인지 가리는 단서
COURSE_HINT = re.compile(r"(CC|GC|컨트리|골프|클럽|리조트|밸리|파크|힐스|뷰)", re.IGNORECASE)
# 이름 칸이 아닌 것들
NOT_A_NAME = re.compile(
    r"^(예약|부킹|신청|선택|가능|마감|잔여|남은|조인|카트|캐디|식사|퍼블릭|회원|비회원"
    r"|아웃|인|OUT|IN|18홀|9홀|무료|포함|불포함|\d+)$", re.IGNORECASE)


def extract_course_name(block: htmlsel.Node) -> str:
    """덩어리 안에서 골프장 이름을 찾는다.

    여러 골프장을 모아 보여 주는 플랫폼에서는 행마다 이름이 다르므로
    반드시 각 행에서 뽑아야 한다.

    CC/컨트리클럽 같은 단서가 있는 텍스트를 먼저 보고, 없으면 시각·금액·상태가
    아닌 텍스트 중 가장 그럴듯한 것을 고른다.
    """
    candidates: list[tuple[int, str]] = []

    for _, text in _leaf_texts(block):
        t = text.strip()
        if not t or len(t) > 40:
            continue
        if TIME_RE.search(t) or PRICE_RE.search(t) or DATE_RE.search(t):
            continue
        if NOT_A_NAME.match(t.replace(" ", "")):
            continue
        if any(w in t.lower() for w in UNAVAILABLE_WORDS):
            continue
        if any(w in t for w in AVAILABLE_WORDS) and len(t) <= 6:
            continue
        if t.replace(" ", "").isdigit():
            continue

        score = 0
        if COURSE_HINT.search(t):
            score += 10
        if 3 <= len(t) <= 20:
            score += 3
        # 한글이 섞여 있으면 이름일 가능성이 높다
        if re.search(r"[가-힣]", t):
            score += 2
        candidates.append((score, t))

    if not candidates:
        return ""
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[0][1]


def extract_link(block: htmlsel.Node, base_url: str = "") -> str:
    import urllib.parse
    link = block.select_one("a[href]")
    if link is None:
        return ""
    href = link.get("href", "")
    if not href or href.startswith(("javascript:", "#")):
        return ""
    return urllib.parse.urljoin(base_url, href) if base_url else href


def is_unavailable(block: htmlsel.Node) -> bool:
    text = block.text.lower()
    return any(w in text for w in UNAVAILABLE_WORDS)


# ---------------------------------------------------------------------------
# 로그인 벽 감지
# ---------------------------------------------------------------------------

LOGIN_WALL_PATTERNS = [
    r"로그인\s*(?:이|후|을)?\s*(?:하[셔시]|필요|해야|후에)",
    r"회원\s*(?:만|전용|님만)",
    r"로그인\s*페이지로",
    r"세션이?\s*만료",
    r"권한이?\s*없",
    r"please\s+log\s*in",
]


def detect_login_wall(html: str, root: Optional[htmlsel.Node] = None) -> bool:
    """로그인해야 목록이 보이는 페이지인지 짐작한다.

    확실하게 알 수는 없다. 로그인 폼이 있으면서 시각이 하나도 없는 경우를
    로그인 벽으로 본다. 헤더에 로그인 링크만 있는 정상 페이지와 구분하기
    위해 비밀번호 입력칸의 존재를 함께 본다.
    """
    root = root or htmlsel.parse(html)

    for pat in LOGIN_WALL_PATTERNS:
        if re.search(pat, html, re.IGNORECASE):
            if not TIME_RE.search(root.text):
                return True

    has_password = bool(root.select('input[type="password"]'))
    has_time = bool(TIME_RE.search(root.text))
    return has_password and not has_time


# ---------------------------------------------------------------------------
# 자동 추출
# ---------------------------------------------------------------------------


def auto_extract(
    html: str,
    *,
    course_name: str = "",
    source_id: str,
    play_date: Optional[date] = None,
    base_url: str = "",
    min_confidence: float = 0.35,
    detect_names: Optional[bool] = None,
) -> ExtractResult:
    """페이지에서 티타임을 자동으로 뽑는다. 설정이 필요 없다.

    course_name 을 주면 그 골프장의 페이지로 보고 모든 티타임에 같은 이름을 붙인다
    (개별 골프장 홈페이지에는 자기 이름이 안 적혀 있는 경우가 많다).

    주지 않으면 여러 골프장을 모아 놓은 목록으로 보고 행마다 이름을 찾는다
    (플랫폼 예약 사이트가 이 경우다).
    """
    if detect_names is None:
        detect_names = not course_name
    result = ExtractResult()
    root = htmlsel.parse(html)

    if detect_login_wall(html, root):
        result.needs_login = True
        result.reason = "로그인해야 목록이 보이는 페이지로 보입니다"
        return result

    blocks = find_repeating_blocks(root)
    if not blocks:
        result.reason = "반복되는 목록 구조를 찾지 못했습니다 (자바스크립트로 그려지는 화면일 수 있음)"
        return result

    sig, nodes = blocks[0]
    raw_score = block_score(nodes)
    # 점수를 0~1로 눌러 담는다. 5점 이상이면 확신한다고 본다.
    result.confidence = min(raw_score / 5.0, 1.0)
    result.block_selector = sig
    result.block_count = len(nodes)

    if result.confidence < min_confidence:
        result.reason = (
            f"목록 후보는 찾았지만({sig}, {len(nodes)}개) "
            f"시각·금액이 충분히 보이지 않습니다"
        )
        return result

    tee_times: list[TeeTime] = []
    skipped_unavailable = 0

    for block in nodes:
        if is_unavailable(block):
            skipped_unavailable += 1
            continue

        t = extract_time(block)
        if t is None:
            continue

        d = extract_date(block) or play_date
        if d is None:
            continue

        name = extract_course_name(block) if detect_names else course_name
        if not name:
            name = course_name        # 행에서 못 찾으면 호출자가 준 이름으로
        if not name:
            continue                  # 이름 없는 티타임은 쓸모가 없다

        tee_times.append(
            TeeTime(
                course_name=name,
                play_date=d,
                tee_time=t,
                green_fee=extract_price(block),
                source=source_id,
                booking_url=extract_link(block, base_url),
                slots=extract_slots(block),
                hole_info="",
                raw={"block_text": block.text[:200]},
            )
        )

    result.tee_times = tee_times
    parts = [f"{sig} 구조에서 {len(nodes)}개 항목 중 {len(tee_times)}건 추출"]
    if skipped_unavailable:
        parts.append(f"마감 {skipped_unavailable}건 제외")
    priced = sum(1 for t in tee_times if t.green_fee >= 0)
    parts.append(f"가격 확인 {priced}건")
    result.reason = ", ".join(parts)
    return result


def auto_extract_json(
    data: Any,
    *,
    course_name: str = "",
    source_id: str,
    play_date: Optional[date] = None,
    base_url: str = "",
) -> ExtractResult:
    """JSON 응답에서 티타임을 자동으로 뽑는다.

    딕셔너리 배열을 찾고, 그 안에서 시각처럼 생긴 값과 금액처럼 생긴 값을
    가진 키를 골라낸다.
    """
    result = ExtractResult()
    arrays = _find_record_arrays(data)
    if not arrays:
        result.reason = "레코드 배열을 찾지 못했습니다"
        return result

    best: Optional[tuple[float, str, list, dict]] = None
    for path, arr in arrays:
        keys = _guess_json_keys(arr)
        if not keys.get("tee_time"):
            continue
        score = 1.0 + (0.5 if keys.get("green_fee") else 0) + min(len(arr), 40) / 40.0
        if best is None or score > best[0]:
            best = (score, path, arr, keys)

    if best is None:
        result.reason = "배열은 찾았지만 시각으로 볼 수 있는 값이 없습니다"
        return result

    score, path, arr, keys = best
    result.confidence = min(score / 2.5, 1.0)
    result.block_selector = path
    result.block_count = len(arr)

    tee_times = []
    for rec in arr:
        t = parse_time(rec.get(keys["tee_time"]))
        if t is None:
            continue
        d = parse_date(rec.get(keys.get("play_date", ""))) or play_date
        if d is None:
            continue
        fee = parse_price(rec.get(keys["green_fee"])) if keys.get("green_fee") else -1
        text = " ".join(str(v) for v in rec.values() if v is not None).lower()
        if any(w in text for w in UNAVAILABLE_WORDS):
            continue
        name = course_name
        if not name and keys.get("course_name"):
            name = str(rec.get(keys["course_name"]) or "").strip()
        if not name:
            continue

        tee_times.append(
            TeeTime(
                course_name=name,
                play_date=d,
                tee_time=t,
                green_fee=fee,
                source=source_id,
                booking_url=base_url,
                slots=None,
                raw={},
            )
        )

    result.tee_times = tee_times
    result.reason = f"{path} 배열 {len(arr)}건 중 {len(tee_times)}건 추출"
    return result


def _find_record_arrays(obj, path="", out=None, depth=0):
    if out is None:
        out = []
    if depth > 8:
        return out
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict):
            out.append((path or "(최상위)", obj))
        for i, v in enumerate(obj[:1]):
            _find_record_arrays(v, f"{path}.{i}" if path else str(i), out, depth + 1)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _find_record_arrays(v, f"{path}.{k}" if path else k, out, depth + 1)
    return out


def _guess_json_keys(arr: list[dict]) -> dict[str, str]:
    """레코드 배열에서 시각·금액·날짜에 해당하는 키를 골라낸다."""
    sample = arr[: min(5, len(arr))]
    keys: dict[str, str] = {}

    for k in sample[0].keys():
        values = [rec.get(k) for rec in sample if rec.get(k) is not None]
        if not values:
            continue
        texts = [str(v) for v in values]

        if "tee_time" not in keys:
            # 07:12 형태이거나 0712 같은 4자리 숫자가 모두 유효한 시각이면 시각으로 본다
            if all(TIME_RE.search(t) for t in texts):
                keys["tee_time"] = k
            elif all(t.isdigit() and len(t) == 4 and int(t[:2]) < 24 and int(t[2:]) < 60
                     for t in texts):
                keys["tee_time"] = k

        if "green_fee" not in keys:
            nums = []
            for v in values:
                n = parse_price(v)
                if n > 0:
                    nums.append(n)
            if len(nums) == len(values) and all(10_000 <= n <= 2_000_000 for n in nums):
                keys["green_fee"] = k

        if "play_date" not in keys and all(DATE_RE.search(t) or
                                           (t.isdigit() and len(t) == 8) for t in texts):
            keys["play_date"] = k

        # 골프장 이름: 값마다 다르고, 한글이 섞인 짧은 문자열
        if "course_name" not in keys and all(isinstance(v, str) for v in values):
            if all(2 <= len(t) <= 30 for t in texts) and any(
                    re.search(r"[가-힣]", t) for t in texts):
                if any(COURSE_HINT.search(t) for t in texts) or len(set(texts)) > 1:
                    keys["course_name"] = k

    return keys
