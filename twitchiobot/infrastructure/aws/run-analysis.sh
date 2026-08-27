#!/usr/bin/env bash
# Launch the latest analysis task once and wait for its container exit status.

set -euo pipefail
export AWS_PAGER=""

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"

load_env_file() {
    local env_file="$SCRIPT_DIR/.env"
    [ -f "$env_file" ] || return
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in ''|'#'*) continue ;; esac
        line="${line#export }"
        [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
        local key="${line%%=*}"
        local value="${line#*=}"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
        [ -z "${!key+x}" ] || continue
        export "$key=$value"
    done < "$env_file"
}

load_env_file

ENVIRONMENT=${ENVIRONMENT:-prod}
if [ "$ENVIRONMENT" = "prod" ]; then
    SERVICE_PREFIX="vieweratlas"
    DEFAULT_CLUSTER="vieweratlas-cluster"
else
    SERVICE_PREFIX="vieweratlas-${ENVIRONMENT}"
    DEFAULT_CLUSTER="vieweratlas-${ENVIRONMENT}-cluster"
fi

AWS_REGION=${AWS_REGION:-us-east-1}
ECS_CLUSTER=${ECS_CLUSTER:-$DEFAULT_CLUSTER}
SUBNET_IDS=${SUBNET_IDS:-}
SECURITY_GROUP_ID=${SECURITY_GROUP_ID:-}
ASSIGN_PUBLIC_IP=${ASSIGN_PUBLIC_IP:-ENABLED}
ANALYSIS_WAIT_TIMEOUT_SECONDS=${ANALYSIS_WAIT_TIMEOUT_SECONDS:-7200}

fail() { echo "[ERROR] $1" >&2; exit 1; }
info() { echo "[INFO] $1"; }

command -v aws >/dev/null 2>&1 || fail "AWS CLI is required"
[ -n "$SUBNET_IDS" ] || fail "SUBNET_IDS is required in .env"
[ -n "$SECURITY_GROUP_ID" ] || fail "SECURITY_GROUP_ID is required in .env"
[[ "$ASSIGN_PUBLIC_IP" =~ ^(ENABLED|DISABLED)$ ]] || \
    fail "ASSIGN_PUBLIC_IP must be ENABLED or DISABLED"
[[ "$ANALYSIS_WAIT_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || \
    fail "ANALYSIS_WAIT_TIMEOUT_SECONDS must be a positive integer"
[ "$ANALYSIS_WAIT_TIMEOUT_SECONDS" -gt 0 ] || \
    fail "ANALYSIS_WAIT_TIMEOUT_SECONDS must be a positive integer"

task_definition=$(aws ecs describe-task-definition \
    --region "$AWS_REGION" \
    --task-definition "${SERVICE_PREFIX}-analysis" \
    --query 'taskDefinition.taskDefinitionArn' \
    --output text) || fail "Could not resolve the latest analysis task definition"

network_configuration="awsvpcConfiguration={subnets=[${SUBNET_IDS}],securityGroups=[${SECURITY_GROUP_ID}],assignPublicIp=${ASSIGN_PUBLIC_IP}}"
task_arn=$(aws ecs run-task \
    --region "$AWS_REGION" \
    --cluster "$ECS_CLUSTER" \
    --task-definition "$task_definition" \
    --launch-type FARGATE \
    --platform-version LATEST \
    --count 1 \
    --enable-ecs-managed-tags \
    --started-by "vieweratlas-manual-analysis-$(date +%Y%m%d%H%M%S)" \
    --network-configuration "$network_configuration" \
    --query 'tasks[0].taskArn' \
    --output text) || fail "Could not launch the analysis task"

[ -n "$task_arn" ] && [ "$task_arn" != "None" ] || \
    fail "ECS did not return an analysis task ARN"
info "Analysis started: $task_arn"
info "Waiting up to ${ANALYSIS_WAIT_TIMEOUT_SECONDS}s; Ctrl-C stops only this local waiter"

deadline=$(( $(date +%s) + ANALYSIS_WAIT_TIMEOUT_SECONDS ))
while true; do
    status=$(aws ecs describe-tasks \
        --region "$AWS_REGION" \
        --cluster "$ECS_CLUSTER" \
        --tasks "$task_arn" \
        --query 'tasks[0].lastStatus' \
        --output text)
    if [ "$status" = "STOPPED" ]; then
        break
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
        fail "Timed out waiting for analysis; the ECS task may still be running: $task_arn"
    fi
    sleep 15
done

exit_code=$(aws ecs describe-tasks \
    --region "$AWS_REGION" \
    --cluster "$ECS_CLUSTER" \
    --tasks "$task_arn" \
    --query 'tasks[0].containers[?name==`analysis`].exitCode | [0]' \
    --output text)
stopped_reason=$(aws ecs describe-tasks \
    --region "$AWS_REGION" \
    --cluster "$ECS_CLUSTER" \
    --tasks "$task_arn" \
    --query 'tasks[0].stoppedReason' \
    --output text)

if [ "$exit_code" != "0" ]; then
    fail "Analysis failed (exit=${exit_code:-unknown}): ${stopped_reason:-no reason returned}"
fi

info "Analysis completed successfully"
info "Next: ./smoke-test.sh analysis"
