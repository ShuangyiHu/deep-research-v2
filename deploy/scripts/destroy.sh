#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  destroy.sh — 彻底删除所有 AWS 资源（不可撤销）
#  用法: ./destroy.sh [--region ap-northeast-1]
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

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  ⚠️  DESTROY — 删除所有 AWS 资源          ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  将删除: VPC, ECS, ElastiCache, ALB, ECR, IAM, Secrets"
echo "  ECR 镜像和 Secrets 里的 API Keys 将永久丢失"
echo ""
read -p "  输入 yes 确认删除 stack '${PROJECT}': " CONFIRM
[[ "$CONFIRM" != "yes" ]] && echo "  已取消" && exit 0

# ── 先清空 ECR（非空 repo CloudFormation 无法删除）────────────
echo ""
echo "  Clearing ECR repositories..."
for repo in api worker; do
  REPO="${PROJECT}-${repo}"
  echo -n "    ${REPO} ... "
  IMAGE_IDS=$(aws ecr list-images \
    --repository-name "$REPO" \
    --region "$REGION" \
    --query 'imageIds[*]' \
    --output json 2>/dev/null || echo "[]")

  if [[ "$IMAGE_IDS" != "[]" && "$IMAGE_IDS" != "" ]]; then
    aws ecr batch-delete-image \
      --repository-name "$REPO" \
      --region "$REGION" \
      --image-ids "$IMAGE_IDS" > /dev/null 2>&1 || true
  fi
  echo "✓"
done

# ── 删除 Stack ───────────────────────────────────────────────
echo ""
echo "  Deleting CloudFormation stack '${PROJECT}'..."
aws cloudformation delete-stack \
  --stack-name "$PROJECT" \
  --region "$REGION"

echo "  Waiting for deletion (~10 min)..."
aws cloudformation wait stack-delete-complete \
  --stack-name "$PROJECT" \
  --region "$REGION"

echo ""
echo "  ✓ Stack '${PROJECT}' 已彻底删除，所有资源释放，不再计费"
echo ""
