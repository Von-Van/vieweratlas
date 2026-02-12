#!/bin/bash
# Create or update EventBridge schedules for analysis and VOD ECS tasks.

set -euo pipefail

load_env_file() {
    local env_file=".env"
    if [ ! -f "$env_file" ]; then
        return
    fi

    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|'#'*) continue ;;
        esac
        line="${line#export }"
        if [[ ! "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
            continue
        fi
        local key="${line%%=*}"
        local value="${line#*=}"
        value="${value%\"}"
        value="${value#\"}"
        value="${value%\'}"
        value="${value#\'}"
        export "$key=$value"
    done < "$env_file"
}

load_env_file

AWS_REGION=${AWS_REGION:-us-east-1}
ECS_CLUSTER=${ECS_CLUSTER:-vieweratlas-cluster}
ASSIGN_PUBLIC_IP=${ASSIGN_PUBLIC_IP:-ENABLED}
SUBNET_IDS=${SUBNET_IDS:-}
SECURITY_GROUP_ID=${SECURITY_GROUP_ID:-}
AWS_ACCOUNT_ID=${AWS_ACCOUNT_ID:-}
ANALYSIS_SCHEDULE=${ANALYSIS_SCHEDULE:-cron(0 3 * * ? *)}
VOD_SCHEDULE=${VOD_SCHEDULE:-rate(6 hours)}
ANALYSIS_RULE_NAME=${ANALYSIS_RULE_NAME:-vieweratlas-analysis-daily}
VOD_RULE_NAME=${VOD_RULE_NAME:-vieweratlas-vod-6h}
EVENTBRIDGE_ROLE_NAME=${EVENTBRIDGE_ROLE_NAME:-vieweratlas-eventbridge-ecs-role}

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1"; }

if ! command -v aws >/dev/null 2>&1; then
    err "AWS CLI is required"
    exit 1
fi

if [ -z "$AWS_REGION" ] || [ -z "$ECS_CLUSTER" ]; then
    err "AWS_REGION and ECS_CLUSTER are required"
    exit 1
fi

if [ -z "$SUBNET_IDS" ] || [ -z "$SECURITY_GROUP_ID" ]; then
    err "SUBNET_IDS and SECURITY_GROUP_ID are required"
    exit 1
fi

if [ -z "$AWS_ACCOUNT_ID" ]; then
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
fi

CLUSTER_ARN="arn:aws:ecs:${AWS_REGION}:${AWS_ACCOUNT_ID}:cluster/${ECS_CLUSTER}"
ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${EVENTBRIDGE_ROLE_NAME}"

ANALYSIS_TASK_ARN=$(aws ecs describe-task-definition \
    --task-definition vieweratlas-analysis \
    --region "$AWS_REGION" \
    --query 'taskDefinition.taskDefinitionArn' \
    --output text)

VOD_TASK_ARN=$(aws ecs describe-task-definition \
    --task-definition vieweratlas-vod-collector \
    --region "$AWS_REGION" \
    --query 'taskDefinition.taskDefinitionArn' \
    --output text)

ensure_eventbridge_role() {
    local trust_json policy_json
    trust_json=$(mktemp)
    policy_json=$(mktemp)

    cat > "$trust_json" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Service": "events.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }
  ]
}
JSON

    cat > "$policy_json" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["ecs:RunTask"],
      "Resource": [
        "arn:aws:ecs:${AWS_REGION}:${AWS_ACCOUNT_ID}:task-definition/vieweratlas-analysis:*",
        "arn:aws:ecs:${AWS_REGION}:${AWS_ACCOUNT_ID}:task-definition/vieweratlas-vod-collector:*"
      ],
      "Condition": {
        "ArnLike": {
          "ecs:cluster": "${CLUSTER_ARN}"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": ["iam:PassRole"],
      "Resource": [
        "arn:aws:iam::${AWS_ACCOUNT_ID}:role/vieweratlas-analysis-task-role",
        "arn:aws:iam::${AWS_ACCOUNT_ID}:role/vieweratlas-analysis-execution-role",
        "arn:aws:iam::${AWS_ACCOUNT_ID}:role/vieweratlas-vod-collector-task-role",
        "arn:aws:iam::${AWS_ACCOUNT_ID}:role/vieweratlas-vod-collector-execution-role"
      ]
    }
  ]
}
JSON

    if ! aws iam get-role --role-name "$EVENTBRIDGE_ROLE_NAME" >/dev/null 2>&1; then
        info "Creating EventBridge role: $EVENTBRIDGE_ROLE_NAME"
        aws iam create-role \
            --role-name "$EVENTBRIDGE_ROLE_NAME" \
            --assume-role-policy-document "file://$trust_json" >/dev/null
    else
        info "EventBridge role exists: $EVENTBRIDGE_ROLE_NAME"
    fi

    aws iam put-role-policy \
        --role-name "$EVENTBRIDGE_ROLE_NAME" \
        --policy-name ViewerAtlasEventBridgeECS \
        --policy-document "file://$policy_json" >/dev/null

    rm -f "$trust_json" "$policy_json"
}

put_target() {
    local rule_name=$1
    local task_arn=$2
    local target_id=$3

    local target_file
    target_file=$(mktemp)

    python3 - "$target_file" "$target_id" "$CLUSTER_ARN" "$ROLE_ARN" "$task_arn" "$SUBNET_IDS" "$SECURITY_GROUP_ID" "$ASSIGN_PUBLIC_IP" <<'PY'
import json
import sys

out = sys.argv[1]
target_id = sys.argv[2]
cluster_arn = sys.argv[3]
role_arn = sys.argv[4]
task_arn = sys.argv[5]
subnets_csv = sys.argv[6]
security_group = sys.argv[7]
assign_public_ip = sys.argv[8]

subnets = [s.strip() for s in subnets_csv.split(',') if s.strip()]

payload = [
    {
        "Id": target_id,
        "Arn": cluster_arn,
        "RoleArn": role_arn,
        "EcsParameters": {
            "TaskDefinitionArn": task_arn,
            "TaskCount": 1,
            "LaunchType": "FARGATE",
            "PlatformVersion": "LATEST",
            "NetworkConfiguration": {
                "awsvpcConfiguration": {
                    "Subnets": subnets,
                    "SecurityGroups": [security_group],
                    "AssignPublicIp": assign_public_ip,
                }
            },
        },
    }
]

with open(out, "w", encoding="utf-8") as f:
    json.dump(payload, f)
PY

    aws events put-targets \
        --region "$AWS_REGION" \
        --rule "$rule_name" \
        --targets "file://$target_file" >/dev/null
    rm -f "$target_file"
}

ensure_eventbridge_role

info "Upserting EventBridge rule: $ANALYSIS_RULE_NAME ($ANALYSIS_SCHEDULE)"
aws events put-rule \
    --region "$AWS_REGION" \
    --name "$ANALYSIS_RULE_NAME" \
    --schedule-expression "$ANALYSIS_SCHEDULE" \
    --description "Run ViewerAtlas analysis task" \
    --state ENABLED >/dev/null
put_target "$ANALYSIS_RULE_NAME" "$ANALYSIS_TASK_ARN" "vieweratlas-analysis-target"

info "Upserting EventBridge rule: $VOD_RULE_NAME ($VOD_SCHEDULE)"
aws events put-rule \
    --region "$AWS_REGION" \
    --name "$VOD_RULE_NAME" \
    --schedule-expression "$VOD_SCHEDULE" \
    --description "Run ViewerAtlas VOD collector task" \
    --state ENABLED >/dev/null
put_target "$VOD_RULE_NAME" "$VOD_TASK_ARN" "vieweratlas-vod-target"

info "Schedules configured successfully"
warn "Review schedules with: aws events list-rules --name-prefix vieweratlas- --region $AWS_REGION"
