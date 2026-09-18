#!/usr/bin/env python3
"""예약 사이트를 열어 구조를 낱낱이 살펴보고 보고서를 만든다.

어떤 사이트가 왜 수집되지 않는지 알아내기 위한 도구다. 페이지를 열고
    - 오간 JSON 요청
    - 반복되는 목록 구조 후보
    - 날짜를 고르는 부분
    - 드롭다운과 폼
을 모두 기록하고, 날짜 후보를 실제로 눌러 본 결과까지 남긴다.

    python3 scripts/explore.py "https://golf.kakao.com/..."
    python3 scripts/explore.py "주소" --show            # 브라우저를 보면서
    python3 scripts/explore.py --connect http://localhost:9222   # 열린 탭 조사

보고서는 화면에 나오고 data/golf/explore-<시각>.txt 로도 저장된다.
화면에 나온 내용을 그대로 복사해 보여 주면 맞춤 설정을 만들 수 있다.

개인정보가 들어갈 수 있으니, 보고서를 남에게 보낼 때는 한 번 훑어보세요.
로그인한 상태로 조사하면 이름이나 예약 내역이 섞일 수 있습니다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from golf import htmlsel, interact                             # noqa: E402
from golf.extract import (PRICE_RE, TIME_RE, _find_record_arrays,  # noqa: E402
                          _guess_json_keys, auto_extract,
                          auto_extract_json, find_repeating_blocks)
from golf.sources.browser_source import (_SKIP_URL, close_context,  # noqa: E402
                                         find_chromium, list_open_tabs,
                                         open_context, playwright_available,
                                         resolve_browser)

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "golf")


class Report:
    """화면에 찍으면서 동시에 파일로도 모아 둔다."""

    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, text: str = "") -> None:
        print(text)
        self.lines.append(text)

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines))
        return path


def describe_json_apis(captured: list, out: Report, play_date) -> None:
    out("\n" + "=" * 68)
    out("  ① 페이지가 부른 JSON 요청")
    out("=" * 68)
    if not captured:
        out("\n  없습니다. 목록이 처음부터 HTML 에 들어 있거나,")
        out("  스크롤·클릭을 해야 부르는 형태일 수 있습니다.")
        return

    out(f"\n  {len(captured)}건\n")
    for i, (url, method, body) in enumerate(captured[:12], 1):
        out(f"  [{i}] {method} {url[:100]}")
        arrays = _find_record_arrays(body)
        if not arrays:
            preview = json.dumps(body, ensure_ascii=False)[:150]
            out(f"      배열 없음. 응답 앞부분: {preview}")
            continue
        for path, arr in arrays[:3]:
            keys = _guess_json_keys(arr) if arr else {}
            out(f"      배열 {path!r} — {len(arr)}건")
            if arr:
                sample = {k: str(v)[:24] for k, v in list(arr[0].items())[:8]}
                out(f"        첫 레코드: {sample}")
            if keys:
                out(f"        추측한 필드: {keys}")
        r = auto_extract_json(body, source_id="explore", play_date=play_date)
        if r.tee_times:
            out(f"      ★ 이 응답에서 티타임 {len(r.tee_times)}건을 뽑을 수 있습니다")
            for t in r.tee_times[:3]:
                fee = f"{t.green_fee:,}원" if t.green_fee >= 0 else "가격미상"
                out(f"          {t.course_name} {t.tee_time:%H:%M} {fee}")


def describe_blocks(html: str, out: Report) -> None:
    out("\n" + "=" * 68)
    out("  ② 반복되는 목록 구조 후보")
    out("=" * 68)
    root = htmlsel.parse(html)
    blocks = find_repeating_blocks(root)
    if not blocks:
        out("\n  없습니다. 목록이 아직 안 그려졌거나, 시각이 보이지 않는 화면입니다.")
        # 시각·금액이 화면에 있기는 한지 알려 준다
        text = root.text
        out(f"\n  화면 전체에서 시각처럼 보이는 것: {len(TIME_RE.findall(text))}개")
        out(f"  금액처럼 보이는 것: {len(PRICE_RE.findall(text))}개")
        if TIME_RE.findall(text):
            out(f"  예: {TIME_RE.findall(text)[:8]}")
        return

    out(f"\n  {len(blocks)}개 (가능성이 높은 순)\n")
    for i, (sig, nodes) in enumerate(blocks[:5], 1):
        out(f"  [{i}] {sig} — {len(nodes)}개 반복")
        out(f"      첫 항목: {nodes[0].text[:110]}")
        if len(nodes) > 1:
            out(f"      둘째 항목: {nodes[1].text[:110]}")


def describe_date_controls(page, out: Report) -> None:
    out("\n" + "=" * 68)
    out("  ③ 날짜를 고르는 부분")
    out("=" * 68)
    found = False

    # 날짜가 onclick 함수 인자로만 들어 있는 사이트가 있다 (골팡이 그렇다).
    # 속성 이름만 보면 놓치므로 값 안에 날짜가 있는지도 본다.
    for attr in ("onclick", "href", "data-params", "onchange"):
        try:
            loc = page.locator(f'[{attr}]')
            n = loc.count()
        except Exception:
            continue
        shown = 0
        for i in range(min(n, 60)):
            el = loc.nth(i)
            try:
                val = el.get_attribute(attr) or ""
            except Exception:
                continue
            if not re.search(r"20\d{2}[-./]?\d{2}[-./]?\d{2}", val):
                continue
            if not shown:
                out(f"\n  {attr} 값 안에 날짜가 들어 있는 요소")
                found = True
            try:
                tag = el.evaluate("e => e.tagName")
                text = re.sub(r"\s+", " ", el.inner_text(timeout=600) or "").strip()[:20]
            except Exception:
                tag, text = "?", ""
            out(f"    <{tag}> 글자=\"{text}\"  {attr}={val[:70]}")
            shown += 1
            if shown >= 6:
                break

    for attr in ("data-date", "data-day", "data-ymd", "data-value", "data-playdate"):
        try:
            loc = page.locator(f"[{attr}]")
            n = loc.count()
        except Exception:
            continue
        if not n:
            continue
        found = True
        out(f"\n  {attr} 속성을 가진 요소 {n}개")
        for i in range(min(n, 6)):
            el = loc.nth(i)
            try:
                val = el.get_attribute(attr) or ""
                tag = el.evaluate("e => e.tagName")
                text = re.sub(r"\s+", " ", el.inner_text(timeout=600) or "").strip()[:24]
                out(f"    <{tag}> {attr}=\"{val}\" 글자=\"{text}\"")
            except Exception:
                pass

    try:
        selects = page.locator("select")
        if selects.count():
            found = True
            out(f"\n  드롭다운 {selects.count()}개")
            for i in range(min(selects.count(), 6)):
                sel = selects.nth(i)
                name = (sel.get_attribute("name") or sel.get_attribute("id") or "?")
                opts = sel.locator("option")
                samples = []
                for j in range(min(opts.count(), 5)):
                    samples.append(
                        (opts.nth(j).inner_text(timeout=500) or "").strip())
                out(f"    {name}: {', '.join(s for s in samples if s)}"
                    f"{' ...' if opts.count() > 5 else ''}")
    except Exception:
        pass

    # 날짜처럼 보이는 글자가 붙은 누를 수 있는 요소
    try:
        loc = page.locator("a, button, li, td").filter(
            has_text=re.compile(r"\d{1,2}\s*[/.월]\s*\d{1,2}|\d{4}-\d{2}-\d{2}"))
        if loc.count():
            found = True
            out(f"\n  날짜처럼 보이는 글자가 붙은 요소 {loc.count()}개")
            for i in range(min(loc.count(), 8)):
                el = loc.nth(i)
                try:
                    tag = el.evaluate("e => e.tagName")
                    text = re.sub(r"\s+", " ",
                                  el.inner_text(timeout=500) or "").strip()[:30]
                    onclick = (el.get_attribute("onclick") or "")[:60]
                    href = (el.get_attribute("href") or "")[:40]
                    extra = f" onclick={onclick}" if onclick else ""
                    extra += f" href={href}" if href else ""
                    out(f"    <{tag}> \"{text}\"{extra or ' (클릭 핸들러 없음)'}")
                except Exception:
                    pass
    except Exception:
        pass

    if not found:
        out("\n  날짜를 고르는 부분을 찾지 못했습니다.")


def try_dates(page, dates: list, out: Report) -> None:
    out("\n" + "=" * 68)
    out("  ④ 날짜를 실제로 눌러 본 결과")
    out("=" * 68)
    out("")
    for d in dates:
        r = interact.select_date(page, d)
        mark = "성공" if r.ok else ("눌렀지만 목록 그대로" if r.clicked else "실패")
        out(f"  {d}  {mark}")
        out(f"      {r.reason[:150]}")
        if r.ok:
            extracted = auto_extract(page.content(), source_id="explore", play_date=d)
            out(f"      → 티타임 {len(extracted.tee_times)}건 "
                f"({extracted.reason[:70]})")
            for t in extracted.tee_times[:3]:
                fee = f"{t.green_fee:,}원" if t.green_fee >= 0 else "가격미상"
                out(f"          {t.course_name} {t.tee_time:%H:%M} {fee}")


def main() -> int:
    ap = argparse.ArgumentParser(description="예약 사이트 구조 조사")
    ap.add_argument("url", nargs="?", help="조사할 주소")
    ap.add_argument("--connect", default="",
                    help="이미 열어 둔 브라우저의 탭을 조사 (예: http://localhost:9222)")
    ap.add_argument("--tab", type=int, default=1, help="--connect 일 때 조사할 탭 번호")
    ap.add_argument("--show", action="store_true", help="브라우저 창을 보면서")
    ap.add_argument("--browser", default="", help="쓸 브라우저 이름이나 경로")
    ap.add_argument("--wait", type=int, default=5000, help="기다릴 시간(밀리초)")
    ap.add_argument("--days", type=int, default=3, help="눌러 볼 날짜 수")
    ap.add_argument("--no-click", action="store_true", help="날짜를 눌러 보지 않는다")
    ap.add_argument("--out", help="보고서 저장 경로")
    args = ap.parse_args()

    if not args.url and not args.connect:
        ap.error("주소를 주거나 --connect 로 열린 탭을 지정하세요")
    if args.url and not args.url.startswith(("http://", "https://")):
        print(f"주소가 아닙니다: {args.url!r}")
        print("  http:// 또는 https:// 로 시작하는 실제 주소가 필요합니다.")
        return 1
    if not playwright_available():
        print("Playwright 가 필요합니다:")
        print("    pip3 install playwright")
        print("    python3 -m playwright install chromium")
        return 1

    exe = resolve_browser(args.browser) if args.browser else ""
    if args.browser and not exe:
        print(f"'{args.browser}' 브라우저를 찾지 못했습니다.")
        return 1

    out = Report()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    today = date.today()
    dates = [today + timedelta(days=i + 1) for i in range(args.days)]

    from playwright.sync_api import sync_playwright

    captured: list = []
    shot_path = os.path.join(OUT_DIR, f"explore-{stamp}.png")
    html_path = os.path.join(OUT_DIR, f"explore-{stamp}.html")
    os.makedirs(OUT_DIR, exist_ok=True)

    try:
        with sync_playwright() as p:
            if args.connect:
                browser = p.chromium.connect_over_cdp(args.connect)
                pages = [pg for ctx in browser.contexts for pg in ctx.pages]
                if not pages:
                    print("열려 있는 탭이 없습니다.")
                    return 1
                tabs = list_open_tabs(args.connect)
                out(f"열린 탭 {len(tabs)}개 중 {args.tab}번을 조사합니다")
                page = pages[min(args.tab - 1, len(pages) - 1)]
                context = None
                attached = True
            else:
                browser, context = open_context(
                    p, site_id="explore", headless=not args.show,
                    executable_path=exe, use_session=False)
                page = context.pages[0] if context.pages else context.new_page()
                attached = False

            def on_response(resp):
                try:
                    if _SKIP_URL.search(resp.url) or not resp.ok:
                        return
                    if "json" not in (resp.header_value("content-type") or "").lower():
                        return
                    captured.append((resp.url, resp.request.method, resp.json()))
                except Exception:
                    pass

            page.on("response", on_response)

            if args.url:
                out(f"여는 중: {args.url}")
                page.goto(args.url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(args.wait)
                interact.scroll_through(page, 3)

            out(f"\n제목: {page.title()}")
            out(f"주소: {page.url}")
            html = page.content()
            out(f"HTML 크기: {len(html):,}자")

            describe_json_apis(captured, out, dates[0])
            describe_blocks(html, out)
            describe_date_controls(page, out)

            if not args.no_click:
                try_dates(page, dates, out)

            try:
                page.screenshot(path=shot_path, full_page=False)
            except Exception:
                pass
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(page.content())

            if attached:
                browser.close()
            else:
                close_context(browser, context)
    except Exception as exc:
        out(f"\n조사 중 오류: {exc}")

    out("\n" + "=" * 68)
    out("  저장한 파일")
    out("=" * 68)
    out(f"  스크린샷: {shot_path}")
    out(f"  HTML    : {html_path}")

    report_path = args.out or os.path.join(OUT_DIR, f"explore-{stamp}.txt")
    out.save(report_path)
    print(f"\n보고서: {report_path}")
    print("\n위 내용을 그대로 복사해 보여 주시면 맞춤 설정을 만들 수 있습니다.")
    print("(로그인한 상태로 조사했다면 개인정보가 섞여 있지 않은지 한 번 보세요)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
