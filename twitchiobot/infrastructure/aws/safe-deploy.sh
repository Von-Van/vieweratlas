#!/bin/bash
# Safe AWS deployment wrapper with cost controls and preflight checks.

set -euo pipefail

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

AWS_REGION=${AWS_REGION:-us-east-1}
S3_BUCKET=${S3_BUCKET:-}
S3_PREFIX=${S3_PREFIX:-vieweratlas/}
ECS_CLUSTER=${ECS_CLUSTER:-vieweratlas-cluster}
ASSIGN_PUBLIC_IP=${ASSIGN_PUBLIC_IP:-ENABLED}
ALERT_EMAIL=${ALERT_EMAIL:-}
SUBNET_IDS=${SUBNET_IDS:-}
SECURITY_GROUP_ID=${SECURITY_GROUP_ID:-}
PUSH_LATEST=${PUSH_LATEST:-false}
COLLECTOR_DESIRED_COUNT=${COLLECTOR_DESIRED_COUNT:-1}
ANALYSIS_DESIRED_COUNT=${ANALYSIS_DESIRED_COUNT:-0}
VOD_COLLECTOR_DESIRED_COUNT=${VOD_COLLECTOR_DESIRED_COUNT:-0}

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
info "Desired counts: collector=$COLLECTOR_DESIRED_COUNT analysis=$ANALYSIS_DESIRED_COUNT vod=$VOD_COLLECTOR_DESIRED_COUNT"
if [ -n "$SUBNET_IDS" ] && [ -n "$SECURITY_GROUP_ID" ]; then
    info "Network config supplied for service/schedule creation"
else
    warn "SUBNET_IDS and SECURITY_GROUP_ID are not fully set"
fi

echo ""
echo "Cost guardrail summary (from config):"
echo "  collection.max_runtime_hours: $MAX_RUNTIME_HOURS"
echo "  collection.max_collection_cycles: $MAX_COLLECTION_CYCLES"
echo "  vod.max_vods_per_run: $MAX_VODS_PER_RUN"

if [ -z "$ALERT_EMAIL" ]; then
    warn "ALERT_EMAIL not set. Budget notifications will not be emailed."
else
    info "Budget alerts email: $ALERT_EMAIL"
fi

read -p "Continue with safe deployment? [y/N]: " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    warn "Deployment cancelled"
    exit 1
fi

BUDGET_NAME="vieweratlas-monthly-limit"
BUDGET_LIMIT=${BUDGET_LIMIT:-50}

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

    aws budgets create-budget \
        --account-id "$AWS_ACCOUNT_ID" \
        --budget file:///tmp/vieweratlas-budget.json \
        --notifications-with-subscribers file:///tmp/vieweratlas-budget-notifications.json >/dev/null 2>&1 || \
        warn "Budget already exists or could not be created"
    info "Budget alert configured at USD $BUDGET_LIMIT"
fi

aws s3 mb "s3://${S3_BUCKET}" --region "$AWS_REGION" >/dev/null 2>&1 || true
aws s3api put-bucket-versioning \
    --bucket "$S3_BUCKET" \
    --versioning-configuration Status=Enabled >/dev/null

cat > /tmp/vieweratlas-lifecycle.json <<'JSON'
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
        {
          "Days": 90,
          "StorageClass": "GLACIER_IR"
        }
      ]
    }
  ]
}
JSON

aws s3api put-bucket-lifecycle-configuration \
    --bucket "$S3_BUCKET" \
    --lifecycle-configuration file:///tmp/vieweratlas-lifecycle.json >/dev/null

for log_group in "/ecs/vieweratlas-collector" "/ecs/vieweratlas-analysis" "/ecs/vieweratlas-vod-collector"; do
    aws logs create-log-group --log-group-name "$log_group" >/dev/null 2>&1 || true
    aws logs put-retention-policy --log-group-name "$log_group" --retention-in-days 7 >/dev/null
 done

info "Invoking deploy.sh"
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
VOD_COLLECTOR_DESIRED_COUNT="$VOD_COLLECTOR_DESIRED_COUNT" \
bash ./deploy.sh

info "Safe deployment completed"
echo ""
echo "Post-deploy checks:"
echo "  1) bash ./smoke-test.sh"
echo "  2) aws logs tail /ecs/vieweratlas-collector --follow --region $AWS_REGION"
echo "  3) aws s3 ls s3://$S3_BUCKET/${S3_PREFIX}raw/snapshots/ --recursive | tail"
