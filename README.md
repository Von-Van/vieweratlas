# ViewerAtlas

[![CI](https://github.com/Von-Van/vieweratlas/actions/workflows/ci.yml/badge.svg)](https://github.com/Von-Van/vieweratlas/actions/workflows/ci.yml)
[![Security](https://github.com/Von-Van/vieweratlas/actions/workflows/security.yml/badge.svg)](https://github.com/Von-Van/vieweratlas/actions/workflows/security.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-9147FF.svg)](LICENSE)

ViewerAtlas maps which Twitch channels share an active audience. Three times a
day it records who chats in the roughly 1,200 most-watched live channels. It
then builds a graph in which two channels are linked by the number of chatters
they share, finds communities in that graph with the Louvain algorithm, and
publishes the result as an interactive map.

![ViewerAtlas community map built from 14 days of surveys](docs/images/community-map-14d.jpg)

*The map produced from 42 surveys (2026-08-13 to 2026-08-26). It shows 900 of
the 2,748 analysed channels, coloured by detected community, rendered locally
from the pipeline's public output.*

## Why ViewerAtlas?

Twitch shows who is live and how many people are watching. It does not show
how audiences overlap: which streamers draw on the same people, or whether a
"scene" around a game or a language is a real community or just a category.
ViewerAtlas answers that from behaviour rather than labels. If the same
accounts chat in two channels, those channels share part of an audience, and
groups of channels that share far more with each other than with everyone else
form a community.

The production preset started from the settings of TwitchAtlas, an earlier
map of Twitch communities. Measurement showed those settings could not work
here: TwitchAtlas's edge threshold of 300 shared viewers produces no edges at
all on five-minute samples. The pipeline was rebuilt around short, equal,
scheduled samples whose thresholds are measured from the data, and those
choices are documented and tested.

## What It Measures

| | Quantity | Source |
| --- | --- | --- |
| **Observed** | For each surveyed channel and five-minute window, the set of accounts that sent at least one chat message (user ID and login; never message text) | EventSub `channel.chat.message` |
| **Observed** | Stream metadata at the start of each survey: concurrent viewers, category, language, rank | Helix `GET /streams` |
| **Derived** | Shared chatters between two channels over a rolling 14-, 30-, or 90-day window | Union of samples, then set intersection |
| **Derived** | Communities, modularity, and community labels (dominant game or language) | Louvain; `cluster_tagger.py` |
| **Published** | Channel-level aggregates only: the capped graph, communities, and mean viewers and trends per channel | `data/frontend-data*.json` |

Two cautions shape how the numbers should be read:

- A **chatter** is someone who sent a message during a sampled window.
  Lurkers are invisible.
- Shared-chatter counts are **lower bounds** that grow with the number of
  surveys. They compare channel pairs within one window; they do not measure
  audience size.

## How It Works

```text
Twitch Helix + EventSub
   │  survey: 3x daily, 12 batches of <=100 channels, one shared 5-minute window per batch
   ▼
private Parquet + manifest (S3)
   │  aggregate each window → remove bots → overlap graph → Louvain → labels
   ▼
public JSON: capped, channel-level only (S3 + CloudFront)
   │
   ▼
React map with 14 / 30 / 90-day filter
```

| Stage | Code | Output |
| --- | --- | --- |
| Collection | [`update_channels.py`](twitchiobot/src/update_channels.py), [`eventsub_survey.py`](twitchiobot/src/eventsub_survey.py) | `raw/snapshots/v2/…/batch=NN.parquet` and `manifest.json` |
| Storage | [`storage.py`](twitchiobot/src/storage.py) | S3, or a local directory with the same keys |
| Processing | [`data_aggregator.py`](twitchiobot/src/data_aggregator.py), [`chatter_filter.py`](twitchiobot/src/chatter_filter.py) | Per-channel chatter sets for each window, with bots removed |
| Analysis | [`graph_builder.py`](twitchiobot/src/graph_builder.py), [`community_detector.py`](twitchiobot/src/community_detector.py), [`cluster_tagger.py`](twitchiobot/src/cluster_tagger.py) | Overlap graph, communities, labels |
| Publication | [`frontend_exporter.py`](twitchiobot/src/frontend_exporter.py) | `data/frontend-data*.json` |
| Visualization | [`frontend/`](frontend/) | Interactive map, channel pages, statistics |

[docs/data-pipeline.md](docs/data-pipeline.md) walks through each stage and
its data formats.

## Example Results

These are real results from the 14-day sample: 42 surveys, 2026-08-13 to
2026-08-26, analysed with the production preset. They were regenerated on
2026-09-22 with the code in this repository, and only aggregates were read.

| Step | Result |
| --- | --- |
| Channel samples loaded | 47,864, covering 11,835 distinct channels |
| Distinct chatters, after removing 229 automated accounts | 897,865 |
| Channels observed at least 3 times with at least 10 chatters | 5,082 |
| Overlap graph at the 14-day threshold (3 shared chatters) | 3,246 channels, 13,055 edges |
| Louvain communities of at least 10 channels | 36 communities (2,748 channels), modularity 0.81 |
| Public map | 900 channels, 4,294 edges, 19 communities |

Three findings from that run:

- **Bots dominate raw overlap.** 229 accounts, 0.03% of all chatters,
  supplied 97% of every channel-pair overlap increment. Left in, they would
  have added 681 edges (5% of the unfiltered graph) that link channels
  through bots alone.
- **Communities follow language and game.** Every detected community has a
  dominant attribute: 13 have one game on at least 60% of their channels, and
  the other 23 share a broadcast language on at least 40%. Neither attribute
  is an input to community detection, so this agreement shows the overlap
  signal carries real structure. It does not explain why audiences cluster.
- **The signal is sparse.** 86% of chatters appear in only one channel. The
  graph is built from the roughly 122,000 who appear in two or more.

On synthetic data with eight planted communities
([`make_demo_data.py`](twitchiobot/scripts/make_demo_data.py)), the same
pipeline recovers all eight planted communities. See
[methodology.md](docs/methodology.md#validation-performed).

## Technical Architecture

Collection, analysis, and delivery are independent one-shot jobs joined only
by S3. Nothing runs continuously.

- **Pipeline:** Python 3.11 with TwitchIO (EventSub), pandas and PyArrow,
  NetworkX, and python-louvain, covered by a pytest suite that needs no network
  or credentials.
- **AWS:** EventBridge Scheduler starts ECS Fargate tasks. Data sits in a
  private S3 data lake with lifecycle expiry. A DynamoDB lease prevents
  overlapping surveys, and Secrets Manager holds the rotating bot credential.
  The site is served by CloudFront with Origin Access Control and security
  headers, and CloudWatch alarms and a budget watch for failures and cost.
- **Frontend:** React 18, TypeScript, and Vite. The page draws a layout the
  pipeline precomputed and validates every payload before rendering it.
- **CI and security:** tests, compile and shell checks, frontend typecheck and
  build, pip-audit, Bandit, npm audit, and Dependabot.

[docs/architecture.md](docs/architecture.md) has the full component map,
infrastructure diagram, and design decisions.

## Running ViewerAtlas

You can run the whole pipeline without a Twitch account, on synthetic surveys:

```bash
git clone https://github.com/Von-Van/vieweratlas.git
cd vieweratlas/twitchiobot
python3.11 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements-dev.txt
pytest -q
python scripts/make_demo_data.py
STORAGE_TYPE=file LOGS_DIR=demo_data python src/main.py analyze rigorous
```

To view the output in the frontend (Node.js 22):

```bash
cd ../frontend
npm ci
mkdir -p public/data && cp ../twitchiobot/demo_data/data/frontend-data*.json public/data/
VITE_DATA_URL=/data/frontend-data.json npm run dev
```

- **Real data.** Collecting your own surveys needs a Twitch application and a
  bot token with the `user:read:chat` scope; see
  [docs/development.md](docs/development.md#collect-real-data-locally).
- **Cloud deployment.** Scheduled AWS collection follows
  [twitchiobot/docs/DEPLOYMENT.md](twitchiobot/docs/DEPLOYMENT.md). Those
  scripts create real resources and costs.
- **Reproducibility.** Production survey data is personal data and is not
  distributed. The synthetic path reproduces the pipeline, not the published
  results.

## Data & Methodology

- [Data pipeline](docs/data-pipeline.md): sources, formats, storage layout,
  and every transformation, with the code that performs it.
- [Methodology](docs/methodology.md): sampling design, bot removal, threshold
  calibration, community detection, labelling, validation, and interpretation.
- [Metrics reference](docs/metrics.md): what each published field measures
  and how to read it.
- [Data policy](twitchiobot/docs/DATA_POLICY.md): what is stored, for how
  long, and who can see it.

**Privacy.** Raw survey files hold Twitch user IDs and logins, which are
pseudonymous personal data. They stay in a private bucket and expire after 100
days. The public site receives only channel-level aggregates, and a
post-deployment smoke test checks that every public payload contains no chatter
identities.

## Limitations

- **Chatters, not viewers.** Lurkers are invisible, and chat-heavy channels,
  genres, and cultures are over-represented.
- **A sample, not a census.** Samples are three five-minute windows a day at
  fixed US Eastern times, across the top ~1,200 live channels. Other channels,
  hours, and time zones are under-covered.
- **Counts depend on sampling effort.** Overlap counts grow faster than
  linearly with surveys and with how often a channel was sampled, so they
  compare pairs within one window only.
- **Heuristic cleaning.** Bot removal can drop a rare human who chats in four
  or more surveyed chats within five minutes, and can miss unlisted bots.
  Chatters are matched by login, so a rename counts twice.
- **Calibration is a judgment.** The 14- and 30-day thresholds were measured
  before the bot filter existed and are due to be re-measured. The 90-day
  window has no measured threshold yet.
- **Observational.** The map shows where audiences overlap, not why: not
  raids, recommendations, or migration.
- **A projection.** The public map shows the 1,000 most-watched analysed
  channels, with at most 25 links each.

The complete list, with mitigations, is in
[methodology.md](docs/methodology.md#limitations-and-known-biases).

## Project Status

| Area | Status |
| --- | --- |
| EventSub survey collection (3× daily on AWS) | Production |
| Rolling-window analysis and the public map (14- and 30-day windows) | Production. The 90-day window publishes itself once 90 days of surveys exist. |
| Automated-account filter | Implemented; threshold re-measurement pending |
| Normalized edge weights (Jaccard, overlap coefficient) | Experimental: implemented and tested, not used in production |
| VOD chat preprocessor, PNG and HTML renders | Local development only; disabled in production |
| CloudFront access analytics (no viewer identifiers) | Optional |
| Identity by stable Twitch ID; broadcaster opt-in | Planned |

## Roadmap

1. Re-measure every window's overlap threshold with the bot filter active,
   and calibrate the 90-day window once it fills.
2. Key chatters by stable Twitch user ID instead of login.
3. Apply the rolling window to legacy inputs, or confirm none remain.
4. Revisit normalized edge weights once overlaps carry more magnitude.
5. Add Python and TypeScript linting to CI.

## Development Process

ViewerAtlas is built with AI coding assistants, which help with
implementation, debugging, refactoring, documentation, analysis, and review.
The project owner sets the research question, the data collected and
published, the metrics and analytical rules, and the architecture. The owner
also interprets and validates results and decides which changes are
accepted. Decisions are recorded next to the code that implements them, with
the measurements behind them. See
[docs/development.md](docs/development.md#development-process).

## Repository Map

| Path | Contents |
| --- | --- |
| [`twitchiobot/src/`](twitchiobot/src/) | Collection, storage, analysis, and export (Python) |
| [`twitchiobot/tests/`](twitchiobot/tests/) | pytest suite |
| [`twitchiobot/scripts/`](twitchiobot/scripts/) | Threshold calibration, survey inspection, Twitch authorization, synthetic data |
| [`twitchiobot/config/`](twitchiobot/config/) | `config.yaml` and the local `.env` template |
| [`twitchiobot/infrastructure/`](twitchiobot/infrastructure/) | Dockerfiles and AWS deployment, monitoring, and operations scripts |
| [`twitchiobot/docs/`](twitchiobot/docs/) | Operator runbooks, developer contracts, data policy |
| [`frontend/`](frontend/) | React map, channel pages, and statistics |
| [`docs/`](docs/) | Project documentation |
| [`.github/workflows/`](.github/workflows/) | CI, security audit, deploy preflight |

## Documentation

- [Architecture](docs/architecture.md), [Data pipeline](docs/data-pipeline.md),
  [Methodology](docs/methodology.md), [Metrics](docs/metrics.md),
  [Development](docs/development.md)
- [Frontend guide](frontend/README.md)
- [Deployment guide](twitchiobot/docs/DEPLOYMENT.md),
  [Daily operations](twitchiobot/docs/DAILY_OPERATIONS.md),
  [Developer contracts](twitchiobot/docs/DEVELOPER.md),
  [Data policy](twitchiobot/docs/DATA_POLICY.md)
- [Security policy](SECURITY.md)

## License

MIT. See [LICENSE](LICENSE).
