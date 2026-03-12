#!/usr/bin/env bash
#
# cloudfront-setup.sh — One-time setup for S3 + CloudFront hosting of the
# ViewerAtlas frontend SPA.
#
# This script is NOT meant to be run automatically. Review and run manually.
#
# What it creates:
#   1. S3 bucket for static frontend assets
#   2. CloudFront distribution with:
#      - S3 origin for the frontend bucket
#      - Second origin pointing at the data-lake bucket (for /data/* path)
#      - Custom error responses for SPA routing (403/404 → /index.html)
#      - Recommended cache policies
#
# Prerequisites:
#   - AWS CLI v2 configured with appropriate credentials
#   - jq installed
#
set -euo pipefail

FRONTEND_BUCKET="${FRONTEND_BUCKET:-vieweratlas-frontend}"
DATA_BUCKET="${DATA_BUCKET:-vieweratlas-data-lake}"
REGION="${AWS_REGION:-us-east-1}"

echo "=== ViewerAtlas CloudFront Setup ==="
echo "Frontend bucket: ${FRONTEND_BUCKET}"
echo "Data bucket:     ${DATA_BUCKET}"
echo "Region:          ${REGION}"
echo ""

# ------------------------------------------------------------------
# Step 1: Create S3 bucket for frontend static files
# ------------------------------------------------------------------
echo "[1/4] Creating frontend S3 bucket..."
if aws s3api head-bucket --bucket "$FRONTEND_BUCKET" 2>/dev/null; then
  echo "  Bucket ${FRONTEND_BUCKET} already exists, skipping."
else
  aws s3api create-bucket \
    --bucket "$FRONTEND_BUCKET" \
    --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION"
  echo "  Created bucket ${FRONTEND_BUCKET}."
fi

# Block all public access (CloudFront uses OAC)
aws s3api put-public-access-block \
  --bucket "$FRONTEND_BUCKET" \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

echo "  Public access blocked."

# ------------------------------------------------------------------
# Step 2: Create Origin Access Control (OAC)
# ------------------------------------------------------------------
echo ""
echo "[2/4] Creating Origin Access Control..."
OAC_NAME="vieweratlas-oac"

OAC_ID=$(aws cloudfront list-origin-access-controls --query \
  "OriginAccessControlList.Items[?Name=='${OAC_NAME}'].Id | [0]" --output text 2>/dev/null || echo "None")

if [ "$OAC_ID" = "None" ] || [ -z "$OAC_ID" ]; then
  OAC_ID=$(aws cloudfront create-origin-access-control \
    --origin-access-control-config "{
      \"Name\": \"${OAC_NAME}\",
      \"Description\": \"OAC for ViewerAtlas S3 origins\",
      \"SigningProtocol\": \"sigv4\",
      \"SigningBehavior\": \"always\",
      \"OriginAccessControlOriginType\": \"s3\"
    }" --query "OriginAccessControl.Id" --output text)
  echo "  Created OAC: ${OAC_ID}"
else
  echo "  OAC already exists: ${OAC_ID}"
fi

# ------------------------------------------------------------------
# Step 3: Create CloudFront distribution
# ------------------------------------------------------------------
echo ""
echo "[3/4] Creating CloudFront distribution..."

DIST_CONFIG=$(cat <<DISTEOF
{
  "CallerReference": "vieweratlas-$(date +%s)",
  "Comment": "ViewerAtlas SPA + data",
  "Enabled": true,
  "DefaultRootObject": "index.html",
  "Origins": {
    "Quantity": 2,
    "Items": [
      {
        "Id": "frontend-s3",
        "DomainName": "${FRONTEND_BUCKET}.s3.${REGION}.amazonaws.com",
        "OriginAccessControlId": "${OAC_ID}",
        "S3OriginConfig": { "OriginAccessIdentity": "" }
      },
      {
        "Id": "data-s3",
        "DomainName": "${DATA_BUCKET}.s3.${REGION}.amazonaws.com",
        "OriginAccessControlId": "${OAC_ID}",
        "S3OriginConfig": { "OriginAccessIdentity": "" }
      }
    ]
  },
  "DefaultCacheBehavior": {
    "TargetOriginId": "frontend-s3",
    "ViewerProtocolPolicy": "redirect-to-https",
    "AllowedMethods": { "Quantity": 2, "Items": ["GET", "HEAD"] },
    "CachedMethods": { "Quantity": 2, "Items": ["GET", "HEAD"] },
    "Compress": true,
    "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",
    "ForwardedValues": {
      "QueryString": false,
      "Cookies": { "Forward": "none" }
    },
    "MinTTL": 0,
    "DefaultTTL": 86400,
    "MaxTTL": 31536000
  },
  "CacheBehaviors": {
    "Quantity": 1,
    "Items": [
      {
        "PathPattern": "/data/*",
        "TargetOriginId": "data-s3",
        "ViewerProtocolPolicy": "redirect-to-https",
        "AllowedMethods": { "Quantity": 2, "Items": ["GET", "HEAD"] },
        "CachedMethods": { "Quantity": 2, "Items": ["GET", "HEAD"] },
        "Compress": true,
        "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",
        "ForwardedValues": {
          "QueryString": false,
          "Cookies": { "Forward": "none" }
        },
        "MinTTL": 0,
        "DefaultTTL": 300,
        "MaxTTL": 3600
      }
    ]
  },
  "CustomErrorResponses": {
    "Quantity": 2,
    "Items": [
      {
        "ErrorCode": 403,
        "ResponsePagePath": "/index.html",
        "ResponseCode": "200",
        "ErrorCachingMinTTL": 60
      },
      {
        "ErrorCode": 404,
        "ResponsePagePath": "/index.html",
        "ResponseCode": "200",
        "ErrorCachingMinTTL": 60
      }
    ]
  },
  "PriceClass": "PriceClass_100",
  "HttpVersion": "http2and3"
}
DISTEOF
)

echo "$DIST_CONFIG" > /tmp/vieweratlas-cf-config.json
echo "  Distribution config written to /tmp/vieweratlas-cf-config.json"
echo ""
echo "  Review the config, then create with:"
echo "    aws cloudfront create-distribution --distribution-config file:///tmp/vieweratlas-cf-config.json"
echo ""

# ------------------------------------------------------------------
# Step 4: Print S3 bucket policy template
# ------------------------------------------------------------------
echo "[4/4] S3 bucket policy for CloudFront OAC access:"
echo ""
cat <<POLICYEOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowCloudFrontOAC",
      "Effect": "Allow",
      "Principal": { "Service": "cloudfront.amazonaws.com" },
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::${FRONTEND_BUCKET}/*",
      "Condition": {
        "StringEquals": {
          "AWS:SourceArn": "arn:aws:cloudfront::<ACCOUNT_ID>:distribution/<DISTRIBUTION_ID>"
        }
      }
    }
  ]
}
POLICYEOF

echo ""
echo "Replace <ACCOUNT_ID> and <DISTRIBUTION_ID> after creating the distribution,"
echo "then apply with:"
echo "  aws s3api put-bucket-policy --bucket ${FRONTEND_BUCKET} --policy file://policy.json"
echo ""
echo "=== Setup guide complete ==="
