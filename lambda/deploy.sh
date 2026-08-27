#!/usr/bin/env bash
#
# AWS 서울 리전(ap-northeast-2)에 AI 법안 알림 Lambda를 배포/업데이트한다.
#
# 사전 준비:
#   1. AWS CLI 설치 및 `aws configure`로 자격 증명 설정 완료
#   2. 아래 4개 환경변수를 이 스크립트 실행 전에 export
#
# 사용법:
#   export ASSEMBLY_API_KEY=...
#   export GMAIL_ADDRESS=...
#   export GMAIL_APP_PASSWORD=...
#   export ALERT_TO=...       # 여러 명이면 콤마로 구분: a@gmail.com,b@gmail.com
#   ./lambda/deploy.sh

set -euo pipefail

REGION="ap-northeast-2"
FUNCTION_NAME="ai-bill-alert"
ROLE_NAME="ai-bill-alert-lambda-role"
RULE_NAME="ai-bill-alert-schedule"

: "${ASSEMBLY_API_KEY:?ASSEMBLY_API_KEY 환경변수를 설정하세요}"
: "${GMAIL_ADDRESS:?GMAIL_ADDRESS 환경변수를 설정하세요}"
: "${GMAIL_APP_PASSWORD:?GMAIL_APP_PASSWORD 환경변수를 설정하세요}"
ALERT_TO="${ALERT_TO:-$GMAIL_ADDRESS}"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET_NAME="ai-bill-alert-state-${ACCOUNT_ID}"

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

# put-role-policy는 매번 덮어쓰기(idempotent)이므로, 기존 역할의 권한을
# 최신 상태로 맞추기 위해 존재 여부와 무관하게 항상 실행한다.
# GetObject/PutObject만으로는 부족하다: 대상 키가 아직 없을 때(최초 실행)
# S3가 ListBucket 권한도 없으면 404 대신 403(AccessDenied)을 반환하는
# AWS의 동작 때문에 ListBucket도 함께 부여해야 한다.
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
cd "$(dirname "$0")"
rm -f function.zip
zip -q function.zip handler.py
echo "패키징 완료: $(pwd)/function.zip"

# ALERT_TO에 콤마로 구분된 여러 이메일이 들어올 수 있어, 콤마가 구분자로
# 쓰이는 shorthand(Variables={A=..,B=..}) 대신 JSON 형식으로 전달한다.
ENV_VARS=$(cat <<JSON
{
  "Variables": {
    "ASSEMBLY_API_KEY": "$ASSEMBLY_API_KEY",
    "GMAIL_ADDRESS": "$GMAIL_ADDRESS",
    "GMAIL_APP_PASSWORD": "$GMAIL_APP_PASSWORD",
    "ALERT_TO": "$ALERT_TO",
    "STATE_BUCKET": "$BUCKET_NAME"
  }
}
JSON
)

echo "== 4) Lambda 함수 생성/업데이트 =="
if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FUNCTION_NAME" --region "$REGION" \
    --zip-file fileb://function.zip >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"
  aws lambda update-function-configuration --function-name "$FUNCTION_NAME" --region "$REGION" \
    --environment "$ENV_VARS" --timeout 30 >/dev/null
  echo "함수 업데이트 완료"
else
  aws lambda create-function --function-name "$FUNCTION_NAME" --region "$REGION" \
    --runtime python3.12 --handler handler.handler --role "$ROLE_ARN" \
    --zip-file fileb://function.zip --timeout 30 --memory-size 128 \
    --environment "$ENV_VARS" >/dev/null
  echo "함수 생성 완료"
fi

LAMBDA_ARN=$(aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" \
  --query 'Configuration.FunctionArn' --output text)

echo "== 5) EventBridge 스케줄 (매일 KST 09/15/21시) =="
aws events put-rule --name "$RULE_NAME" --region "$REGION" \
  --schedule-expression "cron(0 0,6,12 * * ? *)" --state ENABLED >/dev/null

aws lambda add-permission --function-name "$FUNCTION_NAME" --region "$REGION" \
  --statement-id "eventbridge-invoke" --action "lambda:InvokeFunction" \
  --principal events.amazonaws.com \
  --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/${RULE_NAME}" >/dev/null 2>&1 || true

aws events put-targets --rule "$RULE_NAME" --region "$REGION" \
  --targets "Id=1,Arn=$LAMBDA_ARN" >/dev/null

echo ""
echo "배포 완료!"
echo "  함수: $FUNCTION_NAME (리전: $REGION)"
echo "  상태 저장 버킷: $BUCKET_NAME"
echo "  스케줄: 매일 KST 09/15/21시"
echo ""
echo "지금 바로 한 번 실행해서 테스트하려면:"
echo "  aws lambda invoke --function-name $FUNCTION_NAME --region $REGION --cli-read-timeout 60 out.json && cat out.json"
