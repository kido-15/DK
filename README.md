# 정책 모니터링 자동화 (AI Bill Alert · Board Digest)

이 저장소에는 서로 독립적으로 동작하는 자동화 두 개가 있습니다.

| 자동화 | 하는 일 | 실행 주기 | 진입점 |
| --- | --- | --- | --- |
| **① 국회 인공지능 법안 알림** | 열린국회정보 API에서 "인공지능" 포함 발의법률안을 확인해 새 법안을 메일로 알림 | 하루 3회 (KST 09/15/21시) | `lambda/handler.py`, `scripts/check_ai_bills.py` |
| **② 연구기관 게시판 자료 알림** | 지정한 연구기관 게시판에서 **전날 올라온 자료 제목**을 모아 하루 1회 메일로 알림 | 매일 1회 (KST 08시) | `lambda/board_handler.py`, `scripts/check_boards.py` |

Gmail 앱 비밀번호와 AWS 설정(아래 2·3번)은 두 자동화가 함께 사용합니다.

---

# ① 국회 인공지능 법안 알림 (AI Bill Alert)

열린국회정보 Open API에서 **"인공지능"**이 포함된 국회의원 발의법률안을 주기적으로 확인하고,
새로 발의된 법안이 있으면 Gmail로 알림 메일을 보내는 자동화입니다.

- 조회 대상: `국회의원 발의법률안` API (서비스ID `nzmimeepazxkubdpn`), 22대 국회
- 실행 주기: AWS Lambda(서울 리전) + EventBridge로 하루 3회 (KST 09/15/21시, `lambda/deploy.sh`에서 조정 가능)
- 상태 저장: 이미 확인한 의안번호를 S3 버킷에 저장해 중복 알림을 방지

> **왜 GitHub Actions가 아니라 AWS인가요?** 열린국회정보(open.assembly.go.kr)는 해외 IP 접속을 막고 있어서,
> 미국 리전에서 실행되는 GitHub Actions에서는 API 호출이 타임아웃됩니다. 국내(서울) 리전인 AWS Lambda나
> 회원님의 국내 PC에서만 정상 동작합니다. 로컬에서 한 번 실행해보고 싶다면 맨 아래 "로컬에서 직접 실행하기"를 참고하세요.

## 1. 열린국회정보 Open API 인증키 발급

이미 [open.assembly.go.kr](https://open.assembly.go.kr) 회원가입은 하셨다고 하셨으니, 아래 순서로 인증키를 발급받으면 됩니다.

1. open.assembly.go.kr 에 로그인
2. 상단 메뉴 **Open API** → **마이페이지(또는 "인증키 발급/관리")** 로 이동
3. **인증키 신규 발급 신청** 클릭
4. 활용목적을 간단히 입력 (예: "특정 키워드 포함 법안 알림 개인 프로젝트") 후 신청 제출
5. 승인되면(대부분 즉시) 발급된 **인증키(KEY)** 문자열을 복사

이 키를 아래 3번 단계에서 GitHub 저장소의 `ASSEMBLY_API_KEY` 시크릿으로 등록합니다.

> 참고: 발급 전 테스트만 하고 싶다면 인증키 자리에 `sample`을 넣어도 동작은 하지만, 최대 10건만 조회되므로 실제 운영에는 정식 키가 필요합니다.
> 무료(일반) 계정 기준 월 10,000회 호출까지 가능해서, 하루 3회 실행으로는 전혀 문제되지 않습니다.

## 2. Gmail 앱 비밀번호 발급

일반 Gmail 로그인 비밀번호로는 SMTP 발송이 되지 않으므로, **앱 비밀번호**가 필요합니다.

1. Google 계정 → **보안** 메뉴로 이동
2. **2단계 인증**이 꺼져 있다면 먼저 켜기 (앱 비밀번호는 2단계 인증 활성화 후에만 생성 가능)
3. 2단계 인증 화면에서 **앱 비밀번호(App passwords)** 선택
4. 앱 이름을 아무거나 입력(예: "ai-bill-alert") 하고 생성
5. 표시되는 **16자리 비밀번호**를 복사 (공백 제거)

## 3. AWS 계정 및 CLI 준비

1. AWS 계정이 없다면 [aws.amazon.com](https://aws.amazon.com)에서 가입 (신용카드 등록 필요하지만, 이 프로젝트는 Lambda/S3/EventBridge 모두 무료 티어 범위 안에서 동작합니다)
2. **IAM 사용자 생성**: AWS 콘솔 → IAM → 사용자 → 사용자 추가 → 이름 아무거나(예: `ai-bill-alert-deployer`) → 권한은 `AdministratorAccess` 정책 연결 (개인 프로젝트용 1회성 설정이라 편의상 관리자 권한 사용, 이후 필요시 축소 가능)
3. 해당 사용자 → **보안 자격 증명** 탭 → **액세스 키 만들기** → "명령줄 인터페이스(CLI)" 선택 → 생성된 **액세스 키 ID / 비밀 액세스 키** 복사 (이 값도 저에게 보내지 마세요)
4. 로컬 PC에 AWS CLI 설치
   ```bash
   # macOS (Homebrew)
   brew install awscli
   ```
5. 자격 증명 설정
   ```bash
   aws configure
   # AWS Access Key ID: (위에서 복사한 값)
   # AWS Secret Access Key: (위에서 복사한 값)
   # Default region name: ap-northeast-2
   # Default output format: json
   ```

## 4. Lambda 배포

저장소 루트에서 아래처럼 4개 환경변수를 설정하고 배포 스크립트를 실행하세요.

```bash
export ASSEMBLY_API_KEY=발급받은키
export GMAIL_ADDRESS=you@gmail.com
export GMAIL_APP_PASSWORD=앱비밀번호16자리
export ALERT_TO=you@gmail.com
./lambda/deploy.sh
```

스크립트가 S3 버킷, IAM 역할, Lambda 함수, EventBridge 스케줄(하루 3회)을 자동으로 만들어줍니다.
코드나 스케줄을 바꾼 뒤 같은 명령을 다시 실행하면 기존 리소스를 업데이트합니다.

## 5. 동작 확인

배포 스크립트 마지막에 안내되는 명령으로 즉시 한 번 실행해볼 수 있습니다.

```bash
aws lambda invoke --function-name ai-bill-alert --region ap-northeast-2 --cli-read-timeout 60 out.json && cat out.json
```

- 최초 실행 시에는 현재 시점의 법안 목록을 기준선으로 저장만 하고, 알림 메일은 보내지 않습니다.
- 이후 실행부터는 새로 발의된 법안이 있을 때만 메일이 발송됩니다.
- 실행 로그는 AWS 콘솔 → CloudWatch → 로그 그룹 → `/aws/lambda/ai-bill-alert` 에서 확인할 수 있습니다.

## 로컬에서 직접 실행하기

```bash
export ASSEMBLY_API_KEY=발급받은키
export GMAIL_ADDRESS=you@gmail.com
export GMAIL_APP_PASSWORD=앱비밀번호16자리
export ALERT_TO=you@gmail.com
python3 scripts/check_ai_bills.py
```

---

# ② 연구기관 게시판 자료 알림 (Board Digest)

지정한 연구기관·정부기관 게시판을 매일 아침 한 번 확인해서, **전날 새로 올라온 자료의 제목과 링크**를
한 통의 메일로 정리해 보냅니다. 새 자료가 없으면 메일을 보내지 않습니다.

- 대상 게시판: `sites.json`에 URL만 등록하면 추가됩니다 (개수 제한 없음)
- 실행 주기: AWS Lambda(서울 리전) + EventBridge로 **매일 KST 08시** (`SCHEDULE_HOUR_KST`로 변경 가능)
- 상태 저장: 이미 보낸 글을 S3에 기록해 같은 자료가 두 번 오지 않게 합니다
- 외부 라이브러리 없음: 파이썬 표준 라이브러리만 사용하므로 `pip install` 없이 동작합니다

## 2-1. 게시판 추가하기 (가장 중요한 단계)

게시판마다 HTML 구조가 달라서, **목록 URL을 한 번 분석해 설정을 만드는 과정**이 필요합니다.
`--probe`가 구조를 분석해 그대로 붙여넣을 수 있는 설정을 출력해 줍니다.

```bash
# 1) 브라우저에서 원하는 게시판의 '목록' 페이지를 열고 주소창 URL을 복사
# 2) 그 URL을 --probe에 넣어 실행 (국내 PC에서 실행하세요)
python3 scripts/check_boards.py --probe "https://www.example.re.kr/board/list.do"
```

출력 예시:

```
[분석] https://www.example.re.kr/board/list.do
  수신 84,120자

[자동탐지 결과] 추출 15건
  2026-09-15  AI 기본법 하위법령 정비 방안 연구
      https://www.example.re.kr/board/view.do?idx=142
  ...
  날짜 분포: 2026-09-15 2건, 2026-09-14 3건, 2026-09-12 5건

[sites.json에 붙여넣을 설정]
{
  "id": "probe",
  "name": "probe",
  "list_url": "https://www.example.re.kr/board/list.do",
  "row_selector": "table.board_list tbody tr",
  "title_selector": "td.subject a",
  "date_selector": "td.date"
}
```

출력된 JSON에서 `id`(영문 식별자)와 `name`(메일에 표시될 이름)만 고쳐 `sites.json`의 `sites` 배열에 넣으면 끝입니다.

```json
{
  "sites": [
    {
      "id": "kisdi_report",
      "name": "KISDI 연구보고서",
      "list_url": "https://www.example.re.kr/board/list.do",
      "row_selector": "table.board_list tbody tr",
      "title_selector": "td.subject a",
      "date_selector": "td.date"
    }
  ]
}
```

> `id`는 중복 발송 방지 기록의 키로 쓰이므로, 한 번 정한 뒤에는 바꾸지 마세요
> (바꾸면 그 게시판의 과거 글이 새 글로 인식됩니다).

등록한 뒤 확인:

```bash
python3 scripts/check_boards.py --list                        # 등록된 게시판 목록
python3 scripts/check_boards.py --dry-run --no-state --days 7 # 최근 7일치로 메일 없이 시험
```

## 2-2. 설정 항목

필수는 `id`, `name`, `list_url` 3개이고 나머지는 모두 선택입니다. 전체 항목 설명은
`boarddigest/config.py` 상단 주석, 형식별 예시는 `sites.example.json`에 있습니다.

| 항목 | 설명 |
| --- | --- |
| `row_selector` | 글 한 줄에 해당하는 요소 (생략하면 자동탐지) |
| `title_selector` / `date_selector` | 행 안에서 제목·날짜 위치 |
| `link_selector` | 링크가 제목과 다른 요소일 때 |
| `detail_url_template` | 목록 링크가 `javascript:goView('123')` 형태일 때 `"https://…/view.do?no={arg0}"` 처럼 지정 |
| `type` | `"rss"`로 두면 RSS/Atom 피드로 처리 |
| `encoding` | EUC-KR 사이트는 `"cp949"` |
| `pages` / `page_param` | 하루 게시물이 많아 1페이지로 부족할 때 (예: `2`, `"pageIndex"`) |
| `params` / `data` / `headers` | 목록 URL에 붙일 쿼리스트링, POST 본문, 추가 헤더 |
| `skip_row_classes` | 상단 고정 공지 행 제외 (기본 `["notice","notice_top","fixed"]`) |
| `include_keywords` / `exclude_keywords` | 제목 키워드 필터. `sites.json` 최상위에 두면 전체 적용 |
| `enabled` | `false`로 두면 잠시 제외 |

## 2-3. 배포 (매일 자동 실행)

Gmail 앱 비밀번호(위 2번)와 AWS CLI 설정(위 3번)이 끝났다면:

```bash
export GMAIL_ADDRESS=you@gmail.com
export GMAIL_APP_PASSWORD=앱비밀번호16자리
export ALERT_TO=you@gmail.com        # 여러 명이면 콤마로 구분
export SCHEDULE_HOUR_KST=8           # (선택) 발송 시각, 기본 08시
./lambda/deploy_board_digest.sh
```

`board-digest-alert` 함수, 상태 저장 버킷, 매일 1회 EventBridge 스케줄을 만들어 줍니다.
기존 `ai-bill-alert`와는 함수·버킷·스케줄이 모두 분리되어 서로 영향이 없습니다.

즉시 한 번 실행해 확인:

```bash
aws lambda invoke --function-name board-digest-alert --region ap-northeast-2 \
  --cli-read-timeout 200 out.json && cat out.json
```

**게시판을 추가하거나 수정한 뒤에는 `./lambda/deploy_board_digest.sh`를 다시 실행**해야
변경된 `sites.json`이 함수에 반영됩니다.

## 2-4. 로컬에서 직접 실행하기

AWS 없이 국내 PC에서만 돌려도 됩니다.

```bash
export GMAIL_ADDRESS=you@gmail.com
export GMAIL_APP_PASSWORD=앱비밀번호16자리
export ALERT_TO=you@gmail.com
python3 scripts/check_boards.py
```

macOS/Linux에서 매일 아침 8시에 자동 실행하려면 `crontab -e`에 다음 한 줄을 추가합니다.

```
0 8 * * * cd /경로/DK && GMAIL_ADDRESS=you@gmail.com GMAIL_APP_PASSWORD=앱비밀번호 ALERT_TO=you@gmail.com /usr/bin/python3 scripts/check_boards.py >> /tmp/board_digest.log 2>&1
```

주요 옵션:

| 옵션 | 용도 |
| --- | --- |
| `--probe URL` | 게시판 구조 분석 후 추천 설정 출력 |
| `--dry-run` | 메일을 보내지 않고 결과만 출력 |
| `--days 7` | 전날 하루가 아니라 최근 7일치 조회 |
| `--date 2026-09-15` | 기준 날짜 직접 지정 |
| `--site kisdi_report` | 특정 게시판만 조회 |
| `--no-state` | 중복 발송 방지 기록을 무시(시험용) |
| `--html-file page.html` | 저장해둔 HTML 파일로 구조 분석 |

## 2-5. 동작 방식과 한계

- **날짜 판정**: 목록에 표시된 등록일을 KST 기준으로 읽어 전날 것만 고릅니다.
  `2026-09-15`, `2026.09.15.`, `2026년 9월 15일`, `26.09.15`, `20260915`, `어제`, `3일 전` 등을 인식합니다.
- **중복 방지**: 게시판에 날짜가 늦게 반영되거나 함수가 두 번 실행돼도, 이미 보낸 글은 다시 보내지 않습니다.
  기록은 **메일 발송이 성공한 뒤에만** 남기므로, SMTP 오류로 메일이 실패하면 다음 실행에서 다시 시도합니다.
- **수집 실패 알림**: 사이트 구조 변경이나 접속 오류가 생기면 그 내용을 메일에 함께 적어 보냅니다
  (조용히 멈추는 것을 막기 위함). 원치 않으면 Lambda 환경변수 `ALERT_ON_ERROR=0`으로 끌 수 있습니다.
- **한계**: 목록을 자바스크립트로 그려 넣는 게시판(첫 응답 HTML에 글 목록이 없는 경우)은 수집할 수 없습니다.
  이때는 `--probe`가 "자동탐지 실패"로 알려주며, 해당 기관의 RSS 피드가 있으면 `"type": "rss"`로 등록하는 것이 대안입니다.
- **해외 IP 차단**: 일부 국내 기관은 해외 IP를 막습니다. 그래서 서울 리전 Lambda나 국내 PC에서 실행해야 합니다
  (①번 자동화와 같은 이유).

## 2-6. 테스트

```bash
python3 -m unittest tests.test_boarddigest -v
```

임시 HTTP 서버를 띄워 수집 → 파싱 → 전날 필터 → 중복 제거 → 메일 본문 생성 → Lambda 핸들러까지
실제 경로로 검증합니다(외부 네트워크·AWS·메일 발송 없이 동작).
