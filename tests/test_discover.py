"""
링크 패턴 자동 추론 테스트.

실제 국내 기관 게시판과 같은 구조(상단 메뉴 + 로그인/검색 + 목록표 + 페이징)의
샘플 HTML을 주고, 자료 목록 링크를 1순위로 골라내는지 확인한다.

실행: python3 tests/test_discover.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "research"))

import collector  # noqa: E402
import discover  # noqa: E402

FAILURES = []


def check(label, actual, expected):
    if actual != expected:
        FAILURES.append(f"{label}\n    기대: {expected!r}\n    실제: {actual!r}")


# 국내 연구기관 게시판을 흉내낸 페이지
BOARD_PAGE = """<html><head><meta charset="utf-8"></head><body>
<div class="gnb">
  <a href="/main/index.do">홈</a>
  <a href="/intro/greeting.do">원장인사말</a>
  <a href="/member/login.do">로그인</a>
  <a href="/search/searchList.do">통합검색</a>
</div>
<table><tbody>
  <tr><td>412</td><td><a href="/report/view.do?key=m21&artId=1779316">생성형 AI 확산에 따른 미디어 규제체계 재정립 방안</a></td><td>2026.09.10</td></tr>
  <tr><td>411</td><td><a href="/report/view.do?key=m21&artId=1779301">인공지능 기본법 하위법령 쟁점 분석</a></td><td>2026.09.08</td></tr>
  <tr><td>410</td><td><a href="/report/view.do?key=m21&artId=1779288">플랫폼 자율규제 국제 동향</a></td><td>2026.09.05</td></tr>
  <tr><td>409</td><td><a href="/report/view.do?key=m21&artId=1779270">디지털 포용 정책 성과 분석</a></td><td>2026.09.02</td></tr>
  <tr><td>408</td><td><a href="/report/view.do?key=m21&artId=1779255">알고리즘 투명성 확보 방안 연구</a></td><td>2026.08.28</td></tr>
  <tr><td>407</td><td><a href="/report/view.do?key=m21&artId=1779240">통신시장 경쟁상황 평가</a></td><td>2026.08.25</td></tr>
</tbody></table>
<div class="paging">
  <a href="/report/list.do?page=1">1</a>
  <a href="/report/list.do?page=2">2</a>
  <a href="/report/list.do?page=3">3</a>
  <a href="/report/list.do?page=4">4</a>
</div>
<div class="sns">
  <a href="https://www.facebook.com/share">공유</a>
  <a href="/common/fileDown.do?fileId=1">다운로드</a>
  <a href="/common/fileDown.do?fileId=2">다운로드</a>
  <a href="/common/fileDown.do?fileId=3">다운로드</a>
</div>
</body></html>"""

candidates = discover.suggest_patterns(BOARD_PAGE)
check("후보를 하나 이상 찾음", len(candidates) >= 1, True)
check("1순위가 상세보기 링크 패턴", candidates[0]["pattern"], "report/view\\.do")
check("1순위 건수", candidates[0]["count"], 6)

patterns = [c["pattern"] for c in candidates]
check("페이징 링크는 1순위가 아님", patterns[0] != "report/list\\.do", True)
check("다운로드/SNS 링크는 후보에서 제외", any("fileDown" in p for p in patterns), False)
check("로그인·검색 링크는 후보에서 제외", any("login" in p for p in patterns), False)

# 추론한 패턴으로 실제 수집이 되는지
source = {"id": "t", "name": "t", "category": "연구기관", "type": "html",
          "base_url": "https://www.kisdi.re.kr", "link_pattern": candidates[0]["pattern"]}
items = collector.parse_html_list(BOARD_PAGE, source)
check("추론 패턴으로 6건 추출", len(items), 6)
check("제목 정상", items[0].title, "생성형 AI 확산에 따른 미디어 규제체계 재정립 방안")
check("날짜 정상", items[0].published, "2026-09-10")
check("절대주소 변환", items[0].url,
      "https://www.kisdi.re.kr/report/view.do?key=m21&artId=1779316")
ai_items = [i for i in items if collector.matches_keywords(i, collector.DEFAULT_KEYWORDS)]
check("AI 관련만 3건으로 좁혀짐", len(ai_items), 3)

# --- 홈에서 자료실 메뉴 찾기 ------------------------------------------------
HOME_PAGE = """<html><body>
  <a href="/main/index.do">홈</a>
  <a href="/intro/greeting.do">원장 인사말</a>
  <a href="/report/list.do?key=m21">연구보고서</a>
  <a href="/news/pressList.do">보도자료</a>
  <a href="/member/login.do">로그인</a>
  <a href="/periodical/list.do">정기간행물</a>
</body></html>"""
collector.fetch = lambda url, timeout=20: HOME_PAGE
menus = discover.find_list_pages("https://www.kisdi.re.kr", timeout=1)
menu_urls = [u for _, u in menus]
check("연구보고서 메뉴 발견", "https://www.kisdi.re.kr/report/list.do?key=m21" in menu_urls, True)
check("정기간행물 메뉴 발견", "https://www.kisdi.re.kr/periodical/list.do" in menu_urls, True)
check("로그인은 메뉴 후보에서 제외", any("login" in u for u in menu_urls), False)
check("인사말은 메뉴 후보에서 제외", any("greeting" in u for u in menu_urls), False)

# --- RSS 안내 페이지에서 기관 피드 찾기 ---------------------------------------
RSS_INDEX_PAGE = """<html><body>
  <table>
    <tr><td>기획재정부</td><td><a href="/rss/dept_moef.xml">RSS 구독</a></td></tr>
    <tr><td>과학기술정보통신부</td><td><a href="/rss/dept_msit_new.xml">RSS 구독</a></td></tr>
    <tr><td>방송통신위원회</td><td><a href="/rss/dept_kcc_new.xml">RSS 구독</a></td></tr>
  </table>
</body></html>"""
collector.fetch = lambda url, timeout=20: RSS_INDEX_PAGE
feed = discover.find_feed_in_index("https://www.korea.kr/etc/rss.do", "과학기술정보통신부", timeout=1)
check("링크 텍스트가 '구독'이어도 기관명으로 찾아냄", feed,
      "https://www.korea.kr/rss/dept_msit_new.xml")
check("다른 기관 피드도 찾음",
      discover.find_feed_in_index("https://www.korea.kr/etc/rss.do", "방송통신위원회", timeout=1),
      "https://www.korea.kr/rss/dept_kcc_new.xml")
check("없는 기관은 빈 문자열",
      discover.find_feed_in_index("https://www.korea.kr/etc/rss.do", "없는부처", timeout=1), "")

if FAILURES:
    print(f"실패 {len(FAILURES)}건\n")
    for f in FAILURES:
        print("  ✗ " + f)
    sys.exit(1)
print("모든 테스트 통과")
