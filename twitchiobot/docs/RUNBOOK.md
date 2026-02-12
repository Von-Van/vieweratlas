# ViewerAtlas Production Runbook

This runbook covers standard production operations for ViewerAtlas on AWS.

## 1. Service Inventory

- ECS service (always on): `vieweratlas-collector`
- ECS scheduled tasks: `vieweratlas-analysis`, `vieweratlas-vod-collector`
- Schedules managed by: `twitchiobot/infrastructure/aws/create-schedules.sh`
- Monitoring managed by: `twitchiobot/infrastructure/aws/apply-monitoring.sh`

## 2. Incident Triage

1. Confirm blast radius.
   - Is impact limited to collector, analysis, VOD, or all?
2. Check collector service health.
   - `aws ecs describe-services --cluster <cluster> --services vieweratlas-collector --region <region>`
3. Check recent logs.
   - `aws logs tail /ecs/vieweratlas-collector --region <region> --since 30m`
   - `aws logs tail /ecs/vieweratlas-analysis --region <region> --since 30m`
   - `aws logs tail /ecs/vieweratlas-vod-collector --region <region> --since 30m`
4. Check data freshness.
   - `bash twitchiobot/infrastructure/aws/smoke-test.sh`
5. If deploy-related, identify current task definition revisions and image tags.

## 3. Rollback Procedure

1. Select last known-good immutable image tag.
2. Redeploy with explicit tag.
   - `IMAGE_TAG=<known-good-tag> PUSH_LATEST=false bash twitchiobot/infrastructure/aws/deploy.sh`
3. Verify service reaches stable state.
4. Run smoke test.
5. Validate S3 snapshot freshness and analysis outputs.

## 4. Disable/Enable Schedules

Disable:

```bash
aws events disable-rule --name vieweratlas-analysis-daily --region <region>
aws events disable-rule --name vieweratlas-vod-6h --region <region>
```

Enable:

```bash
aws events enable-rule --name vieweratlas-analysis-daily --region <region>
aws events enable-rule --name vieweratlas-vod-6h --region <region>
```

## 5. Secret Rotation

Secrets:
- `vieweratlas/twitch/oauth_token`
- `vieweratlas/twitch/client_id`

Rotate value:

```bash
aws secretsmanager update-secret \
  --secret-id vieweratlas/twitch/oauth_token \
  --secret-string '<new-value>' \
  --region <region>
```

Then force collector refresh:

```bash
aws ecs update-service \
  --cluster <cluster> \
  --service vieweratlas-collector \
  --force-new-deployment \
  --region <region>
```

## 6. Known Failure Playbooks

### A. No New Snapshots in S3

- Validate collector service stable and running count > 0
- Check collector logs for auth/API failures
- Confirm Twitch secrets are valid and not expired
- Confirm `S3_BUCKET`/`S3_PREFIX` in task definition env
- Run smoke test after corrective action

### B. Channel Discovery Fails Repeatedly

- Symptoms: collector exits during channel refresh, channel update errors in logs
- Validate both env vars present: `TWITCH_OAUTH_TOKEN`, `TWITCH_CLIENT_ID`
- Validate Twitch API token scope and expiration
- Verify outbound network path from ECS tasks

### C. Analysis Task Fails

- Check `/ecs/vieweratlas-analysis` logs
- Confirm S3 input prefixes contain recent snapshots
- Trigger one manual analysis run for verification

### D. VOD Backlog or Failures

- Check `/ecs/vieweratlas-vod-collector` logs and queue status
- Confirm VOD schedule/rule target config and network settings
- If using EFS queue persistence, verify mount and EFS health

### E. Alarm/Notification Gaps

- Re-run `apply-monitoring.sh`
- Confirm SNS topic exists and subscriptions are confirmed
- Publish SNS test message

## 7. Post-Incident Checklist

1. Confirm smoke test passes.
2. Confirm one full analysis run and one VOD scheduled run complete.
3. Confirm fresh objects under expected S3 prefixes.
4. Capture incident summary and remediation notes.
