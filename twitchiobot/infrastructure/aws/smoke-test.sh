#!/bin/bash
# Post-deploy production smoke checks for ViewerAtlas.

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

AWS_REGION=${AWS_REGION:-}
ECS_CLUSTER=${ECS_CLUSTER:-}
S3_BUCKET=${S3_BUCKET:-}
S3_PREFIX=${S3_PREFIX:-vieweratlas/}
S3_SNAPSHOT_PREFIX=${S3_SNAPSHOT_PREFIX:-${S3_PREFIX%/}/raw/snapshots/}
S3_FRESHNESS_MAX_AGE_MINUTES=${S3_FRESHNESS_MAX_AGE_MINUTES:-180}
LOG_LOOKBACK_MINUTES=${LOG_LOOKBACK_MINUTES:-30}

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1"; }
fail() { err "$1"; exit 1; }

command -v aws >/dev/null 2>&1 || fail "AWS CLI is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"

[ -n "$AWS_REGION" ] || fail "AWS_REGION is required"
[ -n "$ECS_CLUSTER" ] || fail "ECS_CLUSTER is required"
[ -n "$S3_BUCKET" ] || fail "S3_BUCKET is required"

info "Running smoke checks for cluster=$ECS_CLUSTER region=$AWS_REGION"

cluster_status=$(aws ecs describe-clusters \
    --region "$AWS_REGION" \
    --clusters "$ECS_CLUSTER" \
    --query 'clusters[0].status' \
    --output text 2>/dev/null || true)

[ "$cluster_status" = "ACTIVE" ] || fail "ECS cluster not ACTIVE: $ECS_CLUSTER (status=${cluster_status:-missing})"
info "ECS cluster is ACTIVE"

for family in vieweratlas-collector vieweratlas-analysis vieweratlas-vod-collector; do
    arn=$(aws ecs describe-task-definition \
        --region "$AWS_REGION" \
        --task-definition "$family" \
        --query 'taskDefinition.taskDefinitionArn' \
        --output text 2>/dev/null || true)
    [ -n "$arn" ] && [ "$arn" != "None" ] || fail "Task definition not resolvable: $family"
    info "Task definition OK: $family"
done

service_name="vieweratlas-collector"
service_status=$(aws ecs describe-services \
    --region "$AWS_REGION" \
    --cluster "$ECS_CLUSTER" \
    --services "$service_name" \
    --query 'services[0].status' \
    --output text 2>/dev/null || true)

[ "$service_status" = "ACTIVE" ] || fail "Collector service missing or not ACTIVE: $service_name"

info "Waiting for collector service to stabilize"
aws ecs wait services-stable \
    --region "$AWS_REGION" \
    --cluster "$ECS_CLUSTER" \
    --services "$service_name"

desired_count=$(aws ecs describe-services \
    --region "$AWS_REGION" \
    --cluster "$ECS_CLUSTER" \
    --services "$service_name" \
    --query 'services[0].desiredCount' \
    --output text)
running_count=$(aws ecs describe-services \
    --region "$AWS_REGION" \
    --cluster "$ECS_CLUSTER" \
    --services "$service_name" \
    --query 'services[0].runningCount' \
    --output text)

if [ "$desired_count" -gt 0 ] && [ "$running_count" -lt 1 ]; then
    fail "Collector service not healthy: desired=$desired_count running=$running_count"
fi
info "Collector service healthy: desired=$desired_count running=$running_count"

log_start_ms=$(python3 - <<'PY'
import time
import os
lookback = int(os.environ.get("LOG_LOOKBACK_MINUTES", "30"))
print(int((time.time() - lookback * 60) * 1000))
PY
)

recent_log_count=$(aws logs filter-log-events \
    --region "$AWS_REGION" \
    --log-group-name "/ecs/vieweratlas-collector" \
    --start-time "$log_start_ms" \
    --query 'length(events)' \
    --output text 2>/dev/null || true)

if [ -z "$recent_log_count" ] || [ "$recent_log_count" = "None" ] || [ "$recent_log_count" = "0" ]; then
    fail "No recent collector log events in the last ${LOG_LOOKBACK_MINUTES} minutes"
fi
info "Recent collector logs present (${recent_log_count} events)"

latest_snapshot_ts=$(aws s3api list-objects-v2 \
    --region "$AWS_REGION" \
    --bucket "$S3_BUCKET" \
    --prefix "$S3_SNAPSHOT_PREFIX" \
    --query 'sort_by(Contents,&LastModified)[-1].LastModified' \
    --output text 2>/dev/null || true)

if [ -z "$latest_snapshot_ts" ] || [ "$latest_snapshot_ts" = "None" ]; then
    fail "No snapshot objects found at s3://${S3_BUCKET}/${S3_SNAPSHOT_PREFIX}"
fi

snapshot_age_seconds=$(python3 - "$latest_snapshot_ts" <<'PY'
import datetime
import sys

raw = sys.argv[1]
latest = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
now = datetime.datetime.now(datetime.timezone.utc)
print(int((now - latest).total_seconds()))
PY
)

max_age_seconds=$((S3_FRESHNESS_MAX_AGE_MINUTES * 60))
if [ "$snapshot_age_seconds" -gt "$max_age_seconds" ]; then
    fail "Latest snapshot is too old (${snapshot_age_seconds}s > ${max_age_seconds}s)"
fi
info "Snapshot freshness OK (${snapshot_age_seconds}s old, threshold=${max_age_seconds}s)"

info "Smoke test passed"
