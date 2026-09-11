"""
수집 엔진 오프라인 테스트.

국내 기관 사이트는 개발 환경에서 접속할 수 없어(해외 IP 차단·망 정책),
실제 응답 대신 같은 형태의 샘플 문서로 파서를 검증한다.

실행: python3 tests/test_collector.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "research"))

import collector  # noqa: E402

FAILURES = []


def check(label, actual, expected):
    if actual != expected:
        FAILURES.append(f"{label}\n    기대: {expected!r}\n    실제: {actual!r}")


RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>과기정통부 보도자료</title>
  <item>
    <title>인공지능 기본법 시행령 제정안 입법예고</title>
    <link>https://www.korea.kr/briefing/pressReleaseView.do?newsId=1111</link>
    <pubDate>Wed, 09 Sep 2026 09:30:00 +0900</pubDate>
    <description>AI 기본법 하위법령 마련 추진</description>
  </item>
  <item>
    <title>우편물류 자동화 설비 개선 사업 공고</title>
    <link>https://www.korea.kr/briefing/pressReleaseView.do?newsId=2222</link>
    <pubDate>Tue, 08 Sep 2026 10:00:00 +0900</pubDate>
    <description>우편 서비스 품질 향상</description>
  </item>
</channel></rss>"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Guidelines on transparency obligations under Article 50</title>
    <link rel="alternate" href="/en/library/article-50-guidelines"/>
    <updated>2026-09-05T08:00:00Z</updated>
    <summary>Commission guidelines for generative AI providers</summary>
  </entry>
</feed>"""

HTML_SAMPLE = """<html><head><meta charset="utf-8"></head><body>
<table class="board"><tbody>
  <tr>
    <td>152</td>
    <td><a href="/report/reportView.do?seq=152">생성형 AI 규제 동향과 시사점</a></td>
    <td>김연구</td><td>2026.09.10</td>
  </tr>
  <tr>
    <td>151</td>
    <td><a href="/report/reportView.do?seq=151">통신요금 인가제 개선 방안</a></td>
    <td>박연구</td><td>2026-09-07</td>
  </tr>
  <tr>
    <td>150</td>
    <td><a href="/report/reportView.do?seq=150">인공지능 신뢰성 확보를 위한 입법 과제</a></td>
    <td>이연구</td><td>2024.01.02</td>
  </tr>
</tbody></table>
<a href="javascript:goPage(2)">다음</a>
<a href="/common/login.do">로그인</a>
<script>var a = "/report/reportView.do?seq=999";</script>
</body></html>"""


# --- RSS ------------------------------------------------------------------
rss_source = {"id": "msit", "name": "과기정통부", "category": "정부·부처", "type": "rss",
              "base_url": "https://www.korea.kr"}
rss_items = collector.parse_feed(RSS_SAMPLE, rss_source)
check("RSS 항목 수", len(rss_items), 2)
check("RSS 제목", rss_items[0].title, "인공지능 기본법 시행령 제정안 입법예고")
check("RSS 링크", rss_items[0].url, "https://www.korea.kr/briefing/pressReleaseView.do?newsId=1111")
check("RSS pubDate 파싱", rss_items[0].published, "2026-09-09")

# --- Atom (상대경로 link, href 속성) ---------------------------------------
atom_source = {"id": "ec", "name": "EU 집행위", "category": "해외 규제", "type": "rss",
               "base_url": "https://digital-strategy.ec.europa.eu"}
atom_items = collector.parse_feed(ATOM_SAMPLE, atom_source)
check("Atom 항목 수", len(atom_items), 1)
check("Atom 상대링크 절대화", atom_items[0].url,
      "https://digital-strategy.ec.europa.eu/en/library/article-50-guidelines")
check("Atom updated 파싱", atom_items[0].published, "2026-09-05")

# --- HTML 목록 -------------------------------------------------------------
html_source = {"id": "kisdi", "name": "KISDI", "category": "연구기관", "type": "html",
               "base_url": "https://www.kisdi.re.kr",
               "link_pattern": "reportView\\.do"}
html_items = collector.parse_html_list(HTML_SAMPLE, html_source)
check("HTML 항목 수(로그인·페이징·script 제외)", len(html_items), 3)
check("HTML 제목", html_items[0].title, "생성형 AI 규제 동향과 시사점")
check("HTML 링크 절대화", html_items[0].url,
      "https://www.kisdi.re.kr/report/reportView.do?seq=152")
check("HTML 같은 행 날짜(2026.09.10)", html_items[0].published, "2026-09-10")
check("HTML 날짜 하이픈 표기", html_items[1].published, "2026-09-07")

# --- EUC-KR 디코딩 ---------------------------------------------------------
euckr_bytes = "<html><body><a href='/v.do?id=1'>인공지능 백서</a></body></html>".encode("euc-kr")
check("EUC-KR 디코딩", "인공지능 백서" in collector.decode_body(euckr_bytes, None), True)

# --- 키워드 필터 -----------------------------------------------------------
kw = collector.DEFAULT_KEYWORDS
check("키워드: 인공지능 매칭", collector.matches_keywords(html_items[2], kw), True)
check("키워드: 생성형 AI 매칭", collector.matches_keywords(html_items[0], kw), True)
check("키워드: 무관 항목 제외(통신요금)", collector.matches_keywords(html_items[1], kw), False)
check("키워드: 'AI'가 chain/mail 등에 오탐하지 않음",
      collector.matches_keywords(
          collector.Item("x", "x", "", "Email chain maintenance", "http://a/1"), kw), False)
check("키워드: 영문 AI 단어 매칭",
      collector.matches_keywords(
          collector.Item("x", "x", "", "The AI Act enters into force", "http://a/2"), kw), True)

# --- 기간 필터 -------------------------------------------------------------
now = datetime(2026, 9, 11, tzinfo=collector.KST)
check("기간: 최근 자료 통과", collector.within_max_age(html_items[0], 21, now), True)
check("기간: 2024년 자료 제외", collector.within_max_age(html_items[2], 21, now), False)
check("기간: 날짜 불명이면 통과",
      collector.within_max_age(collector.Item("x", "x", "", "제목", "http://a/3"), 21, now), True)

# --- 중복 키 --------------------------------------------------------------
a = collector.Item("s", "s", "", "제목", "https://a.kr/v.do?b=2&a=1")
b = collector.Item("s", "s", "", "제목(수정)", "https://a.kr/v.do?a=1&b=2")
check("중복 키: 파라미터 순서 무시", a.key(), b.key())
check("중복 키: 말미 슬래시 무시",
      collector.Item("s", "s", "", "t", "https://a.kr/x/").key(),
      collector.Item("s", "s", "", "t", "https://a.kr/x").key())

# --- 실제 설정 파일 로드 ----------------------------------------------------
config = collector.load_config()
check("sources.json 로드", len(config["sources"]) > 0, True)
for src in config["sources"]:
    for required in ("id", "name", "type", "url"):
        if required not in src:
            FAILURES.append(f"sources.json: {src.get('id')} 에 '{required}' 키가 없음")
    if src.get("type") not in ("rss", "html"):
        FAILURES.append(f"sources.json: {src.get('id')} 의 type이 rss/html이 아님")
    if src.get("enabled", True) and not src.get("url"):
        FAILURES.append(f"sources.json: {src.get('id')} 가 활성인데 url이 비어 있음")
    if src.get("type") == "html" and not src.get("link_pattern"):
        FAILURES.append(f"sources.json: {src.get('id')} (html)에 link_pattern이 없음")

if FAILURES:
    print(f"실패 {len(FAILURES)}건\n")
    for f in FAILURES:
        print("  ✗ " + f)
    sys.exit(1)
print("모든 테스트 통과")
