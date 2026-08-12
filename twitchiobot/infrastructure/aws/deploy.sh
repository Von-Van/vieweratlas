#!/bin/bash

# AWS ECS Deployment Script for ViewerAtlas
# Idempotent flow for first-time and repeat deployments.

set -euo pipefail

load_env_file() {
    local env_file=".env"
    if [ ! -f "$env_file" ]; then
        return
    fi

    while IFS= read -r line || [ -n "$line" ]; do
        # Skip comments/blank lines
        case "$line" in
            ''|'#'*)
                continue
                ;;
        esac

        # Support optional leading "export "
        line="${line#export }"

        # Only load KEY=VALUE assignments
        if [[ ! "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
            continue
        fi

        local key="${line%%=*}"
        local value="${line#*=}"
        # Trim surrounding quotes for simple quoted values.
        value="${value%\"}"
        value="${value#\"}"
        value="${value%\'}"
        value="${value#\'}"
        export "$key=$value"
    done < "$env_file"
}

load_env_file

# Environment-aware naming
# ENVIRONMENT=staging → cluster/service names get '-staging-' infix
# ENVIRONMENT=prod (default) → preserves existing 'vieweratlas-*' names
ENVIRONMENT="${ENVIRONMENT:-prod}"
if [ "$ENVIRONMENT" = "prod" ]; then
    SERVICE_PREFIX="vieweratlas"
    _DEFAULT_CLUSTER="vieweratlas-cluster"
else
    SERVICE_PREFIX="vieweratlas-${ENVIRONMENT}"
    _DEFAULT_CLUSTER="vieweratlas-${ENVIRONMENT}-cluster"
fi

# Configuration
AWS_REGION=${AWS_REGION:-us-east-1}
S3_BUCKET=${S3_BUCKET:-}
S3_PREFIX=${S3_PREFIX:-vieweratlas/}
ECS_CLUSTER=${ECS_CLUSTER:-$_DEFAULT_CLUSTER}
ASSIGN_PUBLIC_IP=${ASSIGN_PUBLIC_IP:-DISABLED}
SUBNET_IDS=${SUBNET_IDS:-}
SECURITY_GROUP_ID=${SECURITY_GROUP_ID:-}
PUSH_LATEST=${PUSH_LATEST:-false}
DYNAMODB_STATE_TABLE=${DYNAMODB_STATE_TABLE:-vieweratlas-collection-state}
SQS_CHANNEL_QUEUE_URL=${SQS_CHANNEL_QUEUE_URL:-}  # populated by ensure_sqs_queue
LOG_RETENTION_DAYS=${LOG_RETENTION_DAYS:-7}
BUDGET_LIMIT=${BUDGET_LIMIT:-50}

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    DEFAULT_IMAGE_TAG=$(git rev-parse --short HEAD)
else
    DEFAULT_IMAGE_TAG=$(date +%Y%m%d%H%M%S)
fi
IMAGE_TAG=${IMAGE_TAG:-$DEFAULT_IMAGE_TAG}

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

validate_required_deployment_inputs() {
    if [ -z "$S3_BUCKET" ]; then
        log_error "S3_BUCKET is not set. Set it in .env or export S3_BUCKET=your-bucket"
        exit 1
    fi

    if [ -z "$SUBNET_IDS" ] || [ -z "$SECURITY_GROUP_ID" ]; then
        log_error "SUBNET_IDS and SECURITY_GROUP_ID are required for a production deployment"
        exit 1
    fi

    if [ -z "$ALERT_EMAIL" ]; then
        log_error "ALERT_EMAIL is required so deployment and budget alerts have an owner"
        exit 1
    fi

    if ! [[ "$ASSIGN_PUBLIC_IP" =~ ^(ENABLED|DISABLED)$ ]]; then
        log_error "ASSIGN_PUBLIC_IP must be ENABLED or DISABLED"
        exit 1
    fi

    if [[ "$S3_PREFIX" = /* ]]; then
        log_error "S3_PREFIX must be relative and must not start with /"
        exit 1
    fi

    if ! [[ "$BUDGET_LIMIT" =~ ^[0-9]+([.][0-9]+)?$ ]] || \
        ! awk -v amount="$BUDGET_LIMIT" 'BEGIN { exit !(amount + 0 > 0) }'; then
        log_error "BUDGET_LIMIT must be a positive dollar amount"
        exit 1
    fi
}

validate_network_configuration() {
    local subnet_id subnet_vpc_id security_group_vpc_id expected_vpc_id
    local -a subnet_ids

    IFS=',' read -r -a subnet_ids <<< "$SUBNET_IDS"
    if [ "${#subnet_ids[@]}" -lt 2 ]; then
        log_error "At least two comma-separated SUBNET_IDS are required for a production deployment"
        exit 1
    fi

    for subnet_id in "${subnet_ids[@]}"; do
        if ! [[ "$subnet_id" =~ ^subnet-[0-9a-f]+$ ]]; then
            log_error "Invalid subnet ID: $subnet_id"
            exit 1
        fi

        subnet_vpc_id=$(aws ec2 describe-subnets \
            --subnet-ids "$subnet_id" \
            --region "$AWS_REGION" \
            --query 'Subnets[0].VpcId' \
            --output text 2>/dev/null || true)
        if [ -z "$subnet_vpc_id" ] || [ "$subnet_vpc_id" = "None" ]; then
            log_error "Subnet $subnet_id was not found in $AWS_REGION"
            exit 1
        fi

        if [ -z "${expected_vpc_id:-}" ]; then
            expected_vpc_id="$subnet_vpc_id"
        elif [ "$subnet_vpc_id" != "$expected_vpc_id" ]; then
            log_error "All SUBNET_IDS must belong to the same VPC"
            exit 1
        fi
    done

    if ! [[ "$SECURITY_GROUP_ID" =~ ^sg-[0-9a-f]+$ ]]; then
        log_error "Invalid security group ID: $SECURITY_GROUP_ID"
        exit 1
    fi

    security_group_vpc_id=$(aws ec2 describe-security-groups \
        --group-ids "$SECURITY_GROUP_ID" \
        --region "$AWS_REGION" \
        --query 'SecurityGroups[0].VpcId' \
        --output text 2>/dev/null || true)
    if [ -z "$security_group_vpc_id" ] || [ "$security_group_vpc_id" = "None" ]; then
        log_error "Security group $SECURITY_GROUP_ID was not found in $AWS_REGION"
        exit 1
    fi
    if [ "$security_group_vpc_id" != "$expected_vpc_id" ]; then
        log_error "SECURITY_GROUP_ID must belong to the same VPC as SUBNET_IDS"
        exit 1
    fi
}

verify_required_secrets() {
    local secret_name secret_arn

    for secret_name in vieweratlas/twitch/oauth_token vieweratlas/twitch/client_id; do
        secret_arn=$(aws secretsmanager describe-secret \
            --secret-id "$secret_name" \
            --region "$AWS_REGION" \
            --query 'ARN' \
            --output text 2>/dev/null || true)
        if [ -z "$secret_arn" ] || [ "$secret_arn" = "None" ]; then
            log_error "Required AWS Secrets Manager secret is missing: $secret_name"
            exit 1
        fi
    done
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."

    if ! command -v aws >/dev/null 2>&1; then
        log_error "AWS CLI not found. Please install: https://aws.amazon.com/cli/"
        exit 1
    fi

    if ! command -v docker >/dev/null 2>&1; then
        log_error "Docker not found. Please install: https://www.docker.com/"
        exit 1
    fi
    if ! docker info >/dev/null 2>&1; then
        log_error "Docker is installed but its daemon is not running. Start Docker Desktop and try again."
        exit 1
    fi

    if ! command -v python3 >/dev/null 2>&1; then
        log_error "python3 not found (required for task definition patching)"
        exit 1
    fi

    # Validate AWS credentials
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
    if [ -z "$AWS_ACCOUNT_ID" ]; then
        log_error "AWS credentials not configured. Run: aws configure"
        exit 1
    fi

    validate_required_deployment_inputs
    validate_network_configuration
    verify_required_secrets

    log_info "Prerequisites check passed"
    log_info "  AWS Account: $AWS_ACCOUNT_ID"
    log_info "  Region:      $AWS_REGION"
    log_info "  S3 Bucket:   $S3_BUCKET"
    log_info "  S3 Prefix:   $S3_PREFIX"
    log_info "  Image Tag:   $IMAGE_TAG"
    log_info "  Push latest: $PUSH_LATEST"
    if [ -n "${EFS_ID:-}" ]; then
        log_info "  EFS ID:      $EFS_ID"
    else
        log_warn "  EFS_ID not set — EFS volume mounts will be stripped from task definitions (VOD queue will be task-local/ephemeral)"
    fi
}

# Create ECR repositories if they don't exist
create_ecr_repos() {
    log_info "Ensuring ECR repositories..."

    for repo in vieweratlas-collector vieweratlas-analysis vieweratlas-vod vieweratlas-discovery vieweratlas-worker; do
        if ! aws ecr describe-repositories --repository-names "$repo" --region "$AWS_REGION" >/dev/null 2>&1; then
            log_info "Creating repository: $repo"
            aws ecr create-repository \
                --repository-name "$repo" \
                --region "$AWS_REGION" \
                --image-scanning-configuration scanOnPush=true \
                --encryption-configuration encryptionType=AES256 >/dev/null
        else
            log_info "Repository already exists: $repo"
        fi
    done
}

ensure_s3_bucket_security_controls() {
    log_info "Ensuring S3 bucket security controls..."

    aws s3 mb "s3://${S3_BUCKET}" --region "$AWS_REGION" >/dev/null 2>&1 || true

    aws s3api put-public-access-block \
        --bucket "$S3_BUCKET" \
        --public-access-block-configuration \
        BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true >/dev/null

    aws s3api put-bucket-encryption \
        --bucket "$S3_BUCKET" \
        --server-side-encryption-configuration \
        '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' >/dev/null
}

# Login to ECR
ecr_login() {
    log_info "Logging into ECR..."
    aws ecr get-login-password --region "$AWS_REGION" | \
        docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com" >/dev/null
}

# Build and push Docker images
build_and_push() {
    local service=$1
    local dockerfile=$2
    local image_uri="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/vieweratlas-$service:$IMAGE_TAG"
    local latest_uri="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/vieweratlas-$service:latest"

    log_info "Building $service image..."
    docker build -t "vieweratlas-$service:$IMAGE_TAG" -f "../docker/$dockerfile" ../..

    log_info "Tagging $service image..."
    docker tag "vieweratlas-$service:$IMAGE_TAG" "$image_uri"
    if [ "$PUSH_LATEST" = "true" ]; then
        docker tag "vieweratlas-$service:$IMAGE_TAG" "$latest_uri"
    fi

    log_info "Pushing $service image to ECR..."
    docker push "$image_uri"
    if [ "$PUSH_LATEST" = "true" ]; then
        docker push "$latest_uri"
    fi

    log_info "$service image pushed: $image_uri"
}

ensure_iam_roles() {
    log_info "Ensuring IAM roles and policies..."

    local trust_file s3_policy_file secrets_policy_file
    trust_file=$(mktemp)
    s3_policy_file=$(mktemp)
    secrets_policy_file=$(mktemp)

    cat > "$trust_file" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Service": "ecs-tasks.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }
  ]
}
JSON

    local s3_object_arn="arn:aws:s3:::${S3_BUCKET}/${S3_PREFIX%/}/*"
    cat > "$s3_policy_file" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject"],
      "Resource": ["${s3_object_arn}"]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": ["arn:aws:s3:::${S3_BUCKET}"],
      "Condition": {
        "StringLike": {
          "s3:prefix": ["${S3_PREFIX%/}/*"]
        }
      }
    }
  ]
}
JSON

    cat > "$secrets_policy_file" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": [
        "arn:aws:secretsmanager:${AWS_REGION}:${AWS_ACCOUNT_ID}:secret:vieweratlas/twitch/oauth_token*",
        "arn:aws:secretsmanager:${AWS_REGION}:${AWS_ACCOUNT_ID}:secret:vieweratlas/twitch/client_id*"
      ]
    }
  ]
}
JSON

    ensure_task_role() {
        local role_name=$1
        if ! aws iam get-role --role-name "$role_name" >/dev/null 2>&1; then
            log_info "Creating IAM role: $role_name"
            aws iam create-role --role-name "$role_name" --assume-role-policy-document "file://$trust_file" >/dev/null
        else
            log_info "IAM role exists: $role_name"
        fi
        aws iam put-role-policy \
            --role-name "$role_name" \
            --policy-name ViewerAtlasS3Access \
            --policy-document "file://$s3_policy_file" >/dev/null
    }

    ensure_execution_role() {
        local role_name=$1
        local include_secrets=$2
        if ! aws iam get-role --role-name "$role_name" >/dev/null 2>&1; then
            log_info "Creating IAM role: $role_name"
            aws iam create-role --role-name "$role_name" --assume-role-policy-document "file://$trust_file" >/dev/null
        else
            log_info "IAM role exists: $role_name"
        fi

        aws iam attach-role-policy \
            --role-name "$role_name" \
            --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy >/dev/null

        if [ "$include_secrets" = "yes" ]; then
            aws iam put-role-policy \
                --role-name "$role_name" \
                --policy-name ViewerAtlasSecretsAccess \
                --policy-document "file://$secrets_policy_file" >/dev/null
        fi
    }

    local sqs_dynamo_policy_file
    sqs_dynamo_policy_file=$(mktemp)
    cat > "$sqs_dynamo_policy_file" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sqs:SendMessage",
        "sqs:SendMessageBatch",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueUrl",
        "sqs:GetQueueAttributes"
      ],
      "Resource": ["arn:aws:sqs:${AWS_REGION}:${AWS_ACCOUNT_ID}:${SERVICE_PREFIX}-channel-tasks.fifo"]
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:Query"
      ],
      "Resource": ["arn:aws:dynamodb:${AWS_REGION}:${AWS_ACCOUNT_ID}:table/${DYNAMODB_STATE_TABLE}"]
    }
  ]
}
JSON

    ensure_task_role "${SERVICE_PREFIX}-collector-task-role"
    ensure_task_role "${SERVICE_PREFIX}-analysis-task-role"
    ensure_task_role "${SERVICE_PREFIX}-vod-collector-task-role"
    ensure_task_role "${SERVICE_PREFIX}-discovery-task-role"
    ensure_task_role "${SERVICE_PREFIX}-worker-task-role"

    # Attach SQS+DynamoDB policy to discovery and worker task roles
    for role in "${SERVICE_PREFIX}-discovery-task-role" "${SERVICE_PREFIX}-worker-task-role"; do
        aws iam put-role-policy \
            --role-name "$role" \
            --policy-name ViewerAtlasSQSDynamoAccess \
            --policy-document "file://$sqs_dynamo_policy_file" >/dev/null
    done

    ensure_execution_role "${SERVICE_PREFIX}-collector-execution-role" "yes"
    ensure_execution_role "${SERVICE_PREFIX}-analysis-execution-role" "no"
    ensure_execution_role "${SERVICE_PREFIX}-vod-collector-execution-role" "yes"
    ensure_execution_role "${SERVICE_PREFIX}-discovery-execution-role" "yes"
    ensure_execution_role "${SERVICE_PREFIX}-worker-execution-role" "yes"

    rm -f "$trust_file" "$s3_policy_file" "$secrets_policy_file" "$sqs_dynamo_policy_file"
}

ensure_log_groups() {
    log_info "Ensuring CloudWatch log groups..."
    for service in collector analysis vod-collector discovery worker; do
        local log_group="/ecs/${SERVICE_PREFIX}-${service}"
        aws logs create-log-group --log-group-name "$log_group" --region "$AWS_REGION" >/dev/null 2>&1 || true
        aws logs put-retention-policy \
            --log-group-name "$log_group" \
            --retention-in-days "$LOG_RETENTION_DAYS" \
            --region "$AWS_REGION" >/dev/null
    done
}

# Ensure SQS FIFO queue exists
ensure_sqs_queue() {
    local queue_name="${SERVICE_PREFIX}-channel-tasks.fifo"
    log_info "Ensuring SQS FIFO queue: $queue_name"

    local existing_url
    existing_url=$(aws sqs get-queue-url --queue-name "$queue_name" --region "$AWS_REGION" --query 'QueueUrl' --output text 2>/dev/null || echo "")

    if [ -n "$existing_url" ] && [ "$existing_url" != "None" ]; then
        SQS_CHANNEL_QUEUE_URL="$existing_url"
        log_info "Queue already exists: $SQS_CHANNEL_QUEUE_URL"
    else
        log_info "Creating SQS FIFO queue: $queue_name"
        SQS_CHANNEL_QUEUE_URL=$(aws sqs create-queue \
            --queue-name "$queue_name" \
            --attributes '{
                "FifoQueue": "true",
                "ContentBasedDeduplication": "false",
                "VisibilityTimeout": "900",
                "MessageRetentionPeriod": "86400"
            }' \
            --region "$AWS_REGION" \
            --query 'QueueUrl' \
            --output text)
        log_info "Created queue: $SQS_CHANNEL_QUEUE_URL"
    fi
}

# Ensure DynamoDB table for collection state
ensure_dynamodb_table() {
    log_info "Ensuring DynamoDB table: $DYNAMODB_STATE_TABLE"

    if aws dynamodb describe-table --table-name "$DYNAMODB_STATE_TABLE" --region "$AWS_REGION" >/dev/null 2>&1; then
        log_info "Table already exists: $DYNAMODB_STATE_TABLE"
    else
        log_info "Creating DynamoDB table: $DYNAMODB_STATE_TABLE"
        aws dynamodb create-table \
            --table-name "$DYNAMODB_STATE_TABLE" \
            --attribute-definitions AttributeName=pk,AttributeType=S \
            --key-schema AttributeName=pk,KeyType=HASH \
            --billing-mode PAY_PER_REQUEST \
            --region "$AWS_REGION" >/dev/null

        log_info "Waiting for table to become active..."
        aws dynamodb wait table-exists --table-name "$DYNAMODB_STATE_TABLE" --region "$AWS_REGION"

        aws dynamodb update-time-to-live \
            --table-name "$DYNAMODB_STATE_TABLE" \
            --time-to-live-specification 'Enabled=true,AttributeName=ttl' \
            --region "$AWS_REGION" >/dev/null
        log_info "TTL enabled on attribute 'ttl'"
    fi
}

# Register ECS task definitions
register_task_definitions() {
    log_info "Registering ECS task definitions..."

    for task in collector analysis vod-collector discovery worker; do
        local task_def_file="ecs-task-$task.json"

        if [ ! -f "$task_def_file" ]; then
            log_warn "Task definition file not found: $task_def_file"
            continue
        fi

        local temp_file
        temp_file=$(mktemp)
        sed -e "s/\${AWS_ACCOUNT_ID}/$AWS_ACCOUNT_ID/g" \
            -e "s/\${AWS_REGION}/$AWS_REGION/g" \
            -e "s/\${S3_BUCKET}/$S3_BUCKET/g" \
            -e "s#\${S3_PREFIX}#${S3_PREFIX}#g" \
            -e "s/\${IMAGE_TAG}/$IMAGE_TAG/g" \
            -e "s#\${SQS_CHANNEL_QUEUE_URL}#${SQS_CHANNEL_QUEUE_URL}#g" \
            -e "s/\${DYNAMODB_STATE_TABLE}/$DYNAMODB_STATE_TABLE/g" \
            -e "s/\"family\": \"vieweratlas-$task\"/\"family\": \"${SERVICE_PREFIX}-$task\"/" \
            -e "s|/ecs/vieweratlas-$task|/ecs/${SERVICE_PREFIX}-$task|" \
            -e "s#role/vieweratlas-#role/${SERVICE_PREFIX}-#g" \
            "$task_def_file" > "$temp_file"

        # Handle optional EFS_ID: replace if set, otherwise strip volumes/mountPoints
        if [ -n "${EFS_ID:-}" ]; then
            sed -i.bak "s/\${EFS_ID}/$EFS_ID/g" "$temp_file"
            rm -f "$temp_file.bak"
        else
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
        fi

        log_info "Registering task definition: ${SERVICE_PREFIX}-$task"
        aws ecs register-task-definition \
            --cli-input-json "file://$temp_file" \
            --region "$AWS_REGION" >/dev/null

        rm -f "$temp_file"
    done
}

ensure_cluster() {
    if aws ecs describe-clusters --clusters "$ECS_CLUSTER" --region "$AWS_REGION" --query 'clusters[0].status' --output text 2>/dev/null | grep -q "ACTIVE"; then
        log_info "ECS cluster exists: $ECS_CLUSTER"
    else
        log_info "Creating ECS cluster: $ECS_CLUSTER"
        aws ecs create-cluster --cluster-name "$ECS_CLUSTER" --region "$AWS_REGION" >/dev/null
    fi
}

# ECS uses this AWS-managed role to create and manage resources for ECS services.
# It is normally created automatically the first time an ECS service is created,
# but explicit creation makes the first deployment reliable for new AWS accounts.
ensure_ecs_service_linked_role() {
    local role_name="AWSServiceRoleForECS"

    if aws iam get-role --role-name "$role_name" >/dev/null 2>&1; then
        log_info "ECS service-linked role exists: $role_name"
        return
    fi

    log_info "Creating ECS service-linked role: $role_name"
    if ! aws iam create-service-linked-role \
        --aws-service-name ecs.amazonaws.com >/dev/null 2>&1; then
        # Another deployment may have created it between the check and this call.
        if ! aws iam get-role --role-name "$role_name" >/dev/null 2>&1; then
            log_error "Could not create the ECS service-linked role. Ensure the deploy user can run iam:CreateServiceLinkedRole for ecs.amazonaws.com."
            exit 1
        fi
    fi
}

network_config_arg() {
    local subnets_csv=$1
    local security_group=$2
    local assign_public_ip=$3
    echo "awsvpcConfiguration={subnets=[${subnets_csv}],securityGroups=[${security_group}],assignPublicIp=${assign_public_ip}}"
}

upsert_services() {
    log_info "Ensuring ECS services in cluster: $ECS_CLUSTER"

    ensure_cluster

    for service in collector analysis vod-collector discovery worker; do
        local full_service_name="${SERVICE_PREFIX}-$service"
        local desired_count="0"
        case "$service" in
            collector)
                desired_count=${COLLECTOR_DESIRED_COUNT:-1}
                ;;
            analysis)
                desired_count=${ANALYSIS_DESIRED_COUNT:-0}
                ;;
            vod-collector)
                desired_count=${VOD_COLLECTOR_DESIRED_COUNT:-0}
                ;;
            discovery)
                desired_count=${DISCOVERY_DESIRED_COUNT:-0}
                ;;
            worker)
                desired_count=${WORKER_DESIRED_COUNT:-0}
                ;;
        esac

        local task_def_arn
        task_def_arn=$(aws ecs describe-task-definition \
            --task-definition "$full_service_name" \
            --region "$AWS_REGION" \
            --query 'taskDefinition.taskDefinitionArn' \
            --output text)

        local status
        status=$(aws ecs describe-services \
            --cluster "$ECS_CLUSTER" \
            --services "$full_service_name" \
            --region "$AWS_REGION" \
            --query 'services[0].status' \
            --output text 2>/dev/null || echo "MISSING")

        if [ "$status" = "ACTIVE" ]; then
            log_info "Updating service: $full_service_name"
            aws ecs update-service \
                --cluster "$ECS_CLUSTER" \
                --service "$full_service_name" \
                --task-definition "$task_def_arn" \
                --network-configuration "$(network_config_arg "$SUBNET_IDS" "$SECURITY_GROUP_ID" "$ASSIGN_PUBLIC_IP")" \
                --force-new-deployment \
                --region "$AWS_REGION" >/dev/null
            continue
        fi

        if [ -z "$SUBNET_IDS" ] || [ -z "$SECURITY_GROUP_ID" ]; then
            log_warn "Skipping create for $full_service_name (set SUBNET_IDS and SECURITY_GROUP_ID for first-time service creation)"
            continue
        fi

        log_info "Creating service: $full_service_name"
        aws ecs create-service \
            --cluster "$ECS_CLUSTER" \
            --service-name "$full_service_name" \
            --task-definition "$task_def_arn" \
            --desired-count "$desired_count" \
            --launch-type FARGATE \
            --network-configuration "$(network_config_arg "$SUBNET_IDS" "$SECURITY_GROUP_ID" "$ASSIGN_PUBLIC_IP")" \
            --region "$AWS_REGION" >/dev/null
    done
}

# Main execution
main() {
    log_info "Starting ViewerAtlas deployment to AWS ECS"

    check_prerequisites

    if [ "${1:-}" = "--preflight" ]; then
        log_info "Deployment preflight passed; no AWS resources were changed"
        return
    fi
    if [ "$#" -ne 0 ]; then
        log_error "Usage: ./deploy.sh [--preflight]"
        exit 1
    fi

    # Create this at the start of the real deployment. IAM role changes can take
    # a short time to propagate, and image builds provide that time before ECS
    # needs the role during service creation.
    ensure_ecs_service_linked_role

    create_ecr_repos
    ensure_s3_bucket_security_controls
    ecr_login

    build_and_push "collector" "Dockerfile.collector"
    build_and_push "analysis" "Dockerfile.analysis"
    build_and_push "vod" "Dockerfile.vod"
    build_and_push "discovery" "Dockerfile.discovery"
    build_and_push "worker" "Dockerfile.worker"

    ensure_sqs_queue
    ensure_dynamodb_table
    ensure_iam_roles
    ensure_log_groups
    register_task_definitions
    upsert_services

    log_info "Deployment completed successfully"
    log_info ""
    log_info "Secrets required in AWS Secrets Manager:"
    log_info "  vieweratlas/twitch/oauth_token"
    log_info "  vieweratlas/twitch/client_id"
    log_info ""
    log_info "Deployed image tag: $IMAGE_TAG"
}

main "$@"
