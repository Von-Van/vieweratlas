# AWS SNS Setup for ViewerAtlas Alerts

## Quick Setup

### 1. Create SNS Topic
```bash
aws sns create-topic \
  --name vieweratlas-alerts \
  --region $AWS_REGION
```

### 2. Subscribe Email
```bash
# Replace with your email
aws sns subscribe \
  --topic-arn arn:aws:sns:$AWS_REGION:$AWS_ACCOUNT_ID:vieweratlas-alerts \
  --protocol email \
  --notification-endpoint your-email@example.com \
  --region $AWS_REGION
```

**Important**: Check your email and confirm the subscription!

### 3. Set Environment Variable
```bash
export ALERT_EMAIL="your-email@example.com"
```

### 4. Deploy Alarms
The monitoring dashboard already includes SNS integration. Alarms will now notify:
- **VODQueueBacklogHigh**: Queue exceeds 1000 pending VODs
- **VODFailureRateHigh**: Failure rate > 20%
- **VODProcessingStalled**: No VODs processed in 6 hours
- **ECSTaskFailures**: 3+ task failures in 15 minutes

## Optional: Add SMS Alerts

```bash
aws sns subscribe \
  --topic-arn arn:aws:sns:$AWS_REGION:$AWS_ACCOUNT_ID:vieweratlas-alerts \
  --protocol sms \
  --notification-endpoint "+1234567890" \
  --region $AWS_REGION
```

## Testing

```bash
# Send test notification
aws sns publish \
  --topic-arn arn:aws:sns:$AWS_REGION:$AWS_ACCOUNT_ID:vieweratlas-alerts \
  --message "ViewerAtlas alert system test" \
  --subject "Test Alert" \
  --region $AWS_REGION
```

## Integration with CloudWatch

The monitoring dashboard YAML already includes alarm actions. When deploying:

```bash
cd infrastructure/aws
# Alarms will automatically use the SNS topic ARN
```

All critical failures will now trigger notifications!
