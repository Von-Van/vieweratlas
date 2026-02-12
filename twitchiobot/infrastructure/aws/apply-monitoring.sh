#!/bin/bash
# Apply CloudWatch dashboard/alarms and validate retention/lifecycle controls.

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
AWS_ACCOUNT_ID=${AWS_ACCOUNT_ID:-}
ECS_CLUSTER=${ECS_CLUSTER:-vieweratlas-cluster}
S3_BUCKET=${S3_BUCKET:-}
S3_PREFIX=${S3_PREFIX:-vieweratlas/}
COLLECTOR_SERVICE_NAME=${COLLECTOR_SERVICE_NAME:-vieweratlas-collector}
LOG_RETENTION_DAYS=${LOG_RETENTION_DAYS:-7}
SNAPSHOT_WINDOW_SECONDS=${SNAPSHOT_WINDOW_SECONDS:-3600}
ERROR_SPIKE_THRESHOLD=${ERROR_SPIKE_THRESHOLD:-20}
BUDGET_LIMIT_USD=${BUDGET_LIMIT_USD:-50}
BUDGET_NAME=${BUDGET_NAME:-vieweratlas-monthly-limit}
SNS_TOPIC_ARN=${SNS_TOPIC_ARN:-}
SNS_TOPIC_NAME=${SNS_TOPIC_NAME:-vieweratlas-alerts}
ALERT_EMAIL=${ALERT_EMAIL:-}
MONITORING_CONFIG=${MONITORING_CONFIG:-./monitoring-dashboard.yaml}

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
python3 - <<'PY' >/dev/null 2>&1 || fail "python3 module 'yaml' is required (pip install pyyaml)"
import yaml
PY

[ -n "$S3_BUCKET" ] || fail "S3_BUCKET is required"
[ -f "$MONITORING_CONFIG" ] || fail "Monitoring config not found: $MONITORING_CONFIG"

if [ -z "$AWS_ACCOUNT_ID" ]; then
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
fi

if [ -z "$SNS_TOPIC_ARN" ]; then
    SNS_TOPIC_ARN="arn:aws:sns:${AWS_REGION}:${AWS_ACCOUNT_ID}:${SNS_TOPIC_NAME}"
fi

if ! aws sns get-topic-attributes --region "$AWS_REGION" --topic-arn "$SNS_TOPIC_ARN" >/dev/null 2>&1; then
    fail "SNS topic not found: $SNS_TOPIC_ARN (see SNS_SETUP.md)"
fi

info "Applying monitoring using $MONITORING_CONFIG"

# Build dashboard JSON from monitoring-dashboard.yaml and push to CloudWatch.
dashboard_payload=$(python3 - "$MONITORING_CONFIG" "$AWS_REGION" <<'PY'
import json
import sys

import yaml

config_path = sys.argv[1]
aws_region = sys.argv[2]

with open(config_path, "r", encoding="utf-8") as file_handle:
    data = yaml.safe_load(file_handle)

cfg = data.get("dashboard_config", {})
widgets_cfg = cfg.get("widgets", [])
dashboard_name = cfg.get("name", "ViewerAtlas")

widgets = []
y_pos = 0

for widget in widgets_cfg:
    widget_type = widget.get("type")
    title = widget.get("title", "Untitled")

    if widget_type == "metric":
        metric_rows = []
        for metric in widget.get("metrics", []):
            namespace = metric.get("namespace", "ViewerAtlas")
            name = metric.get("name")
            if not name:
                continue

            row = [namespace, name]
            for dim in metric.get("dimensions", []):
                row.extend([dim.get("name"), dim.get("value")])

            stat = metric.get("stat")
            period = metric.get("period")
            row.append({
                "stat": stat if stat else "Average",
                "period": period if period else 300,
            })
            metric_rows.append(row)

        widgets.append(
            {
                "type": "metric",
                "x": 0,
                "y": y_pos,
                "width": 12,
                "height": 6,
                "properties": {
                    "title": title,
                    "region": aws_region,
                    "metrics": metric_rows,
                    "view": "timeSeries",
                    "stacked": False,
                },
            }
        )
        y_pos += 6

    elif widget_type == "log":
        log_group = widget.get("log_group")
        query = (widget.get("query") or "fields @timestamp, @message | sort @timestamp desc | limit 50").strip()
        query_string = f"SOURCE '{log_group}' | {query}" if log_group else query

        widgets.append(
            {
                "type": "log",
                "x": 0,
                "y": y_pos,
                "width": 24,
                "height": 6,
                "properties": {
                    "title": title,
                    "region": aws_region,
                    "query": query_string,
                    "view": "table",
                },
            }
        )
        y_pos += 6

payload = {
    "dashboard_name": dashboard_name,
    "dashboard_body": json.dumps({"widgets": widgets}),
}
print(json.dumps(payload))
PY
)

dashboard_name=$(python3 - <<'PY'
import json, sys
payload = json.loads(sys.stdin.read())
print(payload["dashboard_name"])
PY
<<< "$dashboard_payload")

dashboard_body=$(python3 - <<'PY'
import json, sys
payload = json.loads(sys.stdin.read())
print(payload["dashboard_body"])
PY
<<< "$dashboard_payload")

aws cloudwatch put-dashboard \
    --region "$AWS_REGION" \
    --dashboard-name "$dashboard_name" \
    --dashboard-body "$dashboard_body" >/dev/null

info "Dashboard applied: $dashboard_name"

for log_group in \
    "/ecs/vieweratlas-collector" \
    "/ecs/vieweratlas-analysis" \
    "/ecs/vieweratlas-vod-collector"; do
    aws logs create-log-group --region "$AWS_REGION" --log-group-name "$log_group" >/dev/null 2>&1 || true
    aws logs put-retention-policy \
        --region "$AWS_REGION" \
        --log-group-name "$log_group" \
        --retention-in-days "$LOG_RETENTION_DAYS" >/dev/null

    retention=$(aws logs describe-log-groups \
        --region "$AWS_REGION" \
        --log-group-name-prefix "$log_group" \
        --query 'logGroups[0].retentionInDays' \
        --output text)
    [ "$retention" = "$LOG_RETENTION_DAYS" ] || fail "Unexpected retention for $log_group (expected $LOG_RETENTION_DAYS, got $retention)"
done
info "CloudWatch log retention validated"

metric_transformations='metricName=SnapshotSavedCount,metricNamespace=ViewerAtlas/Collector,metricValue=1'
aws logs put-metric-filter \
    --region "$AWS_REGION" \
    --log-group-name "/ecs/vieweratlas-collector" \
    --filter-name "ViewerAtlasCollectorSnapshotSaved" \
    --filter-pattern '"Saved:"' \
    --metric-transformations "$metric_transformations" >/dev/null

metric_transformations='metricName=CollectorErrorCount,metricNamespace=ViewerAtlas/Collector,metricValue=1'
aws logs put-metric-filter \
    --region "$AWS_REGION" \
    --log-group-name "/ecs/vieweratlas-collector" \
    --filter-name "ViewerAtlasCollectorErrors" \
    --filter-pattern 'ERROR' \
    --metric-transformations "$metric_transformations" >/dev/null

info "Metric filters applied"

aws ecs update-cluster-settings \
    --region "$AWS_REGION" \
    --cluster "$ECS_CLUSTER" \
    --settings name=containerInsights,value=enabled >/dev/null
info "ECS Container Insights enabled for cluster metrics"

aws cloudwatch put-metric-alarm \
    --region "$AWS_REGION" \
    --alarm-name "ViewerAtlas-ECSCollector-Unavailable" \
    --alarm-description "Collector running task count dropped below 1" \
    --namespace "ECS/ContainerInsights" \
    --metric-name "RunningTaskCount" \
    --dimensions Name=ClusterName,Value="$ECS_CLUSTER" Name=ServiceName,Value="$COLLECTOR_SERVICE_NAME" \
    --statistic Average \
    --period 300 \
    --evaluation-periods 2 \
    --threshold 1 \
    --comparison-operator LessThanThreshold \
    --treat-missing-data breaching \
    --alarm-actions "$SNS_TOPIC_ARN" >/dev/null

aws cloudwatch put-metric-alarm \
    --region "$AWS_REGION" \
    --alarm-name "ViewerAtlas-Collector-Snapshot-Stalled" \
    --alarm-description "No collector snapshot save logs in expected window" \
    --namespace "ViewerAtlas/Collector" \
    --metric-name "SnapshotSavedCount" \
    --statistic Sum \
    --period "$SNAPSHOT_WINDOW_SECONDS" \
    --evaluation-periods 1 \
    --threshold 1 \
    --comparison-operator LessThanThreshold \
    --treat-missing-data breaching \
    --alarm-actions "$SNS_TOPIC_ARN" >/dev/null

aws cloudwatch put-metric-alarm \
    --region "$AWS_REGION" \
    --alarm-name "ViewerAtlas-Collector-Error-Spike" \
    --alarm-description "Collector error log volume exceeded threshold" \
    --namespace "ViewerAtlas/Collector" \
    --metric-name "CollectorErrorCount" \
    --statistic Sum \
    --period 300 \
    --evaluation-periods 1 \
    --threshold "$ERROR_SPIKE_THRESHOLD" \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --treat-missing-data notBreaching \
    --alarm-actions "$SNS_TOPIC_ARN" >/dev/null

info "Core CloudWatch alarms applied"

# Budget alert (actual + forecast). If budget exists, update budget amount and keep notifications idempotent.
cat > /tmp/vieweratlas-budget.json <<JSON
{
  "BudgetName": "${BUDGET_NAME}",
  "BudgetLimit": {
    "Amount": "${BUDGET_LIMIT_USD}",
    "Unit": "USD"
  },
  "TimeUnit": "MONTHLY",
  "BudgetType": "COST"
}
JSON

if aws budgets describe-budget \
    --account-id "$AWS_ACCOUNT_ID" \
    --budget-name "$BUDGET_NAME" >/dev/null 2>&1; then
    aws budgets update-budget \
        --account-id "$AWS_ACCOUNT_ID" \
        --new-budget file:///tmp/vieweratlas-budget.json >/dev/null
else
    aws budgets create-budget \
        --account-id "$AWS_ACCOUNT_ID" \
        --budget file:///tmp/vieweratlas-budget.json >/dev/null
fi

create_budget_notification() {
    local notification_type=$1
    local threshold=$2
    aws budgets create-notification \
        --account-id "$AWS_ACCOUNT_ID" \
        --budget-name "$BUDGET_NAME" \
        --notification "NotificationType=${notification_type},ComparisonOperator=GREATER_THAN,Threshold=${threshold},ThresholdType=PERCENTAGE" \
        --subscribers "SubscriptionType=SNS,Address=${SNS_TOPIC_ARN}" >/dev/null 2>&1 || true
}

create_budget_notification "ACTUAL" "80"
create_budget_notification "FORECASTED" "100"

if [ -n "$ALERT_EMAIL" ]; then
    aws sns subscribe \
        --region "$AWS_REGION" \
        --topic-arn "$SNS_TOPIC_ARN" \
        --protocol email \
        --notification-endpoint "$ALERT_EMAIL" >/dev/null 2>&1 || true
fi

info "Budget controls validated/applied"

lifecycle_json=$(aws s3api get-bucket-lifecycle-configuration \
    --region "$AWS_REGION" \
    --bucket "$S3_BUCKET" \
    --output json 2>/dev/null || true)

if [ -z "$lifecycle_json" ]; then
    fail "No S3 lifecycle configuration found on bucket $S3_BUCKET"
fi

export LIFECYCLE_JSON="$lifecycle_json"

python3 - <<'PY'
import json
import os
import sys

payload = os.environ.get("LIFECYCLE_JSON", "")
if not payload:
    raise SystemExit("Missing lifecycle payload")

config = json.loads(payload)
rules = {rule.get("Id") for rule in config.get("Rules", [])}
expected = {"DeleteOldRawLogs", "DeleteOldVODRaw", "ArchiveProcessedData"}
missing = sorted(expected - rules)
if missing:
    raise SystemExit(f"Missing lifecycle rules: {', '.join(missing)}")
print("Lifecycle configuration validated")
PY

info "Monitoring apply complete. SNS alarms route to: $SNS_TOPIC_ARN"
