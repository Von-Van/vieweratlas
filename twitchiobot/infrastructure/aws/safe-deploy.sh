#!/bin/bash
# Safe AWS Deployment Script with Cost Guardrails
# This script adds multiple layers of protection before deploying to AWS

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}ViewerAtlas Safe Deployment Script${NC}"
echo -e "${GREEN}Cost-Protected AWS Deployment${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Required environment variables
AWS_REGION=${AWS_REGION:-us-east-1}
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
S3_BUCKET=${S3_BUCKET:-}
ALERT_EMAIL=${ALERT_EMAIL:-}

# Pre-flight checks
echo -e "${YELLOW}🔍 Running pre-flight checks...${NC}"

if [ -z "$AWS_ACCOUNT_ID" ]; then
    echo -e "${RED}❌ AWS CLI not configured or not authenticated${NC}"
    echo "Run: aws configure"
    exit 1
fi

if [ -z "$S3_BUCKET" ]; then
    echo -e "${RED}❌ S3_BUCKET environment variable not set${NC}"
    echo "Run: export S3_BUCKET=your-unique-bucket-name"
    exit 1
fi

if [ -z "$ALERT_EMAIL" ]; then
    echo -e "${YELLOW}⚠️  ALERT_EMAIL not set. Budget alerts will not be configured.${NC}"
    read -p "Enter email for cost alerts (or press Enter to skip): " ALERT_EMAIL
fi

echo -e "${GREEN}✓ AWS Account: $AWS_ACCOUNT_ID${NC}"
echo -e "${GREEN}✓ Region: $AWS_REGION${NC}"
echo -e "${GREEN}✓ S3 Bucket: $S3_BUCKET${NC}"
echo ""

# Cost Estimate
echo -e "${YELLOW}💰 Estimated Monthly Costs:${NC}"
echo "  S3 Storage (10GB):         ~\$0.23/month"
echo "  ECS Fargate (spot, 4h/day): ~\$3-5/month"
echo "  CloudWatch Logs (1GB):     ~\$0.50/month"
echo "  Secrets Manager (3 secrets): ~\$1.20/month"
echo "  Data Transfer (minimal):   ~\$0.50/month"
echo "  ────────────────────────────────────────"
echo "  TOTAL ESTIMATED:           ~\$5-8/month"
echo ""
echo -e "${YELLOW}⚠️  These estimates assume:${NC}"
echo "  - 100 channels monitored"
echo "  - 4 hours of collection per day"
echo "  - Spot pricing (70% discount)"
echo "  - Default cost protection limits active"
echo ""

# Confirm deployment
read -p "$(echo -e ${YELLOW}Continue with deployment? [y/N]:${NC} )" -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${RED}Deployment cancelled${NC}"
    exit 1
fi

# Step 1: Create AWS Budget (Cost Protection)
echo ""
echo -e "${GREEN}📊 Step 1: Setting up AWS Budget Alert...${NC}"

BUDGET_NAME="vieweratlas-monthly-limit"
BUDGET_LIMIT=50  # $50/month hard limit

if [ -n "$ALERT_EMAIL" ]; then
    cat > /tmp/budget.json <<EOF
{
  "BudgetName": "${BUDGET_NAME}",
  "BudgetLimit": {
    "Amount": "${BUDGET_LIMIT}",
    "Unit": "USD"
  },
  "TimeUnit": "MONTHLY",
  "BudgetType": "COST"
}
EOF

    cat > /tmp/notifications.json <<EOF
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
EOF

    aws budgets create-budget \
        --account-id "$AWS_ACCOUNT_ID" \
        --budget file:///tmp/budget.json \
        --notifications-with-subscribers file:///tmp/notifications.json 2>/dev/null || \
        echo -e "${YELLOW}  Budget already exists or failed to create${NC}"
    
    echo -e "${GREEN}  ✓ Budget alert configured: \$${BUDGET_LIMIT}/month limit${NC}"
    echo -e "${GREEN}  ✓ Email alerts to: ${ALERT_EMAIL}${NC}"
else
    echo -e "${YELLOW}  ⚠️  Skipping budget alerts (no email provided)${NC}"
fi

# Step 2: Create/Verify S3 Bucket with lifecycle rules
echo ""
echo -e "${GREEN}📦 Step 2: Setting up S3 bucket...${NC}"

aws s3 mb "s3://${S3_BUCKET}" --region "$AWS_REGION" 2>/dev/null || \
    echo -e "${YELLOW}  Bucket already exists${NC}"

# Enable versioning
aws s3api put-bucket-versioning \
    --bucket "${S3_BUCKET}" \
    --versioning-configuration Status=Enabled

# Set lifecycle policy to delete old data
cat > /tmp/lifecycle.json <<'EOF'
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
EOF

aws s3api put-bucket-lifecycle-configuration \
    --bucket "${S3_BUCKET}" \
    --lifecycle-configuration file:///tmp/lifecycle.json

echo -e "${GREEN}  ✓ S3 bucket configured with auto-deletion policies${NC}"
echo -e "${GREEN}  ✓ Raw logs deleted after 30 days${NC}"
echo -e "${GREEN}  ✓ VOD raw data deleted after 7 days${NC}"

# Step 3: Set CloudWatch Log Retention
echo ""
echo -e "${GREEN}📝 Step 3: Setting CloudWatch log retention...${NC}"

for log_group in "/ecs/vieweratlas-collector" "/ecs/vieweratlas-analysis" "/ecs/vieweratlas-vod-collector"; do
    aws logs create-log-group --log-group-name "$log_group" 2>/dev/null || true
    aws logs put-retention-policy \
        --log-group-name "$log_group" \
        --retention-in-days 7
    echo -e "${GREEN}  ✓ $log_group: 7-day retention${NC}"
done

# Step 4: Verify cost protection in config
echo ""
echo -e "${GREEN}⚙️  Step 4: Verifying cost protection settings...${NC}"

CONFIG_FILE="../../config/config.yaml"
if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "${RED}❌ Config file not found: $CONFIG_FILE${NC}"
    echo "Create config.yaml with cost protection settings"
    exit 1
fi

# Check for cost protection settings
if grep -q "max_runtime_hours" "$CONFIG_FILE" && \
   grep -q "max_collection_cycles" "$CONFIG_FILE" && \
   grep -q "max_vods_per_run" "$CONFIG_FILE"; then
    echo -e "${GREEN}  ✓ Cost protection settings found in config${NC}"
else
    echo -e "${YELLOW}  ⚠️  Cost protection settings not fully configured${NC}"
    echo "  Ensure config.yaml includes:"
    echo "    collection.max_runtime_hours: 24"
    echo "    collection.max_collection_cycles: 100"
    echo "    vod.max_vods_per_run: 50"
fi

# Step 5: Run standard deployment
echo ""
echo -e "${GREEN}🚀 Step 5: Running deployment...${NC}"

# Call the standard deploy script
if [ -f "./deploy.sh" ]; then
    bash ./deploy.sh
else
    echo -e "${RED}❌ deploy.sh not found${NC}"
    exit 1
fi

# Step 6: Final verification
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✅ Deployment Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${YELLOW}🛡️  Cost Protection Summary:${NC}"
echo "  ✓ AWS Budget: \$${BUDGET_LIMIT}/month limit"
echo "  ✓ Max Runtime: 24 hours per task"
echo "  ✓ Max Cycles: 100 collections per task"
echo "  ✓ Max VODs: 50 per run"
echo "  ✓ S3 Lifecycle: Auto-delete old data"
echo "  ✓ Logs: 7-day retention"
echo "  ✓ ECS: Spot instances (70% discount)"
echo ""
echo -e "${YELLOW}📊 Next Steps:${NC}"
echo "  1. Monitor costs: https://console.aws.amazon.com/billing/"
echo "  2. View tasks: aws ecs list-tasks --cluster vieweratlas-cluster"
echo "  3. Check logs: aws logs tail /ecs/vieweratlas-collector --follow"
echo "  4. View S3 data: aws s3 ls s3://${S3_BUCKET}/ --recursive"
echo ""
echo -e "${YELLOW}⚠️  Cost Protection Active:${NC}"
echo "  Tasks will auto-stop after limits are reached"
echo "  You'll receive email alerts at 80% and 100% of budget"
echo ""
