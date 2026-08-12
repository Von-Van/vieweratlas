#!/bin/bash
# Launch one controlled survey test, wait for it, and print its milestone logs.

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
        [ -z "${!key+x}" ] || continue
        export "$key=$value"
    done < "$env_file"
}

load_env_file

AWS_REGION=${AWS_REGION:-us-east-1}
ECS_CLUSTER=${ECS_CLUSTER:-vieweratlas-cluster}
ASSIGN_PUBLIC_IP=${ASSIGN_PUBLIC_IP:-ENABLED}
SUBNET_IDS=${SUBNET_IDS:-}
SECURITY_GROUP_ID=${SECURITY_GROUP_ID:-}
ENVIRONMENT=${ENVIRONMENT:-prod}
if [ "$ENVIRONMENT" = "prod" ]; then
    SERVICE_PREFIX=${SERVICE_PREFIX:-vieweratlas}
else
    SERVICE_PREFIX=${SERVICE_PREFIX:-vieweratlas-${ENVIRONMENT}}
fi
MODE=${1:-small}

case "$MODE" in
    small)
        top_channels=5
        batch_size=5
        window_seconds=60
        timeout_seconds=900
        wait_seconds=1200
        ;;
    batch)
        top_channels=100
        batch_size=100
        window_seconds=300
        timeout_seconds=1800
        wait_seconds=2400
        ;;
    full)
        top_channels=1200
        batch_size=100
        window_seconds=300
        timeout_seconds=7200
        wait_seconds=7500
        ;;
    *)
        echo "Usage: ./run-survey-test.sh [small|batch|full]" >&2
        exit 2
        ;;
esac

[ -n "$SUBNET_IDS" ] || { echo "[ERROR] SUBNET_IDS is missing from .env" >&2; exit 1; }
[ -n "$SECURITY_GROUP_ID" ] || { echo "[ERROR] SECURITY_GROUP_ID is missing from .env" >&2; exit 1; }
[[ "$ASSIGN_PUBLIC_IP" =~ ^(ENABLED|DISABLED)$ ]] || {
    echo "[ERROR] ASSIGN_PUBLIC_IP must be ENABLED or DISABLED" >&2
    exit 1
}
command -v aws >/dev/null 2>&1 || { echo "[ERROR] AWS CLI is required" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "[ERROR] python3 is required" >&2; exit 1; }

task_definition=$(aws ecs describe-task-definition \
    --task-definition "${SERVICE_PREFIX}-collector" \
    --region "$AWS_REGION" \
    --query 'taskDefinition.taskDefinitionArn' \
    --output text)

[ -n "$task_definition" ] && [ "$task_definition" != "None" ] || {
    echo "[ERROR] Collector task definition was not found. Run ./safe-deploy.sh first." >&2
    exit 1
}

override_file=$(mktemp)
run_result=$(mktemp)
trap 'rm -f "$override_file" "$run_result"' EXIT

python3 - "$override_file" "$top_channels" "$batch_size" "$window_seconds" "$timeout_seconds" <<'PY'
import json
import sys

_, path, top_channels, batch_size, window_seconds, timeout_seconds = sys.argv
environment = {
    "SURVEY_TOP_CHANNELS_LIMIT": top_channels,
    "SURVEY_BATCH_SIZE": batch_size,
    "SURVEY_WINDOW_SECONDS": window_seconds,
    "SURVEY_TIMEOUT_SECONDS": timeout_seconds,
}
payload = {
    "containerOverrides": [{
        "name": "collector",
        "environment": [
            {"name": name, "value": value}
            for name, value in environment.items()
        ],
    }]
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
PY

echo "[INFO] Starting the '$MODE' survey test ($top_channels channels, ${window_seconds}s window)"
aws ecs run-task \
    --cluster "$ECS_CLUSTER" \
    --region "$AWS_REGION" \
    --launch-type FARGATE \
    --task-definition "$task_definition" \
    --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_IDS],securityGroups=[$SECURITY_GROUP_ID],assignPublicIp=$ASSIGN_PUBLIC_IP}" \
    --overrides "file://$override_file" > "$run_result"

task_arn=$(python3 - "$run_result" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
tasks = payload.get("tasks") or []
if not tasks:
    failures = payload.get("failures") or []
    for failure in failures:
        print(
            f"[ERROR] {failure.get('reason', 'RunTask failed')}: "
            f"{failure.get('detail', '')}",
            file=sys.stderr,
        )
    raise SystemExit(1)
print(tasks[0]["taskArn"])
PY
)

echo "[INFO] Task started: $task_arn"
echo "[INFO] Waiting for it to stop. You can leave this Terminal window open."

deadline=$(( $(date +%s) + wait_seconds ))
last_status=""
while true; do
    status=$(aws ecs describe-tasks \
        --cluster "$ECS_CLUSTER" \
        --tasks "$task_arn" \
        --region "$AWS_REGION" \
        --query 'tasks[0].lastStatus' \
        --output text)
    if [ "$status" != "$last_status" ]; then
        echo "[INFO] Task status: $status"
        last_status="$status"
    fi
    [ "$status" = "STOPPED" ] && break
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "[ERROR] Timed out waiting for the test task; it may still be running." >&2
        exit 1
    fi
    sleep 15
done

task_id=${task_arn##*/}
log_stream="collector/collector/$task_id"
echo "[INFO] Collector milestones:"
milestones_file=$(mktemp)
trap 'rm -f "$override_file" "$run_result" "$milestones_file"' EXIT
aws logs get-log-events \
    --region "$AWS_REGION" \
    --log-group-name "/ecs/${SERVICE_PREFIX}-collector" \
    --log-stream-name "$log_stream" \
    --start-from-head \
    --query "events[?contains(message, 'SURVEY_') || contains(message, 'BATCH_COMPLETED')].message" \
    --output text > "$milestones_file" 2>/dev/null || true

# CloudWatch Logs can lag a stopped ECS task by a few seconds. Retry only when
# the terminal milestone has not arrived yet, so a healthy test is not reported
# as failed because of log-delivery latency.
for _ in 1 2 3 4 5; do
    if grep -qE 'SURVEY_COMPLETED|SURVEY_PARTIAL' "$milestones_file"; then
        break
    fi
    sleep 2
    aws logs get-log-events \
        --region "$AWS_REGION" \
        --log-group-name "/ecs/${SERVICE_PREFIX}-collector" \
        --log-stream-name "$log_stream" \
        --start-from-head \
        --query "events[?contains(message, 'SURVEY_') || contains(message, 'BATCH_COMPLETED')].message" \
        --output text > "$milestones_file" 2>/dev/null || true
done
cat "$milestones_file"

exit_code=$(aws ecs describe-tasks \
    --cluster "$ECS_CLUSTER" \
    --tasks "$task_arn" \
    --region "$AWS_REGION" \
    --query 'tasks[0].containers[?name==`collector`].exitCode | [0]' \
    --output text)

if [ "$exit_code" != "0" ]; then
    stopped_reason=$(aws ecs describe-tasks \
        --cluster "$ECS_CLUSTER" \
        --tasks "$task_arn" \
        --region "$AWS_REGION" \
        --query 'tasks[0].stoppedReason' \
        --output text)
    echo "[ERROR] Survey test failed (exit $exit_code): $stopped_reason" >&2
    echo "Read the complete safe log with:" >&2
    echo "aws logs get-log-events --region $AWS_REGION --log-group-name /ecs/${SERVICE_PREFIX}-collector --log-stream-name $log_stream --start-from-head" >&2
    exit 1
fi

if grep -q "SURVEY_PARTIAL" "$milestones_file" || \
   [ "$(grep -c "SURVEY_COMPLETED" "$milestones_file" || true)" -ne 1 ]; then
    echo "[ERROR] The task stopped, but the survey did not finish cleanly." >&2
    echo "A successful test must contain exactly one SURVEY_COMPLETED and no SURVEY_PARTIAL." >&2
    echo "Read the complete safe log with:" >&2
    echo "aws logs get-log-events --region $AWS_REGION --log-group-name /ecs/${SERVICE_PREFIX}-collector --log-stream-name $log_stream --start-from-head" >&2
    exit 1
fi

echo "[INFO] Survey test completed successfully."
