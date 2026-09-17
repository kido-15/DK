#!/usr/bin/env python3
"""예약 사이트 연결 마법사.

엑스골프 · 카카오골프예약 · 골팡을 순서대로 연결한다.
브라우저에서 목록 주소를 복사해 붙여 넣으면 나머지는 이 스크립트가 한다.

    python3 scripts/setup_sites.py            # 세 사이트를 순서대로
    python3 scripts/setup_sites.py xgolf      # 한 곳만
    python3 scripts/setup_sites.py --status   # 현재 설정 상태 확인

반드시 국내 PC에서 실행하세요. 해외 IP는 차단되는 경우가 많습니다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from golf.courses import DEFAULT_PATH as COURSES_DEFAULT       # noqa: E402
from golf.courses import CourseBook                            # noqa: E402
from golf.sources.web_source import DEFAULT_CONFIG_PATH        # noqa: E402
from golf.sources.web_source import HttpClient, WebSource      # noqa: E402

import importlib.util                                          # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "probe_source", os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_source.py")
)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


# ---------------------------------------------------------------------------
# 사이트 프로필
#
# 주의: 아래 "확인된 것" 은 각 서비스의 성격에 대한 일반적인 설명이고,
# 목록 주소나 응답 구조는 적어 두지 않았다. 세 사이트 모두 공개 API 문서가
# 없고 화면 구조가 수시로 바뀌기 때문에, 추측한 주소를 적어 두면 오히려
# 틀린 설정으로 시간을 버리게 된다. 실제 주소는 아래 안내대로 직접 찾는다.
# ---------------------------------------------------------------------------

SITES = {
    "xgolf": {
        "name": "엑스골프",
        "home": "https://www.xgolf.com",
        "hints": [
            "부킹/조인 목록 화면을 연 뒤 F12 → Network 탭에서 새로고침하세요.",
            "목록이 페이지 이동 없이 갱신되면 XHR/Fetch 요청을 먼저 찾아보세요.",
            "요청이 안 보이면 주소창의 목록 페이지 주소를 그대로 쓰면 됩니다.",
        ],
    },
    "kakao": {
        "name": "카카오골프예약",
        "home": "https://golf.kakao.com",
        "hints": [
            "화면이 스크롤할 때마다 채워지는 형태면 거의 확실히 내부 API를 씁니다.",
            "F12 → Network → Fetch/XHR 필터를 켜고 스크롤해 보세요.",
            "JSON을 돌려주는 요청이 잡히면 그 주소가 가장 안정적입니다.",
        ],
    },
    "golfpang": {
        "name": "골팡",
        "home": "https://www.golfpang.com",
        "hints": [
            "특가/당일 목록 화면이 대상입니다.",
            "모바일 웹(m. 으로 시작하는 주소)이 있으면 그쪽이 구조가 단순해",
            "크롤링이 훨씬 안정적인 경우가 많습니다. 둘 다 시도해 보세요.",
        ],
    },
}

COMMON_NOTES = [
    "로그인해야만 목록이 보이는 화면이라면 이 프로그램으로는 다루지 않습니다.",
    "  (로그인 세션을 흉내 내는 것은 약관 위반 소지가 큽니다)",
    "로그인 없이 보이는 목록만 대상으로 하세요.",
]


# ---------------------------------------------------------------------------
# 도우미
# ---------------------------------------------------------------------------


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(130)
    return val or default


def ask_yn(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    val = ask(f"{prompt} ({d})").lower()
    if not val:
        return default
    return val.startswith("y")


def templatize_date(url: str) -> tuple[str, str]:
    """URL 안의 날짜를 {date:...} 치환자로 바꾼다.

    브라우저에서 복사한 주소에는 그날 날짜가 박혀 있다. 그대로 두면 매번
    같은 날짜만 조회하므로 치환자로 바꿔 줘야 한다.
    """
    patterns = [
        (r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)", "{date:%Y%m%d}"),
        (r"(?<!\d)(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])(?!\d)", "{date:%Y-%m-%d}"),
        (r"(?<!\d)(20\d{2})\.(0[1-9]|1[0-2])\.(0[1-9]|[12]\d|3[01])(?!\d)", "{date:%Y.%m.%d}"),
        (r"(?<!\d)(20\d{2})%2F(0[1-9]|1[0-2])%2F(0[1-9]|[12]\d|3[01])(?!\d)", "{date:%Y%%2F%m%%2F%d}"),
    ]
    for pat, repl in patterns:
        new = re.sub(pat, repl, url, count=1)
        if new != url:
            return new, repl
    return url, ""


def templatize_page(url: str) -> str:
    """page=2 같은 파라미터를 {page} 로 바꾼다."""
    return re.sub(
        r"([?&](?:page|pageNo|pageNum|pageIndex|p|currentPage)=)\d+",
        r"\g<1>{page}",
        url,
        count=1,
        flags=re.IGNORECASE,
    )


def load_config(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError as exc:
                print(f"\n[경고] {path} 를 읽을 수 없습니다: {exc}")
                if not ask_yn("새로 만들까요? (기존 내용은 .bak 으로 백업됩니다)", True):
                    raise SystemExit(1)
                os.replace(path, path + ".bak")
    return {"sources": []}


def save_config(path: str, cfg: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def upsert_source(cfg: dict, source: dict) -> str:
    """같은 id가 있으면 교체, 없으면 추가. 어느 쪽이었는지 돌려준다."""
    sources = cfg.setdefault("sources", [])
    for i, s in enumerate(sources):
        if s.get("id") == source["id"]:
            sources[i] = source
            return "교체"
    sources.append(source)
    return "추가"


# ---------------------------------------------------------------------------
# 한 사이트 설정하기
# ---------------------------------------------------------------------------


def setup_one(key: str, cfg: dict, config_path: str) -> bool:
    site = SITES[key]
    print("\n" + "=" * 68)
    print(f"  {site['name']} 연결")
    print("=" * 68)
    print(f"\n  홈: {site['home']}\n")
    print("  목록 주소 찾는 법:")
    for h in site["hints"]:
        print(f"    - {h}")
    print()
    for n in COMMON_NOTES:
        print(f"    {n}")
    print()
    print("  찾았으면 그 요청을 우클릭 → Copy → Copy link address 로 복사하세요.")
    print("  (건너뛰려면 그냥 Enter)")
    print()

    url = ask("  목록 주소 붙여넣기")
    if not url:
        print("  → 건너뜁니다.")
        return False
    if not url.startswith(("http://", "https://")):
        print("  → http 로 시작하는 주소여야 합니다. 건너뜁니다.")
        return False

    # 날짜/페이지를 치환자로
    url, date_fmt = templatize_date(url)
    if date_fmt:
        print(f"  ✓ 주소 안의 날짜를 {date_fmt} 로 바꿨습니다 (매번 원하는 날짜로 조회됩니다)")
    templated = templatize_page(url)
    if templated != url:
        print("  ✓ 페이지 번호를 {page} 로 바꿨습니다")
        url = templated
    print(f"  → {url}")

    headers = {"Referer": site["home"] + "/"}
    if ask_yn("\n  Referer 헤더를 붙일까요? (403이 나면 필요합니다)", True):
        print(f"  ✓ Referer: {headers['Referer']}")
    else:
        headers = {}

    # 실제 요청
    print("\n  요청해 봅니다...")
    client = HttpClient(headers=headers)
    probe_url = url
    for token, val in [("{date:%Y%m%d}", None), ("{date:%Y-%m-%d}", None),
                       ("{date:%Y.%m.%d}", None), ("{page}", "1")]:
        if token in probe_url:
            if val is None:
                d = date.today() + timedelta(days=7)
                fmt = token[len("{date:"):-1].replace("%%", "%")
                val = d.strftime(fmt)
            probe_url = probe_url.replace(token, val)

    try:
        text = client.get(probe_url)
    except Exception as exc:
        print(f"\n  ✗ 요청 실패: {exc}")
        print("    - 403/401 → 로그인이 필요하거나 헤더가 더 필요한 주소입니다")
        print("    - 타임아웃 → 해외 IP 차단일 수 있습니다. 국내망에서 실행하세요")
        print("    - 그 밖에는 주소를 다시 확인해 주세요")
        return False

    print(f"  ✓ {len(text):,}자 수신")

    fmt = "json" if text.lstrip().startswith(("{", "[")) else "html"
    print(f"  ✓ 형식: {fmt}")

    # 구조 분석
    print("\n  구조를 분석합니다...")
    if fmt == "json":
        source = probe.analyze_json(text, key, site["name"], url)
    else:
        source = probe.analyze_html(text, key, site["name"], url)

    if not source:
        print("\n  ✗ 목록 구조를 찾지 못했습니다.")
        if fmt == "html":
            print("    자바스크립트로 그려지는 화면일 가능성이 큽니다.")
            print("    F12 → Network → Fetch/XHR 에서 JSON 주소를 찾아 다시 시도해 보세요.")
        dump = os.path.join("data", "golf", f"probe-{key}.txt")
        os.makedirs(os.path.dirname(dump), exist_ok=True)
        with open(dump, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"    받은 응답을 저장했습니다: {dump}")
        return False

    # 필수 필드 점검
    fields = source.get("fields") or {}
    missing = [k for k in ("course_name", "tee_time", "green_fee") if k not in fields]
    if missing:
        print(f"\n  [주의] 자동으로 찾지 못한 필드: {', '.join(missing)}")
        print("  위 출력의 '첫 항목 텍스트' 를 보고 직접 넣어야 할 수 있습니다.")
        print(f"  설정 파일({config_path})에서 나중에 고칠 수 있습니다.")

    source["enabled"] = True
    source["respect_robots"] = True
    if headers:
        source["request"]["headers"] = headers

    if not ask_yn("\n  이 설정을 저장할까요?", True):
        print("  → 저장하지 않았습니다.")
        return False

    action = upsert_source(cfg, source)
    save_config(config_path, cfg)
    print(f"  ✓ {config_path} 에 {action}했습니다")

    # 바로 시험 호출
    if ask_yn("\n  지금 바로 시험 호출해 볼까요?", True):
        test_source(source)
    return True


def test_source(source_cfg: dict) -> None:
    """설정한 소스를 실제로 호출해 결과를 보여 준다."""
    src = WebSource(source_cfg)
    d = date.today() + timedelta(days=7)
    print(f"\n  {d} 기준으로 호출합니다...")
    rows = src.fetch([d])
    stats = src.last_stats or {}
    print(f"  요청 {stats.get('requests', 0)}회 → 티타임 {len(rows)}건")
    for err in (stats.get("errors") or [])[:3]:
        print(f"    오류: {err}")

    if not rows:
        print("\n  결과가 0건입니다. 그 날짜에 매물이 없을 수도 있습니다.")
        print("  다른 날짜로도 확인해 보세요:")
        print(f"    python3 golf_cli.py --test-source {source_cfg['id']} --date 2026-10-15")
        return

    book = CourseBook.load(COURSES_DEFAULT)
    print()
    for r in rows[:10]:
        fee = f"{r.green_fee:,}원" if r.green_fee >= 0 else "가격미상"
        mark = ""
        if len(book) and not book.match(r.course_name):
            mark = "  ← 골프장 DB에 없음"
        print(f"    {r.course_name[:20]:20s} {r.tee_time:%H:%M} {fee:>12s}{mark}")
    if len(rows) > 10:
        print(f"    ... 외 {len(rows) - 10}건")

    if not len(book):
        print("\n  골프장 DB가 비어 있습니다. 먼저 실행하세요:")
        print("    python3 scripts/fetch_golf_courses.py")
    else:
        miss = [r.course_name for r in rows if not book.match(r.course_name)]
        if miss:
            uniq = sorted(set(miss))
            print(f"\n  골프장 DB와 매칭 안 된 이름 {len(uniq)}종:")
            print(f"    {', '.join(uniq[:10])}")
            print("  data/golf/courses.csv 의 aliases 칸에 넣어 주면 잡힙니다.")


# ---------------------------------------------------------------------------


def show_status(config_path: str) -> int:
    cfg = load_config(config_path)
    sources = cfg.get("sources") or []
    if not sources:
        print(f"설정된 소스가 없습니다: {config_path}")
        print("python3 scripts/setup_sites.py 를 실행해 연결하세요.")
        return 1

    print(f"{config_path}\n")
    print(f"{'ID':14s} {'상태':6s} {'형식':6s} {'필드':28s} 이름")
    print("-" * 80)
    for s in sources:
        fields = s.get("fields") or {}
        filled = [k for k, v in fields.items()
                  if (v.get("selector") or v.get("path") or v.get("from_request")
                      or v.get("const")) if isinstance(v, dict)]
        state = "사용" if s.get("enabled") else "중지"
        print(f"{s.get('id','?'):14s} {state:6s} {s.get('format','?'):6s} "
              f"{','.join(filled)[:28]:28s} {s.get('name','')}")

    book = CourseBook.load(COURSES_DEFAULT)
    print(f"\n골프장 DB: {len(book)}곳"
          + ("" if len(book) else "  ← scripts/fetch_golf_courses.py 를 먼저 실행하세요"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="예약 사이트 연결 마법사")
    # nargs="*" 와 choices 를 같이 쓰면 인자를 비웠을 때 argparse가 거부하므로
    # 목록만 안내하고 값 검증은 아래에서 직접 한다.
    ap.add_argument("sites", nargs="*", metavar="{" + ",".join(SITES) + "}",
                    help="설정할 사이트. 비우면 전부")
    ap.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="설정 파일 경로")
    ap.add_argument("--status", action="store_true", help="현재 설정 상태만 보기")
    args = ap.parse_args()

    if args.status:
        return show_status(args.config)

    targets = args.sites or list(SITES)
    unknown = [t for t in targets if t not in SITES]
    if unknown:
        print(f"알 수 없는 사이트: {', '.join(unknown)}")
        print(f"사용 가능: {', '.join(SITES)}")
        return 1

    print("=" * 68)
    print("  예약 사이트 연결 마법사")
    print("=" * 68)
    print("\n  이 마법사는 브라우저에서 복사한 목록 주소를 받아")
    print("  구조를 분석하고 설정 파일에 저장합니다.")
    print("\n  준비물: 크롬/엣지 브라우저, F12 개발자도구")
    print("  ※ 반드시 국내 PC에서 실행하세요 (해외 IP는 차단되는 경우가 많습니다)")
    print(f"\n  대상: {', '.join(SITES[t]['name'] for t in targets)}")

    cfg = load_config(args.config)
    done = []
    for key in targets:
        try:
            if setup_one(key, cfg, args.config):
                done.append(SITES[key]["name"])
        except SystemExit:
            raise
        except Exception as exc:
            print(f"\n  ✗ {SITES[key]['name']} 설정 중 오류: {exc}")

    print("\n" + "=" * 68)
    if done:
        print(f"  연결 완료: {', '.join(done)}")
        print("\n  이제 검색해 보세요:")
        print("    python3 golf_web.py")
        print("    python3 golf_cli.py --from 37.4979,127.0276 --max-drive 90 --max-price 200000")
    else:
        print("  연결된 사이트가 없습니다.")
        print("\n  목록 주소를 찾기 어렵다면, 응답을 파일로 저장해 두고 분석할 수도 있습니다:")
        print("    python3 scripts/probe_source.py '주소' --save-body out.html")
        print("    python3 scripts/probe_source.py --from-file out.html")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
