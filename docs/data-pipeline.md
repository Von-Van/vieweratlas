# Data Pipeline

ViewerAtlas turns Twitch chat activity into a channel-overlap map in five
stages. The stages share no process or database. They are joined only by
files in storage: S3 in production, a local directory in development.

```text
Twitch Helix + EventSub
        │   collection       twitchiobot/src/update_channels.py, eventsub_survey.py
        ▼
raw/snapshots/v2/…parquet    private, one file per survey batch + manifest
        │   processing       twitchiobot/src/data_aggregator.py, chatter_filter.py
        ▼
channel → chatter sets       in memory, per analysis window
        │   analysis         graph_builder.py, community_detector.py, cluster_tagger.py
        ▼
graph + communities          processed/analysis_results.json (private)
        │   publication      frontend_exporter.py
        ▼
data/frontend-data*.json     public, channel-level aggregates only
        │   visualization    frontend/src/app/
        ▼
React community map
```

Throughout this page, values are tagged by where they come from:

- **Observed**: recorded directly from Twitch.
- **Derived**: calculated by the pipeline from observed values.
- **Operational**: metadata about the collection run itself.

[metrics.md](metrics.md) defines every published field.
[methodology.md](methodology.md) explains why each step is done the way it is.

## 1. Collection

| | |
| --- | --- |
| Entry point | `python src/main.py survey <config>` → `mode_survey()` in [`main.py`](../twitchiobot/src/main.py) |
| Code | [`update_channels.py`](../twitchiobot/src/update_channels.py), [`eventsub_survey.py`](../twitchiobot/src/eventsub_survey.py), [`survey_lease.py`](../twitchiobot/src/survey_lease.py), [`twitch_credentials.py`](../twitchiobot/src/twitch_credentials.py) |
| Sources | Twitch Helix `GET /streams` (channel discovery) and the EventSub WebSocket `channel.chat.message` subscription (chat presence) |
| Frequency | Three surveys a day at 06:00, 14:00, and 22:00 `America/New_York` ([`create-schedules.sh`](../twitchiobot/infrastructure/aws/create-schedules.sh)). Each takes about 80 minutes, with a hard stop at 7,200 s. |
| Credentials | One bot account token with only the `user:read:chat` scope. The survey refuses a token with any other scope. |

Each survey runs these steps:

1. **Take the lease.** A DynamoDB lease makes a second scheduled task exit
   instead of running at the same time.
2. **Freeze the cohort.** The survey pages Helix `/streams`, which Twitch
   orders by current viewers, until it has 1,200 unique broadcaster IDs. It
   keeps each stream's rank, viewer count, game, language, title, and start
   time. Pages are not a transactional snapshot, so this is an *approximate*
   top 1,200. Duplicates are dropped without using up a rank. A channel that
   later fails is never replaced by one further down.
3. **Survey in batches.** The cohort is split into batches of at most 100
   channels. New subscriptions are rate-limited to 20 per 10 seconds. A
   batch's 300-second listening window opens only after all of its
   subscriptions are ready, so **every channel in a batch is observed over the
   same five minutes**. Messages that arrive outside the window are ignored.
4. **Keep only presence.** For each message in the window,
   `extract_active_author()` keeps `(broadcaster ID, chatter ID, lowercase
   login)`. It drops the bot's own messages, and Shared Chat messages that
   came from another broadcaster's room. Chatters are unique per channel per
   survey, keyed by user ID. Message text, per-message timestamps, and message
   counts are never read into survey state.
5. **Restart on connection loss.** EventSub does not replay missed events. If
   the WebSocket drops during a batch, that attempt's data is thrown away and
   the batch restarts, up to 2 times. Every retained channel therefore has an
   uninterrupted, common window.
6. **Persist.** After each batch the survey writes one Parquet file and
   updates the manifest. The manifest ends as `complete`,
   `complete_with_errors` (the survey finished but some channels failed), or
   `partial` (timeout, shutdown, or unexpected error).

### Raw record: one row per planned channel per survey

Written by `EventSubSurveyRunner._row_for_target()` to
`raw/snapshots/v2/date=YYYY-MM-DD/session=<id>/batch=NN.parquet`, schema
version 2. Batches are numbered from 01. The types below are the Parquet types
of the production files.

| Field | Type | Kind | Meaning |
| --- | --- | --- | --- |
| `chatters_json`, `chatter_ids_json` | string (JSON array) | Observed | Aligned arrays of lowercase logins and Twitch user IDs, sorted by ID, for every account that sent at least one message in the window. Never message text. |
| `unique_author_count` | int64 | Derived | Length of those arrays |
| `viewer_count` | int64 | Observed | Helix concurrent viewers **when the cohort was frozen**, not during the window |
| `game_id`, `game_name`, `language`, `title` | string | Observed | Helix stream metadata at discovery |
| `started_at` | string (ISO 8601) | Observed | When the stream went live |
| `rank` | int64 | Observed | Position in the frozen Helix ranking |
| `channel_id` | string | Observed | Broadcaster user ID |
| `channel`, `channel_login` | string | Observed | Broadcaster login, lowercase (both columns hold it) |
| `sample_started_at`, `sample_ended_at`, `timestamp` | string (ISO 8601) | Operational | The shared listening window. `timestamp` repeats the start time. |
| `sample_duration_seconds` | int64 | Operational | 300 for completed rows, 0 for failed ones |
| `survey_session_id` | string | Operational | Survey ID; by default its UTC start time |
| `batch`, `schema_version` | int64 | Operational | Batch number, and `2` |
| `discovered_at` | string (ISO 8601) | Operational | When the cohort was frozen |
| `selection_source`, `_source` | string | Operational | Always `top_ranked` and `live` today. `opt_in` and `both` are reserved. |
| `collection_status`, `failure_reason` | string | Operational | `completed`, `subscription_failed`, or `websocket_failed`, plus a fixed failure category |

A `completed` row with an empty author list is a real observation: nobody
spoke during the window. Failed rows are kept as operational evidence but never
reach the graph.

### Survey manifest

Each session folder also holds `manifest.json`, also schema version 2. Its
`status` is `running` while the survey works, then `complete`,
`complete_with_errors`, or `partial`. The manifest also records:

- `started_at` and `completed_at`;
- the configured limits: `target_limit`, `batch_size`, `window_seconds`,
  `timeout_seconds`;
- survey-wide counts: `planned`, `attempted`, `completed`, `failed`,
  `zero_authors`, `batches_planned`, `batches_completed`;
- a `batches` array with each batch's counts and the `object_key` of its file.

A `partial` survey also carries `failure_reason`, and an `error` category when
an exception caused it.

## 2. Storage

[`storage.py`](../twitchiobot/src/storage.py) gives two interchangeable
backends: `S3Storage` in production (bucket plus `S3_PREFIX`) and `FileStorage`
locally (root directory `LOGS_DIR`, default `logs`). Keys are the same in both.

| Key | Contents | Written by | Visibility | Lifetime |
| --- | --- | --- | --- | --- |
| `raw/snapshots/v2/date=…/session=…/batch=NN.parquet` | [Raw record](#raw-record-one-row-per-planned-channel-per-survey), one row per planned channel | survey | private | 100 days; noncurrent versions 7 days |
| `raw/snapshots/v2/date=…/session=…/manifest.json` | [Survey manifest](#survey-manifest) | survey | private | same |
| `processed/analysis_results.json` | Partition, labels, run statistics, configuration ([fields](metrics.md#private-run-record-processedanalysis_resultsjson)) | analysis, canonical window | private | overwritten daily |
| `curated/analysis/YYYY-MM-DD/graph_nodes.csv` | `id, viewers, viewer_count, game, title` | analysis, canonical window | private | moved to Standard-IA after 90 days |
| `curated/analysis/YYYY-MM-DD/graph_edges.csv` | `source, target, weight` | analysis, canonical window | private | same |
| `data/frontend-data.json`, `data/frontend-data-<N>d.json` | [Public payload](#public-payload-schema) | analysis | **public** via CloudFront | overwritten daily |

The lifecycle rules live in
[`safe-deploy.sh`](../twitchiobot/infrastructure/aws/safe-deploy.sh), and
[DATA_POLICY.md](../twitchiobot/docs/DATA_POLICY.md) states them as policy.
Retention is ten days longer than the widest analysis window (90 days), so that
window never loses its oldest day while analysis is reading it.

**The manifest is the commit record.** Batch files land before the manifest
reaches a terminal state. Analysis only reads batches whose manifest says
`complete` or `complete_with_errors`, so a survey that was interrupted
mid-run can never leak into the graph.

## 3. Processing

| | |
| --- | --- |
| Entry point | `python src/main.py analyze <preset or yaml>` → `PipelineRunner.run_analysis_pipeline()` |
| Code | [`data_aggregator.py`](../twitchiobot/src/data_aggregator.py), [`chatter_filter.py`](../twitchiobot/src/chatter_filter.py) |
| Frequency | Daily at 01:00 `America/New_York`, after the 22:00 survey |

1. **Plan the windows.** `survey_date_span()` finds the first and last days
   that have a completed survey. A configured window (14, 30, 90 days) is
   analysed only if the data covers it. Uncovered windows are published as
   `PENDING`. A 90-day window over 14 days of data would just be the 14-day
   graph under the wrong label. If no window is covered yet, a schema-valid
   pending payload is published instead.
2. **Select files.** For each window, Parquet keys are kept when their `date=`
   partition falls in `[newest survey day − N + 1, newest survey day]`. The
   window is anchored to the data, not to the clock, so re-running old data
   reproduces the old result. The window's `collectionPeriod` reports the days
   actually covered.
3. **Gate.** Sessions without a completed manifest are skipped. Within the
   remaining sessions, only rows with `collection_status == "completed"` are
   used.
4. **Decode.** `chatters_json` becomes a list of lowercase logins. The IDs stay
   in the files for a planned move to ID-based identity, but analysis keys
   chatters by login today.
5. **Aggregate** each channel across the window *(derived)*:
   - the chatter set: the union of authors from every observation;
   - the observation count: how many survey samples included the channel;
   - a per-day mean of its Helix viewer counts, using live samples only, and
     the mean of those daily means;
   - its most frequent game (ties go to the alphabetically first name);
   - its last-seen language and title.
6. **Remove automated accounts.** The filter drops accounts on a curated list
   of chat-bot services, plus any account active in more than 3 channels in
   the same `(session, batch)` window. It runs before any statistic is
   computed, and it reports counts only (`AUTOMATED_CHATTERS_EXCLUDED`).
   [methodology.md](methodology.md#automated-accounts) gives the measurement
   behind both rules.

Older input formats (flat JSON/CSV logs and VOD presence snapshots) can still
be loaded for local development. They are **not** windowed. See
[limitations](methodology.md#limitations-and-known-biases).

## 4. Analysis

Code: [`main.py`](../twitchiobot/src/main.py) (`_step_*`),
[`graph_builder.py`](../twitchiobot/src/graph_builder.py),
[`community_detector.py`](../twitchiobot/src/community_detector.py),
[`cluster_tagger.py`](../twitchiobot/src/cluster_tagger.py). Each window goes
through the same steps. Values are the production `rigorous` preset in
[`config.py`](../twitchiobot/src/config.py).

1. **Channel filters.** Keep channels observed at least 3 times with at least
   10 distinct chatters.
2. **Overlap graph.** For every channel pair, the weight is the number of
   shared chatters `|A ∩ B|`. An account seen in more than 200 channels is
   skipped entirely. A pair becomes an edge only at or above the window's
   overlap threshold: 3 for 14 days, 4 for 30 days, and a fallback of 2 for
   windows not yet measured, which logs `UNCALIBRATED_WINDOW`. Channels with no
   edge are dropped.
3. **Communities.** Louvain (`python-louvain`, resolution 1.0,
   `random_state=42`). Communities under 10 channels are discarded, and
   modularity is recomputed on what remains.
4. **Labels.** Each community is named from its members' metadata (dominant
   game, dominant language, or a mix).
5. **Private record.** The canonical window writes
   `processed/analysis_results.json`: the full partition, the labels, graph,
   detection, tagging, and aggregation statistics (including the bot-filter
   counts), and the configuration that produced them.

The output depends only on the input and the configuration. Edge insertion,
subgraph node order, and label tie-breaks are all independent of Python's
per-process hash seed. Two runs over the same snapshots produce identical
payloads apart from their timestamps.

## 5. Publication and visualization

[`frontend_exporter.py`](../twitchiobot/src/frontend_exporter.py) projects
each window's graph into a browser-sized public payload:

1. Keep the 1,000 channels with the highest mean viewer count.
2. Add edges strongest-first, at most 25 per channel and 25,000 in total. If
   the total cap is what stops the loop, `EDGE_CAP_BOUND` is logged.
3. Keep the largest connected component.
4. Drop communities that end up with fewer than 4 rendered channels.
5. Give each community a slug and colour. Other windows reuse them by matching
   members to the canonical window (member Jaccard above 0.2), so the map
   doesn't repaint when the time filter changes.
6. Where two communities share a label, append their two largest channels.
7. Compute the layout on the server: communities are packed as discs, and
   channels are placed inside their disc with a seeded spring layout.

Every published edge weight is the **measured integer count** of shared
chatters, whatever `weighting_mode` drove the analysis.

Payloads are written to `data/frontend-data-<N>d.json` for each available
window. The canonical window also writes `data/frontend-data.json`, which the
site loads first. The payload declares `availableWindows`, `pendingWindows`,
and `defaultWindow`, so the browser knows which time-filter buttons have data.

### Public payload schema

This is the only data the website receives. The frontend validates it against
this shape before rendering anything. [metrics.md](metrics.md) defines what each
field measures.

```ts
type FrontendData = {
  generatedAt: string;                 // ISO time of the export
  availableWindows?: number[];         // e.g. [14, 30]; window keys are omitted in single-window runs
  pendingWindows?: number[];           // e.g. [90]: configured, but not enough history yet
  defaultWindow?: number;              // the window the site opens on
  overallStats: {
    totalChannels: number;             // channels in the analysed graph
    totalViewers: number;              // distinct chatters (not viewers) in the window
    communitiesDetected: number;
    modularityScore: number;
    collectionPeriod: string;          // e.g. "Aug 13 – Aug 26, 2026"
    dataPoints: number;                // channel samples loaded
    edgesTotal: number;
    avgOverlapWeight: number;          // mean shared chatters per rendered edge
    renderedChannels: number;
    renderedEdges: number;
  };
  communities: { id: string; label: string; color: string; nodeCount: number; description: string }[];
  channels: {
    id: string;                        // lowercase login (also `name`)
    name: string;
    displayName: string;
    game: string;                      // most frequent category in the window
    viewers: number;                   // mean concurrent viewers while live (Helix)
    communityId: string;
    description: string;
    language: string;                  // broadcaster language, e.g. "En"
    topOverlaps: { channelId: string; channelName: string; shared: number }[];  // up to 5
    viewerHistory: { date: string; viewers: number }[];                         // one point per day
    edgeCount: number;                 // links on the rendered map (at most 25)
    modularityScore: number;           // share of those links inside the channel's community
    layout?: { x: number; y: number }; // position precomputed by the pipeline
  }[];
  edges: { source: string; target: string; weight: number }[];  // weight = shared chatters (integer)
  topCommunitiesBySize: { community: string; channels: number; viewers: number }[];      // top 8
  mostConnectedChannels: { name: string; edges: number; community: string; color: string }[];  // top 10
};
```

The frontend ([`AtlasDataProvider.tsx`](../frontend/src/app/data/AtlasDataProvider.tsx))
fetches `VITE_DATA_URL` from the same origin. It enforces a 10-second timeout
and a size limit, and validates the schema in
[`validateAtlasData.ts`](../frontend/src/app/data/validateAtlasData.ts). Other
windows' files load only when selected. If no URL is configured, or loading or
validation fails, the site falls back to a bundled demonstration dataset and
shows a banner saying so.

The Python `Visualizer` can also render a PNG and a PyVis HTML file. Both are
off in production, where they cost memory and are never uploaded.

## What crosses the public boundary

| Private (never published) | Public |
| --- | --- |
| Chatter IDs and logins, raw Parquet, manifests, the partition of the full graph, the bot-filter classification | Channel logins and display names, per-channel mean viewer counts and daily history, modal game, language, community membership, shared-chatter counts between rendered channels, run-level totals |

[`smoke-test.sh`](../twitchiobot/infrastructure/aws/smoke-test.sh) checks after
every deployment that no public payload carries author identities.
