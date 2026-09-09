#!/usr/bin/env python3
"""
courses_seed.json에 적힌 골프장 홈페이지를 한 번에 내려받아, 실제 금액(원)이 적힌
줄만 뽑아 하나의 파일로 모아줍니다. 26개 사이트를 하나씩 여는 대신, 이 결과 파일
하나만 훑어보면 되도록 만든 스크립트입니다.

이 스크립트도 fetch_distances.py와 마찬가지로 반드시 "로컬(사용자 PC)"에서 실행하세요.
Claude Code 원격 세션은 네트워크 정책상 골프장 사이트를 포함한 대부분의 외부 도메인에
접근할 수 없습니다(카카오 API가 막힌 것과 같은 이유).

사용법:
    python3 fetch_fee_pages.py                      # courses_seed.json의 모든 홈페이지 대상
    python3 fetch_fee_pages.py --output fees.txt

한계(중요):
  - 많은 골프장 사이트가 예약/요금 화면을 로그인 후 또는 자바스크립트로 동적으로
    보여줘서, 이 스크립트(정적 HTML만 읽음)로는 아예 못 읽어오는 곳도 있습니다.
    그런 곳은 결과 파일에 "요금으로 보이는 줄을 찾지 못함"으로 표시됩니다.
  - 홈페이지 메인 화면만 대상으로 하므로, 실제 요금표는 하위 메뉴(이용안내/예약안내 등)에
    있을 수 있습니다. 그런 경우 결과 파일에 나온 안내 문구를 참고해 해당 골프장만
    직접 열어보는 편이 빠를 수 있습니다.
  - 이벤트/특가 요금은 홈페이지에 상시 노출되지 않는 경우가 많아 이 방법으로는
    잡히지 않을 수 있습니다.

즉, "26곳을 전부 자동으로 정확히 긁어오는" 도구가 아니라, "그나마 정적으로 노출된
곳들을 추려서 확인 부담을 줄여주는" 보조 도구입니다. 결과 파일을 이 대화에 붙여넣어
주시면, 실제로 읽을 수 있었던 값들을 골라 웹앱 가격 데이터에 반영해 드리겠습니다.
"""

import argparse
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = None  # macOS의 python.org 설치본에서 인증서 체인이 안 잡히는 경우 대비.
    print("참고: certifi가 없어 기본 SSL 설정을 사용합니다. 인증서 오류가 나면", file=sys.stderr)
    print("      'pip3 install certifi' 실행 후 다시 시도하세요.", file=sys.stderr)

WON_LINE_RE = re.compile(r"[가-힣A-Za-z0-9./~:\-\s]{0,20}[0-9][0-9,]{3,}\s*원[가-힣A-Za-z0-9./~:\-\s]{0,20}")
TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
ANY_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(raw):
    text = TAG_RE.sub(" ", raw)
    text = ANY_TAG_RE.sub("\n", text)
    text = html.unescape(text)
    lines = [ln.strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; FeePageChecker/1.0)"})
    with urllib.request.urlopen(req, timeout=12, context=SSL_CONTEXT) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default=os.path.join(os.path.dirname(__file__), "courses_seed.json"))
    parser.add_argument("--output", default="fee_pages_dump.txt")
    parser.add_argument("--sleep", type=float, default=0.5)
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        courses = json.load(f)

    out_lines = []
    for i, course in enumerate(courses, 1):
        name = course["name"]
        url = course.get("homepage")
        out_lines.append("=" * 60)
        out_lines.append(f"[{i}/{len(courses)}] {name}  ({course.get('region','')})")
        out_lines.append(f"URL: {url or '(등록된 홈페이지 없음)'}")
        if not url:
            out_lines.append("-> 홈페이지 URL이 없어 건너뜀. 직접 검색해서 확인하세요.")
            print(f"  ({i}/{len(courses)}) {name}: URL 없음, 건너뜀")
            continue
        try:
            raw = fetch(url)
            lines = strip_html(raw)
            hits = [ln for ln in lines if WON_LINE_RE.search(ln)]
            hits = list(dict.fromkeys(hits))  # 중복 제거, 순서 유지
            if hits:
                out_lines.append(f"-> 금액으로 보이는 줄 {len(hits)}개:")
                for h in hits[:40]:
                    out_lines.append("   " + h)
                print(f"  ({i}/{len(courses)}) {name}: {len(hits)}개 후보 발견")
            else:
                out_lines.append("-> 이 페이지(정적 HTML)에서는 요금으로 보이는 줄을 찾지 못함.")
                out_lines.append("   (로그인 필요 / 자바스크립트 동적 로딩 / 하위 메뉴에 요금표 존재 가능)")
                print(f"  ({i}/{len(courses)}) {name}: 후보 없음")
        except urllib.error.HTTPError as e:
            out_lines.append(f"-> HTTP 오류 {e.code}, 접속 실패")
            print(f"  ({i}/{len(courses)}) {name}: HTTP {e.code}")
        except Exception as e:  # noqa: BLE001 - 이 스크립트는 사이트별 오류를 요약만 하면 됨
            out_lines.append(f"-> 접속 실패: {e}")
            print(f"  ({i}/{len(courses)}) {name}: 오류 - {e}")
        out_lines.append("")
        time.sleep(args.sleep)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))

    print(f"\n완료: {args.output} 에 저장했습니다. 이 파일 내용을 Claude 대화에 붙여넣어 주세요.")


if __name__ == "__main__":
    main()
