# AI 연구자료 알림 (AI Research Digest)

국내 정책연구기관·정부 부처·해외 규제기관 사이트를 매일 한 번 훑어서,
**AI 관련 신규 공개자료만 골라 Gmail로 보내주는 봇**입니다.

- 실행 주기: AWS Lambda(서울 리전) + EventBridge, 매일 KST 09시
- 상태 저장: 이미 알린 자료를 S3에 기록해 같은 자료를 두 번 보내지 않음
- 의존 패키지: 없음 (표준 라이브러리만 사용 → zip 하나로 배포)

> **왜 서울 리전인가요?** 국내 기관 사이트 상당수가 해외 IP를 차단합니다.
> 기존 `ai-bill-alert`와 같은 이유로 국내 리전에서 실행해야 합니다.

---

## ⚠️ 먼저 읽어주세요 — 최초 1회 점검이 필요합니다

`sources.json`에 들어 있는 각 기관의 **수집 주소는 "후보값"**입니다.
이 코드를 작성한 개발 환경에서는 국내 기관 사이트에 접속할 수 없어(해외 IP 차단 + 망 정책)
실제 응답으로 확인하지 못했습니다. 그래서 모든 소스가 `"verified": false` 상태입니다.

**국내 PC에서 아래 한 줄을 먼저 실행**하면, 살아 있는 주소를 자동으로 찾아 고쳐줍니다.

```bash
python3 research/discover.py --fix
```

`discover.py`는 아래 순서로 **스스로 주소를 찾아 고칩니다.**

1. 현재 주소로 자료가 뽑히면 → 그대로 통과
2. RSS 피드가 있으면 → RSS로 교체 (가장 안정적)
3. 목록 페이지는 열리는데 링크 패턴이 안 맞으면 → 페이지의 링크를 형태별로 묶어 '자료 목록'답게 생긴 것을 자동 선택
4. 주소 자체가 404면 → 홈에서 '발간물/보고서/자료실' 메뉴를 따라가 3을 반복

출력 예시:

```
[  정상  ] kca                  10건 추출 (예: 2026 미디어 이슈&트렌드 Vol.75)
[  실패  ] kisdi                HTTP 404 404
            ★ 패턴 report/view\.do [연구보고서]
              https://www.kisdi.re.kr/report/list.do?key=m21
              · 생성형 AI 확산에 따른 미디어 규제체계 재정립 방안
            └ 적용 후 재확인: 성공 — 12건 추출
```

★ 표시가 1순위 후보이고, `--fix`를 붙이면 그것으로 자동 반영한 뒤 **다시 수집해서 성공 여부까지 확인**합니다.
탐색 과정을 자세히 보려면 `--verbose`를 붙이세요.

`후보를 찾지 못했습니다`로 뜨는 소스만 수동 보정하면 됩니다 → 아래 "소스 추가·수정" 참고.

---

## 사용 순서

### 1. 로컬에서 수집 결과 확인 (메일 발송 없음)

```bash
python3 research/discover.py            # 어느 소스가 살아 있는지 점검
python3 scripts/run_research_digest.py --dry-run --all
```

`--all`은 신규 여부와 무관하게 수집된 전체 목록을 보여줍니다. 키워드 필터가
너무 넓거나 좁지 않은지 여기서 확인하세요.

### 2. Lambda 배포

Gmail 앱 비밀번호와 AWS CLI 설정은 루트 `README.md`의 2·3번 항목과 동일합니다
(이미 `ai-bill-alert`를 배포했다면 그대로 재사용).

```bash
export GMAIL_ADDRESS=you@gmail.com
export GMAIL_APP_PASSWORD=앱비밀번호16자리
export ALERT_TO=you@gmail.com          # 여러 명이면 콤마 구분
./lambda/deploy_research.sh
```

### 3. 동작 확인

```bash
aws lambda invoke --function-name ai-research-digest --region ap-northeast-2 \
  --cli-read-timeout 200 out.json && cat out.json
```

- **첫 실행은 기준선만 저장하고 메일을 보내지 않습니다.** 두 번째 실행부터 신규 자료만 발송됩니다.
- 응답의 `failed_sources`에 뜬 소스는 주소 점검이 필요합니다. 이 목록은 알림 메일 하단에도 함께 실립니다.
- 로그: CloudWatch → `/aws/lambda/ai-research-digest`

---

## 수집 대상 (sources.json)

| 구분 | 기관 |
|---|---|
| 연구기관 | SPRi 소프트웨어정책연구소, KISDI, NIA, IITP, 국회입법조사처(NARS), 국회미래연구원, 한국언론진흥재단, 한국법제연구원, KCA, STEPI |
| 정부·부처 | 과기정통부, 방송통신위원회, 개인정보보호위원회 (정책브리핑 RSS) |
| 해외 규제 | EU 집행위 Shaping Europe's digital future, EU AI Act 모니터, OECD.AI |
| 학술논문 | KCI — **기본 비활성**. 아래 참고 |

### KCI(한국학술지인용색인)가 비활성인 이유

KCI Open API는 별도 인증키가 필요하고, 이 환경에서 실제 엔드포인트 규격을 확인할 수 없었습니다.
추측한 주소를 넣어두면 조용히 실패하는 것보다 나쁘다고 판단해 `enabled: false`로 두었습니다.
인증키를 발급받으신 뒤 실제 응답 형식을 알려주시면 어댑터를 추가하겠습니다.
(RSS/Atom 형식으로 응답한다면 `type: "rss"`에 주소만 채우면 바로 동작합니다.)

---

## 소스 추가·수정

`research/sources.json`의 항목 하나가 사이트 하나입니다.

```jsonc
{
  "id": "kisdi",                              // 고유 id (상태 기록·로그에 쓰임)
  "name": "KISDI 정보통신정책연구원",           // 메일에 표시될 이름
  "category": "연구기관",                      // 메일에서 묶이는 단위
  "type": "html",                             // "rss" 또는 "html"
  "url": "https://www.kisdi.re.kr/report/reportList.do",   // 목록 페이지 또는 피드 주소
  "base_url": "https://www.kisdi.re.kr",      // 상대링크를 절대주소로 만들 때 기준
  "link_pattern": "reportView\\.do",          // (html일 때) 이 정규식에 맞는 링크만 자료로 인정
  "exclude_pattern": "login",                 // (선택) 제외할 링크 패턴
  "keyword_filter": true,                     // false면 AI 키워드 필터 없이 전부 수집
  "max_items": 30,                            // (선택) 한 소스에서 가져올 최대 건수
  "enabled": true
}
```

**`link_pattern` 찾는 법**: 브라우저에서 목록 페이지를 열고, 자료 제목 링크에
마우스 오른쪽 → "링크 주소 복사". 주소에서 상세보기를 가리키는 고정된 부분
(예: `reportView.do`, `nttId=`, `/posts/view/`)을 정규식으로 적으면 됩니다.

특정 소스만 시험해 볼 때:

```bash
python3 scripts/run_research_digest.py --dry-run --all --only kisdi
```

### 키워드 조정

`sources.json`의 `keywords`가 `null`이면 `collector.py`의 `DEFAULT_KEYWORDS`를 씁니다
(인공지능, 생성형, LLM, 알고리즘, AI Act 등). 좁히거나 넓히려면 배열로 직접 적으세요.

```json
"keywords": ["인공지능", "AI기본법", "생성형", "알고리즘", "AI Act"]
```

`max_age_days`(기본 21)는 며칠 이내 자료까지 볼지를 정합니다. 날짜를 못 읽은 자료는
버리지 않고 통과시키며, 중복 발송은 상태 파일이 따로 막습니다.

---

## 테스트

국내 사이트에 접속하지 않고 파서·중복판정·메일 생성까지 검증합니다.

```bash
python3 tests/test_collector.py    # RSS/Atom/HTML 파싱, 날짜·키워드·중복 판정
python3 tests/test_digest.py       # 수집 → 신규 판정 → 메일 본문 생성
python3 tests/test_handler.py      # Lambda 핸들러 (S3·SMTP는 가짜로 대체)
```

## 파일 구성

| 파일 | 역할 |
|---|---|
| `research/collector.py` | 수집 엔진 (RSS/Atom 파서, HTML 목록 파서, 키워드·기간 필터) |
| `research/digest.py` | 신규 판정, 메일 본문(HTML/텍스트) 생성, 발송 |
| `research/sources.json` | 수집 대상 목록 — **여기만 고치면 대상이 바뀝니다** |
| `research/discover.py` | 주소 점검 및 RSS 자동 탐지 (`--fix`로 자동 수정) |
| `lambda/research_handler.py` | Lambda 진입점 (상태를 S3에 저장) |
| `lambda/deploy_research.sh` | 배포 스크립트 |
| `scripts/run_research_digest.py` | 로컬 실행 (상태를 `data/seen_research.json`에 저장) |
