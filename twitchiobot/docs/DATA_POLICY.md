# ViewerAtlas Data Policy (Operational)

This document defines what ViewerAtlas stores, retention expectations, and operator responsibilities.

## 1. Data Classes Stored

1. Raw live chat presence snapshots
- Location: `raw/snapshots/`
- Contains: channel metadata + chatter usernames observed in snapshot windows

2. Raw VOD chat artifacts
- Location: `raw/vod_chat/`
- Contains: VOD-derived chat data used for downstream presence extraction

3. Curated presence and analysis outputs
- Location: `curated/presence_snapshots/`, `curated/analysis/`
- Contains: normalized/processed presence datasets, overlap graph outputs, partition results

4. Operational logs
- CloudWatch log groups:
  - `/ecs/vieweratlas-collector`
  - `/ecs/vieweratlas-analysis`
  - `/ecs/vieweratlas-vod-collector`

## 2. Retention Windows

Production defaults (enforced via lifecycle/retention scripts):

- `raw/snapshots/`: expire after 30 days
- `raw/vod_chat/`: expire after 7 days
- `curated/`: transition to Glacier Instant Retrieval after 90 days
- CloudWatch logs: retain 7 days

## 3. Deletion Behavior

- S3 lifecycle rules perform automatic expiry/transitions.
- Operators may perform manual deletions for incident/compliance reasons.
- Queue/state files on ephemeral storage are not durable by default unless EFS is configured.

## 4. Access and Secrets Handling

- Twitch credentials are stored in AWS Secrets Manager, not in source control.
- IAM roles are scoped for ECS tasks and schedule execution.
- Secrets must not be logged or echoed in normal operational output.

## 5. Operator Responsibilities

1. Keep lifecycle and retention policies applied and validated.
2. Ensure SNS alarm subscriptions remain confirmed and monitored.
3. Rotate Twitch credentials on schedule or on compromise suspicion.
4. Validate data freshness with smoke tests after deploys/incidents.
5. Respond to deletion requests in accordance with applicable policy/legal requirements.

## 6. Explicit Deferral

This file documents operational policy intent. It is not a substitute for formal legal/compliance review.
