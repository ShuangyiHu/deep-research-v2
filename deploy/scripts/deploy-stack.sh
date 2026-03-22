#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  deploy-stack.sh — 首次部署，创建所有 AWS 资源
#
#  只需运行一次。完成后用 build-and-push.sh → start.sh。
#
#  用法:
#    ./deploy-stack.sh \
#      --region ap-northeast-1 \
#      --openai-key sk-... \
#      --anthropic-key sk-ant-... \
#      --google-key AIza...
# ═══════════════════════════════════════════════════════════
set -euo pipefail

# ── 默认值 ──────────────────────────────────────────────────
REGION="${AWS_DEFAULT_REGION:-ap-northeast-1}"
PROJECT="deep-research"
OPENAI_KEY=""
ANTHROPIC_KEY=""
GOOGLE_KEY=""
SENDGRID_KEY="DISABLED"
SENDGRID_FROM="noreply@example.com"

while [[ $# -gt 0 ]]; do
  case $1 in
    --region)        REGION="$2";        shift 2 ;;
    --project)       PROJECT="$2";       shift 2 ;;
    --openai-key)    OPENAI_KEY="$2";    shift 2 ;;
    --anthropic-key) ANTHROPIC_KEY="$2"; shift 2 ;;
    --google-key)    GOOGLE_KEY="$2";    shift 2 ;;
    --sendgrid-key)  SENDGRID_KEY="$2";  shift 2 ;;
    --from-email)    SENDGRID_FROM="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# ── 校验必填参数 ─────────────────────────────────────────────
if [[ -z "$OPENAI_KEY" || -z "$ANTHROPIC_KEY" || -z "$GOOGLE_KEY" ]]; then
  echo ""
  echo "❌ 缺少必填 API Key，完整用法:"
  echo ""
  echo "   ./deploy-stack.sh \\"
  echo "     --region ap-northeast-1 \\"
  echo "     --openai-key sk-xxxxxx \\"
  echo "     --anthropic-key sk-ant-xxxxxx \\"
  echo "     --google-key AIzaxxxxxx"
  echo ""
  exit 1
fi

if ! command -v aws &> /dev/null; then
  echo "❌ 未找到 AWS CLI，请先安装:"
  echo "   https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html"
  exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
TEMPLATE_FILE="$(cd "$(dirname "$0")/.." && pwd)/cloudformation/infrastructure.yaml"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Deep Research — Deploy Infrastructure  ║"
echo "╚══════════════════════════════════════════╝"
echo "  Account : $ACCOUNT_ID"
echo "  Region  : $REGION"
echo "  Stack   : $PROJECT"
echo "  Template: $TEMPLATE_FILE"
echo ""
echo "  预计时间: 10-15 分钟"
echo ""
read -p "  确认部署? (y/N) " -n 1 -r
echo ""
[[ ! $REPLY =~ ^[Yy]$ ]] && echo "  已取消" && exit 0

echo ""
echo "▶ Creating CloudFormation stack..."
echo "  （可前往 AWS Console → CloudFormation 查看实时进度）"
echo ""

aws cloudformation create-stack \
  --stack-name "$PROJECT" \
  --template-body "file://${TEMPLATE_FILE}" \
  --parameters \
    "ParameterKey=ProjectName,ParameterValue=${PROJECT}" \
    "ParameterKey=OpenAIApiKey,ParameterValue=${OPENAI_KEY}" \
    "ParameterKey=AnthropicApiKey,ParameterValue=${ANTHROPIC_KEY}" \
    "ParameterKey=GoogleApiKey,ParameterValue=${GOOGLE_KEY}" \
    "ParameterKey=SendGridApiKey,ParameterValue=${SENDGRID_KEY}" \
    "ParameterKey=SendGridFromEmail,ParameterValue=${SENDGRID_FROM}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$REGION"

echo "  Waiting for stack creation (~10-15 min)..."
aws cloudformation wait stack-create-complete \
  --stack-name "$PROJECT" \
  --region "$REGION"

# ── 读取 Outputs ─────────────────────────────────────────────
get_output() {
  aws cloudformation describe-stacks \
    --stack-name "$PROJECT" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='${1}'].OutputValue" \
    --output text
}

APP_URL=$(get_output "AppURL")
API_ECR=$(get_output "ApiEcrUri")
WORKER_ECR=$(get_output "WorkerEcrUri")

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║      ✓ Infrastructure Ready!             ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  App URL    : ${APP_URL}        (服务启动后可访问)"
echo "  API ECR    : ${API_ECR}"
echo "  Worker ECR : ${WORKER_ECR}"
echo ""
echo "  下一步:"
echo "  1. cd <project_root>"
echo "  2. ./deploy/scripts/build-and-push.sh --region ${REGION}"
echo "  3. ./deploy/scripts/start.sh --region ${REGION}"
echo ""
