# Metrics Reference

This page defines every field in the public payload
(`data/frontend-data*.json`) and the key private statistics. For each field it
says what the value measures, how it is computed, and how to read it.

Every public field is **derived**: computed by
[`frontend_exporter.py`](../twitchiobot/src/frontend_exporter.py) from the
analysed graph. The observed inputs behind them are described in
[data-pipeline.md](data-pipeline.md#raw-record-one-row-per-planned-channel-per-survey).
The reasons behind each method are in [methodology.md](methodology.md).
Thresholds quoted here are those of the production `rigorous` preset.

Two different populations appear below, and they are easy to confuse:

- **Chatters** are accounts that sent at least one chat message during a
  sampled five-minute window, observed through EventSub.
- **Viewers** are Twitch's own concurrent-viewer count for a stream (Helix
  `viewer_count`), read when each survey starts.

Some field names in the public schema say "viewers" where the value counts
chatters. The schema is kept stable for existing clients, so the tables below
state what each field actually holds.

## Run level: `overallStats`

| Field | Definition | Read it as |
| --- | --- | --- |
| `totalChannels` | Channels in the analysed graph after every filter: at least 3 observations, at least 10 chatters, at least one edge, and membership in a community of at least 10 channels | The analysed population, not the rendered map (see `renderedChannels`) |
| `totalViewers` | Distinct **chatters** seen in the window across all loaded channels, after automated accounts are removed and before channel filters | A count of message authors, not an audience size. The site calls it "distinct chatters, bots removed". |
| `communitiesDetected` | Louvain communities with at least `min_community_size` (10) channels in the analysed graph | Can exceed the number of communities on the map |
| `modularityScore` | Weighted Newman modularity of the retained partition, rounded to 2 decimals | Strength of community structure, range −0.5 to 1. It rises with sparsity, so don't compare it across different thresholds. |
| `edgesTotal` | Edges in the analysed graph: channel pairs at or above the window's overlap threshold | Full graph, not the map |
| `avgOverlapWeight` | Mean shared-chatter count over the **rendered** edges, rounded to an integer | The typical strength of a drawn link |
| `dataPoints` | Channel observations (completed survey rows) loaded for the window | Five-minute channel samples, not people |
| `collectionPeriod` | First and last survey day actually covered, for example `Aug 13 – Aug 26, 2026` | Shorter than the window label if history is short |
| `renderedChannels`, `renderedEdges` | Channels and edges in the public projection, after the caps in [data-pipeline.md](data-pipeline.md#5-publication-and-visualization) | What the map draws |

## Channels: `channels[]`

| Field | Definition | Read it as |
| --- | --- | --- |
| `id`, `name` | Lowercase Twitch login | Identity key within one payload |
| `displayName` | Login with its first letter capitalized | Display only. It is not Twitch's display name. |
| `viewers` | Mean of the channel's daily-mean Helix `viewer_count` over live samples in the window. Each day weighs the same however often it was sampled. | Typical concurrent **viewers** while live |
| `viewerHistory[]` | One `{date, viewers}` point per collection day: that day's mean live `viewer_count` | Trend of Twitch-reported viewers. A single point when samples can't be dated. |
| `game` | The category the channel was seen in most often during the window. Ties go alphabetically. | Modal category, not what is live now |
| `language` | Broadcaster language tag from the last sample, capitalized (`En`, `Ja`) | Self-declared by the broadcaster |
| `communityId` | Slug of the channel's community, matched across windows | Stable across the time filter |
| `topOverlaps[]` | Up to 5 rendered neighbours by shared-chatter count: `{channelId, channelName, shared}` | Only neighbours that survived the public cap |
| `edgeCount` | Degree in the rendered graph | At most 25 (the per-channel edge cap) |
| `modularityScore` | Share of the channel's rendered links that stay inside its own community (0–1) | **Not** modularity. The site calls it "in-community link share". |
| `description` | Template text: `{Name} — {game} streamer in the {community} community.` | Generated, not editorial |
| `layout` | `{x, y}` from the server-side layout | Position only. Distance is not a metric. |

## Edges: `edges[]`

| Field | Definition | Read it as |
| --- | --- | --- |
| `source`, `target` | Channel logins | Undirected |
| `weight` | Measured shared-chatter count `\|A ∩ B\|` over the window, whatever `weighting_mode` drove the analysis | A lower bound on the shared active audience in this window. Compare within one payload only. |

## Communities: `communities[]`, `topCommunitiesBySize[]`

| Field | Definition | Read it as |
| --- | --- | --- |
| `communities[].label` | Tagger label. When two communities would share a label, their two largest channels are appended. | Describes member metadata. See [labels](methodology.md#labels). |
| `communities[].nodeCount` | Rendered channels in the community | Map membership, not full-graph size |
| `communities[].color`, `id` | Colour and slug, inherited from the canonical window's best-matching community when their member Jaccard similarity is above 0.2 | Identity across windows |
| `communities[].description` | Template: `{label} community - {n} rendered channels` | Generated |
| `topCommunitiesBySize[]` | The 8 largest rendered communities: `{community, channels, viewers}` | |
| `topCommunitiesBySize[].viewers` | **Sum** of member channels' mean concurrent viewers | Not unique reach: people who watch several channels are counted more than once |

## Most connected: `mostConnectedChannels[]`

| Field | Definition | Read it as |
| --- | --- | --- |
| `name`, `community`, `color` | Display name, community label, and colour | |
| `edges` | Degree in the rendered graph | Capped at 25, so the top of this list is often a tie at the cap |

## Time windows

| Field | Definition |
| --- | --- |
| `availableWindows` | Windows this run published, for example `[14, 30]`. Each has its own `data/frontend-data-<N>d.json`. |
| `pendingWindows` | Configured windows the retained surveys cannot fill yet. The UI marks them pending. |
| `defaultWindow` | The window the site opens on: the configured canonical window when it is available, otherwise the widest available one |
| `generatedAt` | ISO timestamp of the export |

`availableWindows`, `pendingWindows`, and `defaultWindow` are omitted for
single-window deployments. Before any window
has enough history, `data/frontend-data.json` carries a schema-valid payload
with empty arrays and `collectionPeriod: "PENDING — collecting survey history"`.

## Private run record: `processed/analysis_results.json`

Written once per run, by the canonical window only. It is never public.

| Key | Contents |
| --- | --- |
| `config` | `analysis_window_days`, the `overlap_threshold` actually used for that window, `weighting_mode`, `resolution`, `min_channel_viewers`, `min_channel_observations`, `min_community_size` |
| `partition` | Every analysed channel → community ID (full graph, not capped) |
| `labels` | Community ID → label |
| `statistics.graph` | `num_nodes`, `num_edges`, `density` of the analysed graph |
| `statistics.detection` | `num_communities`, `modularity`, `community_sizes`, largest and smallest |
| `statistics.tagging` | Communities labelled by a clear game (share of at least 60%) or a clear language (at least 40%) |
| `statistics.aggregator` | Snapshot and channel counts, distinct chatters, window bounds, snapshot sources, and `automated_chatters` (below) |

`statistics.aggregator.automated_chatters` holds the bot-filter report:

- `accounts`, `known_bots`, `listed`, `concurrent`: accounts removed, credited
  to the first rule that claimed each;
- `memberships`: (channel, account) pairs removed;
- `channels`: channels affected;
- `pair_overlaps`: channel-pair overlap increments those accounts had
  contributed;
- `peak_distribution`: number of accounts at each peak concurrency of 2 or
  more, which is what the threshold should be re-checked against.

The report never names an account.

## Log milestones

Each is a single log line with `key=value` counts. None contains an account
identity.

| Milestone | Meaning |
| --- | --- |
| `SURVEY_STARTED`, `BATCH_COMPLETED`, `SURVEY_COMPLETED`, `SURVEY_COMPLETED_WITH_ERRORS`, `SURVEY_PARTIAL` | Collection progress and terminal state |
| `AUTOMATED_CHATTERS_EXCLUDED` | Bot-filter counts for each analysed window |
| `UNCALIBRATED_WINDOW` | A published window is using the fallback overlap threshold |
| `SHORT_WINDOW` | A window's data covers fewer days than its label |
| `NO_FULL_WINDOW`, `ANALYSIS_PENDING` | No configured window is covered yet; a pending payload was published |
| `EDGE_CAP_BOUND` | The 25,000-edge cap, not the overlap threshold, shaped the public graph |
| `ANALYSIS_COMPLETED`, `ANALYSIS_FAILED` | Scheduled analysis outcome, watched by CloudWatch alarms |
