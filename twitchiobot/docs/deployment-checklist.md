# ViewerAtlas Critical Collector Deployment Checklist

Use this beside [DEPLOYMENT.md](DEPLOYMENT.md). Do not recreate CloudFront, S3,
or the frontend for this update.

## Before deployment

- [ ] Docker Desktop is running.
- [ ] `aws sts get-caller-identity` shows the intended account.
- [ ] `.env` still contains the Virginia bucket, two same-VPC subnets, security
      group, and correct `ASSIGN_PUBLIC_IP` setting.
- [ ] `ASSIGN_PUBLIC_IP=ENABLED` because these public subnets use an Internet
      Gateway and have no NAT gateway; no inbound security-group rule was added.
- [ ] `.env` contains
      `TWITCH_CREDENTIALS_SECRET_ID=vieweratlas/twitch/credentials`.
- [ ] Twitch app redirect URL is exactly
      `http://localhost:17653/callback`.
- [ ] `./authorize-twitch.sh` completed using the dedicated bot account.
- [ ] The requested token scope is only `user:read:chat`.
- [ ] No Twitch token or client secret was added to `.env` or Git.

## Deploy in a paused state

- [ ] `./safe-deploy.sh` completed.
- [ ] Collector task definition uses 512 CPU units and 1,024 MiB memory.
- [ ] Collector command is `python -u main.py survey config.yaml`.
- [ ] Existing collector ECS service desired count is zero.
- [ ] Existing discovery, worker, and VOD services are each zero if present.
- [ ] No discovery, worker, or VOD image/task was built or registered by the
      normal deploy.
- [ ] DynamoDB collection-state table exists with TTL enabled.
- [ ] Collector role can use the survey lease and only the configured Twitch
      credential secret.
- [ ] Schedules were first created disabled:

  ```bash
  SURVEY_SCHEDULE_STATE=DISABLED \
  ANALYSIS_SCHEDULE_STATE=DISABLED \
  ./create-schedules.sh
  ```

- [ ] The old 3:00 UTC EventBridge Rule is disabled.
- [ ] `./apply-monitoring.sh` completed.
- [ ] The AWS notification email was opened and **Confirm subscription** was
      clicked for the address in `ALERT_EMAIL`.
- [ ] `raw/snapshots/v2/` current objects expire after 90 days.
- [ ] Noncurrent versions under that prefix expire after seven days.

## Controlled rollout

- [ ] `./run-survey-test.sh small` completed its five-channel, one-minute
      survey.
- [ ] `./run-survey-test.sh batch` completed its 100-channel, five-minute
      batch.
- [ ] Logs show `SURVEY_STARTED`, `BATCH_COMPLETED`, and `SURVEY_COMPLETED`.
- [ ] Logs do not expose tokens, chatter names, IDs, or message content.
- [ ] S3 contains a terminal `manifest.json` and matching batch Parquet object.
- [ ] Quiet channels are stored as successful zero-author observations.
- [ ] `./run-survey-test.sh full` completed its full 1,200-channel survey
      within the two-hour limit.
- [ ] None of the three tests ended `partial` or `complete_with_errors`.

## Production activation

- [ ] Both schedules were enabled:

  ```bash
  SURVEY_SCHEDULE_STATE=ENABLED \
  ANALYSIS_SCHEDULE_STATE=ENABLED \
  ./create-schedules.sh
  ```

- [ ] Survey schedule is `cron(0 6,14,22 * * ? *)`.
- [ ] Analysis schedule is `cron(0 1 * * ? *)`.
- [ ] Both use `America/New_York` and flexible time window `OFF`.
- [ ] CloudWatch dashboard is `ViewerAtlas-Surveys`.
- [ ] Survey-completion, partial-survey, collector-error, analysis-failure, and
      missing-analysis-completion alarms exist.
- [ ] SNS email subscription is still confirmed, not `PendingConfirmation`.
- [ ] Frontend data still contains aggregates only, with no author identities.
- [ ] After the first scheduled survey, `./smoke-test.sh` prints
      `Survey smoke test passed`.
- [ ] After the 1:00 AM analysis following the evening survey,
      `./smoke-test.sh analysis` proves both analysis outputs are newer than the
      latest completed survey and prints `Analysis freshness smoke test passed`.

## Handoff

- [ ] Operator knows a survey normally takes about 80 minutes.
- [ ] Operator knows how to pause safely with `pause-schedules.sh` and resume a
      validated release with `create-schedules.sh`.
- [ ] Operator knows `./rollback.sh` disables schedules and does not restart IRC.
- [ ] Operator knows the website keeps serving its last valid dataset while
      collection is paused.
- [ ] Cost estimate is deferred until the first complete production survey has
      provided actual Fargate runtime and Parquet sizes.
