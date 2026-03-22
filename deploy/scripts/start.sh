#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  start.sh — 启动 API + Worker（scale to 1）
#  用法: ./start.sh [--region ap-northeast-1]
# ═══════════════════════════════════════════════════════════
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-ap-northeast-1}"
PROJECT="deep-research"

while [[ $# -gt 0 ]]; do
  case $1 in
    --region)  REGION="$2";  shift 2 ;;
    --project) PROJECT="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

CLUSTER="${PROJECT}-cluster"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║      Deep Research — Starting Up         ║"
echo "╚══════════════════════════════════════════╝"
echo "  Cluster: $CLUSTER  |  Region: $REGION"
echo ""

scale_up() {
  local svc=$1
  echo -n "  ▶ ${svc} → desired=1 ... "
  aws ecs update-service \
    --cluster "$CLUSTER" \
    --service "$svc" \
    --desired-count 1 \
    --region "$REGION" \
    --output text \
    --query 'service.serviceName' > /dev/null
  echo "✓"
}

scale_up "${PROJECT}-api"
scale_up "${PROJECT}-worker"

echo ""
echo "  Waiting for services to be stable (≈2 min)..."
echo "  （Ctrl+C 退出等待不影响启动，服务会在后台继续拉起）"
echo ""

for svc in api worker; do
  echo -n "  ⏳ ${PROJECT}-${svc} ... "
  aws ecs wait services-stable \
    --cluster "$CLUSTER" \
    --services "${PROJECT}-${svc}" \
    --region "$REGION"
  echo "Running ✓"
done

# ── 获取访问地址 ─────────────────────────────────────────────
APP_URL=$(aws cloudformation describe-stacks \
  --stack-name "$PROJECT" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='AppURL'].OutputValue" \
  --output text 2>/dev/null || echo "(请前往 CloudFormation Outputs 查看)")

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║          ✓ All Services Running          ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  🌐 Gradio UI : ${APP_URL}"
echo "  📖 API Docs  : ${APP_URL}/docs"
echo "  ❤️  Health    : ${APP_URL}/health"
echo ""
echo "  用完请执行: ./deploy/scripts/stop.sh"
echo ""
