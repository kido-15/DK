#!/usr/bin/env bash
# 수집 주소를 자동 점검·수정한 뒤, 실제로 무엇이 걷히는지 보여준다.
# 국내 PC에서 실행해야 한다 (기관 사이트가 해외 IP를 차단함).
set -uo pipefail
cd "$(dirname "$0")/.."

echo "=============================================="
echo " 1단계. 수집 주소 자동 점검 및 수정"
echo "=============================================="
python3 research/discover.py --fix

echo ""
echo "=============================================="
echo " 2단계. 실제 수집 결과 (메일 발송 없음)"
echo "=============================================="
python3 scripts/run_research_digest.py --dry-run --all

echo ""
echo "=============================================="
echo " 결과가 만족스러우면 아래로 배포하세요."
echo ""
echo "   export GMAIL_ADDRESS=you@gmail.com"
echo "   export GMAIL_APP_PASSWORD=앱비밀번호16자리"
echo "   export ALERT_TO=you@gmail.com"
echo "   ./lambda/deploy_research.sh"
echo "=============================================="
