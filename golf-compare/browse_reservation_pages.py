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
    python3 browse_reservation_pages.py --date 2026-09-12 --only "88컨트리클럽,신라CC"
    python3 browse_reservation_pages.py --date 2026-09-12 --headless   # 이미 로그인 끝난 경우
"""

import argparse
import json
import os
import re
import sys
import time

WON_AMOUNT_RE = re.compile(r"[0-9][0-9,]{3,}\s*원")
TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d\b")
LOGIN_WALL_HINTS = ("로그인이 필요", "로그인 후", "로그인해주세요", "회원 전용", "먼저 로그인", "Login Required")
RESERVATION_LINK_KEYWORDS = ["실시간예약", "온라인예약", "티타임예약", "예약하기", "예약안내", "예약", "Booking", "Reservation"]

PROFILE_DIR = os.path.join(os.path.dirname(__file__), ".browser_profile")


def extract_hits(text):
    """금액(원) 또는 시간(HH:MM) 패턴이 있는 줄과 그 앞뒤 줄을 모은다.

    실제 티타임 목록은 보통 "07:12   4인   180,000원"처럼 시간과 가격이
    같이 나오므로, 둘 중 하나만 있어도 후보로 잡아 앞뒤 문맥과 함께 보여준다.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out, seen = [], set()
    for idx, line in enumerate(lines):
        if not (WON_AMOUNT_RE.search(line) or TIME_RE.search(line)):
            continue
        before = lines[idx - 1] if idx > 0 else ""
        after = lines[idx + 1] if idx + 1 < len(lines) else ""
        entry = f"{before}\n   >> {line}\n   {after}".strip()
        if line not in seen:
            seen.add(line)
            out.append(entry)
    return out


def try_click_keyword_link(page, keywords, timeout=2500):
    for kw in keywords:
        try:
            locator = page.get_by_text(kw, exact=False).first
            if locator.count() > 0:
                locator.click(timeout=timeout)
                page.wait_for_load_state("networkidle", timeout=10000)
                return True
        except Exception:
            continue
    return False


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

    # 2) 달력 위젯에서 날짜 숫자가 정확히 일치하는 셀 클릭 (보이는 범위 안에 있을 때만 성공)
    day = str(int(target_date.split("-")[2]))
    for selector in ["td", "button", "a", "div"]:
        try:
            candidates = page.locator(f"{selector}:has-text('{day}')")
            n = min(candidates.count(), 30)
            for i in range(n):
                cell = candidates.nth(i)
                txt = (cell.inner_text() or "").strip()
                if txt == day:
                    cell.click(timeout=2000)
                    page.wait_for_timeout(1500)
                    return f"calendar-cell({selector})"
        except Exception:
            continue
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default=os.path.join(os.path.dirname(__file__), "courses_seed.json"))
    parser.add_argument("--output", default="reservation_dump.txt")
    parser.add_argument("--date", required=True, help="확인할 날짜 (YYYY-MM-DD)")
    parser.add_argument("--only", default=None, help="쉼표로 구분한 골프장 이름 목록만 실행")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

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

    out_lines = [f"조회 날짜: {args.date}", ""]
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            PROFILE_DIR, headless=args.headless, viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()

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
                if any(hint in text for hint in LOGIN_WALL_HINTS):
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
                    out_lines.append(f"-> 날짜 선택 실패 (현재 화면: {page.url}). 이 사이트는 직접 열어서 확인하는 게 빠를 수 있습니다.")
                    print("  -> 날짜 선택 실패")
                    out_lines.append("")
                    continue

                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                text = page.inner_text("body")
                hits = extract_hits(text)
                if hits:
                    out_lines.append(f"-> {args.date} 화면({page.url}, 날짜선택방식: {method})에서 발견 {len(hits)}개:")
                    for h in hits[:40]:
                        out_lines.append("   " + h.replace("\n", "\n   "))
                    print(f"  -> {len(hits)}개 발견")
                else:
                    out_lines.append(f"-> 날짜는 선택({method})했지만 시간/가격으로 보이는 내용을 찾지 못함 (화면: {page.url}).")
                    print("  -> 후보 없음")
            except Exception as e:  # noqa: BLE001
                out_lines.append(f"-> 오류: {e}")
                print(f"  -> 오류: {e}")

            out_lines.append("")
            time.sleep(0.5)

        context.close()

    with open(args.output, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))

    print(f"\n완료: {args.output} 에 저장했습니다. 이 파일 내용을 Claude 대화에 붙여넣어 주세요.")


if __name__ == "__main__":
    main()
