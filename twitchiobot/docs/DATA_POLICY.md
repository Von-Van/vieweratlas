# ViewerAtlas Data Policy (Operational)

This document defines what ViewerAtlas can store, the public/private data boundary,
retention expectations, and operator responsibilities. It is an operational
template, not a claim that a production deployment currently exists.

## 1. Data Classes Stored

1. Raw live chat presence snapshots
- Location: `raw/snapshots/`
- Contains: channel metadata and Twitch usernames observed in snapshot windows
- Classification: private operational data; usernames are pseudonymous identifiers
  and may still be personal data

2. Raw VOD chat artifacts (disabled by default)
- Location: `raw/vod_chat/`
- Contains: downloaded VOD chat JSON, including message content and metadata
- Default: not retained; `vod.persist_raw_chat` must be explicitly enabled
- Classification: private operational data with a shorter recommended retention

3. Curated presence and analysis outputs
- Location: `curated/presence_snapshots/`, `curated/analysis/`
- Contains: normalized/processed presence datasets, overlap graph outputs, partition results

4. Public frontend export
- Location: `data/frontend-data.json`
- Contains: channel-level graph nodes, weighted overlaps, community labels, and
  aggregate statistics
- Excludes: raw chatter usernames and message content

5. Operational logs
- CloudWatch log groups:
  - `/ecs/vieweratlas-collector`
  - `/ecs/vieweratlas-analysis`
  - `/ecs/vieweratlas-vod-collector`

## 2. Retention Windows

Recommended AWS defaults, enforced only after the included infrastructure scripts
are applied and verified:

- `raw/snapshots/`: Standard-IA at 30 days, Glacier IR at 90 days, expire at 365 days
- `raw/vod_chat/`: Standard-IA at 30 days, expire at 90 days
- `curated/`: Standard-IA at 90 days (no expiration)
- CloudWatch logs: retain 7 days

The public frontend export should be regenerated from curated aggregates and must
not be replaced with a raw presence file.

## 3. Deletion Behavior

- S3 lifecycle rules perform automatic expiry/transitions.
- Operators may perform manual deletions for incident/compliance reasons.
- Queue/state files on ephemeral storage are not durable by default unless EFS is configured.

## 4. Access and Secrets Handling

- Twitch credentials are stored in AWS Secrets Manager, not in source control.
- IAM roles are scoped for ECS tasks and schedule execution.
- Secrets must not be logged or echoed in normal operational output.
- Raw presence and optional raw VOD data must remain behind private storage
  access controls. Only `data/frontend-data.json` is intended for public delivery.

## 5. Operator Responsibilities

1. Keep lifecycle and retention policies applied and validated.
2. Ensure SNS alarm subscriptions remain confirmed and monitored.
3. Rotate Twitch credentials on schedule or on compromise suspicion.
4. Validate data freshness with smoke tests after deploys/incidents.
5. Respond to deletion requests in accordance with applicable policy/legal requirements.
6. Review Twitch's terms and applicable privacy law before collecting or
   publishing data.

## 6. Explicit Deferral

This file documents operational policy intent. It is not a substitute for formal legal/compliance review.
