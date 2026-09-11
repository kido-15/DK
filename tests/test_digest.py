"""
수집 → 신규 판정 → 메일 본문 생성까지의 end-to-end 테스트.

네트워크를 쓰지 않도록 collector.fetch를 샘플 응답으로 바꿔치기한다.
실행: python3 tests/test_digest.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "research"))

import collector  # noqa: E402
import digest  # noqa: E402

FAILURES = []


def check(label, actual, expected):
    if actual != expected:
        FAILURES.append(f"{label}\n    기대: {expected!r}\n    실제: {actual!r}")


FAKE_PAGES = {
    "https://fake.re.kr/list": """<html><body>
        <tr><td><a href="/view.do?id=1">생성형 AI 규제 동향</a></td><td>2026.09.10</td></tr>
        <tr><td><a href="/view.do?id=2">전파 이용료 개편 연구</a></td><td>2026.09.09</td></tr>
        <tr><td><a href="/view.do?id=3">인공지능 윤리 가이드라인 분석</a></td><td>2026.09.08</td></tr>
     </body></html>""",
    "https://fake.go.kr/rss.xml": """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel>
          <item><title>AI 기본법 시행령 입법예고</title>
                <link>https://fake.go.kr/press/1</link>
                <pubDate>Thu, 11 Sep 2026 09:00:00 +0900</pubDate></item>
        </channel></rss>""",
    "https://dead.re.kr/list": None,   # 죽은 소스
}


def fake_fetch(url, timeout=20):
    body = FAKE_PAGES.get(url)
    if body is None:
        raise TimeoutError("timed out")
    return body


collector.fetch = fake_fetch

CONFIG = {
    "max_age_days": 21,
    "sources": [
        {"id": "fake-inst", "name": "가상연구원", "category": "연구기관", "type": "html",
         "url": "https://fake.re.kr/list", "base_url": "https://fake.re.kr",
         "link_pattern": "view\\.do", "keyword_filter": True},
        {"id": "fake-gov", "name": "가상부처", "category": "정부·부처", "type": "rss",
         "url": "https://fake.go.kr/rss.xml", "base_url": "https://fake.go.kr",
         "keyword_filter": True},
        {"id": "dead", "name": "죽은사이트", "category": "연구기관", "type": "html",
         "url": "https://dead.re.kr/list", "base_url": "https://dead.re.kr",
         "link_pattern": "view"},
        {"id": "off", "name": "비활성", "category": "연구기관", "type": "rss",
         "url": "https://never.kr/rss", "enabled": False},
    ],
}

result = collector.collect(CONFIG, timeout=1)

titles = [i.title for i in result.items]
check("AI 무관 항목(전파 이용료) 제외", "전파 이용료 개편 연구" in titles, False)
check("수집 건수", len(result.items), 3)
check("최신순 정렬", result.items[0].title, "AI 기본법 시행령 입법예고")
check("죽은 소스 1건만 에러로 기록", len(result.errors), 1)
check("죽은 소스 id가 에러에 포함", result.errors[0].startswith("dead:"), True)
check("비활성 소스는 아예 호출 안 함",
      any("never.kr" in e or "off:" in e for e in result.errors), False)

# --- 신규 판정 -------------------------------------------------------------
seen = {}
new_first = digest.filter_new(result.items, seen)
check("최초에는 전부 신규", len(new_first), 3)

seen = digest.update_seen(seen, result.items, today="2026-09-11")
new_second = digest.filter_new(result.items, seen)
check("두 번째 실행에서는 신규 0건", len(new_second), 0)

extra = collector.Item("fake-gov", "가상부처", "정부·부처",
                       "AI 신뢰성 인증 시범사업 공고", "https://fake.go.kr/press/2", "2026-09-11")
check("새 항목만 신규로 잡힘", [i.title for i in digest.filter_new(result.items + [extra], seen)],
      ["AI 신뢰성 인증 시범사업 공고"])

# 오래된 기록 정리
old_seen = digest.update_seen({"https://a.kr/old": "2020-01-01"}, [], today="2026-09-11")
check("200일 넘은 기록은 정리됨", "https://a.kr/old" in old_seen, False)

# --- 메일 본문 -------------------------------------------------------------
text = digest.build_text(result.items, result.errors)
check("텍스트 본문에 카테고리 포함", "■ 정부·부처" in text, True)
check("텍스트 본문에 링크 포함", "https://fake.re.kr/view.do?id=1" in text, True)
check("텍스트 본문에 실패 소스 보고", "수집에 실패한 소스" in text, True)

html = digest.build_html(result.items, result.errors)
check("HTML에 제목 링크 포함", 'href="https://fake.go.kr/press/1"' in html, True)
check("HTML 카테고리 2개 렌더링", html.count("<table"), 2)

# XSS/깨짐 방지: 제목에 태그가 들어와도 이스케이프되어야 한다
evil = collector.Item("x", "가상연구원", "연구기관",
                      '<script>alert("x")</script> AI 보고서', "https://a.kr/1", "2026-09-11")
evil_html = digest.build_html([evil], [])
check("제목의 HTML 태그 이스케이프", "<script>" in evil_html, False)
check("이스케이프된 형태로 존재", "&lt;script&gt;" in evil_html, True)

subject = digest.build_subject(result.items)
check("메일 제목에 건수 포함", "신규 3건" in subject, True)

if FAILURES:
    print(f"실패 {len(FAILURES)}건\n")
    for f in FAILURES:
        print("  ✗ " + f)
    sys.exit(1)
print("모든 테스트 통과")
