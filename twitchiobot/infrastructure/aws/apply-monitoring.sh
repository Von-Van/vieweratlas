#!/bin/bash
# Apply CloudWatch dashboard/alarms and validate retention/lifecycle controls.

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
AWS_ACCOUNT_ID=${AWS_ACCOUNT_ID:-}
ECS_CLUSTER=${ECS_CLUSTER:-$_DEFAULT_CLUSTER}
S3_BUCKET=${S3_BUCKET:-}
S3_PREFIX=${S3_PREFIX:-vieweratlas/}
LOG_RETENTION_DAYS=${LOG_RETENTION_DAYS:-7}
SURVEY_COMPLETION_ALARM_WINDOW_SECONDS=${SURVEY_COMPLETION_ALARM_WINDOW_SECONDS:-43200}
ERROR_SPIKE_THRESHOLD=${ERROR_SPIKE_THRESHOLD:-20}
ANALYSIS_COMPLETION_ALARM_PERIOD_SECONDS=${ANALYSIS_COMPLETION_ALARM_PERIOD_SECONDS:-21600}
ANALYSIS_COMPLETION_EVALUATION_PERIODS=${ANALYSIS_COMPLETION_EVALUATION_PERIODS:-5}
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
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
if ! "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1; then
import yaml
PY
    project_venv_python="$(cd "$SCRIPT_DIR/../../.." && pwd)/.venv/bin/python"
    if [ -x "$project_venv_python" ] && "$project_venv_python" - <<'PY' >/dev/null 2>&1; then
import yaml
PY
        PYTHON_BIN="$project_venv_python"
        info "Using the project virtual environment for PyYAML"
    else
        fail "PyYAML is required. Activate the project virtual environment or run: python3 -m pip install pyyaml"
    fi
fi

[ -n "$S3_BUCKET" ] || fail "S3_BUCKET is required"
[ -f "$MONITORING_CONFIG" ] || fail "Monitoring config not found: $MONITORING_CONFIG"

if [ -z "$AWS_ACCOUNT_ID" ]; then
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
fi

if [ -z "$SNS_TOPIC_ARN" ]; then
    SNS_TOPIC_ARN=$(aws sns create-topic \
        --region "$AWS_REGION" \
        --name "$SNS_TOPIC_NAME" \
        --query TopicArn \
        --output text) || fail "Could not create or find the SNS alert topic"
elif ! aws sns get-topic-attributes \
    --region "$AWS_REGION" \
    --topic-arn "$SNS_TOPIC_ARN" >/dev/null 2>&1; then
    fail "The configured SNS_TOPIC_ARN does not exist or is not accessible"
fi

info "Alert topic ready: $SNS_TOPIC_ARN"

if [ -n "$ALERT_EMAIL" ]; then
    subscriptions_file=$(mktemp)
    aws sns list-subscriptions-by-topic \
        --region "$AWS_REGION" \
        --topic-arn "$SNS_TOPIC_ARN" \
        --output json > "$subscriptions_file"
    email_is_subscribed=$("$PYTHON_BIN" - "$subscriptions_file" "$ALERT_EMAIL" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
email = sys.argv[2].strip().lower()
matches = any(
    item.get("Protocol") == "email"
    and str(item.get("Endpoint", "")).strip().lower() == email
    for item in payload.get("Subscriptions", [])
)
print("yes" if matches else "no")
PY
)
    rm -f "$subscriptions_file"
    if [ "$email_is_subscribed" = "no" ]; then
        aws sns subscribe \
            --region "$AWS_REGION" \
            --topic-arn "$SNS_TOPIC_ARN" \
            --protocol email \
            --notification-endpoint "$ALERT_EMAIL" >/dev/null
        warn "AWS sent an alert-subscription email to $ALERT_EMAIL; open it and choose Confirm subscription"
    else
        info "Alert email is already subscribed or awaiting confirmation"
    fi
else
    warn "ALERT_EMAIL is empty; alarms will exist but nobody will receive email until an address is subscribed"
fi

info "Applying monitoring using $MONITORING_CONFIG"

# Build dashboard JSON from monitoring-dashboard.yaml and push to CloudWatch.
dashboard_payload=$("$PYTHON_BIN" - "$MONITORING_CONFIG" "$AWS_REGION" "$SERVICE_PREFIX" <<'PY'
import json
import sys

import yaml

config_path = sys.argv[1]
aws_region = sys.argv[2]
service_prefix = sys.argv[3]

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
        if isinstance(log_group, str):
            log_group = log_group.replace("/ecs/vieweratlas-", f"/ecs/{service_prefix}-")
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

dashboard_name=$(echo "$dashboard_payload" | "$PYTHON_BIN" -c "import json,sys; print(json.loads(sys.stdin.read())['dashboard_name'])")
dashboard_body=$(echo "$dashboard_payload" | "$PYTHON_BIN" -c "import json,sys; print(json.loads(sys.stdin.read())['dashboard_body'])")

aws cloudwatch put-dashboard \
    --region "$AWS_REGION" \
    --dashboard-name "$dashboard_name" \
    --dashboard-body "$dashboard_body" >/dev/null

info "Dashboard applied: $dashboard_name"

for log_group in \
    "/ecs/${SERVICE_PREFIX}-collector" \
    "/ecs/${SERVICE_PREFIX}-analysis"; do
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

metric_transformations='metricName=SurveyStartedCount,metricNamespace=ViewerAtlas/Collector,metricValue=1'
aws logs put-metric-filter \
    --region "$AWS_REGION" \
    --log-group-name "/ecs/${SERVICE_PREFIX}-collector" \
    --filter-name "ViewerAtlasSurveyStarted" \
    --filter-pattern '"SURVEY_STARTED"' \
    --metric-transformations "$metric_transformations" >/dev/null

metric_transformations='metricName=SurveyTerminalCount,metricNamespace=ViewerAtlas/Collector,metricValue=1'
aws logs put-metric-filter \
    --region "$AWS_REGION" \
    --log-group-name "/ecs/${SERVICE_PREFIX}-collector" \
    --filter-name "ViewerAtlasSurveyCompleted" \
    --filter-pattern '"SURVEY_COMPLETED"' \
    --metric-transformations "$metric_transformations" >/dev/null

metric_transformations='metricName=SurveyTerminalCount,metricNamespace=ViewerAtlas/Collector,metricValue=1'
aws logs put-metric-filter \
    --region "$AWS_REGION" \
    --log-group-name "/ecs/${SERVICE_PREFIX}-collector" \
    --filter-name "ViewerAtlasSurveyPartialTerminal" \
    --filter-pattern '"SURVEY_PARTIAL"' \
    --metric-transformations "$metric_transformations" >/dev/null

metric_transformations='metricName=SurveyPartialCount,metricNamespace=ViewerAtlas/Collector,metricValue=1'
aws logs put-metric-filter \
    --region "$AWS_REGION" \
    --log-group-name "/ecs/${SERVICE_PREFIX}-collector" \
    --filter-name "ViewerAtlasSurveyPartial" \
    --filter-pattern '"SURVEY_PARTIAL"' \
    --metric-transformations "$metric_transformations" >/dev/null

metric_transformations='metricName=CollectorErrorCount,metricNamespace=ViewerAtlas/Collector,metricValue=1'
aws logs put-metric-filter \
    --region "$AWS_REGION" \
    --log-group-name "/ecs/${SERVICE_PREFIX}-collector" \
    --filter-name "ViewerAtlasCollectorErrors" \
    --filter-pattern 'ERROR' \
    --metric-transformations "$metric_transformations" >/dev/null

metric_transformations='metricName=AnalysisCompletedCount,metricNamespace=ViewerAtlas/Analysis,metricValue=1'
aws logs put-metric-filter \
    --region "$AWS_REGION" \
    --log-group-name "/ecs/${SERVICE_PREFIX}-analysis" \
    --filter-name "ViewerAtlasAnalysisCompleted" \
    --filter-pattern '"ANALYSIS_COMPLETED"' \
    --metric-transformations "$metric_transformations" >/dev/null

metric_transformations='metricName=AnalysisFailedCount,metricNamespace=ViewerAtlas/Analysis,metricValue=1'
aws logs put-metric-filter \
    --region "$AWS_REGION" \
    --log-group-name "/ecs/${SERVICE_PREFIX}-analysis" \
    --filter-name "ViewerAtlasAnalysisFailed" \
    --filter-pattern '"ANALYSIS_FAILED"' \
    --metric-transformations "$metric_transformations" >/dev/null

info "Metric filters applied"

aws ecs update-cluster-settings \
    --region "$AWS_REGION" \
    --cluster "$ECS_CLUSTER" \
    --settings name=containerInsights,value=enabled >/dev/null
info "ECS Container Insights enabled for cluster metrics"

aws cloudwatch put-metric-alarm \
    --region "$AWS_REGION" \
    --alarm-name "${SERVICE_PREFIX}-Survey-Completion-Missing" \
    --alarm-description "No terminal survey event appeared within the expected schedule window" \
    --namespace "ViewerAtlas/Collector" \
    --metric-name "SurveyTerminalCount" \
    --statistic Sum \
    --period "$SURVEY_COMPLETION_ALARM_WINDOW_SECONDS" \
    --evaluation-periods 1 \
    --threshold 1 \
    --comparison-operator LessThanThreshold \
    --treat-missing-data breaching \
    --alarm-actions "$SNS_TOPIC_ARN" >/dev/null

# Five rolling six-hour periods avoid a predictable false alarm between UTC
# metric-day boundaries and the 1 AM Eastern run, while still alerting when no
# successful analysis has appeared for about 30 hours.
aws cloudwatch put-metric-alarm \
    --region "$AWS_REGION" \
    --alarm-name "${SERVICE_PREFIX}-Analysis-Completion-Missing" \
    --alarm-description "No successful daily analysis appeared within the expected 30-hour window" \
    --namespace "ViewerAtlas/Analysis" \
    --metric-name "AnalysisCompletedCount" \
    --statistic Sum \
    --period "$ANALYSIS_COMPLETION_ALARM_PERIOD_SECONDS" \
    --evaluation-periods "$ANALYSIS_COMPLETION_EVALUATION_PERIODS" \
    --datapoints-to-alarm "$ANALYSIS_COMPLETION_EVALUATION_PERIODS" \
    --threshold 1 \
    --comparison-operator LessThanThreshold \
    --treat-missing-data breaching \
    --alarm-actions "$SNS_TOPIC_ARN" >/dev/null

aws cloudwatch put-metric-alarm \
    --region "$AWS_REGION" \
    --alarm-name "${SERVICE_PREFIX}-Analysis-Failed" \
    --alarm-description "The scheduled daily analysis reported a failure" \
    --namespace "ViewerAtlas/Analysis" \
    --metric-name "AnalysisFailedCount" \
    --statistic Sum \
    --period 300 \
    --evaluation-periods 1 \
    --threshold 1 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --treat-missing-data notBreaching \
    --alarm-actions "$SNS_TOPIC_ARN" >/dev/null

aws cloudwatch put-metric-alarm \
    --region "$AWS_REGION" \
    --alarm-name "${SERVICE_PREFIX}-Survey-Partial" \
    --alarm-description "A scheduled survey ended partial or with collection errors" \
    --namespace "ViewerAtlas/Collector" \
    --metric-name "SurveyPartialCount" \
    --statistic Sum \
    --period "$SURVEY_COMPLETION_ALARM_WINDOW_SECONDS" \
    --evaluation-periods 1 \
    --threshold 1 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --treat-missing-data notBreaching \
    --alarm-actions "$SNS_TOPIC_ARN" >/dev/null

aws cloudwatch put-metric-alarm \
    --region "$AWS_REGION" \
    --alarm-name "${SERVICE_PREFIX}-Collector-Error-Spike" \
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

# Remove alarms tied to the retired continuously running collector service.
aws cloudwatch delete-alarms \
    --region "$AWS_REGION" \
    --alarm-names \
        "${SERVICE_PREFIX}-ECSCollector-Unavailable" \
        "${SERVICE_PREFIX}-Collector-Snapshot-Stalled" >/dev/null

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

budget_applied=false
if aws budgets describe-budget \
    --account-id "$AWS_ACCOUNT_ID" \
    --budget-name "$BUDGET_NAME" >/dev/null 2>&1; then
    if aws budgets update-budget \
        --account-id "$AWS_ACCOUNT_ID" \
        --new-budget file:///tmp/vieweratlas-budget.json >/dev/null 2>&1; then
        budget_applied=true
    fi
else
    if aws budgets create-budget \
        --account-id "$AWS_ACCOUNT_ID" \
        --budget file:///tmp/vieweratlas-budget.json >/dev/null 2>&1; then
        budget_applied=true
    fi
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

if [ "$budget_applied" = "true" ]; then
    create_budget_notification "ACTUAL" "80"
    create_budget_notification "FORECASTED" "100"
    info "Budget controls validated/applied"
else
    warn "Budget was not changed (the AWS user may lack budgets:ModifyBudget); monitoring will continue"
fi

lifecycle_json=$(aws s3api get-bucket-lifecycle-configuration \
    --region "$AWS_REGION" \
    --bucket "$S3_BUCKET" \
    --output json 2>/dev/null || true)

if [ -z "$lifecycle_json" ]; then
    fail "No S3 lifecycle configuration found on bucket $S3_BUCKET"
fi

export LIFECYCLE_JSON="$lifecycle_json"

"$PYTHON_BIN" - <<'PY'
import json
import os
import sys

payload = os.environ.get("LIFECYCLE_JSON", "")
if not payload:
    raise SystemExit("Missing lifecycle payload")

config = json.loads(payload)
rules = {rule.get("ID") for rule in config.get("Rules", [])}
expected = {"DeleteSurveySnapshotsV2After90Days", "DeleteOldVODRaw", "ArchiveProcessedData"}
missing = sorted(expected - rules)
if missing:
    raise SystemExit(f"Missing lifecycle rules: {', '.join(missing)}")

survey_rule = next(
    rule for rule in config.get("Rules", [])
    if rule.get("ID") == "DeleteSurveySnapshotsV2After90Days"
)
if survey_rule.get("Expiration", {}).get("Days") != 90:
    raise SystemExit("Survey snapshots must expire after 90 days")
if survey_rule.get("NoncurrentVersionExpiration", {}).get("NoncurrentDays") != 7:
    raise SystemExit("Noncurrent survey snapshot versions must expire after 7 days")
print("Lifecycle configuration validated")
PY

info "Monitoring apply complete. SNS alarms route to: $SNS_TOPIC_ARN"
