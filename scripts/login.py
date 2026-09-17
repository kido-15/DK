#!/usr/bin/env python3
"""예약 사이트에 직접 로그인해 두는 도구.

엑스골프, 카카오골프예약처럼 로그인해야 티타임이 보이는 사이트를 위한 것이다.

브라우저 창을 띄워 주면 **직접 로그인**하시면 된다. 로그인이 끝나면 그 세션이
저장되어, 이후 수집할 때 로그인된 상태로 목록을 읽는다.

    python3 scripts/login.py xgolf         # 엑스골프에 로그인
    python3 scripts/login.py               # 세 곳을 순서대로
    python3 scripts/login.py --status      # 어디에 로그인돼 있는지
    python3 scripts/login.py --clear xgolf # 저장된 로그인 지우기

무엇이 저장되나요
    브라우저가 발급받은 쿠키와 프로필만 저장됩니다.
    아이디와 비밀번호는 이 프로그램이 보지도, 저장하지도 않습니다.
    직접 입력하신 내용은 브라우저와 해당 사이트 사이에서만 오갑니다.

주의할 점
    - 저장 폴더(data/golf/sessions)에는 로그인된 상태가 담깁니다. 남과 공유하지 마세요.
    - 세션은 시간이 지나면 만료됩니다. 수집이 안 되면 다시 로그인하세요.
    - 본인 계정으로 본인이 볼 수 있는 화면만 읽습니다.
    - 사이트 이용약관에서 자동 수집을 금지한다면 사용하지 마세요.
      과도하게 요청하면 계정이 제한될 수 있습니다.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from golf.extract import detect_login_wall                      # noqa: E402
from golf.sources.browser_source import (INSTALL_HINT, clear_session,  # noqa: E402
                                         close_context, has_session,
                                         open_context, playwright_available,
                                         save_cookies, session_dir)

SITES = {
    "xgolf": {
        "name": "엑스골프",
        "login_url": "https://www.xgolf.com",
        "note": "로그인 후 부킹 목록이 보이는지 확인하고 돌아오세요.",
    },
    "kakao": {
        "name": "카카오골프예약",
        "login_url": "https://golf.kakao.com",
        "note": "카카오 계정으로 로그인합니다. 2단계 인증이 있으면 함께 진행하세요.",
    },
    "golfpang": {
        "name": "골팡",
        "login_url": "https://www.golfpang.com",
        "note": "로그인 없이도 목록이 보이면 건너뛰어도 됩니다.",
    },
}


def cmd_status() -> int:
    print("저장된 로그인 세션\n")
    any_found = False
    for key, site in SITES.items():
        ok = has_session(key)
        any_found = any_found or ok
        mark = "로그인됨" if ok else "없음"
        print(f"  {site['name']:14s} {mark}")
        if ok:
            print(f"      {session_dir(key)}")
    if not any_found:
        print("\n아직 로그인한 사이트가 없습니다.")
        print("  python3 scripts/login.py xgolf")
    else:
        print("\n세션은 시간이 지나면 만료됩니다.")
        print("수집이 안 되면 다시 로그인하세요: python3 scripts/login.py <사이트>")
    return 0


def cmd_clear(keys: list[str]) -> int:
    for key in keys:
        if key not in SITES:
            print(f"알 수 없는 사이트: {key}")
            continue
        if clear_session(key):
            print(f"  {SITES[key]['name']} 로그인 세션을 지웠습니다.")
        else:
            print(f"  {SITES[key]['name']} 는 저장된 세션이 없습니다.")
    return 0


def login_one(key: str, *, check_url: str = "") -> bool:
    """브라우저 창을 띄워 직접 로그인하게 한다."""
    site = SITES[key]
    print("\n" + "=" * 68)
    print(f"  {site['name']} 로그인")
    print("=" * 68)

    if has_session(key):
        print("\n  이미 저장된 로그인 세션이 있습니다.")
        ans = input("  다시 로그인하시겠습니까? (y/N): ").strip().lower()
        if not ans.startswith("y"):
            print("  건너뜁니다.")
            return False

    print(f"\n  브라우저 창이 열립니다: {site['login_url']}")
    print(f"  {site['note']}")
    print("\n  창에서 직접 로그인해 주세요.")
    print("  이 프로그램은 아이디와 비밀번호를 보지 않습니다.")
    print("  로그인이 끝나면 이 터미널로 돌아와 Enter 를 누르세요.\n")

    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            # 세션을 남겨야 하므로 persistent context 로, 창은 보이게 연다
            browser, context = open_context(p, site_id=key, headless=False,
                                            use_session=True)
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(site["login_url"], wait_until="domcontentloaded",
                          timeout=60000)
            except Exception as exc:
                print(f"  페이지를 여는 데 실패했습니다: {exc}")
                print("  창에서 직접 주소를 입력해 이동하셔도 됩니다.")

            try:
                input("  로그인을 마치셨으면 Enter: ")
            except (EOFError, KeyboardInterrupt):
                print()

            # 로그인 결과를 간단히 확인한다
            verified = None
            target = check_url or site["login_url"]
            try:
                page.goto(target, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
                html = page.content()
                verified = not detect_login_wall(html)
            except Exception:
                verified = None

            # 쿠키를 따로 꺼내 둔다. 목록 API 를 찾은 뒤에는 브라우저 없이
            # 일반 요청으로 부르는데, 그때도 로그인 상태가 필요하기 때문이다.
            cookie_count = 0
            try:
                cookies = context.cookies()
                save_cookies(key, cookies)
                cookie_count = len(cookies)
            except Exception:
                pass

            close_context(browser, context)
    except Exception as exc:
        print(f"\n  브라우저를 띄우지 못했습니다: {exc}")
        print("\n  화면이 없는 환경(서버 등)에서는 창을 띄울 수 없습니다.")
        print("  개인 PC에서 실행해 주세요.")
        return False

    if not has_session(key):
        print("\n  세션이 저장되지 않았습니다. 다시 시도해 주세요.")
        return False

    print(f"\n  ✓ 로그인 세션을 저장했습니다: {session_dir(key)}")
    if cookie_count:
        print(f"    쿠키 {cookie_count}개 (아이디·비밀번호는 저장되지 않습니다)")
    if verified is False:
        print("  다만 확인 페이지에서 여전히 로그인 화면이 보입니다.")
        print("  로그인이 덜 됐거나, 확인할 주소가 달라서일 수 있습니다.")
    print("\n  이 폴더에는 로그인된 상태가 담겨 있으니 남과 공유하지 마세요.")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="예약 사이트 로그인 도우미")
    ap.add_argument("sites", nargs="*", metavar="{" + ",".join(SITES) + "}",
                    help="로그인할 사이트. 비우면 전부")
    ap.add_argument("--status", action="store_true", help="로그인 상태 확인")
    ap.add_argument("--clear", nargs="*", metavar="사이트", help="저장된 로그인 지우기")
    ap.add_argument("--check-url", default="",
                    help="로그인 확인에 쓸 목록 페이지 주소")
    args = ap.parse_args()

    if args.status:
        return cmd_status()
    if args.clear is not None:
        return cmd_clear(args.clear or list(SITES))

    if not playwright_available():
        print(INSTALL_HINT)
        return 1

    targets = args.sites or list(SITES)
    unknown = [t for t in targets if t not in SITES]
    if unknown:
        print(f"알 수 없는 사이트: {', '.join(unknown)}")
        print(f"사용 가능: {', '.join(SITES)}")
        return 1

    print("=" * 68)
    print("  예약 사이트 로그인 도우미")
    print("=" * 68)
    print("\n  브라우저 창에서 직접 로그인하시면, 그 상태를 저장해 두었다가")
    print("  이후 티타임을 수집할 때 사용합니다.")
    print("\n  아이디와 비밀번호는 저장하지 않습니다. 쿠키만 남습니다.")

    done = []
    for key in targets:
        try:
            if login_one(key, check_url=args.check_url):
                done.append(SITES[key]["name"])
        except KeyboardInterrupt:
            print("\n중단했습니다.")
            break

    print("\n" + "=" * 68)
    if done:
        print(f"  로그인 완료: {', '.join(done)}")
        print("\n  이제 연결하고 수집하세요:")
        print("    python3 scripts/setup_sites.py")
    else:
        print("  로그인된 사이트가 없습니다.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
