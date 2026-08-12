# Deploying the ViewerAtlas Survey Collector

This guide is written for the person operating ViewerAtlas, not for an AWS
engineer. Follow the sections in order and stop if a command prints `[ERROR]`.

## What this update changes

ViewerAtlas no longer leaves a chat collector running all day. AWS starts one
temporary survey at **6:00 AM, 2:00 PM, and 10:00 PM Eastern time**. Each survey
works through up to 1,200 live channels in groups of 100, listens to each group
for five minutes, saves a private survey record, and exits.

A full survey normally takes about **80 minutes**. Joining Twitch rooms and
retrying a connection may make it a little shorter or longer. A two-hour safety
limit stops an unhealthy run. The 1:00 AM Eastern analysis uses the finished
surveys to refresh the website.

This code-only update does **not** require recreating CloudFront, either S3
bucket, the website, or the VPC. Do not repeat the CloudFront section of the
original setup guide.

## Before starting

1. Start Docker Desktop and wait until it says it is running.
2. Open Terminal and go to the AWS folder:

   ```bash
   cd /Users/jakemauldin/Documents/GitHub/vieweratlas/twitchiobot/infrastructure/aws
   export AWS_PAGER=""
   ```

3. Sign back in to AWS if necessary, then confirm AWS knows who you are:

   ```bash
   aws sts get-caller-identity
   ```

   A block containing your AWS account number means the sign-in worked.

4. Open `.env` in VS Code. Keep the values that already identify your bucket,
   subnets, and security group. Make sure these entries exist:

   ```dotenv
   AWS_REGION=us-east-1
   S3_BUCKET=vieweratlas-data-lake
   S3_PREFIX=vieweratlas/
   ECS_CLUSTER=vieweratlas-cluster
   SUBNET_IDS=subnet-xxxxxxxx,subnet-yyyyyyyy
   SECURITY_GROUP_ID=sg-xxxxxxxx
   ASSIGN_PUBLIC_IP=ENABLED
   TWITCH_CREDENTIALS_SECRET_ID=vieweratlas/twitch/credentials
   ALERT_EMAIL=your-email@example.com
   BUDGET_LIMIT=50
   ```

   Use the two subnets and security group from the same Virginia VPC you used
   previously. Those subnets connect through an Internet Gateway and do not
   have a NAT gateway, so `ASSIGN_PUBLIC_IP` must be `ENABLED`. This provides
   outbound internet access only; the security group does not need an inbound
   rule. Do not put a Twitch token or client secret in `.env`.

## Authorize the ViewerAtlas Twitch account

This is a one-time replacement for the old `.env` Twitch key. It creates one
refreshable JSON credential in AWS Secrets Manager. The Client ID, Client
Secret, bot account ID, access token, and refresh token are written and rotated
together, so operators never have to combine separate secrets by hand.

1. In the [Twitch developer console](https://dev.twitch.tv/console/apps), open
   the ViewerAtlas application and add this exact OAuth Redirect URL:

   ```text
   http://localhost:17653/callback
   ```

2. From the AWS folder, run:

   ```bash
   ./authorize-twitch.sh
   ```

3. Enter the application's Client ID and Client Secret when asked. A Twitch
   page opens. Sign in as the dedicated ViewerAtlas bot account and authorize
   the single `user:read:chat` permission.

4. Return to Terminal and confirm the save when asked. The success message says
   the credential is ready in AWS Secrets Manager. The secret should be named:

   ```text
   vieweratlas/twitch/credentials
   ```

The access and refresh tokens rotate together inside that secret. Never paste
either token into a document, terminal command, GitHub, or `.env`.

## Deploy without starting the automatic schedule

1. Build and deploy the collector and analysis images:

   ```bash
   ./safe-deploy.sh
   ```

   Review the summary and type `y` when it asks whether to continue. The normal
   deployment intentionally leaves every ECS service at a desired count of
   zero. That is correct: EventBridge Scheduler starts temporary tasks later.

2. Create both schedules in a paused state while testing:

   ```bash
   SURVEY_SCHEDULE_STATE=DISABLED \
   ANALYSIS_SCHEDULE_STATE=DISABLED \
   ./create-schedules.sh
   ```

3. Apply the 90-day private-data retention rules and monitoring:

   ```bash
   ./apply-monitoring.sh
   ```

   If the AWS user cannot manage Budgets, the script may warn that the optional
   budget could not be updated; collection and monitoring can still be deployed.
   If `ALERT_EMAIL` is new, AWS sends a confirmation email. Open that message
   and click **Confirm subscription** before relying on alerts. AWS cannot send
   ViewerAtlas alarm email until the subscription is confirmed.

## Test the new collector

Run these three tests in the order shown. Do not move to the next test unless
the current helper ends successfully. If you open a new Terminal window during
this process, repeat the `cd` and `export AWS_PAGER=""` commands from “Before
starting” first.

The first test surveys five channels for one minute. The helper starts the AWS
task, waits for it to finish, and prints the important log milestones:

```bash
./run-survey-test.sh small
```

Success includes `SURVEY_STARTED`, `BATCH_COMPLETED`, and exactly one
`SURVEY_COMPLETED`. The helper rejects `SURVEY_PARTIAL` and a task without the
completion milestone. The logs contain counts and session IDs, not chatter
names or messages.

Next, test one full production-sized group of 100 channels for five minutes:

```bash
./run-survey-test.sh batch
```

The helper deliberately waits in Terminal; no key press is needed. `Ctrl-C`
stops only the local waiting display, not an AWS task that has already started.
If that happens, use the log command printed by the helper or wait before
starting another test.

After either completed test, verify the manifest and private Parquet data:

```bash
aws s3 ls \
  s3://vieweratlas-data-lake/vieweratlas/raw/snapshots/v2/ \
  --recursive | tail -20
```

You should see a `manifest.json` and one or more `batch=NN.parquet` files. An
empty author list for a quiet channel is valid and is not a failed survey.

## Start production collection

Run one complete production-sized survey before enabling automation. This is
the final rollout gate and normally takes roughly 80 minutes:

```bash
./run-survey-test.sh full
```

Enable both schedules only after the five-channel, 100-channel, and complete
1,200-channel tests all pass. Do not enable them after a `partial` or
`complete_with_errors` result:

```bash
SURVEY_SCHEDULE_STATE=ENABLED \
ANALYSIS_SCHEDULE_STATE=ENABLED \
./create-schedules.sh
```

Check what AWS saved:

```bash
aws scheduler get-schedule \
  --name vieweratlas-survey-three-daily \
  --region us-east-1 \
  --query '{state:State,times:ScheduleExpression,timezone:ScheduleExpressionTimezone,flexible:FlexibleTimeWindow.Mode}'

aws scheduler get-schedule \
  --name vieweratlas-analysis-daily \
  --region us-east-1 \
  --query '{state:State,times:ScheduleExpression,timezone:ScheduleExpressionTimezone,flexible:FlexibleTimeWindow.Mode}'
```

The first result should say `ENABLED`, `cron(0 6,14,22 * * ? *)`,
`America/New_York`, and `OFF`. The analysis result should show
`cron(0 1 * * ? *)` with the same timezone. These local times automatically
follow daylight-saving changes.

After the first full scheduled survey finishes, run the normal survey/privacy
check:

```bash
./smoke-test.sh
```

`Survey smoke test passed` confirms the schedules, completed manifest, Parquet
schema, and public-data privacy boundary. It is safe to run after any survey;
the website may still show its last valid dataset until the overnight analysis.

After the **1:00 AM analysis** following the 10:00 PM survey, and before the
next 6:00 AM survey starts, run the strict refresh check:

```bash
./smoke-test.sh analysis
```

`Analysis freshness smoke test passed` additionally proves both analysis
outputs are newer than the latest completed survey.


## Pause, resume, and roll back

To pause future surveys while preparing an update, leave the website and data
alone and use the disable-only helper:

```bash
./pause-schedules.sh survey
```

This helper changes only the existing schedule state. It does not require
working ECS task definitions, IAM setup, or VPC egress, and it does not interrupt
a survey already running; let that survey finish if possible. To resume after
the corrected release has passed all rollout checks:

```bash
SURVEY_SCHEDULE_STATE=ENABLED ./create-schedules.sh
```

For an emergency rollback, run:

```bash
./rollback.sh
```

That disables both new schedules and leaves CloudFront serving the last valid
frontend data. It deliberately does **not** start the retired IRC collector.
After the corrected release has passed its manual tests, enable the schedules
again with the command in “Start production collection.”

## What is intentionally not active

- The old continuously running collector service remains at desired count zero.
- The discovery/SQS worker route is retired because separate workers cannot
  safely coordinate Twitch's 100-room account limit.
- VOD collection remains disabled until its authorization and retention model
  receives a separate update.
- Website channel opt-in is reserved for a later release. No broadcaster needs
  to opt in for this top-stream survey release.
