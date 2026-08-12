#!/bin/bash
# Disable existing ViewerAtlas EventBridge Scheduler schedules without touching
# ECS, IAM, task definitions, VPC networking, or schedule target configuration.
#
# Usage:
#   ./pause-schedules.sh survey   # pause collection only (default)
#   ./pause-schedules.sh analysis # pause analysis only
#   ./pause-schedules.sh all      # emergency/release rollback

set -euo pipefail
export AWS_PAGER=""
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

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

ENVIRONMENT="${ENVIRONMENT:-prod}"
if [ "$ENVIRONMENT" = "prod" ]; then
    SERVICE_PREFIX="vieweratlas"
else
    SERVICE_PREFIX="vieweratlas-${ENVIRONMENT}"
fi

AWS_REGION=${AWS_REGION:-us-east-1}
SURVEY_SCHEDULE_NAME=${SURVEY_SCHEDULE_NAME:-${SERVICE_PREFIX}-survey-three-daily}
ANALYSIS_SCHEDULE_NAME=${ANALYSIS_SCHEDULE_NAME:-${SERVICE_PREFIX}-analysis-daily}
MODE=${1:-survey}

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1"; }
fail() { err "$1"; exit 1; }

command -v aws >/dev/null 2>&1 || fail "AWS CLI is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"

case "$MODE" in
    survey)
        schedules=("$SURVEY_SCHEDULE_NAME")
        ;;
    analysis)
        schedules=("$ANALYSIS_SCHEDULE_NAME")
        ;;
    all)
        schedules=("$SURVEY_SCHEDULE_NAME" "$ANALYSIS_SCHEDULE_NAME")
        ;;
    *)
        fail "Usage: ./pause-schedules.sh [survey|analysis|all]"
        ;;
esac

tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT
failures=0

pause_schedule() {
    local name=$1 safe_name current_file error_file update_file state
    safe_name=${name//[^A-Za-z0-9_-]/_}
    current_file="$tmp_dir/${safe_name}-current.json"
    error_file="$tmp_dir/${safe_name}-error.txt"
    update_file="$tmp_dir/${safe_name}-update.json"

    if ! aws scheduler get-schedule \
        --region "$AWS_REGION" \
        --name "$name" > "$current_file" 2> "$error_file"; then
        if grep -q "ResourceNotFoundException" "$error_file"; then
            info "Schedule does not exist, so it cannot start work: $name"
            return 0
        fi
        err "Could not read schedule $name; it may still be enabled"
        return 1
    fi

    # EventBridge Scheduler's UpdateSchedule API requires the existing schedule
    # definition as well as State. Copy only writable fields from GetSchedule,
    # preserve every target/timing value, and change State to DISABLED.
    python3 - "$current_file" "$update_file" <<'PY'
import json
import sys

source_path, output_path = sys.argv[1:]
with open(source_path, encoding="utf-8") as handle:
    source = json.load(handle)

writable_fields = (
    "ActionAfterCompletion",
    "Description",
    "EndDate",
    "FlexibleTimeWindow",
    "GroupName",
    "KmsKeyArn",
    "Name",
    "ScheduleExpression",
    "ScheduleExpressionTimezone",
    "StartDate",
    "Target",
)
payload = {
    field: source[field]
    for field in writable_fields
    if field in source and source[field] is not None
}
required = {"Name", "FlexibleTimeWindow", "ScheduleExpression", "Target"}
missing = sorted(required - payload.keys())
if missing:
    raise SystemExit("Schedule response is missing required fields: " + ", ".join(missing))
payload["State"] = "DISABLED"

with open(output_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
PY

    if ! aws scheduler update-schedule \
        --region "$AWS_REGION" \
        --cli-input-json "file://$update_file" >/dev/null; then
        err "Could not disable schedule $name; it may still be enabled"
        return 1
    fi

    state=$(aws scheduler get-schedule \
        --region "$AWS_REGION" \
        --name "$name" \
        --query State \
        --output text 2>/dev/null || true)
    if [ "$state" != "DISABLED" ]; then
        err "Schedule $name did not verify as DISABLED (state=${state:-unknown})"
        return 1
    fi
    info "Schedule disabled: $name"
}

for schedule in "${schedules[@]}"; do
    if ! pause_schedule "$schedule"; then
        failures=$((failures + 1))
    fi
done

if [ "$failures" -ne 0 ]; then
    fail "One or more schedules could not be confirmed disabled"
fi

info "Requested schedules are safely paused. No ECS, IAM, VPC, or target settings were changed."
