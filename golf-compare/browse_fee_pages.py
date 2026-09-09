#!/usr/bin/env python3
"""
실제 크롬 브라우저를 띄워서 골프장 홈페이지의 요금 페이지를 순서대로 방문하고,
자바스크립트까지 렌더링된 화면에서 "숫자+원" 형태로 보이는 줄을 모아 파일로 저장합니다.

fetch_fee_pages.py(단순 HTML 요청)와 달리 이 스크립트는:
  - 자바스크립트로 그려지는 요금표도 읽을 수 있습니다(실제 브라우저라서).
  - 로그인이 필요한 사이트는, 뜬 브라우저 창에서 "직접 한 번" 로그인하시면 됩니다.
    로그인 정보는 이 스크립트나 Claude가 절대 보지 않습니다 — 브라우저 창에 사용자가
    직접 입력하는 것이고, 그 세션(쿠키)만 로컬 폴더(golf-compare/.browser_profile/)에
    저장되어 다음 실행부터는 다시 로그인할 필요가 없습니다. 이 폴더는 .gitignore에 있어
    깃에 올라가지 않습니다.

준비:
    pip3 install playwright
    playwright install chromium      # 브라우저 바이너리 최초 1회 다운로드 (수백 MB, 몇 분 소요)

사용법:
    python3 browse_fee_pages.py                       # courses_seed.json 전체
    python3 browse_fee_pages.py --only "88컨트리클럽,신라CC"   # 일부만 테스트
    python3 browse_fee_pages.py --headless             # 이미 로그인 다 돼 있으면 창 없이 실행

로그인이 필요한 사이트를 만나면 터미널에 안내가 뜨고 멈춥니다. 뜬 브라우저 창에서
로그인을 마친 뒤, 터미널로 돌아와 Enter를 누르면 이어서 진행합니다.
"""

import argparse
import json
import os
import re
import sys
import time

WON_AMOUNT_RE = re.compile(r"[0-9][0-9,]{3,}\s*원")
LOGIN_WALL_HINTS = ("로그인이 필요", "로그인 후", "로그인해주세요", "회원 전용", "먼저 로그인", "Login Required")
FEE_LINK_KEYWORDS = ["요금안내", "이용요금", "그린피", "요금표", "예약안내", "이용안내", "Fee", "PRICE", "Price"]

PROFILE_DIR = os.path.join(os.path.dirname(__file__), ".browser_profile")


def extract_hits(text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out, seen = [], set()
    for idx, line in enumerate(lines):
        if not WON_AMOUNT_RE.search(line):
            continue
        before = lines[idx - 1] if idx > 0 else ""
        after = lines[idx + 1] if idx + 1 < len(lines) else ""
        entry = f"{before}\n   >> {line}\n   {after}".strip()
        key = line  # 중복 판단은 금액 줄 기준으로
        if key not in seen:
            seen.add(key)
            out.append(entry)
    return out


def try_click_fee_link(page):
    for kw in FEE_LINK_KEYWORDS:
        try:
            locator = page.get_by_text(kw, exact=False).first
            if locator.count() > 0:
                locator.click(timeout=3000)
                page.wait_for_load_state("networkidle", timeout=10000)
                return True
        except Exception:
            continue
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default=os.path.join(os.path.dirname(__file__), "courses_seed.json"))
    parser.add_argument("--output", default="fee_pages_browsed.txt")
    parser.add_argument("--only", default=None, help="쉼표로 구분한 골프장 이름 목록만 실행")
    parser.add_argument("--headless", action="store_true", help="창 없이 실행(이미 로그인 완료된 경우)")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright가 설치되어 있지 않습니다. 아래를 먼저 실행하세요:", file=sys.stderr)
        print("  pip3 install playwright", file=sys.stderr)
        print("  playwright install chromium", file=sys.stderr)
        sys.exit(1)

    with open(args.input, encoding="utf-8") as f:
        courses = json.load(f)

    if args.only:
        wanted = {n.strip() for n in args.only.split(",")}
        courses = [c for c in courses if c["name"] in wanted]

    out_lines = []
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=args.headless,
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        for i, course in enumerate(courses, 1):
            name = course["name"]
            url = course.get("homepage")
            out_lines.append("=" * 60)
            out_lines.append(f"[{i}/{len(courses)}] {name}  ({course.get('region', '')})")
            out_lines.append(f"URL: {url or '(등록된 홈페이지 없음)'}")
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
                try_click_fee_link(page)

                text = page.inner_text("body")
                if any(hint in text for hint in LOGIN_WALL_HINTS):
                    print(f"  -> '{name}' 페이지가 로그인을 요구하는 것 같습니다.")
                    print("     뜬 브라우저 창에서 로그인을 마친 뒤, 여기로 돌아와 Enter를 눌러주세요.")
                    print("     (그냥 넘어가려면 아무것도 안 하고 Enter만 눌러도 됩니다)")
                    input("     계속하려면 Enter >> ")
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass
                    text = page.inner_text("body")

                hits = extract_hits(text)
                if hits:
                    out_lines.append(f"-> 현재 화면({page.url})에서 금액으로 보이는 부분 {len(hits)}개:")
                    for h in hits[:40]:
                        out_lines.append("   " + h.replace("\n", "\n   "))
                    print(f"  -> {len(hits)}개 후보 발견")
                else:
                    out_lines.append(f"-> 현재 화면({page.url})에서 금액으로 보이는 줄을 찾지 못함.")
                    print("  -> 후보 없음")
            except Exception as e:  # noqa: BLE001 - 사이트별 오류는 요약만 하고 다음으로 진행
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
