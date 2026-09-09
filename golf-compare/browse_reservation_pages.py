#!/usr/bin/env python3
"""
실제 예약 캘린더에 들어가서, 지정한 날짜에 뜨는 실제 티타임/가격을 확인합니다.

browse_fee_pages.py(공지사항성 요금 안내 페이지)와 다르게, "예약" 메뉴를 찾아
들어가서 실제 날짜를 선택하고 그 날 뜨는 진짜 예약가를 읽으려고 시도합니다.
다만 예약 시스템은 사이트마다 달력 UI, 로그인 시점, 코스 선택 순서가 전부
달라서, 모든 사이트에서 성공한다고 보장할 수 없습니다. 안 되는 곳은
결과 파일에 그 이유(날짜 선택 실패 등)가 남습니다 — 그런 곳은 직접 열어서
확인하시는 게 빠를 수 있습니다.

로그인은 browse_fee_pages.py와 마찬가지로, 뜬 브라우저 창에서 사용자가 직접
하고(비밀번호는 Claude/스크립트에 전달되지 않음), 세션은 로컬 폴더
(golf-compare/.browser_profile/ — browse_fee_pages.py와 공유)에 저장됩니다.

준비:
    pip3 install playwright
    playwright install chromium      # 최초 1회

사용법:
    python3 browse_reservation_pages.py --date 2026-09-12
    python3 browse_reservation_pages.py --date 2026-09-12 --max-price 150000
    python3 browse_reservation_pages.py --date 2026-09-12 --only "88컨트리클럽,신라CC"
    python3 browse_reservation_pages.py --date 2026-09-12 --headless   # 이미 로그인 끝난 경우

--max-price를 주면, 화면에서 찾은 숫자 중 그 값 이하인 게 하나라도 있는 줄만
결과에 남깁니다(예: 정상가/할인가 두 값 중 할인가가 기준 이하면 포함).
"""

import argparse
import json
import os
import re
import sys
import time

WON_AMOUNT_RE = re.compile(r"[0-9][0-9,]{3,}\s*원")
PRICE_NUM_RE = re.compile(r"\b\d{1,3}(?:,\d{3})+\b")  # "270,000"처럼 "원" 없이 콤마로 끊긴 금액도 잡기 위함
TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d\b")
LOGIN_WALL_HINTS = ("로그인이 필요", "로그인 후", "로그인해주세요", "회원 전용", "먼저 로그인", "Login Required")
LOGIN_URL_HINTS = ("login", "signin", "member/login")
RESERVATION_LINK_KEYWORDS = ["실시간예약", "온라인예약", "티타임예약", "예약하기", "예약안내", "예약", "Booking", "Reservation"]
SEARCH_BUTTON_KEYWORDS = ["조회하기", "예약조회", "조회", "검색", "확인", "Search"]
UNAVAILABLE_HINTS = ("마감", "예약완료", "매진", "예약불가", "선택불가", "종료", "부킹마감", "예약중", "대기중", "보류")
DISABLED_SELECTOR = "[disabled], .disabled, .soldout, .sold-out, .closed, button:disabled"


def looks_like_login_wall(page, text):
    if any(hint in text for hint in LOGIN_WALL_HINTS):
        return True
    url_lower = page.url.lower()
    return any(hint in url_lower for hint in LOGIN_URL_HINTS)


def try_click_search_button(page):
    """날짜만 고르고 바로 시간표가 안 뜨는 사이트를 위해, 조회/검색 버튼을 눌러본다."""
    for kw in SEARCH_BUTTON_KEYWORDS:
        try:
            locator = page.get_by_role("button", name=kw, exact=False).first
            if locator.count() > 0:
                locator.click(timeout=2000)
                page.wait_for_load_state("networkidle", timeout=8000)
                return True
        except Exception:
            continue
    return False

PROFILE_DIR = os.path.join(os.path.dirname(__file__), ".browser_profile")
DEBUG_DIR = os.path.join(os.path.dirname(__file__), "debug_pages")

CALENDAR_HINT_SELECTORS = [
    "[class*='calendar' i]", "[id*='calendar' i]",
    "[class*='datepick' i]", "[id*='datepick' i]",
    "[class*='date-picker' i]", "[class*='cal-' i]",
]


def dump_debug_page(page, course_name, max_chars=20000):
    """날짜 선택이 실패한 페이지의 캘린더로 보이는 부분 HTML + 스크린샷을 저장한다.

    Claude가 이 세션에서 사이트를 직접 열어볼 수 없어서, 매번 선택자를 추측해서
    고쳐왔다. 이 함수로 실제 마크업을 파일로 남기면, 그 내용을 대화에 붙여넣어
    받아서 훨씬 정확하게 고칠 수 있다.
    """
    os.makedirs(DEBUG_DIR, exist_ok=True)
    safe_name = re.sub(r"[^\w가-힣-]+", "_", course_name)

    snippets = []
    for sel in CALENDAR_HINT_SELECTORS:
        try:
            loc = page.locator(sel)
            n = min(loc.count(), 3)
            for i in range(n):
                html = loc.nth(i).evaluate("el => el.outerHTML")
                if html and html not in snippets:
                    snippets.append(html)
        except Exception:
            continue
    if not snippets:
        try:
            tables = page.locator("table")
            n = min(tables.count(), 5)
            for i in range(n):
                html = tables.nth(i).evaluate("el => el.outerHTML")
                if html:
                    snippets.append(html)
        except Exception:
            pass

    combined = ("\n\n" + "-" * 40 + "\n\n").join(snippets)[:max_chars]

    # 아이콘 하나만 걸리는 등 결과가 너무 부실하면(캘린더가 아직 안 그려졌거나
    # table/일반적인 class명을 안 쓰는 구조), 스크립트/스타일을 뺀 전체 페이지
    # HTML을 대신 저장한다 — 뭐라도 있어야 실제 구조를 보고 고칠 수 있다.
    if len(combined.strip()) < 300:
        try:
            full_html = page.content()
            full_html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", full_html, flags=re.IGNORECASE | re.DOTALL)
        except Exception:
            full_html = ""
        if full_html.strip():
            combined = (
                f"(캘린더로 추정되는 요소가 부실해 전체 페이지 HTML로 대체함)\n\n"
                + full_html[:max_chars]
            )

    html_path = os.path.join(DEBUG_DIR, f"{safe_name}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(f"URL: {page.url}\n\n")
        f.write(combined or "(캘린더/날짜 관련 요소를 찾지 못함 — table도 없음)")

    png_path = os.path.join(DEBUG_DIR, f"{safe_name}.png")
    try:
        page.screenshot(path=png_path)
    except Exception:
        png_path = None

    return html_path, png_path


def extract_hits(text, max_price=None):
    """금액(원) 또는 시간(HH:MM) 패턴이 있는 줄과 그 앞뒤 줄을 모은다.

    실제 티타임 목록은 보통 "07:12   4인   180,000원"처럼 시간과 가격이
    같이 나오므로, 둘 중 하나만 있어도 후보로 잡아 앞뒤 문맥과 함께 보여준다.
    max_price가 주어지면, 그 줄에서 찾은 금액 중 하나라도 그 값 이하여야
    포함한다(정상가/할인가처럼 여러 금액이 한 줄에 있는 경우 대비).
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out, seen = [], set()
    for idx, line in enumerate(lines):
        nums = [int(n.replace(",", "")) for n in PRICE_NUM_RE.findall(line)]
        if not (WON_AMOUNT_RE.search(line) or TIME_RE.search(line) or nums):
            continue
        if max_price is not None and (not nums or min(nums) > max_price):
            continue
        before = lines[idx - 1] if idx > 0 else ""
        after = lines[idx + 1] if idx + 1 < len(lines) else ""
        entry = f"{before}\n   >> {line}\n   {after}".strip()
        if line not in seen:
            seen.add(line)
            out.append(entry)
    return out


def extract_available_slots(page, max_price=None):
    """<tr> 단위로 훑어서, 시간+가격이 있고 "마감/예약불가" 표시나 비활성화된
    버튼이 없는(=실제로 예약 가능해 보이는) 행만 남긴다.

    기존 extract_hits는 화면 텍스트를 통째로 줄 단위로 훑어서 시간/가격
    패턴만 봤기 때문에, 이미 마감된 시간도 그냥 다 같이 잡혔다(실제로 더크로스비GC
    새벽 18홀에서 예약 불가한 시간이 섞여 나온 문제). 행 단위로 보면 그 행 안에
    "마감" 같은 문구나 disabled 버튼이 있는지 확인할 수 있어 더 정확하다.
    """
    out = []
    try:
        rows = page.locator("tr")
        n = min(rows.count(), 500)
    except Exception:
        return out

    for i in range(n):
        row = rows.nth(i)
        try:
            text = (row.inner_text() or "").strip()
        except Exception:
            continue
        if not text or not TIME_RE.search(text):
            continue
        nums = [int(m.replace(",", "")) for m in PRICE_NUM_RE.findall(text)]
        if not (WON_AMOUNT_RE.search(text) or nums):
            continue
        if any(h in text for h in UNAVAILABLE_HINTS):
            continue
        try:
            if row.locator(DISABLED_SELECTOR).count() > 0:
                continue
        except Exception:
            pass
        if max_price is not None and (not nums or min(nums) > max_price):
            continue
        line = " | ".join(ln.strip() for ln in text.splitlines() if ln.strip())
        if line and line not in out:
            out.append(line)
    return out


EXCLUDE_LINK_HINTS = ("개인정보", "방침", "약관", "정책", "policy", "privacy", "무단수집", "영상정보")


def try_click_keyword_link(page, keywords, timeout=2500):
    """예약 메뉴로 보이는 링크를 클릭한다.

    베어크리크GC에서 "예약"이라는 글자가 들어간 CCTV/개인정보처리방침 링크를
    잘못 클릭해서 엉뚱한 페이지로 간 적이 있어서, 1) 링크/버튼 역할을 가진
    요소를 정확히 일치하는 텍스트로 먼저 찾고, 2) 방침/약관 관련 문구가 섞인
    후보는 건너뛰도록 했다.
    """
    for kw in keywords:
        for role in ("link", "button"):
            try:
                locator = page.get_by_role(role, name=kw, exact=True).first
                if locator.count() > 0:
                    text = locator.inner_text() or ""
                    if any(h in text for h in EXCLUDE_LINK_HINTS):
                        continue
                    locator.click(timeout=timeout)
                    page.wait_for_load_state("networkidle", timeout=10000)
                    return True
            except Exception:
                continue

    for kw in keywords:
        try:
            candidates = page.get_by_text(kw, exact=False)
            n = min(candidates.count(), 10)
            for i in range(n):
                loc = candidates.nth(i)
                text = loc.inner_text() or ""
                if any(h in text for h in EXCLUDE_LINK_HINTS):
                    continue
                loc.click(timeout=timeout)
                page.wait_for_load_state("networkidle", timeout=10000)
                return True
        except Exception:
            continue
    return False


NEXT_MONTH_KEYWORDS = ["다음달", "다음 달", "다음월", "Next month", "Next", ">", "»", "▶", "›"]
DISABLED_CLASS_HINTS = ("disabled", "past", "inactive", "unavailable", "off")


def _find_day_cell(page, day):
    """day(문자열 숫자)와 셀 텍스트의 맨 앞 숫자가 정확히 일치하는, 비활성화되지 않은 셀을 찾는다.

    ":has-text('20')"는 부분 일치라 "18홀", "2인" 같은 것도 걸리므로, 후보를 넓게 모은 뒤
    각 셀 텍스트의 맨 앞 숫자 토큰만 비교한다(요일 표기가 같이 있는 "20 토" 같은 셀도 대응).
    """
    for selector in ["td", "button", "a", "div", "span"]:
        try:
            candidates = page.locator(f"{selector}:has-text('{day}')")
            n = min(candidates.count(), 40)
            for i in range(n):
                cell = candidates.nth(i)
                raw = (cell.inner_text() or "").strip()
                m = re.match(r"\d+", raw)
                if not m or m.group(0) != day:
                    continue
                cls = (cell.get_attribute("class") or "").lower()
                if any(h in cls for h in DISABLED_CLASS_HINTS):
                    continue
                return cell
        except Exception:
            continue
    return None


def _find_by_aria_label(page, target_date):
    y, mo, d = target_date.split("-")
    for text in (f"{int(mo)}월 {int(d)}일", f"{y}-{mo}-{d}", f"{y}년 {int(mo)}월 {int(d)}일"):
        try:
            loc = page.locator(f"[aria-label*='{text}']").first
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


def _click_cell(page, cell, method_name):
    try:
        cell.click(timeout=2000)
        page.wait_for_timeout(1500)
        return method_name
    except Exception:
        return None


def try_select_date(page, target_date):
    """target_date: 'YYYY-MM-DD'. 성공하면 방식 이름을, 실패하면 None을 반환."""
    # 1) 네이티브 <input type="date">
    try:
        inp = page.locator('input[type="date"]').first
        if inp.count() > 0:
            inp.fill(target_date)
            inp.press("Enter")
            page.wait_for_timeout(1500)
            return "date-input"
    except Exception:
        pass

    day = str(int(target_date.split("-")[2]))

    # 1.5) 달력이 클릭 전엔 안 보이고, 날짜 입력칸/버튼을 눌러야 펼쳐지는 사이트 대응
    if not _find_day_cell(page, day):
        for kw in ["날짜 선택", "날짜선택", "예약일자", "체크인", "달력"]:
            try:
                trigger = page.get_by_text(kw, exact=False).first
                if trigger.count() > 0:
                    trigger.click(timeout=1500)
                    page.wait_for_timeout(800)
                    break
            except Exception:
                continue

    # 2) 달력 위젯에서 날짜 숫자 셀 클릭 (현재 보이는 화면 안에 있을 때)
    cell = _find_day_cell(page, day)
    if cell:
        result = _click_cell(page, cell, "calendar-cell")
        if result:
            return result

    # 3) aria-label에 날짜 전체가 박혀 있는 접근성 캘린더 대응
    cell = _find_by_aria_label(page, target_date)
    if cell:
        result = _click_cell(page, cell, "calendar-aria-label")
        if result:
            return result

    # 4) 원하는 날짜가 기본 화면(이번 달)에 없으면, "다음달" 버튼을 눌러가며 최대 3번 더 찾아본다.
    for attempt in range(1, 4):
        clicked_next = False
        for kw in NEXT_MONTH_KEYWORDS:
            try:
                nxt = page.get_by_text(kw, exact=False).first
                if nxt.count() > 0:
                    nxt.click(timeout=1500)
                    page.wait_for_timeout(1000)
                    clicked_next = True
                    break
            except Exception:
                continue
        if not clicked_next:
            break
        cell = _find_day_cell(page, day)
        if cell:
            result = _click_cell(page, cell, f"calendar-cell(다음달x{attempt})")
            if result:
                return result

    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default=os.path.join(os.path.dirname(__file__), "courses_seed.json"))
    parser.add_argument("--output", default="reservation_dump.txt")
    parser.add_argument("--date", required=True, help="확인할 날짜 (YYYY-MM-DD, 예: 2026-09-12)")
    parser.add_argument("--only", default=None, help="쉼표로 구분한 골프장 이름 목록만 실행")
    parser.add_argument("--exclude", default=None, help="쉼표로 구분한 골프장 이름을 제외하고 실행")
    parser.add_argument("--max-price", type=int, default=None, help="이 금액(원) 이하가 있는 줄만 결과에 남김")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
        print(f"--date 형식이 잘못됐습니다: '{args.date}' (예: 2026-09-12 처럼 숫자와 하이픈만)", file=sys.stderr)
        sys.exit(1)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright가 설치되어 있지 않습니다: pip3 install playwright && playwright install chromium", file=sys.stderr)
        sys.exit(1)

    with open(args.input, encoding="utf-8") as f:
        courses = json.load(f)
    if args.only:
        wanted = {n.strip() for n in args.only.split(",")}
        courses = [c for c in courses if c["name"] in wanted]
    if args.exclude:
        excluded = {n.strip() for n in args.exclude.split(",")}
        courses = [c for c in courses if c["name"] not in excluded]

    out_lines = []
    summary = []  # (골프장명, 발견 개수) — 조건에 맞는 항목이 있던 곳만
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=args.headless,
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            args=["--disable-blink-features=AutomationControlled"],
        )
        # 일부 사이트(특히 로그인/본인인증 팝업)가 자동화 브라우저를 감지해 창을
        # 강제로 닫아버리는 경우가 있어, navigator.webdriver 흔적을 숨긴다.
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        # Playwright는 alert()/confirm() 같은 브라우저 네이티브 팝업을 기본적으로
        # 사용자가 보기도 전에 자동으로 닫아버린다(핸들러를 등록 안 하면 즉시 dismiss).
        # "로그인 실패", "회원가입이 필요합니다" 같은 안내가 화면에 뜨자마자
        # 사라지는 것처럼 보이는 원인이 이것 — 최소한 메시지 내용을 터미널에 출력해서
        # 무슨 안내였는지는 알 수 있게 한다.
        def _on_dialog(dialog):
            print(f"  [팝업 메시지] ({dialog.type}) {dialog.message}")
            dialog.dismiss()

        def _attach(p):
            p.on("dialog", _on_dialog)

            def _on_close():
                try:
                    print(f"  [창 닫힘] {p.url}")
                except Exception:
                    print("  [창 닫힘]")
            p.on("close", _on_close)

        # 로그인 등이 새 창(진짜 팝업 윈도우)으로 열리는 사이트도 있어서, 그 창에도
        # 똑같이 핸들러가 붙도록 새 페이지가 열릴 때마다 등록한다.
        def _on_new_page(new_page):
            print(f"  [새 창 열림] {new_page.url}")
            _attach(new_page)

        context.on("page", _on_new_page)
        _attach(page)

        for i, course in enumerate(courses, 1):
            name = course["name"]
            url = course.get("homepage")
            out_lines.append("=" * 60)
            out_lines.append(f"[{i}/{len(courses)}] {name}  ({course.get('region', '')})")
            print(f"\n[{i}/{len(courses)}] {name} 방문 중...")

            if not url:
                out_lines.append("-> 홈페이지 URL이 없어 건너뜀.")
                continue

            try:
                page.goto(url, wait_until="load", timeout=20000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass

                try_click_keyword_link(page, RESERVATION_LINK_KEYWORDS)

                target_url = page.url
                text = page.inner_text("body")
                if looks_like_login_wall(page, text):
                    print(f"  -> '{name}' 예약 페이지가 로그인을 요구합니다.")
                    print("     뜬 창에서 로그인 후, 여기로 돌아와 Enter를 눌러주세요.")
                    input("     계속하려면 Enter >> ")
                    try:
                        page.goto(target_url, wait_until="load", timeout=15000)
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass
                    try_click_keyword_link(page, RESERVATION_LINK_KEYWORDS)

                method = try_select_date(page, args.date)
                if not method:
                    html_path, png_path = dump_debug_page(page, name)
                    out_lines.append(f"-> 날짜 선택 실패 (현재 화면: {page.url}). 이 사이트는 직접 열어서 확인하는 게 빠를 수 있습니다.")
                    out_lines.append(f"   디버그 정보 저장: {html_path}" + (f", {png_path}" if png_path else ""))
                    print(f"  -> 날짜 선택 실패 (디버그: {html_path})")
                    out_lines.append("")
                    continue

                try_click_search_button(page)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass

                price_note = f", {args.max_price:,}원 이하만" if args.max_price is not None else ""

                # 1) 행(<tr>) 단위로 "마감/예약불가" 표시나 비활성화 버튼이 없는,
                #    실제로 예약 가능해 보이는 시간만 우선 찾는다.
                avail_hits = extract_available_slots(page, max_price=args.max_price)
                if avail_hits:
                    out_lines.append(
                        f"-> {args.date} 화면({page.url}, 날짜선택방식: {method}{price_note}) "
                        f"예약 가능해 보이는 시간 {len(avail_hits)}개:"
                    )
                    for h in avail_hits[:40]:
                        out_lines.append("   " + h)
                    print(f"  -> 예약 가능 {len(avail_hits)}개")
                    summary.append((name, len(avail_hits)))
                    out_lines.append("")
                    time.sleep(0.5)
                    continue

                # 2) 표가 <tr> 구조가 아니라 행 단위 판정이 안 되는 사이트를 위한 대체 경로.
                #    이 경로는 마감/예약불가 여부를 확인 못 하므로 그 사실을 명시한다.
                text = page.inner_text("body")
                hits = extract_hits(text, max_price=args.max_price)
                if hits:
                    out_lines.append(
                        f"-> {args.date} 화면({page.url}, 날짜선택방식: {method}{price_note})에서 발견 {len(hits)}개 "
                        f"(⚠ 마감/예약불가 여부 확인 안 됨 — 표가 <tr> 구조가 아니라서 행 단위 판정 실패, 직접 확인 필요):"
                    )
                    for h in hits[:40]:
                        out_lines.append("   " + h.replace("\n", "\n   "))
                    print(f"  -> {len(hits)}개 발견(마감 여부 미확인)")
                    summary.append((name + " (마감 여부 미확인)", len(hits)))
                elif args.max_price is not None:
                    out_lines.append(f"-> 날짜는 선택({method})했지만 {args.max_price:,}원 이하로 보이는 항목을 찾지 못함 (화면: {page.url}).")
                    print("  -> 조건에 맞는 항목 없음")
                else:
                    out_lines.append(f"-> 날짜는 선택({method})했지만 시간/가격으로 보이는 내용을 찾지 못함 (화면: {page.url}).")
                    print("  -> 후보 없음")
            except Exception as e:  # noqa: BLE001
                out_lines.append(f"-> 오류: {e}")
                print(f"  -> 오류: {e}")

            out_lines.append("")
            time.sleep(0.5)

        context.close()

    header = [f"조회 날짜: {args.date}"]
    if args.max_price is not None:
        header.append(f"가격 조건: {args.max_price:,}원 이하")
    header.append("")
    header.append("[요약] 조건에 맞는 항목이 발견된 골프장:")
    if summary:
        for name, count in summary:
            header.append(f"  - {name}: {count}개")
    else:
        header.append("  (없음 — 대부분 로그인/날짜선택 실패이거나 조건에 맞는 항목이 없었습니다. 아래 상세 내용 참고)")
    header.append("")

    with open(args.output, "w", encoding="utf-8") as f:
        f.write("\n".join(header + out_lines))

    print(f"\n완료: {args.output} 에 저장했습니다. 이 파일 내용을 Claude 대화에 붙여넣어 주세요.")


if __name__ == "__main__":
    main()
