#!/bin/bash
# Idempotently create the one-shot survey and daily analysis schedules with
# EventBridge Scheduler. Scheduler time zones are required so Eastern daylight
# saving changes do not shift the intended local run times.

set -euo pipefail
export AWS_PAGER=""

load_env_file() {
    local env_file=".env"
    [ -f "$env_file" ] || return
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in ''|'#'*) continue ;; esac
        line="${line#export }"
        [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
        local key="${line%%=*}"
        local value="${line#*=}"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
        # Explicit command-line environment values take precedence over .env.
        [ -z "${!key+x}" ] || continue
        export "$key=$value"
    done < "$env_file"
}

load_env_file

ENVIRONMENT="${ENVIRONMENT:-prod}"
if [ "$ENVIRONMENT" = "prod" ]; then
    SERVICE_PREFIX="vieweratlas"
    DEFAULT_CLUSTER="vieweratlas-cluster"
else
    SERVICE_PREFIX="vieweratlas-${ENVIRONMENT}"
    DEFAULT_CLUSTER="vieweratlas-${ENVIRONMENT}-cluster"
fi

AWS_REGION=${AWS_REGION:-us-east-1}
AWS_ACCOUNT_ID=${AWS_ACCOUNT_ID:-}
ECS_CLUSTER=${ECS_CLUSTER:-$DEFAULT_CLUSTER}
ASSIGN_PUBLIC_IP=${ASSIGN_PUBLIC_IP:-ENABLED}
SUBNET_IDS=${SUBNET_IDS:-}
SECURITY_GROUP_ID=${SECURITY_GROUP_ID:-}
SCHEDULE_TIMEZONE=${SCHEDULE_TIMEZONE:-America/New_York}
SURVEY_SCHEDULE=${SURVEY_SCHEDULE:-cron(0 6,14,22 * * ? *)}
ANALYSIS_SCHEDULE=${ANALYSIS_SCHEDULE:-cron(0 1 * * ? *)}
SURVEY_SCHEDULE_NAME=${SURVEY_SCHEDULE_NAME:-${SERVICE_PREFIX}-survey-three-daily}
ANALYSIS_SCHEDULE_NAME=${ANALYSIS_SCHEDULE_NAME:-${SERVICE_PREFIX}-analysis-daily}
SURVEY_SCHEDULE_STATE=${SURVEY_SCHEDULE_STATE:-ENABLED}
ANALYSIS_SCHEDULE_STATE=${ANALYSIS_SCHEDULE_STATE:-ENABLED}
SCHEDULER_ROLE_NAME=${SCHEDULER_ROLE_NAME:-${SERVICE_PREFIX}-scheduler-ecs-role}
LEGACY_ANALYSIS_RULE_NAME=${LEGACY_ANALYSIS_RULE_NAME:-${SERVICE_PREFIX}-analysis-daily}
LEGACY_VOD_RULE_NAME=${LEGACY_VOD_RULE_NAME:-${SERVICE_PREFIX}-vod-6h}

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1"; }
fail() { err "$1"; exit 1; }

command -v aws >/dev/null 2>&1 || fail "AWS CLI is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
[ -n "$SUBNET_IDS" ] || fail "SUBNET_IDS is required"
[ -n "$SECURITY_GROUP_ID" ] || fail "SECURITY_GROUP_ID is required"
[[ "$ASSIGN_PUBLIC_IP" =~ ^(ENABLED|DISABLED)$ ]] || fail "ASSIGN_PUBLIC_IP must be ENABLED or DISABLED"
[[ "$SURVEY_SCHEDULE_STATE" =~ ^(ENABLED|DISABLED)$ ]] || fail "SURVEY_SCHEDULE_STATE must be ENABLED or DISABLED"
[[ "$ANALYSIS_SCHEDULE_STATE" =~ ^(ENABLED|DISABLED)$ ]] || fail "ANALYSIS_SCHEDULE_STATE must be ENABLED or DISABLED"

validate_subnet_egress() {
    local subnet_id vpc_id route_table_id route_target
    local -a subnet_ids
    IFS=',' read -r -a subnet_ids <<< "$SUBNET_IDS"
    for subnet_id in "${subnet_ids[@]}"; do
        vpc_id=$(aws ec2 describe-subnets \
            --subnet-ids "$subnet_id" --region "$AWS_REGION" \
            --query 'Subnets[0].VpcId' --output text 2>/dev/null || true)
        [ -n "$vpc_id" ] && [ "$vpc_id" != "None" ] || fail "Subnet was not found: $subnet_id"
        route_table_id=$(aws ec2 describe-route-tables \
            --filters "Name=association.subnet-id,Values=$subnet_id" \
            --region "$AWS_REGION" --query 'RouteTables[0].RouteTableId' \
            --output text 2>/dev/null || true)
        if [ -z "$route_table_id" ] || [ "$route_table_id" = "None" ]; then
            route_table_id=$(aws ec2 describe-route-tables \
                --filters "Name=vpc-id,Values=$vpc_id" "Name=association.main,Values=true" \
                --region "$AWS_REGION" --query 'RouteTables[0].RouteTableId' \
                --output text 2>/dev/null || true)
        fi
        [ -n "$route_table_id" ] && [ "$route_table_id" != "None" ] || \
            fail "No route table found for subnet $subnet_id"

        if [ "$ASSIGN_PUBLIC_IP" = "ENABLED" ]; then
            route_target=$(aws ec2 describe-route-tables \
                --route-table-ids "$route_table_id" --region "$AWS_REGION" \
                --query 'RouteTables[0].Routes[?DestinationCidrBlock==`0.0.0.0/0`].GatewayId | [0]' \
                --output text 2>/dev/null || true)
            [[ "$route_target" =~ ^igw- ]] || \
                fail "Subnet $subnet_id needs an Internet Gateway default route when ASSIGN_PUBLIC_IP=ENABLED"
        else
            route_target=$(aws ec2 describe-route-tables \
                --route-table-ids "$route_table_id" --region "$AWS_REGION" \
                --query 'RouteTables[0].Routes[?DestinationCidrBlock==`0.0.0.0/0`].NatGatewayId | [0]' \
                --output text 2>/dev/null || true)
            [[ "$route_target" =~ ^nat- ]] || \
                fail "ASSIGN_PUBLIC_IP=DISABLED requires NAT egress; set it to ENABLED for the current public subnets"
        fi
    done
}

validate_subnet_egress

if [ -z "$AWS_ACCOUNT_ID" ]; then
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
fi

CLUSTER_ARN="arn:aws:ecs:${AWS_REGION}:${AWS_ACCOUNT_ID}:cluster/${ECS_CLUSTER}"
ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${SCHEDULER_ROLE_NAME}"

SURVEY_TASK_ARN=$(aws ecs describe-task-definition \
    --task-definition "${SERVICE_PREFIX}-collector" \
    --region "$AWS_REGION" \
    --query 'taskDefinition.taskDefinitionArn' \
    --output text)
ANALYSIS_TASK_ARN=$(aws ecs describe-task-definition \
    --task-definition "${SERVICE_PREFIX}-analysis" \
    --region "$AWS_REGION" \
    --query 'taskDefinition.taskDefinitionArn' \
    --output text)

ensure_scheduler_role() {
    local trust_file policy_file
    trust_file=$(mktemp)
    policy_file=$(mktemp)

    python3 - "$trust_file" <<'PY'
import json, sys
payload = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "scheduler.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
PY

    python3 - "$policy_file" "$AWS_REGION" "$AWS_ACCOUNT_ID" "$SERVICE_PREFIX" "$CLUSTER_ARN" <<'PY'
import json, sys
_, output, region, account, prefix, cluster_arn = sys.argv
payload = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "ecs:RunTask",
            "Resource": [
                f"arn:aws:ecs:{region}:{account}:task-definition/{prefix}-collector:*",
                f"arn:aws:ecs:{region}:{account}:task-definition/{prefix}-analysis:*",
            ],
            "Condition": {"ArnEquals": {"ecs:cluster": cluster_arn}},
        },
        {
            "Effect": "Allow",
            "Action": "iam:PassRole",
            "Resource": [
                f"arn:aws:iam::{account}:role/{prefix}-collector-task-role",
                f"arn:aws:iam::{account}:role/{prefix}-collector-execution-role",
                f"arn:aws:iam::{account}:role/{prefix}-analysis-task-role",
                f"arn:aws:iam::{account}:role/{prefix}-analysis-execution-role",
            ],
        },
    ],
}
with open(output, "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
PY

    if aws iam get-role --role-name "$SCHEDULER_ROLE_NAME" >/dev/null 2>&1; then
        info "Updating Scheduler role: $SCHEDULER_ROLE_NAME"
        aws iam update-assume-role-policy \
            --role-name "$SCHEDULER_ROLE_NAME" \
            --policy-document "file://$trust_file" >/dev/null
    else
        info "Creating Scheduler role: $SCHEDULER_ROLE_NAME"
        aws iam create-role \
            --role-name "$SCHEDULER_ROLE_NAME" \
            --assume-role-policy-document "file://$trust_file" >/dev/null
    fi

    aws iam put-role-policy \
        --role-name "$SCHEDULER_ROLE_NAME" \
        --policy-name ViewerAtlasSchedulerECS \
        --policy-document "file://$policy_file" >/dev/null
    rm -f "$trust_file" "$policy_file"
}

upsert_schedule() {
    local name=$1 expression=$2 task_arn=$3 state=$4 description=$5
    local target_file
    target_file=$(mktemp)

    python3 - "$target_file" "$CLUSTER_ARN" "$ROLE_ARN" "$task_arn" "$SUBNET_IDS" "$SECURITY_GROUP_ID" "$ASSIGN_PUBLIC_IP" <<'PY'
import json, sys
_, output, cluster, role, task, subnets_csv, security_group, public_ip = sys.argv
target = {
    "Arn": cluster,
    "RoleArn": role,
    "EcsParameters": {
        "TaskDefinitionArn": task,
        "TaskCount": 1,
        "LaunchType": "FARGATE",
        "PlatformVersion": "LATEST",
        "EnableECSManagedTags": True,
        "NetworkConfiguration": {
            "awsvpcConfiguration": {
                "Subnets": [value.strip() for value in subnets_csv.split(",") if value.strip()],
                "SecurityGroups": [security_group],
                "AssignPublicIp": public_ip,
            }
        },
    },
    # This bounds delivery retries. The application separately enforces the
    # two-hour survey runtime and releases its DynamoDB lease on exit.
    "RetryPolicy": {"MaximumEventAgeInSeconds": 7200, "MaximumRetryAttempts": 0},
}
with open(output, "w", encoding="utf-8") as handle:
    json.dump(target, handle)
PY

    if aws scheduler get-schedule --region "$AWS_REGION" --name "$name" >/dev/null 2>&1; then
        info "Updating Scheduler schedule: $name ($expression, $SCHEDULE_TIMEZONE, $state)"
        aws scheduler update-schedule \
            --region "$AWS_REGION" \
            --name "$name" \
            --description "$description" \
            --schedule-expression "$expression" \
            --schedule-expression-timezone "$SCHEDULE_TIMEZONE" \
            --flexible-time-window '{"Mode":"OFF"}' \
            --state "$state" \
            --target "file://$target_file" >/dev/null
    else
        info "Creating Scheduler schedule: $name ($expression, $SCHEDULE_TIMEZONE, $state)"
        aws scheduler create-schedule \
            --region "$AWS_REGION" \
            --name "$name" \
            --description "$description" \
            --schedule-expression "$expression" \
            --schedule-expression-timezone "$SCHEDULE_TIMEZONE" \
            --flexible-time-window '{"Mode":"OFF"}' \
            --state "$state" \
            --target "file://$target_file" >/dev/null
    fi
    rm -f "$target_file"
}

ensure_scheduler_role
upsert_schedule \
    "$SURVEY_SCHEDULE_NAME" "$SURVEY_SCHEDULE" "$SURVEY_TASK_ARN" "$SURVEY_SCHEDULE_STATE" \
    "Survey up to 1,200 Twitch channels at 6 AM, 2 PM, and 10 PM Eastern"
upsert_schedule \
    "$ANALYSIS_SCHEDULE_NAME" "$ANALYSIS_SCHEDULE" "$ANALYSIS_TASK_ARN" "$ANALYSIS_SCHEDULE_STATE" \
    "Run ViewerAtlas analysis daily at 1 AM Eastern"

# The previous deployment used an EventBridge Rule at 03:00 UTC. Scheduler and
# Rules are separate services, so explicitly disable that legacy trigger.
if aws events describe-rule --region "$AWS_REGION" --name "$LEGACY_ANALYSIS_RULE_NAME" >/dev/null 2>&1; then
    aws events disable-rule --region "$AWS_REGION" --name "$LEGACY_ANALYSIS_RULE_NAME" >/dev/null
    info "Disabled legacy EventBridge Rule: $LEGACY_ANALYSIS_RULE_NAME"
else
    info "No legacy 03:00 UTC EventBridge Rule found"
fi

if aws events describe-rule --region "$AWS_REGION" --name "$LEGACY_VOD_RULE_NAME" >/dev/null 2>&1; then
    aws events disable-rule --region "$AWS_REGION" --name "$LEGACY_VOD_RULE_NAME" >/dev/null
    info "Disabled retired VOD EventBridge Rule: $LEGACY_VOD_RULE_NAME"
fi

info "Schedules configured successfully"
info "Survey:  $SURVEY_SCHEDULE_NAME ($SURVEY_SCHEDULE, $SCHEDULE_TIMEZONE, $SURVEY_SCHEDULE_STATE)"
info "Analysis: $ANALYSIS_SCHEDULE_NAME ($ANALYSIS_SCHEDULE, $SCHEDULE_TIMEZONE, $ANALYSIS_SCHEDULE_STATE)"
info "Review them with: aws scheduler list-schedules --region $AWS_REGION --name-prefix ${SERVICE_PREFIX}-"
