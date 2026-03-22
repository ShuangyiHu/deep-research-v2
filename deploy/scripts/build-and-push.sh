#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  build-and-push.sh — 构建 API & Worker 镜像并推送到 ECR
#
#  前置条件:
#    - AWS CLI 已配置 (aws configure)
#    - Docker Desktop 已启动
#    - CloudFormation stack 已部署 (deploy-stack.sh 跑过)
#
#  用法: ./build-and-push.sh [--region ap-northeast-1]
#  在项目根目录运行（Dockerfile.api / Dockerfile.worker 所在目录）
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

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_BASE="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
API_URI="${ECR_BASE}/${PROJECT}-api:latest"
WORKER_URI="${ECR_BASE}/${PROJECT}-worker:latest"

# 脚本所在的 deploy/scripts/，项目根目录是上两级
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Deep Research — Build & Push Images    ║"
echo "╚══════════════════════════════════════════╝"
echo "  Account     : $ACCOUNT_ID"
echo "  Region      : $REGION"
echo "  Project root: $PROJECT_ROOT"
echo ""

# ── Step 1: ECR 登录 ────────────────────────────────────────
echo "[1/6] Logging into ECR..."
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ECR_BASE"
echo "      ✓"

# ── Step 2: 构建 API 镜像 ───────────────────────────────────
echo "[2/6] Building API image (Dockerfile.api)..."
docker build \
  --platform linux/amd64 \
  -f "${PROJECT_ROOT}/Dockerfile.api" \
  -t "${PROJECT}-api:latest" \
  "$PROJECT_ROOT"
echo "      ✓"

# ── Step 3: 推送 API 镜像 ───────────────────────────────────
echo "[3/6] Pushing API image to ECR..."
docker tag "${PROJECT}-api:latest" "$API_URI"
docker push "$API_URI"
echo "      ✓ $API_URI"

# ── Step 4: 构建 Worker 镜像 ────────────────────────────────
echo "[4/6] Building Worker image (Dockerfile.worker)..."
docker build \
  --platform linux/amd64 \
  -f "${PROJECT_ROOT}/Dockerfile.worker" \
  -t "${PROJECT}-worker:latest" \
  "$PROJECT_ROOT"
echo "      ✓"

# ── Step 5: 推送 Worker 镜像 ────────────────────────────────
echo "[5/6] Pushing Worker image to ECR..."
docker tag "${PROJECT}-worker:latest" "$WORKER_URI"
docker push "$WORKER_URI"
echo "      ✓ $WORKER_URI"

# ── Step 6: 更新 CloudFormation stack 的镜像 URI ────────────
echo "[6/6] Updating CloudFormation stack with new image URIs..."
aws cloudformation update-stack \
  --stack-name "$PROJECT" \
  --use-previous-template \
  --parameters \
    "ParameterKey=ApiImageUri,ParameterValue=${API_URI}" \
    "ParameterKey=WorkerImageUri,ParameterValue=${WORKER_URI}" \
    "ParameterKey=OpenAIApiKey,UsePreviousValue=true" \
    "ParameterKey=AnthropicApiKey,UsePreviousValue=true" \
    "ParameterKey=GoogleApiKey,UsePreviousValue=true" \
    "ParameterKey=SendGridApiKey,UsePreviousValue=true" \
    "ParameterKey=SendGridFromEmail,UsePreviousValue=true" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$REGION"

echo "      Waiting for stack update..."
aws cloudformation wait stack-update-complete \
  --stack-name "$PROJECT" \
  --region "$REGION"
echo "      ✓"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║       ✓ Build & Push Complete            ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  API    : $API_URI"
echo "  Worker : $WORKER_URI"
echo ""
echo "  下一步: ./deploy/scripts/start.sh --region ${REGION}"
echo ""
