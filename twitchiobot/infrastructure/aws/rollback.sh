#!/bin/bash
# Roll back ECS services to a previous task definition revision.
#
# Usage:
#   ENVIRONMENT=staging bash rollback.sh            # reverts to current_revision - 1
#   TASK_DEF_REVISION=42 ENVIRONMENT=prod bash rollback.sh  # reverts to specific revision
#
# The script:
#   1. Resolves the target revision for each service (previous or explicit)
#   2. Updates ECS services to use that revision
#   3. Waits for services to stabilize
#   4. Runs smoke-test.sh to confirm health

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
S3_BUCKET=${S3_BUCKET:-}
S3_PREFIX=${S3_PREFIX:-vieweratlas/}

# Environment-aware naming (mirrors deploy.sh)
ENVIRONMENT="${ENVIRONMENT:-prod}"
if [ "$ENVIRONMENT" = "prod" ]; then
    SERVICE_PREFIX="vieweratlas"
    ECS_CLUSTER="${ECS_CLUSTER:-vieweratlas-cluster}"
else
    SERVICE_PREFIX="vieweratlas-${ENVIRONMENT}"
    ECS_CLUSTER="${ECS_CLUSTER:-vieweratlas-${ENVIRONMENT}-cluster}"
fi

# Optional: pin all services to this revision number (default: current - 1)
TASK_DEF_REVISION=${TASK_DEF_REVISION:-}

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; }
fail() { err "$1"; exit 1; }

command -v aws >/dev/null 2>&1    || fail "AWS CLI is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"

[ -n "$AWS_REGION" ]  || fail "AWS_REGION is required"
[ -n "$S3_BUCKET" ]   || fail "S3_BUCKET is required"

AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
[ -n "$AWS_ACCOUNT_ID" ] || fail "AWS credentials not configured. Run: aws configure"

info "Rolling back ENVIRONMENT=${ENVIRONMENT} cluster=${ECS_CLUSTER}"
if [ -n "$TASK_DEF_REVISION" ]; then
    info "  Pinned to revision: ${TASK_DEF_REVISION}"
else
    info "  Reverting to: current_revision - 1 (per service)"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Roll back each service
# ---------------------------------------------------------------------------
for service in collector analysis vod-collector; do
    full_service_name="${SERVICE_PREFIX}-${service}"
    task_family="${SERVICE_PREFIX}-${service}"

    # Get current active revision
    current_revision=$(aws ecs describe-task-definition \
        --task-definition "$task_family" \
        --region "$AWS_REGION" \
        --query 'taskDefinition.revision' \
        --output text 2>/dev/null || echo "")

    if [ -z "$current_revision" ] || [ "$current_revision" = "None" ]; then
        warn "Task definition family ${task_family} not found — skipping ${full_service_name}"
        continue
    fi

    # Determine target revision
    if [ -n "$TASK_DEF_REVISION" ]; then
        target_revision="$TASK_DEF_REVISION"
    else
        target_revision=$((current_revision - 1))
    fi

    if [ "$target_revision" -lt 1 ]; then
        warn "No previous revision available for ${task_family} (current=${current_revision}) — skipping"
        continue
    fi

    # Verify target revision exists
    target_arn=$(aws ecs describe-task-definition \
        --task-definition "${task_family}:${target_revision}" \
        --region "$AWS_REGION" \
        --query 'taskDefinition.taskDefinitionArn' \
        --output text 2>/dev/null || echo "")

    if [ -z "$target_arn" ] || [ "$target_arn" = "None" ]; then
        fail "Target task definition ${task_family}:${target_revision} not found — rollback aborted for ${full_service_name}"
    fi

    # Check service exists and is ACTIVE
    svc_status=$(aws ecs describe-services \
        --cluster "$ECS_CLUSTER" \
        --services "$full_service_name" \
        --region "$AWS_REGION" \
        --query 'services[0].status' \
        --output text 2>/dev/null || echo "MISSING")

    if [ "$svc_status" != "ACTIVE" ]; then
        warn "Service ${full_service_name} not ACTIVE (status=${svc_status}) — skipping"
        continue
    fi

    info "Rolling back ${full_service_name}: revision ${current_revision} → ${target_revision}"
    aws ecs update-service \
        --cluster "$ECS_CLUSTER" \
        --service "$full_service_name" \
        --task-definition "${target_arn}" \
        --force-new-deployment \
        --region "$AWS_REGION" >/dev/null

    info "  Rollback triggered for ${full_service_name}"
done

# ---------------------------------------------------------------------------
# Wait for collector service to stabilize
# ---------------------------------------------------------------------------
info "Waiting for ${SERVICE_PREFIX}-collector to stabilize after rollback..."
aws ecs wait services-stable \
    --region "$AWS_REGION" \
    --cluster "$ECS_CLUSTER" \
    --services "${SERVICE_PREFIX}-collector"
info "Collector service stable after rollback"

# ---------------------------------------------------------------------------
# Confirm with smoke test
# ---------------------------------------------------------------------------
info "Running smoke test to confirm rollback health..."
ENVIRONMENT="$ENVIRONMENT" \
AWS_REGION="$AWS_REGION" \
ECS_CLUSTER="$ECS_CLUSTER" \
S3_BUCKET="$S3_BUCKET" \
S3_PREFIX="$S3_PREFIX" \
bash "${SCRIPT_DIR}/smoke-test.sh"

info "Rollback complete for ENVIRONMENT=${ENVIRONMENT}"
