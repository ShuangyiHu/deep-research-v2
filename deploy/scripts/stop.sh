#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  stop.sh — 停止所有 ECS 任务（scale to 0，不删除资源）
#  用法: ./stop.sh [--region ap-northeast-1]
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
echo "║      Deep Research — Stopping            ║"
echo "╚══════════════════════════════════════════╝"
echo ""

scale_down() {
  local svc=$1
  echo -n "  ■ ${svc} → desired=0 ... "
  aws ecs update-service \
    --cluster "$CLUSTER" \
    --service "$svc" \
    --desired-count 0 \
    --region "$REGION" \
    --output text \
    --query 'service.serviceName' > /dev/null
  echo "✓"
}

scale_down "${PROJECT}-api"
scale_down "${PROJECT}-worker"

echo ""
echo "  ✓ ECS 任务已停止，不再计费"
echo ""
echo "  💡 ALB + Redis + NAT 仍在运行（~\$1.2/天）"
echo "     彻底删除请运行: ./deploy/scripts/destroy.sh"
echo ""
echo "  下次使用: ./deploy/scripts/start.sh"
echo ""
