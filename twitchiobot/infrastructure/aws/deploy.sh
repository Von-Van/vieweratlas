#!/bin/bash

# AWS ECS Deployment Script for ViewerAtlas
# This script builds and deploys Docker images to ECR, then updates ECS services

set -e

# Load .env file if present (for local runs; CI/CD should set vars directly)
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

# Configuration
AWS_REGION=${AWS_REGION:-us-east-1}
S3_BUCKET=${S3_BUCKET:-}

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

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    if ! command -v aws &> /dev/null; then
        log_error "AWS CLI not found. Please install: https://aws.amazon.com/cli/"
        exit 1
    fi
    
    if ! command -v docker &> /dev/null; then
        log_error "Docker not found. Please install: https://www.docker.com/"
        exit 1
    fi

    # Validate AWS credentials
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
    if [ -z "$AWS_ACCOUNT_ID" ]; then
        log_error "AWS credentials not configured. Run: aws configure"
        exit 1
    fi

    # Validate required variables
    if [ -z "$S3_BUCKET" ]; then
        log_error "S3_BUCKET is not set. Set it in .env or export S3_BUCKET=your-bucket"
        exit 1
    fi

    log_info "Prerequisites check passed"
    log_info "  AWS Account: $AWS_ACCOUNT_ID"
    log_info "  Region:      $AWS_REGION"
    log_info "  S3 Bucket:   $S3_BUCKET"
    if [ -n "${EFS_ID:-}" ]; then
        log_info "  EFS ID:      $EFS_ID"
    else
        log_warn "  EFS_ID not set — EFS volume mounts will be skipped in task definitions"
    fi
}

# Create ECR repositories if they don't exist
create_ecr_repos() {
    log_info "Creating ECR repositories..."
    
    for repo in vieweratlas-collector vieweratlas-analysis vieweratlas-vod; do
        if ! aws ecr describe-repositories --repository-names "$repo" --region "$AWS_REGION" &> /dev/null; then
            log_info "Creating repository: $repo"
            aws ecr create-repository \
                --repository-name "$repo" \
                --region "$AWS_REGION" \
                --image-scanning-configuration scanOnPush=true \
                --encryption-configuration encryptionType=AES256
        else
            log_info "Repository already exists: $repo"
        fi
    done
}

# Login to ECR
ecr_login() {
    log_info "Logging into ECR..."
    aws ecr get-login-password --region "$AWS_REGION" | \
        docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
}

# Build and push Docker images
build_and_push() {
    local service=$1
    local dockerfile=$2
    local image_uri="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/vieweratlas-$service:latest"
    
    log_info "Building $service image..."
    docker build -t "vieweratlas-$service:latest" -f "../docker/$dockerfile" ../..
    
    log_info "Tagging $service image..."
    docker tag "vieweratlas-$service:latest" "$image_uri"
    
    log_info "Pushing $service image to ECR..."
    docker push "$image_uri"
    
    log_info "$service image pushed: $image_uri"
}

# Register ECS task definitions
register_task_definitions() {
    log_info "Registering ECS task definitions..."
    
    for task in collector analysis vod-collector; do
        local task_def_file="ecs-task-$task.json"
        
        if [ ! -f "$task_def_file" ]; then
            log_warn "Task definition file not found: $task_def_file"
            continue
        fi
        
        # Replace placeholders in task definition
        local temp_file=$(mktemp)
        sed -e "s/\${AWS_ACCOUNT_ID}/$AWS_ACCOUNT_ID/g" \
            -e "s/\${AWS_REGION}/$AWS_REGION/g" \
            -e "s/\${S3_BUCKET}/$S3_BUCKET/g" \
            "$task_def_file" > "$temp_file"

        # Handle optional EFS_ID: replace if set, otherwise strip volumes/mountPoints
        if [ -n "${EFS_ID:-}" ]; then
            sed -i.bak "s/\${EFS_ID}/$EFS_ID/g" "$temp_file"
            rm -f "$temp_file.bak"
        else
            # Remove EFS volume and mountPoint blocks so the task registers without EFS
            python3 -c "
import json, sys
with open('$temp_file') as f:
    td = json.load(f)
td.pop('volumes', None)
for container in td.get('containerDefinitions', []):
    container.pop('mountPoints', None)
with open('$temp_file', 'w') as f:
    json.dump(td, f, indent=2)
" 2>/dev/null || log_warn "Could not strip EFS sections from $task_def_file (python3 not found)"
        fi
        
        log_info "Registering task definition: vieweratlas-$task"
        aws ecs register-task-definition \
            --cli-input-json "file://$temp_file" \
            --region "$AWS_REGION"
        
        rm "$temp_file"
    done
}

# Update ECS services
update_services() {
    local cluster=${ECS_CLUSTER:-vieweratlas-cluster}
    
    log_info "Updating ECS services in cluster: $cluster"
    
    for service in collector analysis vod-collector; do
        if aws ecs describe-services --cluster "$cluster" --services "vieweratlas-$service" --region "$AWS_REGION" | grep -q "vieweratlas-$service"; then
            log_info "Updating service: vieweratlas-$service"
            aws ecs update-service \
                --cluster "$cluster" \
                --service "vieweratlas-$service" \
                --force-new-deployment \
                --region "$AWS_REGION"
        else
            log_warn "Service not found: vieweratlas-$service (skipping update)"
        fi
    done
}

# Main execution
main() {
    log_info "Starting ViewerAtlas deployment to AWS ECS"
    
    check_prerequisites
    
    log_info ""
    create_ecr_repos
    ecr_login
    
    build_and_push "collector" "Dockerfile.collector"
    build_and_push "analysis" "Dockerfile.analysis"
    build_and_push "vod" "Dockerfile.vod"
    
    register_task_definitions
    
    if [ -n "$ECS_CLUSTER" ]; then
        update_services
    else
        log_warn "ECS_CLUSTER not set, skipping service updates"
        log_info "To update services, run: ECS_CLUSTER=your-cluster ./deploy.sh"
    fi
    
    log_info ""
    log_info "Deployment completed successfully!"
    log_info ""
    log_info "Reminder: Twitch credentials must be stored in Secrets Manager:"
    log_info "  aws secretsmanager create-secret --name vieweratlas/twitch/oauth_token \\"
    log_info "      --secret-string 'your-oauth-token' --region $AWS_REGION"
    log_info "  aws secretsmanager create-secret --name vieweratlas/twitch/client_id \\"
    log_info "      --secret-string 'your-client-id' --region $AWS_REGION"
}

main "$@"
