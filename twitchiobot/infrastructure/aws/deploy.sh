#!/bin/bash

# AWS ECS Deployment Script for ViewerAtlas
# Idempotent flow for first-time and repeat deployments.

set -euo pipefail
export AWS_PAGER=""

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
ASSIGN_PUBLIC_IP=${ASSIGN_PUBLIC_IP:-ENABLED}
SUBNET_IDS=${SUBNET_IDS:-}
SECURITY_GROUP_ID=${SECURITY_GROUP_ID:-}
PUSH_LATEST=${PUSH_LATEST:-false}
# Must match the task definitions' architecture. They set no runtimePlatform,
# so ECS Fargate expects linux/amd64 regardless of the build machine.
BUILD_PLATFORM=${BUILD_PLATFORM:-linux/amd64}
DYNAMODB_STATE_TABLE=${DYNAMODB_STATE_TABLE:-vieweratlas-collection-state}
LOG_RETENTION_DAYS=${LOG_RETENTION_DAYS:-7}
BUDGET_LIMIT=${BUDGET_LIMIT:-50}
ALERT_EMAIL=${ALERT_EMAIL:-}
TWITCH_CREDENTIALS_SECRET_ID=${TWITCH_CREDENTIALS_SECRET_ID:-vieweratlas/twitch/credentials}

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
    local subnet_id subnet_vpc_id security_group_vpc_id expected_vpc_id route_table_id route_target
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

    # Scheduled Fargate tasks must reach Twitch and AWS over HTTPS. With a
    # public IP, every selected subnet needs a direct Internet Gateway route.
    # Without one, it needs a NAT route. Checking this here prevents tasks that
    # launch successfully but can never connect to EventSub.
    for subnet_id in "${subnet_ids[@]}"; do
        route_table_id=$(aws ec2 describe-route-tables \
            --filters "Name=association.subnet-id,Values=$subnet_id" \
            --region "$AWS_REGION" \
            --query 'RouteTables[0].RouteTableId' \
            --output text 2>/dev/null || true)
        if [ -z "$route_table_id" ] || [ "$route_table_id" = "None" ]; then
            route_table_id=$(aws ec2 describe-route-tables \
                --filters "Name=vpc-id,Values=$expected_vpc_id" "Name=association.main,Values=true" \
                --region "$AWS_REGION" \
                --query 'RouteTables[0].RouteTableId' \
                --output text 2>/dev/null || true)
        fi
        if [ -z "$route_table_id" ] || [ "$route_table_id" = "None" ]; then
            log_error "Could not resolve a route table for subnet $subnet_id"
            exit 1
        fi

        if [ "$ASSIGN_PUBLIC_IP" = "ENABLED" ]; then
            route_target=$(aws ec2 describe-route-tables \
                --route-table-ids "$route_table_id" \
                --region "$AWS_REGION" \
                --query 'RouteTables[0].Routes[?DestinationCidrBlock==`0.0.0.0/0`].GatewayId | [0]' \
                --output text 2>/dev/null || true)
            if ! [[ "$route_target" =~ ^igw- ]]; then
                log_error "Subnet $subnet_id needs a 0.0.0.0/0 Internet Gateway route when ASSIGN_PUBLIC_IP=ENABLED"
                exit 1
            fi
        else
            route_target=$(aws ec2 describe-route-tables \
                --route-table-ids "$route_table_id" \
                --region "$AWS_REGION" \
                --query 'RouteTables[0].Routes[?DestinationCidrBlock==`0.0.0.0/0`].NatGatewayId | [0]' \
                --output text 2>/dev/null || true)
            if ! [[ "$route_target" =~ ^nat- ]]; then
                route_target=$(aws ec2 describe-route-tables \
                    --route-table-ids "$route_table_id" \
                    --region "$AWS_REGION" \
                    --query 'RouteTables[0].Routes[?DestinationCidrBlock==`0.0.0.0/0`].InstanceId | [0]' \
                    --output text 2>/dev/null || true)
            fi
            if ! [[ "$route_target" =~ ^(nat-|i-) ]]; then
                log_error "ASSIGN_PUBLIC_IP=DISABLED requires a NAT route for subnet $subnet_id"
                log_error "These public ViewerAtlas subnets use an Internet Gateway, so set ASSIGN_PUBLIC_IP=ENABLED in .env"
                exit 1
            fi
        fi
    done
}

verify_required_secrets() {
    local secret_name secret_arn

    secret_name="$TWITCH_CREDENTIALS_SECRET_ID"
    secret_arn=$(aws secretsmanager describe-secret \
        --secret-id "$secret_name" \
        --region "$AWS_REGION" \
        --query 'ARN' \
        --output text 2>/dev/null || true)
    if [ -z "$secret_arn" ] || [ "$secret_arn" = "None" ]; then
        log_error "Required AWS Secrets Manager secret is missing: $secret_name"
        log_error "Run ./authorize-twitch.sh before deploying."
        exit 1
    fi
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
}

# Create ECR repositories if they don't exist
create_ecr_repos() {
    log_info "Ensuring ECR repositories..."

    for repo in vieweratlas-collector vieweratlas-analysis; do
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

    log_info "Building $service image for $BUILD_PLATFORM..."
    # Fargate task definitions here have no runtimePlatform, so ECS expects
    # linux/amd64. Building on an Apple Silicon Mac would otherwise produce an
    # arm64-only manifest and fail the pull with CannotPullContainerError.
    docker build --platform "$BUILD_PLATFORM" \
        -t "vieweratlas-$service:$IMAGE_TAG" -f "../docker/$dockerfile" ../..

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

    local trust_file analysis_s3_policy_file collector_s3_policy_file collector_runtime_policy_file
    trust_file=$(mktemp)
    analysis_s3_policy_file=$(mktemp)
    collector_s3_policy_file=$(mktemp)
    collector_runtime_policy_file=$(mktemp)

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
    cat > "$analysis_s3_policy_file" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetBucketLocation"],
      "Resource": ["arn:aws:s3:::${S3_BUCKET}"]
    },
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

    local survey_object_arn="arn:aws:s3:::${S3_BUCKET}/${S3_PREFIX%/}/raw/snapshots/v2/*"
    local survey_list_prefix="${S3_PREFIX%/}/raw/snapshots/v2/*"
    cat > "$collector_s3_policy_file" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetBucketLocation"],
      "Resource": ["arn:aws:s3:::${S3_BUCKET}"]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": ["${survey_object_arn}"]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": ["arn:aws:s3:::${S3_BUCKET}"],
      "Condition": {
        "StringLike": {
          "s3:prefix": ["${survey_list_prefix}"]
        }
      }
    }
  ]
}
JSON

    cat > "$collector_runtime_policy_file" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue"],
      "Resource": [
        "arn:aws:secretsmanager:${AWS_REGION}:${AWS_ACCOUNT_ID}:secret:${TWITCH_CREDENTIALS_SECRET_ID}*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem"
      ],
      "Resource": [
        "arn:aws:dynamodb:${AWS_REGION}:${AWS_ACCOUNT_ID}:table/${DYNAMODB_STATE_TABLE}"
      ]
    }
  ]
}
JSON

    ensure_task_role() {
        local role_name=$1
        local policy_file=$2
        if ! aws iam get-role --role-name "$role_name" >/dev/null 2>&1; then
            log_info "Creating IAM role: $role_name"
            aws iam create-role --role-name "$role_name" --assume-role-policy-document "file://$trust_file" >/dev/null
        else
            log_info "IAM role exists: $role_name"
        fi
        aws iam put-role-policy \
            --role-name "$role_name" \
            --policy-name ViewerAtlasS3Access \
            --policy-document "file://$policy_file" >/dev/null
    }

    ensure_execution_role() {
        local role_name=$1
        if ! aws iam get-role --role-name "$role_name" >/dev/null 2>&1; then
            log_info "Creating IAM role: $role_name"
            aws iam create-role --role-name "$role_name" --assume-role-policy-document "file://$trust_file" >/dev/null
        else
            log_info "IAM role exists: $role_name"
        fi

        aws iam attach-role-policy \
            --role-name "$role_name" \
            --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy >/dev/null
    }

    ensure_task_role "${SERVICE_PREFIX}-collector-task-role" "$collector_s3_policy_file"
    ensure_task_role "${SERVICE_PREFIX}-analysis-task-role" "$analysis_s3_policy_file"

    # The survey retrieves and atomically rotates Twitch credentials at runtime,
    # and uses the existing state table for its overlap-prevention lease.
    aws iam put-role-policy \
        --role-name "${SERVICE_PREFIX}-collector-task-role" \
        --policy-name ViewerAtlasCollectorRuntimeAccess \
        --policy-document "file://$collector_runtime_policy_file" >/dev/null

    ensure_execution_role "${SERVICE_PREFIX}-collector-execution-role"
    ensure_execution_role "${SERVICE_PREFIX}-analysis-execution-role"

    # Old revisions injected separate Twitch secrets through execution roles.
    # The EventSub collector reads the one atomic credential through its task
    # role, so remove the obsolete inline policy anywhere an old task role may
    # still exist. Treat AccessDenied as a deployment failure rather than
    # silently retaining unnecessary secret access.
    remove_retired_secret_policy() {
        local role_name=$1 error_output
        if ! aws iam get-role --role-name "$role_name" >/dev/null 2>&1; then
            return
        fi
        if error_output=$(aws iam delete-role-policy \
            --role-name "$role_name" \
            --policy-name ViewerAtlasSecretsAccess 2>&1); then
            log_info "Removed retired Twitch secret policy from $role_name"
        elif [[ "$error_output" != *"NoSuchEntity"* ]]; then
            log_error "Could not remove the retired Twitch secret policy from $role_name"
            return 1
        fi
    }

    for retired_role in \
        "${SERVICE_PREFIX}-collector-execution-role" \
        "${SERVICE_PREFIX}-vod-collector-execution-role" \
        "${SERVICE_PREFIX}-discovery-execution-role" \
        "${SERVICE_PREFIX}-worker-execution-role"; do
        remove_retired_secret_policy "$retired_role"
    done

    rm -f "$trust_file" "$analysis_s3_policy_file" "$collector_s3_policy_file" "$collector_runtime_policy_file"
}

ensure_log_groups() {
    log_info "Ensuring CloudWatch log groups..."
    for service in collector analysis; do
        local log_group="/ecs/${SERVICE_PREFIX}-${service}"
        aws logs create-log-group --log-group-name "$log_group" --region "$AWS_REGION" >/dev/null 2>&1 || true
        aws logs put-retention-policy \
            --log-group-name "$log_group" \
            --retention-in-days "$LOG_RETENTION_DAYS" \
            --region "$AWS_REGION" >/dev/null
    done
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

    fi

    local ttl_status
    ttl_status=$(aws dynamodb describe-time-to-live \
        --table-name "$DYNAMODB_STATE_TABLE" \
        --region "$AWS_REGION" \
        --query 'TimeToLiveDescription.TimeToLiveStatus' \
        --output text 2>/dev/null || true)
    if [ "$ttl_status" = "DISABLED" ] || [ -z "$ttl_status" ] || [ "$ttl_status" = "None" ]; then
        aws dynamodb update-time-to-live \
            --table-name "$DYNAMODB_STATE_TABLE" \
            --time-to-live-specification 'Enabled=true,AttributeName=ttl' \
            --region "$AWS_REGION" >/dev/null
        log_info "TTL enabling on attribute 'ttl'"
    else
        log_info "DynamoDB TTL state: $ttl_status"
    fi
}

# Register ECS task definitions
register_task_definitions() {
    log_info "Registering ECS task definitions..."

    for task in collector analysis; do
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
            -e "s/\${DYNAMODB_STATE_TABLE}/$DYNAMODB_STATE_TABLE/g" \
            -e "s#\${TWITCH_CREDENTIALS_SECRET_ID}#${TWITCH_CREDENTIALS_SECRET_ID}#g" \
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

quiesce_long_running_services() {
    log_info "Keeping all one-shot task families at ECS service desired count 0"
    ensure_cluster

    # Scheduler launches collector and analysis tasks directly. Do not create
    # permanent services. If services from an older deployment exist, update the
    # current task definition where applicable and force desired count to zero.
    for service in collector analysis vod-collector discovery worker; do
        local full_service_name="${SERVICE_PREFIX}-$service"
        local status task_def_arn
        status=$(aws ecs describe-services \
            --cluster "$ECS_CLUSTER" \
            --services "$full_service_name" \
            --region "$AWS_REGION" \
            --query 'services[0].status' \
            --output text 2>/dev/null || echo "MISSING")
        [ "$status" = "ACTIVE" ] || continue

        if [[ "$service" =~ ^(collector|analysis)$ ]]; then
            task_def_arn=$(aws ecs describe-task-definition \
                --task-definition "$full_service_name" \
                --region "$AWS_REGION" \
                --query 'taskDefinition.taskDefinitionArn' \
                --output text)
            log_info "Updating and stopping legacy service: $full_service_name"
            aws ecs update-service \
                --cluster "$ECS_CLUSTER" \
                --service "$full_service_name" \
                --task-definition "$task_def_arn" \
                --desired-count 0 \
                --region "$AWS_REGION" >/dev/null
        else
            log_info "Stopping retired distributed service: $full_service_name"
            aws ecs update-service \
                --cluster "$ECS_CLUSTER" \
                --service "$full_service_name" \
                --desired-count 0 \
                --region "$AWS_REGION" >/dev/null
        fi
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

    create_ecr_repos
    ensure_s3_bucket_security_controls
    ecr_login

    build_and_push "collector" "Dockerfile.collector"
    build_and_push "analysis" "Dockerfile.analysis"

    ensure_dynamodb_table
    ensure_iam_roles
    ensure_log_groups
    register_task_definitions
    quiesce_long_running_services

    log_info "Deployment completed successfully"
    log_info ""
    log_info "Twitch credential secret: $TWITCH_CREDENTIALS_SECRET_ID"
    log_info "The collector runs only through EventBridge Scheduler; ECS service desired count is 0."
    log_info ""
    log_info "Deployed image tag: $IMAGE_TAG"
}

main "$@"
