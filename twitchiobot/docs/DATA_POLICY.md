# ViewerAtlas Data Policy (Operational)

This policy describes the EventSub survey release. It is an operational control,
not a substitute for legal or privacy review.

## Private survey data

ViewerAtlas observes only people who actively send a message during a channel's
five-minute sample. It does not collect lurkers or fetch the channel's general
viewer list.

For each channel and survey session, the collector stores one unique entry per
stable Twitch user ID, alongside the user's normalized login at that time. A
person sending many messages still creates only one entry in that channel's
sample. Twitch user ID, not spelling or capitalization of the login, controls
deduplication.

The collector does **not** store:

- message text or fragments;
- per-message timestamps;
- message counts;
- a person's activity in logs; or
- duplicated author entries within the same channel/session.

Survey files live under:

```text
raw/snapshots/v2/date=YYYY-MM-DD/session=<session-id>/
```

Each folder contains an operational manifest and one private Parquet file per
batch. Author IDs and logins are personal data even though ViewerAtlas uses them
as pseudonymous analytical identifiers. The bucket must remain private.

## Public data

The CloudFront website may receive only aggregated channel/community results in
`data/frontend-data.json`. It must never contain Twitch author IDs, logins,
messages, raw author arrays, or the private survey manifest.

The frontend schema stays unchanged in this release. A smoke test enforces this
private/public boundary after deployment.

## Site analytics

The CloudFront distribution writes standard access logs (v2) to
`analytics/cloudfront/` in the private bucket. This is the only analytics the
site has: no analytics JavaScript is served, no cookies are set, no third party
receives anything, and the Content-Security-Policy stays `'self'` throughout.

The delivery selects its fields explicitly and **excludes every viewer
identifier** — no `c-ip`, no `c-ip-version`, no `cs(Cookie)`, no
`x-forwarded-for`. What is written is the date and time, the edge location, the
request method, path and query, the response status and size, the referrer, the
user agent, the cache result and the host header.

The consequence is deliberate: these logs can report how often a page was
requested, and cannot report how many people requested it. Restoring `c-ip` to
obtain unique-visitor counts would put personal data in this prefix and requires
revisiting this policy, shortening the retention below, and treating the prefix
as personal data everywhere it is queried. Reconstructing a viewer identity from
the remaining fields is equally out of bounds.

Access logs expire after **30 days**.

## Retention

- Current versions under `raw/snapshots/v2/` expire after **100 days**.
  The published analysis windows run to 90 days; the extra ten days keep the
  oldest day of that window from expiring while analysis is still reading it.
  This is the maximum retention for survey data and must not be raised to
  create a longer window without revisiting this policy.
- Replaced or deleted versions under that prefix expire after **seven days**.
- Legacy `raw/snapshots/` data retains its former 365-day lifecycle while it is
  migrated or allowed to age out.
- Raw VOD chat remains disabled; its existing 90-day rule is kept only for any
  old objects already present.
- Curated aggregate data moves to Standard-IA after 90 days and does not contain
  raw author arrays.
- Collector and analysis CloudWatch logs expire after seven days.

Bucket versioning must remain enabled or the noncurrent-version rule cannot do
its job. Operators should verify lifecycle rules after every infrastructure
change.

## Credentials and access

Production Twitch credentials are one JSON document in AWS Secrets Manager,
normally `vieweratlas/twitch/credentials`. It contains the Client ID, Client
Secret, bot user ID, access token, and refresh token. The collector reads and
atomically rotates that credential; it must never log the document or individual
secret values.

The collector task role is limited to:

- object read/write access under the private
  `raw/snapshots/v2/` survey prefix, plus the minimum bucket metadata and
  prefix-list access needed to use that location;
- `GetSecretValue` and `PutSecretValue` for that one Twitch secret; and
- the DynamoDB item operations required for the survey overlap-prevention lease.

The public S3/CloudFront path must not grant access to `raw/`.

## Survey status and failures

Manifests may be `running`, `complete`, `complete_with_errors`, or `partial`.
Operational logs contain only session IDs, batch numbers, channel counts, and
failure categories. They intentionally omit viewer identity.

A quiet channel with no active message authors is a completed observation, not
a failure. A partial survey must not be treated as a complete cohort by analysis.

## Deferred collection modes

VOD collection remains disabled in production, and the retired discovery/SQS
worker implementation has been removed. The future
website opt-in feature will require broadcaster authorization, revocation
handling, and a private opt-in registry. It may use the reserved
`selection_source` values `opt_in` and `both`, but must preserve the same public
privacy boundary before activation.

## Operator responsibilities

1. Keep S3, manifests, and raw survey files private.
2. Confirm the 90-day/seven-day lifecycle rules and seven-day log retention.
3. Use the guided authorization helper instead of copying tokens into files.
4. Pause schedules during incidents or untested updates.
5. Investigate `SURVEY_PARTIAL` alarms (a survey that stopped early; routine
   per-channel attrition reports `SURVEY_COMPLETED_WITH_ERRORS` instead) and
   verify recovery with a controlled
   survey, then run `smoke-test.sh` after the following 1:00 AM analysis.
6. Honor applicable deletion requirements and review Twitch terms and privacy
   obligations before expanding collection.
