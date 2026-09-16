#!/usr/bin/env bash
#
# AWS 서울 리전(ap-northeast-2)에 '연구기관 게시판 전날 자료 알림' Lambda를 배포/업데이트한다.
# 기존 ai-bill-alert와는 별도 함수/버킷/스케줄로 동작하므로 서로 영향이 없다.
#
# 사전 준비:
#   1. AWS CLI 설치 및 `aws configure` 완료 (README 3번 항목과 동일)
#   2. sites.json에 게시판을 1개 이상 등록 (python3 scripts/check_boards.py --list 로 확인)
#
# 사용법:
#   export GMAIL_ADDRESS=you@gmail.com
#   export GMAIL_APP_PASSWORD=앱비밀번호16자리
#   export ALERT_TO=you@gmail.com          # 여러 명이면 콤마로 구분
#   export SCHEDULE_HOUR_KST=8             # (선택) 발송 시각, 기본 KST 08시
#   ./lambda/deploy_board_digest.sh

set -euo pipefail

REGION="ap-northeast-2"
FUNCTION_NAME="board-digest-alert"
ROLE_NAME="board-digest-alert-lambda-role"
RULE_NAME="board-digest-alert-schedule"
STATE_KEY="seen_board_items.json"

: "${GMAIL_ADDRESS:?GMAIL_ADDRESS 환경변수를 설정하세요}"
: "${GMAIL_APP_PASSWORD:?GMAIL_APP_PASSWORD 환경변수를 설정하세요}"
ALERT_TO="${ALERT_TO:-$GMAIL_ADDRESS}"
LOOKBACK_DAYS="${LOOKBACK_DAYS:-1}"
SCHEDULE_HOUR_KST="${SCHEDULE_HOUR_KST:-8}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# KST 시각을 UTC cron으로 변환 (KST = UTC+9)
UTC_HOUR=$(( (SCHEDULE_HOUR_KST + 24 - 9) % 24 ))

if ! python3 -c "
import json,sys
cfg=json.load(open('$REPO_ROOT/sites.json'))
sites=[s for s in cfg.get('sites',[]) if s.get('enabled',True)]
sys.exit(0 if sites else 1)
" 2>/dev/null; then
  echo "경고: sites.json에 활성화된 게시판이 없습니다. 배포는 되지만 메일이 오지 않습니다."
  echo "      python3 scripts/check_boards.py --probe '<게시판 목록 URL>' 로 설정을 만들어 추가하세요."
  echo ""
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET_NAME="board-digest-state-${ACCOUNT_ID}"

echo "== 1) 상태 저장용 S3 버킷: $BUCKET_NAME =="
if ! aws s3api head-bucket --bucket "$BUCKET_NAME" 2>/dev/null; then
  aws s3api create-bucket --bucket "$BUCKET_NAME" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION"
  echo "버킷 생성 완료"
else
  echo "버킷 이미 존재함"
fi

echo "== 2) Lambda 실행 역할: $ROLE_NAME =="
ROLE_IS_NEW=false
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]
    }' >/dev/null
  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  ROLE_IS_NEW=true
  echo "역할 생성 완료"
else
  echo "역할 이미 존재함"
fi

# ListBucket도 함께 부여한다: 상태 파일이 아직 없는 최초 실행에서 S3가
# 404 대신 403(AccessDenied)을 반환하는 동작 때문이다. (ai-bill-alert와 동일)
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name "s3-state-access" \
  --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [
      {\"Effect\": \"Allow\", \"Action\": [\"s3:GetObject\", \"s3:PutObject\"], \"Resource\": \"arn:aws:s3:::${BUCKET_NAME}/*\"},
      {\"Effect\": \"Allow\", \"Action\": \"s3:ListBucket\", \"Resource\": \"arn:aws:s3:::${BUCKET_NAME}\"}
    ]
  }"

if [ "$ROLE_IS_NEW" = true ]; then
  echo "권한 전파 대기 중(10초)..."
  sleep 10
fi
ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)

echo "== 3) Lambda 코드 패키징 =="
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
cp "$REPO_ROOT/lambda/board_handler.py" "$BUILD_DIR/"
cp "$REPO_ROOT/sites.json" "$BUILD_DIR/"
cp -R "$REPO_ROOT/boarddigest" "$BUILD_DIR/boarddigest"
find "$BUILD_DIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
ZIP_PATH="$REPO_ROOT/lambda/board_digest.zip"
rm -f "$ZIP_PATH"
(cd "$BUILD_DIR" && zip -qr "$ZIP_PATH" .)
echo "패키징 완료: $ZIP_PATH ($(du -h "$ZIP_PATH" | cut -f1))"

# ALERT_TO에 콤마가 들어갈 수 있어 shorthand 대신 JSON으로 전달한다.
export ALERT_TO BUCKET_NAME STATE_KEY LOOKBACK_DAYS
ENV_VARS=$(python3 - <<PYJSON
import json, os
print(json.dumps({"Variables": {
    "GMAIL_ADDRESS": os.environ["GMAIL_ADDRESS"],
    "GMAIL_APP_PASSWORD": os.environ["GMAIL_APP_PASSWORD"],
    "ALERT_TO": os.environ["ALERT_TO"],
    "STATE_BUCKET": os.environ["BUCKET_NAME"],
    "STATE_KEY": os.environ["STATE_KEY"],
    "LOOKBACK_DAYS": os.environ["LOOKBACK_DAYS"],
}}, ensure_ascii=False))
PYJSON
)

echo "== 4) Lambda 함수 생성/업데이트 =="
if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FUNCTION_NAME" --region "$REGION" \
    --zip-file "fileb://$ZIP_PATH" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"
  aws lambda update-function-configuration --function-name "$FUNCTION_NAME" --region "$REGION" \
    --environment "$ENV_VARS" --timeout 180 --memory-size 256 >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"
  echo "함수 업데이트 완료"
else
  aws lambda create-function --function-name "$FUNCTION_NAME" --region "$REGION" \
    --runtime python3.12 --handler board_handler.handler --role "$ROLE_ARN" \
    --zip-file "fileb://$ZIP_PATH" --timeout 180 --memory-size 256 \
    --environment "$ENV_VARS" >/dev/null
  echo "함수 생성 완료"
fi

LAMBDA_ARN=$(aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" \
  --query 'Configuration.FunctionArn' --output text)

echo "== 5) EventBridge 스케줄 (매일 KST ${SCHEDULE_HOUR_KST}시) =="
aws events put-rule --name "$RULE_NAME" --region "$REGION" \
  --schedule-expression "cron(0 ${UTC_HOUR} * * ? *)" --state ENABLED >/dev/null

aws lambda add-permission --function-name "$FUNCTION_NAME" --region "$REGION" \
  --statement-id "eventbridge-invoke" --action "lambda:InvokeFunction" \
  --principal events.amazonaws.com \
  --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/${RULE_NAME}" >/dev/null 2>&1 || true

aws events put-targets --rule "$RULE_NAME" --region "$REGION" \
  --targets "Id=1,Arn=$LAMBDA_ARN" >/dev/null

echo ""
echo "배포 완료!"
echo "  함수: $FUNCTION_NAME (리전: $REGION)"
echo "  상태 저장: s3://$BUCKET_NAME/$STATE_KEY"
echo "  스케줄: 매일 KST ${SCHEDULE_HOUR_KST}시 (UTC cron: 0 ${UTC_HOUR} * * ? *)"
echo "  조회 범위: 전날부터 ${LOOKBACK_DAYS}일치"
echo ""
echo "지금 바로 한 번 실행해서 테스트하려면:"
echo "  aws lambda invoke --function-name $FUNCTION_NAME --region $REGION --cli-read-timeout 200 out.json && cat out.json"
echo ""
echo "게시판을 추가/수정한 뒤에는 같은 명령을 다시 실행하면 sites.json이 함수에 반영됩니다."
