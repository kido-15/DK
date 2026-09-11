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

# ===========================================================================
# 실제 탐색에서 잘못 잡혔던 사례들 (2026-09-11 첫 --fix 실행 로그 기준)
# ===========================================================================

# [사례 1] KISDI: 메뉴 링크를 자료 목록으로 오인했다.
#          "KISDI Premium Report", "KISDI Perspectives" 는 날짜 없는 메뉴다.
KISDI_MENU_PAGE = """<html><body>
  <a href="/report/list.do?key=m01">KISDI Premium Report</a>
  <a href="/report/list.do?key=m02">KISDI Perspectives</a>
  <a href="/report/list.do?key=m03">KISDI STAT Report</a>
  <a href="/report/list.do?key=m04">기본연구 보고서</a>
  <a href="/report/list.do?key=m05">정책연구 보고서</a>
  <a href="/report/list.do?key=m06">현안연구 보고서</a>
  <a href="/sub.do?key=s01">KISDI 발간물</a>
  <a href="/sub.do?key=s02">KISDI 학술지</a>
</body></html>"""
menu_items = collector.parse_html_list(
    KISDI_MENU_PAGE, {"id": "t", "name": "t", "base_url": "https://www.kisdi.re.kr",
                      "link_pattern": "report/list\\.do"})
passed, reason = discover.passes_gate(menu_items)
check("[회귀] 메뉴 링크 묶음은 게이트 탈락", passed, False)
check("[회귀] 탈락 사유가 날짜 부재", "날짜" in reason, True)

# [사례 2] NARS: "연구 보고서", "NARS info" 같은 짧은 메뉴
NARS_MENU_PAGE = """<html><body>
  <a href="/report/list.do?cmsCode=CM0043">연구 보고서</a>
  <a href="/report/list.do?cmsCode=CM0044">NARS info</a>
  <a href="/report/list.do?cmsCode=CM0045">간행물</a>
  <a href="/report/list.do?cmsCode=CM0046">입법정보</a>
  <a href="/report/list.do?cmsCode=CM0047">현안분석</a>
  <a href="/report/list.do?cmsCode=CM0048">이슈와논점</a>
</body></html>"""
nars_items = collector.parse_html_list(
    NARS_MENU_PAGE, {"id": "t", "name": "t", "base_url": "https://www.nars.go.kr",
                     "link_pattern": "report/list\\.do"})
passed, _ = discover.passes_gate(nars_items)
check("[회귀] 짧은 메뉴 이름 묶음도 탈락", passed, False)

# [사례 3] SPRi: 글 번호가 경로에 박혀 같은 게시판이 쪼개졌다.
#          /posts/view/24024, /posts/view/24018 ... 은 하나의 패턴이어야 한다.
SPRI_LIST_PAGE = """<html><body>
  <ul>
    <li><a href="/posts/view/24024">에이전틱 AI 시대의 핵심 자원, AX 인재: 현황 진단 및 정책 과제</a><span>2026.09.11</span></li>
    <li><a href="/posts/view/24018">데이터 기반 가상융합(XR) 기술 콘텐츠 글로벌 동향 분석</a><span>2026.09.09</span></li>
    <li><a href="/posts/view/24004">신뢰할 수 있는 AI: 글로벌 현황과 향후 정책적 과제</a><span>2026.09.05</span></li>
    <li><a href="/posts/view/23998">AI 교육 혁신과 AI 융합인재 양성 전략</a><span>2026.09.02</span></li>
    <li><a href="/posts/view/23990">소프트웨어 산업 실태조사 결과 보고</a><span>2026.08.29</span></li>
    <li><a href="/posts/view/23985">디지털 전환 지표 분석</a><span>2026.08.26</span></li>
  </ul>
</body></html>"""
spri_cands = discover.suggest_patterns(SPRI_LIST_PAGE, base_netloc="spri.kr")
check("[회귀] 글 번호가 달라도 하나의 패턴으로 묶임", spri_cands[0]["count"], 6)
check("[회귀] 숫자 세그먼트가 정규식으로 치환됨", spri_cands[0]["pattern"], "posts/view/\\d+")
spri_items = collector.parse_html_list(
    SPRI_LIST_PAGE, {"id": "t", "name": "t", "base_url": "https://spri.kr",
                     "link_pattern": spri_cands[0]["pattern"]})
check("[회귀] 추론 패턴으로 6건 추출", len(spri_items), 6)
passed, _ = discover.passes_gate(spri_items)
check("[회귀] 진짜 자료 목록은 게이트 통과", passed, True)
check("[회귀] 날짜 파싱 정상", spri_items[0].published, "2026-09-11")

# [사례 4] 언론진흥재단: 외부 사이트(newstore.or.kr) 광고 링크가 잡혔다.
check("[회귀] 외부 도메인 링크는 형태 요약에서 제외",
      discover.link_shape("https://www.newstore.or.kr/cstmr/detail.do?id=1", "www.kpf.or.kr"),
      None)
check("[회귀] 같은 도메인(www 유무 차이)은 유지",
      discover.link_shape("https://kpf.or.kr/front/research/detail.do?id=1", "www.kpf.or.kr")
      is not None, True)

# [사례 5] #앵커만 다른 주소는 같은 페이지로 취급
check("[회귀] fragment 제거", discover.clean_url("https://www.kpf.or.kr#tab1_5"),
      "https://www.kpf.or.kr")

# [사례 6] 같은 제목이 반복되는 '더보기/다운로드' 묶음
dup_items = [collector.Item("t", "t", "", "다운로드", f"https://a.kr/f/{i}", "2026-09-11")
             for i in range(8)]
passed, reason = discover.passes_gate(dup_items)
check("[회귀] 같은 제목 반복은 탈락", passed, False)

# [사례 7] 항목이 너무 적은 묶음 (SPRi가 2건만 뽑고 '성공'이라 했던 경우)
few_items = [collector.Item("t", "t", "", "AI 정책 보고서 제목입니다", f"https://a.kr/v/{i}",
                            "2026-09-11") for i in range(3)]
passed, reason = discover.passes_gate(few_items)
check("[회귀] 3건짜리 묶음은 탈락", passed, False)

if FAILURES:
    print(f"\n회귀 테스트 실패 {len(FAILURES)}건\n")
    for f in FAILURES:
        print("  ✗ " + f)
    sys.exit(1)
print("회귀 테스트까지 모두 통과")
