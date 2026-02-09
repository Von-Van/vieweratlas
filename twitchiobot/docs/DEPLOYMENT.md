# ViewerAtlas — Deployment Guide

Step-by-step instructions for deploying ViewerAtlas to AWS using ECS Fargate.

> **Estimated time:** 30–45 minutes for first-time setup.
> **Estimated cost:** ~$5–8/month at 100 channels, 4 h/day collection (Spot pricing).

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [One-Time AWS Setup](#2-one-time-aws-setup)
3. [Build & Deploy](#3-build--deploy)
4. [Verify](#4-verify)
5. [Cost Controls](#5-cost-controls)
6. [Local Development (Docker Compose)](#6-local-development-docker-compose)
7. [Troubleshooting](#7-troubleshooting)

---

## 1. Prerequisites

| Requirement | How to check | Install link |
|---|---|---|
| AWS account | — | <https://aws.amazon.com/> |
| AWS CLI v2 | `aws --version` | <https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html> |
| Docker | `docker --version` | <https://docs.docker.com/get-docker/> |
| Python 3.11+ | `python3 --version` | <https://www.python.org/downloads/> |
| Twitch Application | — | <https://dev.twitch.tv/console/apps> |

### Twitch Credentials

1. Go to <https://dev.twitch.tv/console/apps> and create a new application.
2. Note your **Client ID**.
3. Generate an **OAuth token** with the `chat:read` scope (e.g. via <https://twitchapps.com/tmi/>).

### AWS CLI Configuration

```bash
aws configure
# Enter your Access Key ID, Secret Access Key, and default region (us-east-1).
```

---

## 2. One-Time AWS Setup

Run these commands once to provision the infrastructure.

### 2a. Create S3 Bucket

```bash
export AWS_REGION=us-east-1
export S3_BUCKET=vieweratlas-data-lake   # Must be globally unique

aws s3 mb "s3://${S3_BUCKET}" --region "$AWS_REGION"

# Enable versioning
aws s3api put-bucket-versioning \
    --bucket "$S3_BUCKET" \
    --versioning-configuration Status=Enabled
```

### 2b. Configure S3 Lifecycle Rules

Automatically expire old data to control costs:

```bash
cat > /tmp/lifecycle.json <<'EOF'
{
  "Rules": [
    {
      "Id": "DeleteOldRawLogs",
      "Status": "Enabled",
      "Filter": {"Prefix": "raw/snapshots/"},
      "Expiration": {"Days": 30}
    },
    {
      "Id": "DeleteOldVODRaw",
      "Status": "Enabled",
      "Filter": {"Prefix": "raw/vod_chat/"},
      "Expiration": {"Days": 7}
    },
    {
      "Id": "ArchiveProcessedData",
      "Status": "Enabled",
      "Filter": {"Prefix": "curated/"},
      "Transitions": [
        {"Days": 90, "StorageClass": "GLACIER_IR"}
      ]
    }
  ]
}
EOF

aws s3api put-bucket-lifecycle-configuration \
    --bucket "$S3_BUCKET" \
    --lifecycle-configuration file:///tmp/lifecycle.json
```

### 2c. Store Twitch Credentials in Secrets Manager

```bash
aws secretsmanager create-secret \
    --name vieweratlas/twitch/oauth_token \
    --secret-string "your-oauth-token-here" \
    --region "$AWS_REGION"

aws secretsmanager create-secret \
    --name vieweratlas/twitch/client_id \
    --secret-string "your-client-id-here" \
    --region "$AWS_REGION"
```

> **Rotating the OAuth token:** Update the secret value with
> `aws secretsmanager update-secret --secret-id vieweratlas/twitch/oauth_token --secret-string "new-token"`.
> Restart the collector task afterwards.

### 2d. Create ECR Repositories

```bash
for repo in vieweratlas-collector vieweratlas-analysis vieweratlas-vod; do
    aws ecr create-repository \
        --repository-name "$repo" \
        --region "$AWS_REGION" \
        --image-scanning-configuration scanOnPush=true
done
```

### 2e. Create ECS Cluster (Fargate)

```bash
aws ecs create-cluster \
    --cluster-name vieweratlas-cluster \
    --capacity-providers FARGATE FARGATE_SPOT \
    --default-capacity-provider-strategy \
        capacityProvider=FARGATE_SPOT,weight=1 \
    --region "$AWS_REGION"
```

### 2f. Create CloudWatch Log Groups

```bash
for log_group in /ecs/vieweratlas-collector /ecs/vieweratlas-analysis /ecs/vieweratlas-vod-collector; do
    aws logs create-log-group --log-group-name "$log_group" --region "$AWS_REGION" 2>/dev/null || true
    aws logs put-retention-policy \
        --log-group-name "$log_group" \
        --retention-in-days 7
done
```

### 2g. Create IAM Roles

The task and execution roles are defined in `infrastructure/aws/iam-roles.json`.
Create them via the CLI:

```bash
cd infrastructure/aws

# Task role (allows ECS tasks to access S3, CloudWatch)
aws iam create-role \
    --role-name vieweratlas-collector-task-role \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

# Attach S3 and CloudWatch permissions (repeat for analysis + vod roles)
# See iam-roles.json for full policy documents.

# Execution role (allows ECS to pull images from ECR and read Secrets Manager)
aws iam create-role \
    --role-name vieweratlas-collector-execution-role \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

aws iam attach-role-policy \
    --role-name vieweratlas-collector-execution-role \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy

# Grant Secrets Manager access to the execution role
aws iam put-role-policy \
    --role-name vieweratlas-collector-execution-role \
    --policy-name SecretsManagerAccess \
    --policy-document "{
        \"Version\": \"2012-10-17\",
        \"Statement\": [{
            \"Effect\": \"Allow\",
            \"Action\": [\"secretsmanager:GetSecretValue\"],
            \"Resource\": [
                \"arn:aws:secretsmanager:${AWS_REGION}:$(aws sts get-caller-identity --query Account --output text):secret:vieweratlas/twitch/*\"
            ]
        }]
    }"
```

> **Tip:** Repeat the role creation for `vieweratlas-analysis-*` and
> `vieweratlas-vod-collector-*` roles. The analysis role does not need
> Secrets Manager access (it doesn't use Twitch credentials).

### 2h. Networking (VPC / Subnets / Security Group)

ECS Fargate tasks need a VPC with subnets that have internet access (for
Twitch API + S3 access).

**Option A — Use default VPC** (simplest):

```bash
# Get default VPC subnets
aws ec2 describe-subnets \
    --filters "Name=default-for-az,Values=true" \
    --query 'Subnets[].SubnetId' --output text

# Create a security group allowing outbound traffic only
aws ec2 create-security-group \
    --group-name vieweratlas-ecs-sg \
    --description "ViewerAtlas ECS tasks - outbound only" \
    --vpc-id <your-vpc-id>

# Note: Fargate tasks only need outbound (egress) access.
# The default security group allows all outbound traffic.
```

**Option B — Use existing VPC:** Note the subnet IDs and security group ID
for the EventBridge schedule targets.

### 2i. Set Up SNS Topic for Alerts

See [infrastructure/aws/SNS_SETUP.md](infrastructure/aws/SNS_SETUP.md) for
detailed instructions on creating the SNS topic and subscribing your email.

---

## 3. Build & Deploy

### 3a. Configure Environment Variables

```bash
cd infrastructure/aws
cp .env.example .env
# Edit .env with your values:
#   S3_BUCKET, AWS_REGION, ECS_CLUSTER, ALERT_EMAIL, etc.
```

### 3b. Run the Deployment Script

**Safe deploy** (recommended — includes cost guardrails):

```bash
chmod +x safe-deploy.sh deploy.sh
./safe-deploy.sh
```

This will:
1. Create an AWS Budget alert ($50/month)
2. Configure S3 lifecycle rules
3. Set CloudWatch log retention
4. Build all three Docker images
5. Push images to ECR
6. Register ECS task definitions
7. Optionally update running ECS services

**Manual deploy** (if you've already run the one-time setup):

```bash
./deploy.sh
```

### 3c. Create ECS Services

After task definitions are registered, create the services:

```bash
# Collector — long-running service
aws ecs create-service \
    --cluster vieweratlas-cluster \
    --service-name vieweratlas-collector \
    --task-definition vieweratlas-collector \
    --desired-count 1 \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[<subnet-id>],securityGroups=[<sg-id>],assignPublicIp=ENABLED}" \
    --capacity-provider-strategy capacityProvider=FARGATE_SPOT,weight=1 \
    --region "$AWS_REGION"
```

### 3d. Schedule Analysis & VOD Tasks (EventBridge)

Analysis and VOD collection run on schedules rather than as always-on services:

```bash
# Create EventBridge role (see eventbridge-schedules.json for full policy)
aws iam create-role \
    --role-name vieweratlas-eventbridge-ecs-role \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"events.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

# Daily analysis at 3 AM UTC
aws events put-rule \
    --name vieweratlas-analysis-daily \
    --schedule-expression "cron(0 3 * * ? *)" \
    --description "Run analysis pipeline daily"

# VOD collection every 6 hours
aws events put-rule \
    --name vieweratlas-vod-6h \
    --schedule-expression "rate(6 hours)" \
    --description "Run VOD collector every 6 hours"

# Attach targets (see eventbridge-schedules.json for full target config)
```

---

## 4. Verify

### Check Collector Logs

```bash
aws logs tail /ecs/vieweratlas-collector --follow --region "$AWS_REGION"
```

### Confirm S3 Snapshots Are Landing

```bash
aws s3 ls "s3://${S3_BUCKET}/vieweratlas/raw/snapshots/" --recursive | head -20
```

### Trigger a Manual Analysis Run

```bash
aws ecs run-task \
    --cluster vieweratlas-cluster \
    --task-definition vieweratlas-analysis \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[<subnet-id>],securityGroups=[<sg-id>],assignPublicIp=ENABLED}" \
    --region "$AWS_REGION"
```

### View Running Tasks

```bash
aws ecs list-tasks --cluster vieweratlas-cluster --region "$AWS_REGION"
```

---

## 5. Cost Controls

ViewerAtlas includes multiple layers of cost protection:

| Layer | Mechanism | Default |
|---|---|---|
| **AWS Budget** | Email alert at 80%; hard alert at 100% | $50/month |
| **S3 Lifecycle** | Auto-delete raw snapshots after 30 days, VOD raw after 7 days, Glacier after 90 days | Enabled |
| **CloudWatch Logs** | Retention set to 7 days | Enabled |
| **ECS Spot** | FARGATE_SPOT capacity provider (70% discount) | Enabled |
| **Task runtime** | `max_runtime_hours` in config.yaml | 24 h (collector), 4 h (VOD) |
| **Collection cycles** | `max_collection_cycles` in config.yaml | 100 |
| **VOD limit** | `max_vods_per_run` in config.yaml | 50 |

### Adjusting Cost Protection

Edit `config/config.yaml`:

```yaml
collection:
  max_runtime_hours: 8        # Reduce to lower ECS costs
  max_collection_cycles: 24

vod:
  max_vods_per_run: 10        # Reduce VOD processing
  max_processing_hours: 2
```

### EFS — Optional

The ECS task definitions reference an EFS filesystem for `channels.txt` and
`vod_queue.json`. This is **optional**:

- **channels.txt** is baked into the Docker image and auto-updated at runtime
  by `update_channels.py`, so EFS is unnecessary for the collector.
- **vod_queue.json** can be stored in S3 instead of EFS.

If you don't use EFS, leave `EFS_ID` unset in `.env`. The deploy script will
automatically strip the `volumes` and `mountPoints` sections from task
definitions.

---

## 6. Local Development (Docker Compose)

For local testing without AWS:

```bash
cd infrastructure/docker

# Create .env in the docker directory (or symlink from aws/.env)
cat > .env <<EOF
TWITCH_OAUTH_TOKEN=oauth:your-token-here
TWITCH_CLIENT_ID=your-client-id
STORAGE_TYPE=file
EOF

# Build and start all services
docker-compose up --build

# Or run just the collector
docker-compose up --build collector

# Run analysis on collected data
docker-compose run analysis
```

### Verify Docker Images Build

```bash
docker-compose build collector
docker-compose build analysis
docker-compose build vod-collector
```

---

## 7. Troubleshooting

### Container fails to start

```bash
# Check ECS task stopped reason
aws ecs describe-tasks \
    --cluster vieweratlas-cluster \
    --tasks <task-arn> \
    --query 'tasks[0].{status:lastStatus,reason:stoppedReason,containers:containers[*].{name:name,reason:reason,exitCode:exitCode}}' \
    --region "$AWS_REGION"

# Check CloudWatch logs
aws logs tail /ecs/vieweratlas-collector --since 1h --region "$AWS_REGION"
```

### "Auth failed" in collector logs

The Twitch OAuth token has expired. Update Secrets Manager:

```bash
aws secretsmanager update-secret \
    --secret-id vieweratlas/twitch/oauth_token \
    --secret-string "new-oauth-token" \
    --region "$AWS_REGION"

# Restart the collector service to pick up the new token
aws ecs update-service \
    --cluster vieweratlas-cluster \
    --service vieweratlas-collector \
    --force-new-deployment \
    --region "$AWS_REGION"
```

### No data in S3 after collection

1. Verify the collector is running: `aws ecs list-tasks --cluster vieweratlas-cluster`
2. Check that `STORAGE_TYPE=s3` is set in the task definition environment
3. Verify the task role has S3 write permissions
4. Check CloudWatch logs for errors

### Analysis produces "Community N" generic labels

This means channel metadata (game, language) isn't being propagated. Ensure:
1. The collector's `TWITCH_CLIENT_ID` is set (needed for Helix API calls)
2. Snapshots in S3 contain `game_name` and `language` fields
3. The analysis task is reading from the correct S3 prefix

---

## Architecture Reference

```
┌──────────────┐   ┌───────────────┐   ┌──────────────────────┐
│ ECR          │   │ Secrets Mgr   │   │ EventBridge          │
│ 3 repos      │   │ oauth_token   │   │ analysis: daily 3AM  │
│              │   │ client_id     │   │ vod: every 6h        │
└──────┬───────┘   └──────┬────────┘   └──────┬───────────────┘
       │                  │                    │
       ▼                  ▼                    ▼
┌─────────────────────────────────────────────────────────────┐
│                     ECS FARGATE CLUSTER                     │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ Collector   │  │ Analysis     │  │ VOD Collector    │   │
│  │ 0.25 vCPU   │  │ 1 vCPU       │  │ 0.5 vCPU         │   │
│  │ Long-running│  │ Scheduled    │  │ Scheduled        │   │
│  └──────┬──────┘  └──────┬───────┘  └──────┬───────────┘   │
└─────────┼────────────────┼─────────────────┼───────────────┘
          │                │                  │
          ▼                ▼                  ▼
┌─────────────────────────────────────────────────────────────┐
│                      S3 DATA LAKE                           │
│  raw/snapshots/          ← live chat        (30-day TTL)    │
│  raw/vod_chat/           ← VOD chat          (7-day TTL)    │
│  curated/presence_snapshots/  ← Parquet     (90d→Glacier)   │
│  curated/analysis/       ← graph + partition results        │
└─────────────────────────────────────────────────────────────┘
```
