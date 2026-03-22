# Deep Research — AWS 部署手册

## 整体流程图

```
Step 1: 安装工具           →  aws-cli, docker
Step 2: 部署 CloudFormation →  创建所有 AWS 资源（VPC、Redis、ECS、ALB…）
Step 3: 构建并推送镜像      →  docker build → ECR
Step 4: 更新 Stack          →  填入真实镜像 URI
Step 5: 启动 Worker         →  1条命令
Step 6: 使用 / 关闭         →  用完关 Worker 省钱
```

---

## Step 1 — 准备本地工具

```bash
# 安装 AWS CLI（macOS）
brew install awscli

# 配置凭证（需要 IAM 用户，权限包含 CloudFormation / ECS / ECR / ElastiCache）
aws configure
# 输入: AWS Access Key ID, Secret Access Key, Region (e.g. ap-southeast-1), output format: json

# 验证配置
aws sts get-caller-identity
```

---

## Step 2 — 部署 CloudFormation Stack（首次，用 Placeholder 镜像）

> 第一次部署时镜像还没推送，先用 Placeholder，Stack 会创建好所有资源。

```bash
aws cloudformation create-stack \
  --stack-name deep-research \
  --template-body file://deep-research-cfn.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters \
    ParameterKey=OpenAIApiKey,ParameterValue="sk-YOUR_KEY" \
    ParameterKey=AnthropicApiKey,ParameterValue="sk-ant-YOUR_KEY" \
    ParameterKey=GoogleApiKey,ParameterValue="AIza_YOUR_KEY" \
    ParameterKey=SendGridApiKey,ParameterValue="SG.YOUR_KEY"
```

等待 Stack 创建完成（约 10-15 分钟）：

```bash
aws cloudformation wait stack-create-complete --stack-name deep-research

# 查看输出（ECR URI、ALB 地址等）
aws cloudformation describe-stacks \
  --stack-name deep-research \
  --query 'Stacks[0].Outputs' \
  --output table
```

---

## Step 3 — 构建并推送 Docker 镜像到 ECR

```bash
# 获取你的 Account ID 和 Region
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure get region)

# 登录 ECR
aws ecr get-login-password --region $REGION | \
  docker login --username AWS --password-stdin \
  $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com

# ── API 镜像 ─────────────────────────────────────
docker build -f Dockerfile.api \
  -t $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/deep-research-api:latest .

docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/deep-research-api:latest

# ── Worker 镜像 ──────────────────────────────────
docker build -f Dockerfile.worker \
  -t $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/deep-research-worker:latest .

docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/deep-research-worker:latest
```

---

## Step 4 — 更新 Stack，填入真实镜像 URI

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure get region)

aws cloudformation update-stack \
  --stack-name deep-research \
  --template-body file://deep-research-cfn.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters \
    ParameterKey=OpenAIApiKey,UsePreviousValue=true \
    ParameterKey=AnthropicApiKey,UsePreviousValue=true \
    ParameterKey=GoogleApiKey,UsePreviousValue=true \
    ParameterKey=SendGridApiKey,UsePreviousValue=true \
    ParameterKey=ApiImageUri,ParameterValue="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/deep-research-api:latest" \
    ParameterKey=WorkerImageUri,ParameterValue="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/deep-research-worker:latest"

# 等待更新完成
aws cloudformation wait stack-update-complete --stack-name deep-research
```

---

## Step 5 — 正常使用流程

### 开始工作时（启动 Worker）

```bash
aws ecs update-service \
  --cluster deep-research-cluster \
  --service deep-research-worker \
  --desired-count 1 \
  --region $(aws configure get region)
```

等约 1 分钟 Worker 启动后，打开浏览器：

```bash
# 获取 ALB 地址
aws cloudformation describe-stacks \
  --stack-name deep-research \
  --query 'Stacks[0].Outputs[?OutputKey==`AppURL`].OutputValue' \
  --output text
```

### 用完之后（关闭 Worker 省钱）

```bash
aws ecs update-service \
  --cluster deep-research-cluster \
  --service deep-research-worker \
  --desired-count 0 \
  --region $(aws configure get region)
```

> API 和 Gradio 服务保持运行（费用很低，约 $1.5/天），
> Worker 关闭后不产生计算费用。
> 如果连 API 也不需要在线，可以把它们也改成 desired-count=0。

---

## Step 6 — 完全关闭（彻底省钱）

```bash
# 全部服务降为 0
for svc in deep-research-api deep-research-gradio deep-research-worker; do
  aws ecs update-service \
    --cluster deep-research-cluster \
    --service $svc \
    --desired-count 0 \
    --region $(aws configure get region)
done
```

注意：ElastiCache Redis 即使没有任务也会产生费用（约 $13/月）。
如果长时间不用，可以在 AWS Console 手动删除 Redis 节点，
需要时重新创建（或者直接删除整个 Stack）。

---

## 删除所有资源（彻底清理）

```bash
aws cloudformation delete-stack --stack-name deep-research

# 等待删除完成（约 10 分钟）
aws cloudformation wait stack-delete-complete --stack-name deep-research
```

> ⚠️ ECR 镜像不会自动删除，需手动清理：
> aws ecr delete-repository --repository-name deep-research-api --force
> aws ecr delete-repository --repository-name deep-research-worker --force

---

## 费用参考（按使用模式）

| 场景 | API+Gradio | Worker | Redis | 月费估算 |
|------|-----------|--------|-------|---------|
| 全天在线 | 运行 | 运行 | 运行 | ~$75 |
| 仅 API 在线，按需开 Worker | 运行 | 按需 | 运行 | ~$45 |
| **全部按需（推荐）** | **按需** | **按需** | **运行** | **~$15** |
| 彻底关闭 | 关 | 关 | 关 | $0 |

---

## 常见问题

**Q: ECS 服务启动后一直 PENDING？**
检查 CloudWatch Logs：
```bash
aws logs tail /ecs/deep-research-api --follow
```

**Q: Gradio 连不上 API？**
`ui/app.py` 里的 `API_BASE` 需要改成 ALB 地址：
```python
# 修改 deep_research/ui/app.py 第7行
API_BASE = "http://<ALB_DNS_NAME>/api/v1"
```
改完之后重新 docker build → push → 更新 Stack。

**Q: 镜像拉取失败（ImagePullBackOff）？**
ECS Task 需要网络访问 ECR。本模板已设置
`AssignPublicIp: ENABLED` + 公有子网，应该没问题。
如果仍报错，检查 ECS Security Group 的 Outbound 规则是否允许 443。

**Q: Worker 拉取不到任务？**
确认 Worker 环境变量里的 Redis 地址和 API Service 一致。
可以进入 ECS Console → 任务 → 查看环境变量确认。
