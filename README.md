# 국회 인공지능 법안 알림 (AI Bill Alert)

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
