# ViewerAtlas Developer Guide

This guide describes the current EventSub survey release. The old Twitch IRC
collector, continuously running collector mode, discovery/SQS workers, and VOD
tasks are retained only as inactive source history. Do not deploy or restart
them.

For operator-facing instructions, use [DEPLOYMENT.md](DEPLOYMENT.md) and
[DAILY_OPERATIONS.md](DAILY_OPERATIONS.md). This page focuses on the current
code and its contracts.

## Supported entry points

Run commands from `twitchiobot/`:

```bash
python src/main.py survey config.yaml
python src/main.py analyze config.yaml
```

`survey` is a one-shot process: it acquires the DynamoDB lease, completes one
cohort, writes its terminal manifest, releases the lease, and exits. It is the
only supported production collection entry point. `analyze` reads eligible
private snapshots and produces aggregate public data.

Production VOD, discovery, worker, and long-running collector services must
remain at desired count zero. They are excluded from the normal build and
deployment path.

## Survey flow

The current collection path is split across these modules:

- `update_channels.py` defines `ChannelTargetProvider` and the current
  `TopStreamsProvider`. It freezes up to 1,200 unique broadcaster IDs from
  structured Helix stream records without filling failed targets from below
  the frozen ranking.
- `eventsub_survey.py` owns the TwitchIO 3.2.2 EventSub WebSocket, join limiter,
  common listening windows, retries, teardown, v2 rows, and survey manifest.
- `twitch_credentials.py` loads and atomically rotates the single Twitch
  credential.
- `survey_lease.py` prevents overlapping survey tasks.
- `storage.py` writes private Parquet and manifest objects.

A normal full survey processes twelve strict batches of no more than 100
channels. New joins are limited to 20 per ten seconds. Each batch begins its
shared 300-second listening window only after its successful subscriptions are
ready or setup retries are exhausted. All subscriptions are deleted before the
next batch begins.

The collector retries an individual subscription setup twice. A hard WebSocket
loss clears the in-memory results and restarts the whole batch, up to two
retries, because Twitch does not replay missed events. A full run normally
takes roughly 80 minutes and has a 7,200-second application timeout.

The production schedule uses EventBridge Scheduler with timezone
`America/New_York`, flexible timing disabled, and these local expressions:

- survey: `cron(0 6,14,22 * * ? *)`;
- analysis: `cron(0 1 * * ? *)`.

## Author and privacy rules

`channel.chat.message` events contribute only a stable Twitch author ID and a
normalized lowercase login. Authors are unique within each
`(survey_session_id, channel_id)` pair. The collector excludes its own bot and
Shared Chat messages originating in another broadcaster's room.

The collector does not persist message text, fragments, per-message
timestamps, or message counts. A channel with no active authors during the
window is a successful zero-author observation. Logs contain operational
milestones and counts, never author identities or credential values.

See [DATA_POLICY.md](DATA_POLICY.md) for retention and access requirements.

## Private v2 data contract

Each completed batch is written to:

```text
raw/snapshots/v2/date=YYYY-MM-DD/session=<id>/batch=<nn>.parquet
```

The session folder also contains `manifest.json`. Manifest states are
`running`, `complete`, `complete_with_errors`, and `partial`. Analysis accepts
only terminal complete cohorts, skips failed or partial channel observations,
and keeps legacy snapshot compatibility during migration.

Every channel row records its frozen rank and discovery metadata, exact common
window, collection status, unique-author count, and aligned deterministic JSON
arrays for author IDs and logins. These files are private. Current v2 objects
expire after 90 days and noncurrent versions after seven days.

The public `data/frontend-data.json` schema is unchanged. It contains aggregate
channel and graph results only; it must never receive author IDs, logins, raw
arrays, or survey manifests.

## Twitch authentication

Production uses one Secrets Manager JSON credential containing:

- Client ID;
- Client Secret;
- bot user ID;
- access token; and
- refresh token.

The bot authorization requests only `user:read:chat`. Access and refresh tokens
are persisted together whenever Twitch rotates them. Use
`infrastructure/aws/authorize-twitch.sh`; never assemble or edit the production
secret by hand, and never put its fields in `.env`.

`TWITCH_CREDENTIALS_SECRET_ID` identifies the one secret. The collector role is
limited to that secret, the DynamoDB lease operations, and private v2 survey
storage. Development tests should use injected credential stores rather than
real production tokens.

## Configuration and canaries

The production values live in `config/config.yaml` and the AWS task definition.
The relevant collection defaults are 1,200 targets, batches of 100, a
300-second window, two subscription retries, two full-batch retries, and a
7,200-second timeout. A batch size above 100 is rejected.

The controlled-rollout helper supplies temporary task overrides for the only
supported canary sizes:

```bash
./run-survey-test.sh small
./run-survey-test.sh batch
./run-survey-test.sh full
```

Run them in that order. The first is five channels for one minute, the second
is 100 channels for five minutes, and the third is the complete 1,200-channel
survey. Schedule activation comes only after all three succeed.

## AWS execution and observability

The normal deployment builds only collector and analysis images. The collector
runs as a 0.5-vCPU, 1-GB Fargate task and stays stopped between scheduled
surveys. The application-level DynamoDB lease rejects accidental overlap.

Identity-free milestones are `SURVEY_STARTED`, `BATCH_COMPLETED`,
`SURVEY_COMPLETED`, `SURVEY_COMPLETED_WITH_ERRORS`, `SURVEY_PARTIAL`,
`ANALYSIS_COMPLETED`, and
`ANALYSIS_FAILED`. Monitoring uses these milestones instead of a long-running
service heartbeat. The strict smoke test additionally requires both analysis
objects to be newer than the latest completed survey manifest.

Pause with `pause-schedules.sh`; it preserves schedule targets and does not
depend on ECS, IAM, or VPC validation. CloudFront continues serving the last
valid public dataset. Rollback must never reactivate the retired IRC collector.

## Verification

Install development dependencies and run the tests from `twitchiobot/`:

```bash
python -m pip install -r requirements-dev.txt
pytest -q
```

Infrastructure scripts can be checked without changing AWS:

```bash
bash -n infrastructure/aws/*.sh
python -m json.tool infrastructure/aws/ecs-task-collector.json >/dev/null
python -m json.tool infrastructure/aws/ecs-task-analysis.json >/dev/null
```

For a real rollout, follow [DEPLOYMENT.md](DEPLOYMENT.md). It deploys in a
paused state and enforces the `small` → `batch` → `full` order before schedule
activation.

## Deferred work

- Graph mathematics and the 30/60/90-day presentation controls are a separate
  follow-up that will consume the stable IDs already captured.
- Website channel opt-in is reserved by `selection_source` values `opt_in` and
  `both`, but is not implemented in this release.
- The cost estimate is calculated after the first complete production survey
  from measured Fargate runtime, public IPv4 time, Parquet size, storage,
  requests, logs, and Secrets Manager usage.
