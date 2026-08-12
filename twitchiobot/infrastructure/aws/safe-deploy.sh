#!/bin/bash
# Safe AWS deployment wrapper with cost controls and preflight checks.

set -euo pipefail
export AWS_PAGER=""

load_env_file() {
    local env_file=".env"
    if [ ! -f "$env_file" ]; then
        return
    fi

    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|'#'*)
                continue
                ;;
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

read_config_value() {
    local section=$1
    local key=$2
    local default_value=$3
    local config_file=$4

    local value
    value=$(awk -v s="$section" -v k="$key" '
        $0 ~ "^" s ":" {in_section=1; next}
        in_section && $0 ~ "^[^[:space:]]" {in_section=0}
        in_section && $1 ~ "^" k ":" {
            line=$0
            sub(/#.*/, "", line)
            sub(/^[^:]+:[[:space:]]*/, "", line)
            gsub(/\"/, "", line)
            gsub(/[[:space:]]+$/, "", line)
            print line
            exit
        }
    ' "$config_file")

    if [ -z "$value" ]; then
        value="$default_value"
    fi
    echo "$value"
}

load_env_file

# Environment-aware naming (mirrors deploy.sh)
ENVIRONMENT="${ENVIRONMENT:-prod}"
if [ "$ENVIRONMENT" = "prod" ]; then
    SERVICE_PREFIX="vieweratlas"
    _DEFAULT_CLUSTER="vieweratlas-cluster"
else
    SERVICE_PREFIX="vieweratlas-${ENVIRONMENT}"
    _DEFAULT_CLUSTER="vieweratlas-${ENVIRONMENT}-cluster"
fi

AWS_REGION=${AWS_REGION:-us-east-1}
S3_BUCKET=${S3_BUCKET:-}
S3_PREFIX=${S3_PREFIX:-vieweratlas/}
S3_KEY_PREFIX="${S3_PREFIX%/}"
[ -z "$S3_KEY_PREFIX" ] || S3_KEY_PREFIX="${S3_KEY_PREFIX}/"
ECS_CLUSTER=${ECS_CLUSTER:-$_DEFAULT_CLUSTER}
ASSIGN_PUBLIC_IP=${ASSIGN_PUBLIC_IP:-ENABLED}
ALERT_EMAIL=${ALERT_EMAIL:-}
SUBNET_IDS=${SUBNET_IDS:-}
SECURITY_GROUP_ID=${SECURITY_GROUP_ID:-}
PUSH_LATEST=${PUSH_LATEST:-false}
COLLECTOR_DESIRED_COUNT=0
ANALYSIS_DESIRED_COUNT=${ANALYSIS_DESIRED_COUNT:-0}
BUDGET_LIMIT=${BUDGET_LIMIT:-50}

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    DEFAULT_IMAGE_TAG=$(git rev-parse --short HEAD)
else
    DEFAULT_IMAGE_TAG=$(date +%Y%m%d%H%M%S)
fi
IMAGE_TAG=${IMAGE_TAG:-$DEFAULT_IMAGE_TAG}

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1"; }

AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
if [ -z "$AWS_ACCOUNT_ID" ]; then
    err "AWS CLI not configured or not authenticated"
    exit 1
fi

if [ -z "$S3_BUCKET" ]; then
    err "S3_BUCKET environment variable not set"
    exit 1
fi

CONFIG_FILE="../../config/config.yaml"
if [ ! -f "$CONFIG_FILE" ]; then
    err "Config file not found: $CONFIG_FILE"
    exit 1
fi

MAX_RUNTIME_HOURS=$(read_config_value "collection" "max_runtime_hours" "24" "$CONFIG_FILE")
MAX_COLLECTION_CYCLES=$(read_config_value "collection" "max_collection_cycles" "100" "$CONFIG_FILE")
MAX_VODS_PER_RUN=$(read_config_value "vod" "max_vods_per_run" "50" "$CONFIG_FILE")

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}ViewerAtlas Safe Deployment${NC}"
echo -e "${GREEN}========================================${NC}"

info "AWS Account: $AWS_ACCOUNT_ID"
info "Region: $AWS_REGION"
info "S3 Bucket: $S3_BUCKET"
info "S3 Prefix: $S3_PREFIX"
info "ECS Cluster: $ECS_CLUSTER"
info "Image Tag: $IMAGE_TAG"
info "Push latest: $PUSH_LATEST"
info "All ECS services remain at desired count 0; EventBridge Scheduler launches one-shot tasks"
if [ -n "$SUBNET_IDS" ] && [ -n "$SECURITY_GROUP_ID" ]; then
    info "Network config supplied for service/schedule creation"
else
    err "SUBNET_IDS and SECURITY_GROUP_ID are required for a production deployment"
    exit 1
fi

echo ""
echo "Cost guardrail summary (from config):"
echo "  survey task hard limit: 2 hours"
echo "  expected survey duration: roughly 80 minutes"
echo "  VOD/discovery/worker paths: disabled and not deployed"

if [ -z "$ALERT_EMAIL" ]; then
    err "ALERT_EMAIL is required so budget notifications have an owner"
    exit 1
else
    info "Budget alerts email: $ALERT_EMAIL"
fi

info "Running read-only deployment preflight..."
if ! bash ./deploy.sh --preflight; then
    err "Deployment preflight failed; no AWS resources were changed"
    exit 1
fi

read -p "Continue with safe deployment? [y/N]: " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    warn "Deployment cancelled"
    exit 1
fi

BUDGET_NAME="vieweratlas-monthly-limit"

if [ -n "$ALERT_EMAIL" ]; then
    cat > /tmp/vieweratlas-budget.json <<JSON
{
  "BudgetName": "${BUDGET_NAME}",
  "BudgetLimit": {
    "Amount": "${BUDGET_LIMIT}",
    "Unit": "USD"
  },
  "TimeUnit": "MONTHLY",
  "BudgetType": "COST"
}
JSON

    cat > /tmp/vieweratlas-budget-notifications.json <<JSON
[
  {
    "Notification": {
      "NotificationType": "ACTUAL",
      "ComparisonOperator": "GREATER_THAN",
      "Threshold": 80
    },
    "Subscribers": [
      {
        "SubscriptionType": "EMAIL",
        "Address": "${ALERT_EMAIL}"
      }
    ]
  },
  {
    "Notification": {
      "NotificationType": "FORECASTED",
      "ComparisonOperator": "GREATER_THAN",
      "Threshold": 100
    },
    "Subscribers": [
      {
        "SubscriptionType": "EMAIL",
        "Address": "${ALERT_EMAIL}"
      }
    ]
  }
]
JSON

    if aws budgets create-budget \
        --account-id "$AWS_ACCOUNT_ID" \
        --budget file:///tmp/vieweratlas-budget.json \
        --notifications-with-subscribers file:///tmp/vieweratlas-budget-notifications.json >/dev/null 2>&1; then
        info "Budget alert configured at USD $BUDGET_LIMIT"
    else
        warn "Budget already exists or could not be changed; deployment will continue"
    fi
fi

aws s3 mb "s3://${S3_BUCKET}" --region "$AWS_REGION" >/dev/null 2>&1 || true

aws s3api put-public-access-block \
    --bucket "$S3_BUCKET" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true >/dev/null

aws s3api put-bucket-encryption \
    --bucket "$S3_BUCKET" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' >/dev/null

aws s3api put-bucket-versioning \
    --bucket "$S3_BUCKET" \
    --versioning-configuration Status=Enabled >/dev/null

cat > /tmp/vieweratlas-lifecycle.json <<JSON
{
  "Rules": [
    {
      "ID": "DeleteSurveySnapshotsV2After90Days",
      "Status": "Enabled",
      "Filter": {"Prefix": "${S3_KEY_PREFIX}raw/snapshots/v2/"},
      "Expiration": {"Days": 90},
      "NoncurrentVersionExpiration": {"NoncurrentDays": 7}
    },
    {
      "ID": "DeleteLegacyRawLogs",
      "Status": "Enabled",
      "Filter": {"Prefix": "${S3_KEY_PREFIX}raw/snapshots/"},
      "Transitions": [
        {"Days": 30, "StorageClass": "STANDARD_IA"},
        {"Days": 90, "StorageClass": "GLACIER_IR"}
      ],
      "Expiration": {"Days": 365}
    },
    {
      "ID": "DeleteOldVODRaw",
      "Status": "Enabled",
      "Filter": {"Prefix": "${S3_KEY_PREFIX}raw/vod_chat/"},
      "Transitions": [
        {"Days": 30, "StorageClass": "STANDARD_IA"}
      ],
      "Expiration": {"Days": 90}
    },
    {
      "ID": "ArchiveProcessedData",
      "Status": "Enabled",
      "Filter": {"Prefix": "${S3_KEY_PREFIX}curated/"},
      "Transitions": [
        {"Days": 90, "StorageClass": "STANDARD_IA"}
      ]
    }
  ]
}
JSON

aws s3api put-bucket-lifecycle-configuration \
    --bucket "$S3_BUCKET" \
    --lifecycle-configuration file:///tmp/vieweratlas-lifecycle.json >/dev/null

for log_group in \
    "/ecs/${SERVICE_PREFIX}-collector" \
    "/ecs/${SERVICE_PREFIX}-analysis"; do
    aws logs create-log-group --log-group-name "$log_group" >/dev/null 2>&1 || true
    aws logs put-retention-policy --log-group-name "$log_group" --retention-in-days 7 >/dev/null
done

info "Invoking deploy.sh"
ENVIRONMENT="$ENVIRONMENT" \
AWS_REGION="$AWS_REGION" \
S3_BUCKET="$S3_BUCKET" \
S3_PREFIX="$S3_PREFIX" \
ECS_CLUSTER="$ECS_CLUSTER" \
ASSIGN_PUBLIC_IP="$ASSIGN_PUBLIC_IP" \
SUBNET_IDS="$SUBNET_IDS" \
SECURITY_GROUP_ID="$SECURITY_GROUP_ID" \
ALERT_EMAIL="$ALERT_EMAIL" \
IMAGE_TAG="$IMAGE_TAG" \
PUSH_LATEST="$PUSH_LATEST" \
COLLECTOR_DESIRED_COUNT="$COLLECTOR_DESIRED_COUNT" \
ANALYSIS_DESIRED_COUNT="$ANALYSIS_DESIRED_COUNT" \
bash ./deploy.sh

info "Safe deployment completed"
echo ""
echo "Post-deploy checks:"
echo "  1) Create schedules PAUSED:"
echo "     SURVEY_SCHEDULE_STATE=DISABLED ANALYSIS_SCHEDULE_STATE=DISABLED ./create-schedules.sh"
echo "  2) Follow DEPLOYMENT.md: small, batch, then full survey tests"
echo "  3) Enable schedules only after all three tests pass"
