#!/bin/bash
# Promote a staging-validated image tag to production.
#
# Usage:
#   IMAGE_TAG=abc1234 bash promote.sh
#
# The script:
#   1. Verifies the image tag exists in ECR for collector and analysis
#   2. Updates prod ECS task definitions to use the validated tag
#   3. Updates any historical ECS services while keeping desired count zero
#
# Scheduled tasks must still pass the controlled rollout in DEPLOYMENT.md.

set -euo pipefail
export AWS_PAGER=""

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
DYNAMODB_STATE_TABLE=${DYNAMODB_STATE_TABLE:-vieweratlas-collection-state}
TWITCH_CREDENTIALS_SECRET_ID=${TWITCH_CREDENTIALS_SECRET_ID:-vieweratlas/twitch/credentials}

# Prod always uses the prod SERVICE_PREFIX / cluster
PROD_SERVICE_PREFIX="vieweratlas"
PROD_CLUSTER=${PROD_CLUSTER:-vieweratlas-cluster}

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

[ -n "${IMAGE_TAG:-}" ] || fail "IMAGE_TAG is required (e.g. IMAGE_TAG=abc1234 bash promote.sh)"
[ -n "$AWS_REGION" ]   || fail "AWS_REGION is required"
[ -n "$S3_BUCKET" ]    || fail "S3_BUCKET is required"

AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
[ -n "$AWS_ACCOUNT_ID" ] || fail "AWS credentials not configured. Run: aws configure"

info "Promoting IMAGE_TAG=${IMAGE_TAG} to prod cluster=${PROD_CLUSTER}"
info "  AWS Account: $AWS_ACCOUNT_ID  Region: $AWS_REGION"

# ---------------------------------------------------------------------------
# Step 1: Verify image tag exists in ECR for all services
# ---------------------------------------------------------------------------
info "Verifying image tag ${IMAGE_TAG} exists in ECR..."
for service in collector analysis; do
    repo="vieweratlas-${service}"
    image_exists=$(aws ecr describe-images \
        --repository-name "$repo" \
        --region "$AWS_REGION" \
        --image-ids imageTag="$IMAGE_TAG" \
        --query 'imageDetails[0].imageDigest' \
        --output text 2>/dev/null || echo "")
    if [ -z "$image_exists" ] || [ "$image_exists" = "None" ]; then
        fail "Image tag ${IMAGE_TAG} not found in ECR repo ${repo}. Build and push to staging first."
    fi
    info "  ECR OK: ${repo}:${IMAGE_TAG}"
done

# ---------------------------------------------------------------------------
# Step 2: Register new prod task definitions with the validated IMAGE_TAG
# ---------------------------------------------------------------------------
info "Registering prod task definitions with IMAGE_TAG=${IMAGE_TAG}..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for task in collector analysis; do
    task_def_file="${SCRIPT_DIR}/ecs-task-${task}.json"
    if [ ! -f "$task_def_file" ]; then
        warn "Task definition file not found: $task_def_file — skipping"
        continue
    fi

    temp_file=$(mktemp)
    sed -e "s/\${AWS_ACCOUNT_ID}/$AWS_ACCOUNT_ID/g" \
        -e "s/\${AWS_REGION}/$AWS_REGION/g" \
        -e "s/\${S3_BUCKET}/$S3_BUCKET/g" \
        -e "s#\${S3_PREFIX}#${S3_PREFIX}#g" \
        -e "s/\${IMAGE_TAG}/$IMAGE_TAG/g" \
        -e "s/\${DYNAMODB_STATE_TABLE}/$DYNAMODB_STATE_TABLE/g" \
        -e "s#\${TWITCH_CREDENTIALS_SECRET_ID}#${TWITCH_CREDENTIALS_SECRET_ID}#g" \
        "$task_def_file" > "$temp_file"

    # Strip EFS volume references if EFS_ID not set (prod may not use EFS)
    if [ -z "${EFS_ID:-}" ]; then
        python3 -c "
import json
with open('$temp_file') as f:
    td = json.load(f)
td.pop('volumes', None)
for container in td.get('containerDefinitions', []):
    container.pop('mountPoints', None)
with open('$temp_file', 'w') as f:
    json.dump(td, f, indent=2)
" >/dev/null
    else
        sed -i.bak "s/\${EFS_ID}/$EFS_ID/g" "$temp_file"
        rm -f "$temp_file.bak"
    fi

    info "Registering task definition: ${PROD_SERVICE_PREFIX}-${task}"
    aws ecs register-task-definition \
        --cli-input-json "file://$temp_file" \
        --region "$AWS_REGION" >/dev/null

    rm -f "$temp_file"
done

# ---------------------------------------------------------------------------
# Step 3: Force-deploy prod ECS services
# ---------------------------------------------------------------------------
info "Deploying to prod cluster=${PROD_CLUSTER}..."
for service in collector analysis; do
    full_service_name="${PROD_SERVICE_PREFIX}-${service}"

    task_def_arn=$(aws ecs describe-task-definition \
        --task-definition "$full_service_name" \
        --region "$AWS_REGION" \
        --query 'taskDefinition.taskDefinitionArn' \
        --output text 2>/dev/null || echo "")

    if [ -z "$task_def_arn" ] || [ "$task_def_arn" = "None" ]; then
        warn "Task definition not found for ${full_service_name} — skipping service update"
        continue
    fi

    svc_status=$(aws ecs describe-services \
        --cluster "$PROD_CLUSTER" \
        --services "$full_service_name" \
        --region "$AWS_REGION" \
        --query 'services[0].status' \
        --output text 2>/dev/null || echo "MISSING")

    if [ "$svc_status" != "ACTIVE" ]; then
        warn "Service ${full_service_name} not ACTIVE in ${PROD_CLUSTER} (status=${svc_status}) — skipping"
        continue
    fi

    info "Updating service: ${full_service_name}"
    aws ecs update-service \
        --cluster "$PROD_CLUSTER" \
        --service "$full_service_name" \
        --task-definition "$task_def_arn" \
        --desired-count 0 \
        --force-new-deployment \
        --region "$AWS_REGION" >/dev/null
done

info "Promotion registered: IMAGE_TAG=${IMAGE_TAG}"
warn "Schedules were not changed. Run the small, 100-channel, and full 1,200-channel surveys in DEPLOYMENT.md before enabling production schedules."
