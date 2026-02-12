# ViewerAtlas Deployment (AWS, Script-First)

This guide reflects the current production deployment model:
- Always-on ECS service: `vieweratlas-collector`
- Scheduled ECS tasks: `vieweratlas-analysis`, `vieweratlas-vod-collector`
- Script-first automation (no Terraform/CDK in this cycle)
- Immutable image tags are the deployment unit

## 1. Prerequisites

- AWS CLI v2 authenticated to target account
- Docker
- Python 3.11+
- Twitch credentials stored in AWS Secrets Manager:
  - `vieweratlas/twitch/oauth_token`
  - `vieweratlas/twitch/client_id`
- Existing SNS topic for alerts (see `twitchiobot/infrastructure/aws/SNS_SETUP.md`)

## 2. Environment Contract

Copy `twitchiobot/infrastructure/aws/.env.example` to `.env` and set required values.

Required for `deploy.sh` / `safe-deploy.sh`:
- `AWS_REGION`
- `S3_BUCKET`
- `S3_PREFIX`
- `ECS_CLUSTER`
- `ASSIGN_PUBLIC_IP`
- `SUBNET_IDS` (required for first service creation)
- `SECURITY_GROUP_ID` (required for first service creation)

Deployment behavior:
- `IMAGE_TAG`: defaults to current git short SHA
- `PUSH_LATEST`: defaults to `false`
- `COLLECTOR_DESIRED_COUNT`, `ANALYSIS_DESIRED_COUNT`, `VOD_COLLECTOR_DESIRED_COUNT`

Required for `create-schedules.sh`:
- `AWS_REGION`
- `AWS_ACCOUNT_ID` (auto-detected if omitted)
- `ECS_CLUSTER`
- `SUBNET_IDS`
- `SECURITY_GROUP_ID`
- `ASSIGN_PUBLIC_IP`

Required for `smoke-test.sh`:
- `AWS_REGION`
- `ECS_CLUSTER`
- `S3_BUCKET`
- Optional: `S3_FRESHNESS_MAX_AGE_MINUTES`

Required for `apply-monitoring.sh`:
- `AWS_REGION`
- `S3_BUCKET`
- `ECS_CLUSTER`
- `SNS_TOPIC_ARN` (or `SNS_TOPIC_NAME`)

## 3. Deploy

```bash
cd twitchiobot/infrastructure/aws
chmod +x deploy.sh safe-deploy.sh create-schedules.sh smoke-test.sh apply-monitoring.sh

# Recommended: guarded deployment (budget/lifecycle/log retention checks + deploy)
./safe-deploy.sh

# Or direct deployment
./deploy.sh
```

`deploy.sh` behavior:
- Idempotently ensures ECR repositories
- Builds and pushes immutable-tag images
- Pushes `latest` only when `PUSH_LATEST=true`
- Idempotently ensures IAM roles/policies
- Registers ECS task definitions
- Creates or updates ECS services idempotently
- If `EFS_ID` is unset, strips EFS volumes/mounts deterministically from task definitions

## 4. Configure Schedules

```bash
cd twitchiobot/infrastructure/aws
./create-schedules.sh
```

`eventbridge-schedules.json` is a reference template only. `create-schedules.sh` is the source of truth.

## 5. Apply Monitoring and Alerts

```bash
cd twitchiobot/infrastructure/aws
./apply-monitoring.sh
```

This applies:
- CloudWatch dashboard from `monitoring-dashboard.yaml`
- CloudWatch metric filters and alarms routed to SNS
- ECS Container Insights enablement for service-level alarms
- Budget threshold + forecast notifications
- Validation of S3 lifecycle rules and CloudWatch log retention

## 6. Post-Deploy Verification (Required)

Run in this order:

1. `./deploy.sh` (or `./safe-deploy.sh`) completes successfully
2. `./smoke-test.sh` passes
3. One manual analysis run succeeds
4. One scheduled VOD run succeeds
5. Objects appear under expected S3 prefixes:
   - `raw/snapshots/`
   - `raw/vod_chat/`
   - `curated/presence_snapshots/`
   - `curated/analysis/`

## 7. Rollback

- Redeploy with a known-good immutable `IMAGE_TAG`
- Force new ECS deployment via `deploy.sh` service update path
- If needed, disable schedules:
  - `aws events disable-rule --name vieweratlas-analysis-daily --region <region>`
  - `aws events disable-rule --name vieweratlas-vod-6h --region <region>`

For incident operations, see `twitchiobot/docs/RUNBOOK.md`.
