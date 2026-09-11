#!/usr/bin/env bash
#
# AWS 서울 리전(ap-northeast-2)에 AI 연구자료 알림 Lambda를 배포/업데이트한다.
#
# 국내 기관 사이트는 해외 IP를 막는 곳이 많아 서울 리전에서 실행해야 한다.
# (기존 ai-bill-alert 와 같은 이유)
#
# 사용법:
#   export GMAIL_ADDRESS=you@gmail.com
#   export GMAIL_APP_PASSWORD=앱비밀번호16자리
#   export ALERT_TO=you@gmail.com      # 여러 명이면 콤마 구분
#   ./lambda/deploy_research.sh

set -euo pipefail

REGION="ap-northeast-2"
FUNCTION_NAME="ai-research-digest"
ROLE_NAME="ai-research-digest-lambda-role"
RULE_NAME="ai-research-digest-schedule"
# 매일 KST 09시 = UTC 00시
SCHEDULE="cron(0 0 * * ? *)"

: "${GMAIL_ADDRESS:?GMAIL_ADDRESS 환경변수를 설정하세요}"
: "${GMAIL_APP_PASSWORD:?GMAIL_APP_PASSWORD 환경변수를 설정하세요}"
ALERT_TO="${ALERT_TO:-$GMAIL_ADDRESS}"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET_NAME="ai-research-digest-state-${ACCOUNT_ID}"

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

# 대상 키가 아직 없을 때 S3는 ListBucket 권한이 없으면 404 대신 403을 반환한다.
# 최초 실행에서 걸리지 않도록 ListBucket도 함께 부여한다. (ai-bill-alert에서 겪은 문제)
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
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT

# handler가 `import collector` / `import digest` 로 찾을 수 있게 zip 루트에 평평하게 담는다.
cp "$REPO_ROOT/lambda/research_handler.py" "$BUILD_DIR/"
cp "$REPO_ROOT/research/collector.py" "$REPO_ROOT/research/digest.py" \
   "$REPO_ROOT/research/sources.json" "$BUILD_DIR/"
(cd "$BUILD_DIR" && zip -q -r function.zip .)
echo "패키징 완료 ($(du -h "$BUILD_DIR/function.zip" | cut -f1))"

ENV_VARS=$(cat <<JSON
{
  "Variables": {
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
    --zip-file "fileb://$BUILD_DIR/function.zip" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"
  aws lambda update-function-configuration --function-name "$FUNCTION_NAME" --region "$REGION" \
    --environment "$ENV_VARS" --timeout 180 --memory-size 256 >/dev/null
  echo "함수 업데이트 완료"
else
  aws lambda create-function --function-name "$FUNCTION_NAME" --region "$REGION" \
    --runtime python3.12 --handler research_handler.handler --role "$ROLE_ARN" \
    --zip-file "fileb://$BUILD_DIR/function.zip" --timeout 180 --memory-size 256 \
    --environment "$ENV_VARS" >/dev/null
  echo "함수 생성 완료"
fi

LAMBDA_ARN=$(aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" \
  --query 'Configuration.FunctionArn' --output text)

echo "== 5) EventBridge 스케줄 (매일 KST 09시) =="
aws events put-rule --name "$RULE_NAME" --region "$REGION" \
  --schedule-expression "$SCHEDULE" --state ENABLED >/dev/null

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
echo "  스케줄: 매일 KST 09시"
echo ""
echo "지금 한 번 실행해서 확인하려면:"
echo "  aws lambda invoke --function-name $FUNCTION_NAME --region $REGION --cli-read-timeout 200 out.json && cat out.json"
echo ""
echo "첫 실행은 기준선만 저장하고 메일을 보내지 않습니다. 두 번째 실행부터 신규 자료만 발송됩니다."
echo "응답의 failed_sources 에 뜬 소스는 sources.json의 url/link_pattern 점검이 필요합니다."
