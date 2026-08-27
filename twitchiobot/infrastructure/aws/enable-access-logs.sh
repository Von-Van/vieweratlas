#!/bin/bash
# Enable CloudFront standard access logging (v2) into the private data lake.
#
# This is the whole of ViewerAtlas site analytics: no JavaScript, no cookies, no
# third party, and no change to the site's Content-Security-Policy. Requests are
# already passing through CloudFront; this only asks it to write them down.
#
# Two deliberate choices, both of which have to stay deliberate:
#
#   1. Logging v2 (CloudWatch Logs delivery), not legacy standard logging.
#      Legacy logging writes with S3 ACLs, and the buckets safe-deploy.sh creates
#      leave ObjectOwnership at the BucketOwnerEnforced default, where ACLs are
#      disabled. Legacy logging cannot deliver to them at all.
#
#   2. No viewer IP address. RECORD_FIELDS below omits c-ip, c-ip-version,
#      cs(Cookie) and x-forwarded-for, so the logs hold no identifier for the
#      person making the request. That costs unique-visitor counts — these logs
#      can report views, not people — and it is the point: a project whose
#      premise is a private data boundary should not log its readers to count
#      them. Adding c-ip back is a privacy decision, not a config tweak, and
#      needs DATA_POLICY.md updated to match.
#
# Usage:
#   DISTRIBUTION_ID=E123ABC ./enable-access-logs.sh

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

ENVIRONMENT="${ENVIRONMENT:-prod}"
if [ "$ENVIRONMENT" = "prod" ]; then
    SERVICE_PREFIX="vieweratlas"
else
    SERVICE_PREFIX="vieweratlas-${ENVIRONMENT}"
fi

AWS_REGION=${AWS_REGION:-us-east-1}
AWS_ACCOUNT_ID=${AWS_ACCOUNT_ID:-}
S3_BUCKET=${S3_BUCKET:-}
S3_PREFIX=${S3_PREFIX:-}
DISTRIBUTION_ID=${DISTRIBUTION_ID:-}

S3_KEY_PREFIX="${S3_PREFIX%/}"
[ -z "$S3_KEY_PREFIX" ] || S3_KEY_PREFIX="${S3_KEY_PREFIX}/"
LOG_PREFIX="${S3_KEY_PREFIX}analytics/cloudfront"

SOURCE_NAME="${SERVICE_PREFIX}-access-logs-source"
DESTINATION_NAME="${SERVICE_PREFIX}-access-logs-destination"

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $1"; }
fail() { echo -e "${RED}[ERROR]${NC} $1" >&2; exit 1; }

[ -n "$S3_BUCKET" ] || fail "S3_BUCKET is required (set it in .env)"
[ -n "$DISTRIBUTION_ID" ] || fail "DISTRIBUTION_ID is required"

if [ -z "$AWS_ACCOUNT_ID" ]; then
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
fi

DISTRIBUTION_ARN="arn:aws:cloudfront::${AWS_ACCOUNT_ID}:distribution/${DISTRIBUTION_ID}"
BUCKET_ARN="arn:aws:s3:::${S3_BUCKET}"

# Everything needed to answer "what got viewed, from where, and did it work",
# and nothing that identifies who asked. Keep this list in step with the column
# order in analytics-schema.sql — Athena maps these positionally.
RECORD_FIELDS="date,time,x-edge-location,sc-bytes,cs-method,cs-uri-stem,sc-status,cs(Referer),cs(User-Agent),cs-uri-query,x-edge-result-type,x-host-header,time-taken"

info "Distribution : ${DISTRIBUTION_ID}"
info "Destination  : s3://${S3_BUCKET}/${LOG_PREFIX}/"
info "Record fields: ${RECORD_FIELDS}"
echo ""

# ------------------------------------------------------------------
# Step 1: Delivery source — the distribution's access logs
# ------------------------------------------------------------------
info "[1/3] Creating delivery source..."
aws logs put-delivery-source \
    --name "$SOURCE_NAME" \
    --resource-arn "$DISTRIBUTION_ARN" \
    --log-type ACCESS_LOGS \
    --region us-east-1 >/dev/null
info "  Delivery source: ${SOURCE_NAME}"

# ------------------------------------------------------------------
# Step 2: Delivery destination — the private data lake bucket
# ------------------------------------------------------------------
info "[2/3] Creating delivery destination..."
DESTINATION_ARN=$(aws logs put-delivery-destination \
    --name "$DESTINATION_NAME" \
    --delivery-destination-configuration "destinationResourceArn=${BUCKET_ARN}" \
    --region us-east-1 \
    --query "deliveryDestination.arn" --output text)
info "  Delivery destination: ${DESTINATION_ARN}"

# ------------------------------------------------------------------
# Step 3: Join them, selecting the fields and the S3 layout
# ------------------------------------------------------------------
# Hive-style partitioning so Athena's partition projection can prune by date
# without a crawler.
info "[3/3] Creating delivery..."
aws logs create-delivery \
    --delivery-source-name "$SOURCE_NAME" \
    --delivery-destination-arn "$DESTINATION_ARN" \
    --record-fields "$RECORD_FIELDS" \
    --s3-delivery-configuration \
        "suffixPath=${LOG_PREFIX}/year={yyyy}/month={MM}/day={dd},enableHiveCompatiblePath=true" \
    --region us-east-1 >/dev/null || \
    fail "Delivery creation failed. If a delivery already exists for this source, delete it first with: aws logs delete-delivery --id <id>"

echo ""
info "Access logging enabled."
echo ""
echo "Next steps:"
echo "  1. Confirm the lifecycle rule on ${LOG_PREFIX}/ exists (safe-deploy.sh creates it)."
echo "  2. Create the Athena table from analytics-schema.sql."
echo "  3. Logs take up to an hour to first appear."
echo ""
echo "Verify a request path survives the SPA rewrite before relying on"
echo "per-route counts: the distribution rewrites 403/404 to /index.html, so"
echo "check whether cs-uri-stem records /map or /index.html for a deep link."
