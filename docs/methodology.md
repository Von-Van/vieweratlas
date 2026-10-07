# Methodology

This page explains what ViewerAtlas measures, how each derived quantity is
built, why it is built that way, and what the results can and cannot support.
[data-pipeline.md](data-pipeline.md) says where each step runs;
[metrics.md](metrics.md) defines each published field.

Figures quoted as "the 14-day sample" come from 42 real surveys run
2026-08-13 to 2026-08-26. They were measured with the current code on
2026-09-22, and only aggregates were read. Figures quoted
from code comments are cited to where they are recorded.

## Research question

**Which Twitch channels share an active audience, and do those shared
audiences form recognizable communities?**

ViewerAtlas turns this into something measurable as *chat co-presence*: an
account that sends messages in two channels within the same rolling window
links those channels. Communities are groups of channels that are linked
more densely to each other than the rest of the graph would predict.

This is a descriptive, observational design. It maps structure in who
chats where. It does not explain why that structure exists.

## Observed, derived, interpreted

| Layer | Examples | Status |
| --- | --- | --- |
| **Observed** | The accounts that chatted in channel C during window W; Helix metadata for C at survey start (viewers, game, language, rank) | Recorded facts, subject to the sampling design |
| **Derived** | A channel's chatter set over a window; shared-chatter counts; the overlap graph; communities; modularity; labels; mean viewer counts; the capped public map and its layout | Deterministic functions of the observations plus configuration. Every threshold is a choice, documented below. |
| **Interpretation** | "These channels share an audience"; "this community is Spanish-language variety" | Readings of derived values. Only as strong as the limitations below allow. |

## Unit of observation and sampling design

The unit is one **channel sample**: the set of distinct accounts that sent at
least one chat message in a channel during one shared five-minute window.

- **Who is sampled.** Only people who chat. Lurkers, the majority of any
  audience, are invisible, and a channel's sample depends on how chatty its
  audience and culture are.
- **Which channels.** The approximate top 1,200 live channels by concurrent
  viewers when each survey starts, ranked by Helix. Small channels are
  outside the population by design.
- **When.** Three fixed times a day in US Eastern time (06:00, 14:00, 22:00).
  A channel whose audience peaks outside those hours is sampled at its quiet
  times, or not at all. Coverage is not neutral across time zones.
- **How often.** The top of the ranking churns, so channels are observed
  unevenly. In the 14-day sample, 11,835 distinct channels appeared across 42
  surveys. 37.8% of them were observed once, the median was 2 observations,
  and the most-observed channel had 41.
- **Why equal windows.** Every channel in a batch is sampled over the same
  300 seconds, and a batch that loses its connection is rerun from scratch.
  Samples are therefore comparable within a survey, and no channel is
  credited with a longer or broken window. This replaced an earlier
  continuously running IRC collector (see
  [architecture.md](architecture.md#key-design-decisions)).

## From samples to channel audiences

**Union over a rolling window.** A channel's *observed chatter set* is the
union of its samples in the window. Overlap between two channels is the size
of the intersection of their sets.

This makes overlap counts **grow faster than linearly with the number of
surveys**. Each additional sample adds chatters to both sets, and the
intersection recovers roughly the product of the two sampling fractions. So:

- counts are lower bounds on the true shared active audience, not estimates of
  it;
- counts are comparable only between channel pairs in the *same run and
  window*, and even then they partly reflect how often each channel was
  sampled;
- thresholds measured for one window length do not transfer to another. This
  is why each window has its own.

**Why rolling windows.** Without a window, every chatter set only grows. Graph
density climbs every day and a fixed threshold drifts from selective to
permissive (`analysis_window_days` comment in
[`config.py`](../twitchiobot/src/config.py)). Windows are anchored to the
newest survey date rather than to the wall clock, so re-analysing the same
data reproduces the same graph.

**Why only completed surveys and rows.** Batches are written before the
survey's final status. Reading them without the manifest's `complete` or
`complete_with_errors` status would let interrupted surveys into the graph.
Failed channel rows describe the collection run, not an audience.

**Identity.** Chatters are matched by lowercase login. Stable user IDs are
collected but not yet used, so an account that renames counts as two people.

**Sparsity.** In the 14-day sample, 86.4% of the 897,865 distinct chatters
appear in only one channel. The whole overlap signal comes from the roughly
122,000 who appear in two or more.

## Automated accounts

**Problem.** Chat bots post messages, so they are recorded like people. A bot
present in *N* channels adds one "shared chatter" to each of the
*N(N−1)/2* pairs among them, so a handful of accounts can outweigh everyone
else.

**Measurement.** Over the 14-day sample, the 229 accounts the filter removes
supplied **97.0%** of all channel-pair overlap increments: 8,630,829 of them.
StreamElements and Nightbot alone chatted in 2,903 and 2,801 channels.
Without the filter, the production 14-day graph would have 13,736 edges, and
681 of them (5.0%) exist only because of these accounts. With the production
node filters, removing them also cuts candidate channel pairs by 27% (104,024
to 76,291). These figures were re-derived on 2026-09-22 from the same data
the filter was designed on.

**Rules.** [`chatter_filter.py`](../twitchiobot/src/chatter_filter.py):

1. **Known services.** A curated list of chat-bot service accounts, which
   deployments can extend with `excluded_chatters`. Many service bots answer
   commands rather than post on a timer, so they rarely appear in several
   chats at once, and the second rule would not catch them. The list
   deliberately omits channel-specific bots: they create no overlap, and
   guessing bots by name also catches people. The module records that 955 of
   1,102 other bot-named accounts appeared in a single channel.
2. **Concurrency.** An account active in more than 3 channels within one
   survey window is treated as automated. All channels in a batch are watched
   over the same five minutes, and people rarely keep up with more than a
   couple of chats at once. The module docstring records the account-age
   evidence for the cut-off, measured when the rule was introduced. Accounts
   that peaked at 1–2 concurrent chats looked like the general population.
   At 4 concurrent chats, 72% came from one narrow band of recently created
   account IDs, and at 6 or more, 90% did. That analysis is recorded, not
   re-run here.

**Why at analysis time.** The raw files are untouched, so correcting either
rule applies retroactively to every retained survey. The classification is
never written anywhere, because a behavioural rule can misjudge a person.
Only per-rule counts are logged.

**Costs.** A person who genuinely chats in four or more surveyed channels
within five minutes is excluded. A command-driven bot missing from the list
still gets through.

## Graph construction

**Nodes.** Channels observed at least **3 times** with at least **10**
distinct chatters (`min_channel_observations`, `min_channel_viewers`). A
channel seen once is a single five-minute sample and cannot give reliable
overlap at any threshold. In the 14-day sample these filters keep 5,082 of
11,835 channels.

**Edge weight.** The production weight is `shared_count = |A ∩ B|`. Two
normalized alternatives are implemented: `jaccard` (|A ∩ B| / |A ∪ B|) and
`overlap_coef` (|A ∩ B| / min(|A|, |B|)). They exist because raw counts reward
channels that were simply sampled more often. In the original sweep (a
four-day, 15-survey sample), both scored below the raw count. The comment in
`get_rigorous_config()` records best scores of 0.699 for Jaccard and 0.726 for
the overlap coefficient. Production keeps `shared_count` until overlaps carry
more magnitude.

**High-degree accounts.** An account present in more than 200 channels is
skipped entirely (`max_viewer_channel_degree`), because it would add a
combinatorial number of weak edges. It is dropped rather than down-weighted.

**Threshold.** A pair becomes an edge only if it shares at least the window's
threshold. Channels left without edges are removed.

## Calibrating the overlap threshold

Thresholds are measured, not inherited. The TwitchAtlas-style value of 300
produced a graph with zero edges on five-minute EventSub samples (recorded in
the test suite).

**Procedure.** [`sweep_threshold.py`](../twitchiobot/scripts/sweep_threshold.py),
run once per window by
[`calibrate_windows.sh`](../twitchiobot/scripts/calibrate_windows.sh):

1. Build the graph once at threshold 1, with the production filters and the
   bot filter.
2. Re-threshold it at candidate values (fixed values plus the median, p75, p90,
   and p95 of pair overlaps).
3. For each candidate, report edges, density, the share of channels still
   connected, the number of communities, and modularity.

**Decision rule.** The script suggests the threshold with the highest
modularity. The production values deliberately do not follow it, and the
reasoning is recorded in `get_rigorous_config()`:

All three were re-swept on 2026-10-01 with the automated-account filter active.

- *14-day window.* The sweep suggested 10 (modularity 0.844, 28% of channels
  connected). **4 was chosen**, the p90 of pair overlaps: modularity 0.826
  with 55% connected. Before the filter the same rule gave 3, and there 10
  published 654 channels against 899.
- *30-day window.* The sweep suggested 9 (0.818, 45% connected). **5 was
  chosen**, the p90: 0.808 with 62% connected.
- *90-day window.* Surveys only spanned 50 days, so **5** (the p90 on that
  sample; the sweep suggested 25) is a placeholder. The window stays PENDING
  until it fills, and should be re-swept then.

Modularity rewards sparsity on its own, so its maximum overshoots what an
overlap map is for. The chosen value trades a little modularity for coverage,
and that is a judgment, not an optimum.

**Drift.** The p90 rose by one in both measured windows between sweeps even
though the bot filter lowers overlap counts, because overlap grows with
history. Expect every value to keep climbing and re-sweep periodically.

## Community detection

**Algorithm.** Louvain modularity optimization (`python-louvain`
`best_partition`, resolution 1.0) on the weighted graph.

- `random_state=42` fixes Louvain's node order. Without it, the same graph
  gives a different number of communities on each run, and day-to-day
  movement on the map would be partly noise.
- Communities under **10 channels** are discarded, because Louvain routinely
  emits singletons and pairs that are noise rather than structure.
  Modularity is then recomputed on the retained channels.
- The pipeline is deterministic end to end. Edge insertion order, subgraph
  node order, and label tie-breaks no longer depend on Python's hash seed.
  Before this was fixed on 2026-09-22, the map layout changed on every run,
  and a tied community label could flip between runs.

**Reading modularity.** Modularity compares the edge weight inside
communities with what the same degree sequence would give at random. Values
well above 0.3 are commonly read as clear community structure. In the 14-day
sample it is 0.81. Three caveats apply:

- modularity rises as a graph gets sparser, so it is not a quality score on
  its own;
- Louvain has a resolution limit, so small real groups can be merged into
  larger ones;
- many near-optimal partitions exist, and the published one is one of them,
  chosen reproducibly.

## Labels

[`cluster_tagger.py`](../twitchiobot/src/cluster_tagger.py) names each
community from its members' metadata. Shares are taken over *all* channels in
the community, so a community whose metadata is mostly missing cannot be
named after its few known members.

| Condition | Label |
| --- | --- |
| One game on ≥ 60% of channels | `Game` |
| A game on ≥ 40% and a language on ≥ 40% | `Game (lang)` |
| A language on ≥ 40% and no game on ≥ 40% | `Variety (lang)` |
| No language on ≥ 40% | The top two games (`Game A / Game B Mix`), or the only game present |
| No game or language metadata | `Variety Community (n channels)` or `Community n` |

A channel's game is the category it was most often seen in during the window.
Its language is the broadcaster's language tag. Ties go to the alphabetically
first name. When two communities end up with the same label, the export adds
their two largest channels to tell them apart.

Labels describe member metadata. They are not an input to detection, so
agreement between a label and a community is a finding, not a tautology. In
the 14-day sample, every one of the 36 communities met a labelling threshold:
13 have one game on at least 60% of their channels, and the other 23 share one
broadcast language on at least 40%. This is a sanity check that the overlap
signal carries real structure. It is not evidence of why audiences cluster.

## The public map is a projection

The browser cannot usefully draw thousands of channels, so the published map
is a capped view of the analysed graph:

- the 1,000 channels with the highest mean viewer count;
- each channel's strongest 25 edges, and at most 25,000 edges in total;
- the largest connected component;
- communities with at least 4 rendered channels.

In the 14-day sample, the analysed graph has 2,748 channels, 12,523 edges, and
36 communities. The map shows 900 channels, 4,294 edges, and 19 communities.
Run-level statistics (`totalChannels`, `communitiesDetected`, `edgesTotal`)
describe the full graph. `renderedChannels` and `renderedEdges` describe the
map. Per-channel edge counts on the map are capped at 25, so a "most
connected" ranking saturates at the cap.

## Validation performed

- **Unit and integration tests.** 272 pytest tests cover aggregation, windows,
  the bot filter, graph weights, Louvain wrappers, labelling, export
  invariants (no private fields, measured counts, caps), survey collection,
  credential rotation, and the lease. CI runs them on every push.
- **Real-data checks** (2026-09-22, aggregates only). The bot-filter figures
  above were reproduced. Three runs with different hash seeds gave identical
  public payloads and private results. The determinism fixes made then left
  the partition, labels, and modularity of the 14-day graph unchanged; only
  the layout coordinates moved.
- **Planted-structure recovery.** [`make_demo_data.py`](../twitchiobot/scripts/make_demo_data.py)
  generates synthetic surveys with 8 planted communities of 24 channels,
  plus bots and a coordinated farm. The production preset finds exactly 8
  communities of 24 channels in both the 14-day and 30-day windows
  (modularity 0.78 and 0.75). On the 30-day window, checked channel by
  channel, each matches one planted community. The filter removes the 2 bot
  services and all 5 farm accounts. This checks that the pipeline can recover structure that is
  known to exist. It says nothing about Twitch.

## How to read the results

**Supported**

- Relative statements within one run and window: *A and B share more observed
  chatters than A and C.*
- Structural statements: *these channels form a group whose chat audiences
  overlap more than the modularity null model expects.*
- Descriptive statements about group composition: dominant language or game.

**Not supported**

- Audience sizes or reach. Shared-chatter counts are lower bounds on a
  sampled subset.
- Comparing counts across windows, across days with different sampling depth,
  or with runs that used different thresholds.
- Causes: raids, recommendations, audience migration, or influence.
- Anything about lurkers or individual viewers.
- Channels outside the surveyed top 1,200, or hours outside the survey times.

## Limitations and known biases

1. **Chatters, not viewers.** Chat-heavy channels, genres, and cultures are
   over-represented.
2. **Fixed Eastern-time schedule.** Channels with different peak hours or time
   zones are sampled at unrepresentative times.
3. **Top-1,200 population**, ranked by concurrent viewers at survey start. The
   ranking is approximate, and `viewer_count` is taken at discovery, up to
   about 80 minutes before a channel's batch runs.
4. **Unequal sampling depth.** Raw counts partly measure how often a channel
   was sampled. Normalized weights exist but are not used in production yet.
5. **Super-linear growth.** Counts are not comparable across windows, and
   thresholds must be measured per window.
6. **Identity by login.** Renamed accounts count twice.
7. **Heuristic bot removal.** It can drop rare humans and miss unlisted
   command bots.
8. **Hard degree cap.** Accounts in more than 200 channels are dropped rather
   than down-weighted.
9. **Provisional and drifting thresholds.** The 90-day value was measured on
   50 days of surveys, and all three drift upward as history grows.
10. **Louvain limits.** Resolution limit and non-unique optima. Communities
    under 10 channels are discarded.
11. **Heuristic labels.** They use modal game and the broadcaster's language
    tag, and the 40% and 60% cut-offs are conventions.
12. **Projection.** The public map hides smaller channels and weak ties, and
    per-channel link counts saturate at 25.
13. **Legacy inputs are not windowed.** Flat JSON/CSV logs and VOD presence
    snapshots, if present in storage, are merged into every window. The
    production bucket should be checked for them; see
    [development.md](development.md#known-technical-debt).
14. **Attrition.** Surveys that finished `complete_with_errors` are included.
    Their failed channels are simply missing from that survey.
