#!/usr/bin/env bash
#
# deploy.sh — Build the ViewerAtlas frontend and sync to S3.
#
# Prerequisites:
#   - AWS CLI configured with appropriate credentials
#   - Node.js / npm available in PATH
#   - S3 bucket already created (see cloudfront-setup.sh)
#
# Usage:
#   ./deploy.sh                          # uses default bucket
#   S3_BUCKET=my-bucket ./deploy.sh      # override bucket name
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
S3_BUCKET="${S3_BUCKET:-vieweratlas-frontend}"
DISTRIBUTION_ID="${DISTRIBUTION_ID:-}"

echo "==> Building frontend..."
cd "$SCRIPT_DIR"
npm ci
npm run build

echo "==> Syncing dist/ to s3://${S3_BUCKET}/"
aws s3 sync dist/ "s3://${S3_BUCKET}/" \
  --delete \
  --cache-control "public, max-age=31536000, immutable" \
  --exclude "index.html" \
  --exclude "data/*"

# index.html should not be cached aggressively
aws s3 cp dist/index.html "s3://${S3_BUCKET}/index.html" \
  --cache-control "public, max-age=60, s-maxage=300"

echo "==> Upload complete."

# Optionally invalidate CloudFront cache
if [ -n "$DISTRIBUTION_ID" ]; then
  echo "==> Invalidating CloudFront distribution ${DISTRIBUTION_ID}..."
  aws cloudfront create-invalidation \
    --distribution-id "$DISTRIBUTION_ID" \
    --paths "/index.html" "/assets/*"
  echo "==> Invalidation requested."
fi

echo "==> Done."
