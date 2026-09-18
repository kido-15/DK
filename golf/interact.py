"""브라우저 화면을 대신 조작한다.

예약 사이트는 날짜를 눌러야 목록이 바뀌는 경우가 많다. 사이트마다 날짜가
달력이기도 하고, 가로로 늘어선 탭이기도 하고, 드롭다운이기도 하다.

특정 사이트의 구조를 미리 적어 둘 수 없으므로, 날짜를 가리키는 것처럼 보이는
요소를 여러 방식으로 찾아 눌러 보고, **목록이 실제로 바뀌었는지** 확인한다.
바뀌지 않으면 다음 후보를 시도한다. 확인 없이 눌렀다고 성공으로 치면,
엉뚱한 날짜의 목록을 그 날짜 것으로 저장하게 된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

# 목록이 들어 있을 만한 영역. 클릭 뒤 이 부분이 달라졌는지로 성공을 판단한다.
LIST_HINTS = [
    "table tbody", "[class*=list]", "[class*=result]", "[id*=list]",
    "[id*=result]", "[class*=tee]", "[class*=time]", "main", "body",
]

# 날짜를 담고 있을 법한 속성들
DATE_ATTRS = ["data-date", "data-day", "data-value", "data-ymd", "data-time",
              "data-playdate", "value", "id", "rel", "title", "aria-label"]

# 값 안에 날짜가 "섞여" 있는 속성들. 값 전체가 날짜인 DATE_ATTRS 와 달리 부분 일치로 찾는다.
#
# 골팡이 이런 형태다:
#     <li onclick="selectQuick('1','2026-09-20','16')">09/20(일)</li>
#
# data-date 같은 속성은 없고 날짜가 함수 인자로만 들어 있어서, 정확히 일치하는
# 속성만 찾으면 이런 사이트의 날짜는 영영 못 고른다.
DATE_IN_ATTRS = ["onclick", "href", "data-params", "data-args", "onchange", "ng-click"]


@dataclass
class ClickResult:
    clicked: bool = False
    how: str = ""            # 무엇을 눌렀는지 (사람이 읽을 수 있게)
    changed: bool = False    # 누른 뒤 목록이 바뀌었는지
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.clicked and self.changed


def date_tokens(target: date) -> list[str]:
    """그 날짜를 가리킬 법한 표기들. 구체적인 것부터."""
    return [
        f"{target:%Y-%m-%d}",
        f"{target:%Y%m%d}",
        f"{target:%Y.%m.%d}",
        f"{target:%Y/%m/%d}",
        f"{target.month}월 {target.day}일",
        f"{target:%m.%d}",
        f"{target:%m/%d}",
        f"{target.month}/{target.day}",
        f"{target.month}.{target.day}",
    ]


def _list_fingerprint(page) -> str:
    """목록 부분이 어떻게 생겼는지 짧게 요약한다. 클릭 전후를 비교하는 데 쓴다."""
    for selector in LIST_HINTS:
        try:
            loc = page.locator(selector).first
            if loc.count() == 0:
                continue
            text = loc.inner_text(timeout=1500)
        except Exception:
            continue
        if text and len(text.strip()) > 40:
            body = re.sub(r"\s+", " ", text)[:1200]
            return f"{selector}:{len(body)}:{hash(body)}"
    try:
        body = re.sub(r"\s+", " ", page.inner_text("body", timeout=2000))[:1500]
        return f"body:{len(body)}:{hash(body)}"
    except Exception:
        return ""


def _wait_settle(page, ms: int = 1800) -> None:
    """클릭 뒤 목록이 채워질 시간을 준다."""
    try:
        page.wait_for_load_state("networkidle", timeout=ms)
    except Exception:
        pass
    page.wait_for_timeout(600)


def _click_score(el) -> int:
    """누르면 반응할 가능성이 높은 요소일수록 높은 점수.

    같은 글자가 여러 요소에 걸린다. 예를 들어 "9/21" 은 감싸고 있는 <li> 와
    실제 클릭 핸들러가 달린 <a> 양쪽에 걸리는데, <li> 를 누르면 아무 일도
    일어나지 않는다. 안쪽의 진짜 누를 수 있는 것을 먼저 시도해야 한다.
    """
    try:
        tag = (el.evaluate("e => e.tagName") or "").lower()
    except Exception:
        return 0
    score = 0
    try:
        if el.get_attribute("onclick"):
            score += 10
        if el.get_attribute("href"):
            score += 4
    except Exception:
        pass
    score += {"a": 8, "button": 8, "input": 6, "label": 3,
              "td": 2, "th": 2, "span": 2, "li": 1, "div": 1}.get(tag, 0)
    return score


def _try_click(page, locator, label: str, before: str,
               max_tries: int = 4) -> ClickResult:
    """그 글자에 걸린 요소들을 눌러 보고 목록이 바뀌었는지 확인한다.

    하나만 눌러 보고 마는 대신, 누를 만한 순서대로 몇 개를 시도한다.
    """
    try:
        count = locator.count()
    except Exception as exc:
        return ClickResult(reason=f"{label}: 찾지 못함 ({str(exc)[:40]})")
    if count == 0:
        return ClickResult(reason=f"{label}: 없음")

    candidates = []
    for i in range(min(count, 8)):
        el = locator.nth(i)
        try:
            if not el.is_visible(timeout=800):
                continue
        except Exception:
            continue
        candidates.append((_click_score(el), i, el))

    if not candidates:
        return ClickResult(reason=f"{label}: 보이지 않음")
    candidates.sort(key=lambda c: c[0], reverse=True)

    last = ClickResult(reason=f"{label}: 눌러도 목록이 바뀌지 않았습니다")
    for _, _, el in candidates[:max_tries]:
        try:
            el.scroll_into_view_if_needed(timeout=1500)
            el.click(timeout=3000)
        except Exception as exc:
            last = ClickResult(reason=f"{label}: 누르지 못함 ({str(exc)[:40]})")
            continue

        _wait_settle(page)
        after = _list_fingerprint(page)
        if after != before:
            return ClickResult(clicked=True, how=label, changed=True,
                               reason=f"{label} 를 눌렀습니다")
        last = ClickResult(clicked=True, how=label, changed=False,
                           reason=f"{label}: 눌렀지만 목록이 그대로입니다")
    return last


def select_date(page, target: date, *, verbose: bool = False) -> ClickResult:
    """화면에서 그 날짜를 골라 준다.

    속성에 날짜가 박힌 요소를 먼저 찾고, 없으면 날짜 표기 텍스트를 찾고,
    마지막으로 달력처럼 보이는 곳의 날짜 숫자를 찾는다.
    누른 뒤 목록이 바뀌지 않으면 실패로 보고 다음 후보를 시도한다.
    """
    before = _list_fingerprint(page)
    attempts: list[str] = []

    exact_tokens = (f"{target:%Y-%m-%d}", f"{target:%Y%m%d}", f"{target:%Y.%m.%d}")

    # 1) 속성 값이 곧 날짜인 요소 — 가장 확실하다
    for token in exact_tokens:
        for attr in DATE_ATTRS:
            result = _try_click(page, page.locator(f'[{attr}="{token}"]'),
                                f'{attr}="{token}"', before)
            attempts.append(result.reason)
            if result.ok:
                return result
            if result.clicked:
                before = _list_fingerprint(page)

    # 2) 속성 값 안에 날짜가 섞여 있는 요소
    #    onclick="selectQuick('1','2026-09-20','16')" 같은 형태가 여기에 걸린다
    for token in exact_tokens:
        for attr in DATE_IN_ATTRS:
            result = _try_click(page, page.locator(f'[{attr}*="{token}"]'),
                                f'{attr} 안의 "{token}"', before)
            attempts.append(result.reason)
            if result.ok:
                return result
            if result.clicked:
                before = _list_fingerprint(page)

    # 3) 날짜 표기 텍스트를 가진 누를 수 있는 요소
    clickable = "a, button, li, td, th, span[onclick], div[onclick], label"
    for token in date_tokens(target):
        try:
            loc = page.locator(clickable).filter(has_text=re.compile(re.escape(token)))
        except Exception:
            continue
        result = _try_click(page, loc, f'"{token}" 이 적힌 항목', before)
        attempts.append(result.reason)
        if result.ok:
            return result
        if result.clicked:
            before = _list_fingerprint(page)

    # 4) 달력처럼 보이는 곳의 날짜 숫자
    #    그냥 숫자를 누르면 엉뚱한 것을 누를 수 있어, 달력 영역 안에서만 찾는다.
    for scope in ("[class*=calendar]", "[class*=datepicker]", "[class*=date]",
                  "[id*=calendar]", "[class*=day]"):
        try:
            area = page.locator(scope).first
            if area.count() == 0:
                continue
            loc = area.locator("a, button, td, li, span").filter(
                has_text=re.compile(rf"^\s*{target.day}\s*$"))
        except Exception:
            continue
        result = _try_click(page, loc, f"{scope} 안의 {target.day}일", before)
        attempts.append(result.reason)
        if result.ok:
            return result
        if result.clicked:
            before = _list_fingerprint(page)

    # 5) 드롭다운에 날짜가 있는 경우
    for token in (f"{target:%Y%m%d}", f"{target:%Y-%m-%d}"):
        try:
            selects = page.locator("select")
            for i in range(min(selects.count(), 5)):
                sel = selects.nth(i)
                try:
                    sel.select_option(value=token, timeout=1500)
                except Exception:
                    try:
                        sel.select_option(label=f"{target:%Y-%m-%d}", timeout=1500)
                    except Exception:
                        continue
                _wait_settle(page)
                after = _list_fingerprint(page)
                if after != before:
                    return ClickResult(clicked=True, how=f"드롭다운에서 {token} 선택",
                                       changed=True,
                                       reason=f"드롭다운에서 {token} 를 골랐습니다")
                before = after
        except Exception:
            pass

    detail = "; ".join(a for a in attempts if a)[:300]
    return ClickResult(reason=f"{target} 를 고를 방법을 찾지 못했습니다. {detail}")


def click_text(page, text: str) -> ClickResult:
    """화면에서 그 글자가 적힌 것을 눌러 준다 (검색, 조회 등)."""
    before = _list_fingerprint(page)
    loc = page.locator("a, button, input[type=submit], input[type=button], "
                       "span[onclick], div[onclick]").filter(
        has_text=re.compile(re.escape(text)))
    result = _try_click(page, loc, f'"{text}"', before)
    if result.clicked:
        return result
    # 버튼이 value 속성만 가진 경우
    for selector in (f'input[value*="{text}"]', f'button[title*="{text}"]'):
        result = _try_click(page, page.locator(selector), f'"{text}"', before)
        if result.clicked:
            return result
    return result


def click_selector(page, selector: str) -> ClickResult:
    """직접 지정한 요소를 눌러 준다."""
    before = _list_fingerprint(page)
    return _try_click(page, page.locator(selector), selector, before)


def load_more(page, rounds: int = 4) -> int:
    """'더보기' 류를 눌러 목록을 더 불러온다. 누른 횟수를 돌려준다."""
    labels = ["더보기", "더 보기", "더불러오기", "다음", "more", "load more", "See more"]
    clicked = 0
    for _ in range(rounds):
        before = _list_fingerprint(page)
        hit = False
        for label in labels:
            try:
                loc = page.locator("a, button").filter(
                    has_text=re.compile(re.escape(label), re.IGNORECASE))
                if loc.count() == 0:
                    continue
                el = loc.first
                if not el.is_visible(timeout=800):
                    continue
                el.click(timeout=2500)
                _wait_settle(page, 1500)
                hit = True
                clicked += 1
                break
            except Exception:
                continue
        if not hit or _list_fingerprint(page) == before:
            break
    return clicked


def scroll_through(page, times: int = 4, pause_ms: int = 700) -> None:
    """목록이 스크롤로 채워지는 화면을 위해 아래로 내린다."""
    for _ in range(times):
        try:
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(pause_ms)
        except Exception:
            return


def describe_date_controls(page, limit: int = 12) -> list[str]:
    """날짜를 고르는 부분이 어떻게 생겼는지 알려 준다.

    자동으로 날짜를 못 골랐을 때, 사람이 무엇을 눌러야 할지 짐작할 수 있게
    화면의 날짜 관련 요소를 추려 보여 준다.
    """
    out: list[str] = []
    try:
        for attr in ("data-date", "data-day", "data-ymd", "data-value"):
            loc = page.locator(f"[{attr}]")
            for i in range(min(loc.count(), 4)):
                el = loc.nth(i)
                val = el.get_attribute(attr) or ""
                text = (el.inner_text(timeout=800) or "").strip()[:20]
                out.append(f'{attr}="{val}" 글자="{text}"')
                if len(out) >= limit:
                    return out
    except Exception:
        pass

    try:
        loc = page.locator("select")
        for i in range(min(loc.count(), 4)):
            sel = loc.nth(i)
            name = sel.get_attribute("name") or sel.get_attribute("id") or "?"
            opts = sel.locator("option")
            samples = []
            for j in range(min(opts.count(), 3)):
                samples.append((opts.nth(j).inner_text(timeout=600) or "").strip())
            out.append(f"드롭다운 {name}: {', '.join(s for s in samples if s)}")
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out
