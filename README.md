# 국회 인공지능 법안 알림 (AI Bill Alert)

열린국회정보 Open API에서 **"인공지능"**이 포함된 국회의원 발의법률안을 주기적으로 확인하고,
새로 발의된 법안이 있으면 Gmail로 알림 메일을 보내는 자동화입니다.

- 조회 대상: `국회의원 발의법률안` API (서비스ID `nzmimeepazxkubdpn`), 22대 국회
- 실행 주기: GitHub Actions 스케줄러로 하루 3회 (KST 09/15/21시, `.github/workflows/ai-bill-alert.yml`에서 조정 가능)
- 상태 저장: 이미 확인한 의안번호를 `data/seen_bills.json`에 저장해 중복 알림을 방지 (매 실행마다 자동 커밋)

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

## 3. GitHub 저장소 시크릿 등록

이 저장소의 **Settings → Secrets and variables → Actions → New repository secret** 에서 아래 4개를 등록하세요.

| 이름 | 값 |
|---|---|
| `ASSEMBLY_API_KEY` | 1번에서 발급받은 열린국회정보 인증키 |
| `GMAIL_ADDRESS` | 발신용 Gmail 주소 (예: `kido6402@gmail.com`) |
| `GMAIL_APP_PASSWORD` | 2번에서 발급받은 16자리 앱 비밀번호 |
| `ALERT_TO` | 알림 받을 이메일 주소 (`kido6402@gmail.com`, 생략 시 `GMAIL_ADDRESS`로 발송) |

## 4. 동작 확인

시크릿 등록 후 **Actions 탭 → "AI 관련 법안 알림" 워크플로 → Run workflow** 로 수동 실행해 정상 동작을 확인할 수 있습니다.

- 최초 실행 시에는 현재 시점의 법안 목록을 기준선으로 저장만 하고, 알림 메일은 보내지 않습니다.
- 이후 실행부터는 새로 발의된 법안이 있을 때만 메일이 발송됩니다.

## 로컬에서 직접 실행하기

```bash
export ASSEMBLY_API_KEY=발급받은키
export GMAIL_ADDRESS=you@gmail.com
export GMAIL_APP_PASSWORD=앱비밀번호16자리
export ALERT_TO=you@gmail.com
python3 scripts/check_ai_bills.py
```
