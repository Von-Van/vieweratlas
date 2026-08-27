# ViewerAtlas Daily Operations

ViewerAtlas is healthy when three temporary survey tasks finish each day and
the 1:00 AM Eastern analysis leaves a valid public dataset. A collector that is
not running between surveys is normal.

## Normal timetable (Eastern time)

| Time | Expected activity |
| --- | --- |
| 6:00 AM | Survey starts; usually finishes around 7:20 AM |
| 2:00 PM | Survey starts; usually finishes around 3:20 PM |
| 10:00 PM | Survey starts; usually finishes around 11:20 PM |
| 1:00 AM | Daily analysis refreshes the public dataset |

Each survey may take up to two hours. The schedule uses
`America/New_York`, so the local timetable remains the same through daylight
saving changes.

## Quick health check

Open Terminal, enter the AWS folder, and disable the AWS pager. Repeat these
two setup commands whenever you open a new Terminal window:

```bash
cd /Users/jakemauldin/Documents/GitHub/vieweratlas/twitchiobot/infrastructure/aws
export AWS_PAGER=""
```

Then run this after a survey should have finished:

```bash
./smoke-test.sh
```

Success ends with `Survey smoke test passed`. It verifies both schedules, the
latest completed survey manifest, one batch file, and the public-data privacy
boundary. It does not require the overnight analysis to have run yet.

After the 1:00 AM analysis and before the next 6:00 AM survey, verify that the
analysis used the newest completed survey:

```bash
./smoke-test.sh analysis
```

Success ends with `Analysis freshness smoke test passed`.

To read recent milestones without opening a pager:

```bash
aws logs tail /ecs/vieweratlas-collector \
  --region us-east-1 \
  --since 12h
```

Healthy logs contain `SURVEY_STARTED`, twelve `BATCH_COMPLETED` lines for a full
cohort, and `SURVEY_COMPLETED` (or `SURVEY_COMPLETED_WITH_ERRORS` when some
channels dropped out; that survey is still analysed). `SURVEY_PARTIAL`,
authentication errors, or a
survey running longer than two hours require investigation.

## Check the private survey files

```bash
aws s3 ls \
  s3://vieweratlas-data-lake/vieweratlas/raw/snapshots/v2/ \
  --recursive | tail -30
```

Each session folder should contain `manifest.json` plus `batch=01.parquet`
through its last planned batch. Empty chatter arrays are valid for channels
where nobody spoke during the five-minute sample.

## Pause or resume collection

Pause future surveys while updating or investigating:

```bash
./pause-schedules.sh survey
```

The current task, if any, is allowed to finish. The website continues showing
the last successful public dataset. Resume after testing:

```bash
SURVEY_SCHEDULE_STATE=ENABLED ./create-schedules.sh
```

To pause analysis as well, use the emergency-safe all-schedules option:

```bash
./pause-schedules.sh all
```

## If a survey fails

1. Pause the survey schedule.
2. Read the last two hours of collector logs:

   ```bash
   aws logs tail /ecs/vieweratlas-collector \
     --region us-east-1 \
     --since 2h
   ```

3. Check whether the latest manifest says `partial` or
   `complete_with_errors`.
4. If authentication failed, rerun `./authorize-twitch.sh`; do not manually
   paste tokens into `.env`.
5. Run the five-channel test from [DEPLOYMENT.md](DEPLOYMENT.md).
6. Resume only after the test ends with `SURVEY_COMPLETED`.

For a release-level failure, run `./rollback.sh`. It disables collection and
analysis without reactivating the retired IRC collector.

## Weekly checks

- Review the `ViewerAtlas-Surveys` CloudWatch dashboard and its survey and daily
  analysis alarms.
- Confirm the SNS email subscription remains confirmed rather than
  `PendingConfirmation`. If AWS sends a new confirmation email, open it and
  click **Confirm subscription**.
- Confirm S3 lifecycle still shows 100-day current-version and seven-day
  noncurrent-version expiry for `raw/snapshots/v2/`. The extra ten days over
  the widest published analysis window (90) keep that window's oldest day
  from expiring while analysis is reading it.
- Confirm `analytics/cloudfront/` still expires after 30 days.
- Review the AWS bill. After the first full survey, estimate recurring cost from
  its measured task duration and Parquet size, using roughly 90 surveys per
  30-day month.
- Never expect discovery, worker, VOD, or the old continuous collector to run.
