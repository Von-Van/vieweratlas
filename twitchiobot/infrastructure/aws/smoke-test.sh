#!/bin/bash
# Validate the scheduled EventSub survey, its durable manifest, and public data.

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
ECS_CLUSTER=${ECS_CLUSTER:-$DEFAULT_CLUSTER}
S3_BUCKET=${S3_BUCKET:-}
S3_PREFIX=${S3_PREFIX:-vieweratlas/}
S3_KEY_PREFIX="${S3_PREFIX%/}"
[ -z "$S3_KEY_PREFIX" ] || S3_KEY_PREFIX="${S3_KEY_PREFIX}/"
SURVEY_SCHEDULE_NAME=${SURVEY_SCHEDULE_NAME:-${SERVICE_PREFIX}-survey-three-daily}
ANALYSIS_SCHEDULE_NAME=${ANALYSIS_SCHEDULE_NAME:-${SERVICE_PREFIX}-analysis-daily}
SCHEDULE_TIMEZONE=${SCHEDULE_TIMEZONE:-America/New_York}
S3_SURVEY_PREFIX=${S3_SURVEY_PREFIX:-${S3_KEY_PREFIX}raw/snapshots/v2/}
S3_FRONTEND_DATA_KEY=${S3_FRONTEND_DATA_KEY:-${S3_KEY_PREFIX}data/frontend-data.json}
S3_ANALYSIS_RESULTS_KEY=${S3_ANALYSIS_RESULTS_KEY:-${S3_KEY_PREFIX}processed/analysis_results.json}
S3_FRESHNESS_MAX_AGE_MINUTES=${S3_FRESHNESS_MAX_AGE_MINUTES:-720}
EXPECT_SCHEDULES_ENABLED=${EXPECT_SCHEDULES_ENABLED:-true}
ALLOW_SURVEY_ERRORS=${ALLOW_SURVEY_ERRORS:-false}
SMOKE_MODE=${1:-survey}

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1"; }
fail() { err "$1"; exit 1; }

command -v aws >/dev/null 2>&1 || fail "AWS CLI is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
[ -n "$S3_BUCKET" ] || fail "S3_BUCKET is required"
[[ "$SMOKE_MODE" =~ ^(survey|analysis)$ ]] || \
    fail "Usage: ./smoke-test.sh [analysis]"

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd "$SCRIPT_DIR/../../.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
[ ! -x "$PROJECT_DIR/.venv/bin/python" ] || PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"

tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT

if [ "$SMOKE_MODE" = "analysis" ]; then
    info "Running scheduled-survey and strict analysis-freshness smoke checks"
else
    info "Running scheduled-survey smoke checks"
fi

cluster_status=$(aws ecs describe-clusters \
    --region "$AWS_REGION" --clusters "$ECS_CLUSTER" \
    --query 'clusters[0].status' --output text 2>/dev/null || true)
[ "$cluster_status" = "ACTIVE" ] || fail "ECS cluster is not ACTIVE: $ECS_CLUSTER"

for family in "${SERVICE_PREFIX}-collector" "${SERVICE_PREFIX}-analysis"; do
    task_arn=$(aws ecs describe-task-definition \
        --region "$AWS_REGION" --task-definition "$family" \
        --query 'taskDefinition.taskDefinitionArn' --output text 2>/dev/null || true)
    [ -n "$task_arn" ] && [ "$task_arn" != "None" ] || fail "Task definition is missing: $family"
done
info "ECS cluster and one-shot task definitions are ready"

# A leftover continuous collector would duplicate surveys and violate the
# account-wide chat-room limit. It may exist for rollback history, but must be 0.
collector_desired=$(aws ecs describe-services \
    --region "$AWS_REGION" --cluster "$ECS_CLUSTER" \
    --services "${SERVICE_PREFIX}-collector" \
    --query 'services[0].desiredCount' --output text 2>/dev/null || true)
if [ -n "$collector_desired" ] && [ "$collector_desired" != "None" ] && [ "$collector_desired" != "0" ]; then
    fail "Legacy collector service must have desired count 0 (found $collector_desired)"
fi

verify_schedule() {
    local name=$1 expected_expression=$2 schedule_file
    schedule_file="$tmp_dir/${name}.json"
    aws scheduler get-schedule --region "$AWS_REGION" --name "$name" > "$schedule_file" 2>/dev/null || \
        fail "EventBridge Scheduler schedule is missing: $name"
    "$PYTHON_BIN" - "$schedule_file" "$SCHEDULE_TIMEZONE" "$expected_expression" "$EXPECT_SCHEDULES_ENABLED" <<'PY'
import json, sys
path, timezone, expression, expect_enabled = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    schedule = json.load(handle)
if schedule.get("ScheduleExpressionTimezone") != timezone:
    raise SystemExit(f"Wrong schedule timezone: {schedule.get('ScheduleExpressionTimezone')}")
if schedule.get("ScheduleExpression") != expression:
    raise SystemExit(f"Wrong schedule expression: {schedule.get('ScheduleExpression')}")
if schedule.get("FlexibleTimeWindow", {}).get("Mode") != "OFF":
    raise SystemExit("Flexible schedule timing must be OFF")
if expect_enabled.lower() == "true" and schedule.get("State") != "ENABLED":
    raise SystemExit(f"Schedule is not enabled: {schedule.get('State')}")
PY
}

verify_schedule "$SURVEY_SCHEDULE_NAME" 'cron(0 6,14,22 * * ? *)'
verify_schedule "$ANALYSIS_SCHEDULE_NAME" 'cron(0 1 * * ? *)'
info "Eastern-time schedules are configured correctly"

manifest_listing_file="$tmp_dir/manifest-listing.json"
if ! aws s3api list-objects-v2 \
    --region "$AWS_REGION" --bucket "$S3_BUCKET" --prefix "$S3_SURVEY_PREFIX" \
    --output json > "$manifest_listing_file" 2>/dev/null; then
    fail "Could not list survey manifests under s3://${S3_BUCKET}/${S3_SURVEY_PREFIX}"
fi
latest_manifest_key=$("$PYTHON_BIN" - "$manifest_listing_file" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
manifests = [
    item for item in payload.get("Contents", [])
    if isinstance(item, dict) and str(item.get("Key", "")).endswith("manifest.json")
]
if manifests:
    print(max(manifests, key=lambda item: item.get("LastModified", ""))["Key"])
PY
)
[ -n "$latest_manifest_key" ] && [ "$latest_manifest_key" != "None" ] || \
    fail "No survey manifest found under s3://${S3_BUCKET}/${S3_SURVEY_PREFIX}"

manifest_last_modified=$(aws s3api head-object \
    --region "$AWS_REGION" --bucket "$S3_BUCKET" --key "$latest_manifest_key" \
    --query LastModified --output text)
manifest_age_seconds=$("$PYTHON_BIN" - "$manifest_last_modified" <<'PY'
from datetime import datetime, timezone
import sys
stamp = datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
print(int((datetime.now(timezone.utc) - stamp).total_seconds()))
PY
)
max_age_seconds=$((S3_FRESHNESS_MAX_AGE_MINUTES * 60))
[ "$manifest_age_seconds" -le "$max_age_seconds" ] || \
    fail "Latest survey manifest is stale (${manifest_age_seconds}s old)"

manifest_file="$tmp_dir/manifest.json"
batch_key_file="$tmp_dir/batch-key.txt"
aws s3 cp "s3://${S3_BUCKET}/${latest_manifest_key}" "$manifest_file" --region "$AWS_REGION" >/dev/null

"$PYTHON_BIN" - "$manifest_file" "$batch_key_file" "$ALLOW_SURVEY_ERRORS" <<'PY'
import json, sys
manifest_path, output_path, allow_errors = sys.argv[1:]
with open(manifest_path, encoding="utf-8") as handle:
    manifest = json.load(handle)
status = manifest.get("status")
allowed = {"complete", "complete_with_errors"} if allow_errors.lower() == "true" else {"complete"}
if status not in allowed:
    raise SystemExit(f"Latest survey is not healthy (status={status!r})")
planned = manifest.get("planned")
completed = manifest.get("completed")
failed = manifest.get("failed")
if not isinstance(planned, int) or not 1 <= planned <= 1200:
    raise SystemExit("Manifest planned count is invalid")
if not isinstance(completed, int) or not isinstance(failed, int) or completed + failed != planned:
    raise SystemExit("Manifest channel totals are inconsistent")
batches = manifest.get("batches")
if not isinstance(batches, list) or len(batches) != manifest.get("batches_completed"):
    raise SystemExit("Manifest batch totals are inconsistent")
keys = [batch.get("object_key") for batch in batches if isinstance(batch, dict)]
if not keys or any(not isinstance(key, str) or not key.endswith(".parquet") for key in keys):
    raise SystemExit("Manifest does not reference valid batch objects")
with open(output_path, "w", encoding="utf-8") as handle:
    handle.write(keys[-1])
print(f"Manifest validated: status={status}, planned={planned}, completed={completed}, failed={failed}")
PY

batch_key=$(<"$batch_key_file")
batch_object_key="${S3_KEY_PREFIX}${batch_key}"
batch_file="$tmp_dir/latest-batch.parquet"
aws s3 cp "s3://${S3_BUCKET}/${batch_object_key}" "$batch_file" --region "$AWS_REGION" >/dev/null || \
    fail "Manifest batch object is missing: $batch_object_key"

"$PYTHON_BIN" - "$batch_file" <<'PY'
import json, sys
try:
    import pyarrow.parquet as pq
except ImportError as exc:
    raise SystemExit("pyarrow is required; activate the project virtual environment") from exc
table = pq.read_table(sys.argv[1])
required = {
    "schema_version", "survey_session_id", "batch", "rank", "selection_source",
    "channel_id", "channel_login", "sample_started_at", "sample_ended_at",
    "sample_duration_seconds", "collection_status", "unique_author_count",
    "chatter_ids_json", "chatters_json",
}
missing = sorted(required - set(table.column_names))
if missing:
    raise SystemExit("Survey batch is missing columns: " + ", ".join(missing))
for row in table.select(["chatter_ids_json", "chatters_json", "unique_author_count"]).to_pylist():
    ids = json.loads(row["chatter_ids_json"] or "[]")
    names = json.loads(row["chatters_json"] or "[]")
    if len(ids) != len(names) or len(ids) != row["unique_author_count"] or len(ids) != len(set(ids)):
        raise SystemExit("Survey batch author arrays are not aligned and unique")
print(f"Survey batch validated: {table.num_rows} channel rows")
PY

# Default mode is usable immediately after any survey: the website may keep its
# last valid dataset until 1 AM. Explicit analysis mode proves both outputs were
# refreshed after the latest completed survey, catching a no-op task even when
# older, superficially valid files still exist.
if [ "$SMOKE_MODE" = "analysis" ]; then
    analysis_last_modified=$(aws s3api head-object \
        --region "$AWS_REGION" --bucket "$S3_BUCKET" --key "$S3_ANALYSIS_RESULTS_KEY" \
        --query LastModified --output text 2>/dev/null) || \
        fail "Analysis results are missing at s3://${S3_BUCKET}/${S3_ANALYSIS_RESULTS_KEY}"
    frontend_last_modified=$(aws s3api head-object \
        --region "$AWS_REGION" --bucket "$S3_BUCKET" --key "$S3_FRONTEND_DATA_KEY" \
        --query LastModified --output text 2>/dev/null) || \
        fail "Frontend data is missing at s3://${S3_BUCKET}/${S3_FRONTEND_DATA_KEY}"

    "$PYTHON_BIN" - \
        "$manifest_last_modified" \
        "$analysis_last_modified" \
        "$frontend_last_modified" <<'PY'
from datetime import datetime
import sys

timestamps = [
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    for value in sys.argv[1:]
]
manifest, analysis, frontend = timestamps
if analysis < manifest:
    raise SystemExit(
        "Analysis results predate the latest completed survey; rerun after the 1:00 AM analysis"
    )
if frontend < manifest:
    raise SystemExit(
        "Frontend data predates the latest completed survey; rerun after the 1:00 AM analysis"
    )
print(
    "Analysis freshness validated: analysis and frontend outputs are newer than "
    "the latest completed survey"
)
PY

    analysis_file="$tmp_dir/analysis-results.json"
    aws s3 cp "s3://${S3_BUCKET}/${S3_ANALYSIS_RESULTS_KEY}" "$analysis_file" --region "$AWS_REGION" >/dev/null
    "$PYTHON_BIN" - "$analysis_file" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
for key in ("timestamp", "partition", "statistics"):
    if key not in payload:
        raise SystemExit(f"Analysis results are missing required field: {key}")
if not isinstance(payload["partition"], dict) or not isinstance(payload["statistics"], dict):
    raise SystemExit("Analysis results contain invalid aggregate structures")
print(f"Analysis results validated: {len(payload['partition'])} assigned channels")
PY
fi

# Validate that the refreshed public artifact contains graph aggregates and no
# author identities.
frontend_file="$tmp_dir/frontend-data.json"
aws s3 cp "s3://${S3_BUCKET}/${S3_FRONTEND_DATA_KEY}" "$frontend_file" --region "$AWS_REGION" >/dev/null || \
    fail "Frontend data is missing at s3://${S3_BUCKET}/${S3_FRONTEND_DATA_KEY}"
"$PYTHON_BIN" - "$frontend_file" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
for key in ("communities", "channels", "edges"):
    if not isinstance(payload.get(key), list):
        raise SystemExit(f"Frontend payload is missing array: {key}")
private_names = {
    "chatters", "chatters_json", "chatter_ids", "chatter_ids_json",
    "chatter_user_id", "chatter_user_login", "message", "message_text",
}
def leaks(value):
    if isinstance(value, dict):
        return any(key.lower() in private_names or leaks(child) for key, child in value.items())
    if isinstance(value, list):
        return any(leaks(item) for item in value)
    return False
if leaks(payload):
    raise SystemExit("Frontend payload leaks private author or message data")
print(f"Public frontend validated: {len(payload['channels'])} channels")
PY

if [ "$SMOKE_MODE" = "analysis" ]; then
    info "Analysis freshness smoke test passed"
else
    info "Survey smoke test passed"
fi
