# Development and Reproduction

This guide covers running ViewerAtlas on your own machine, from the test suite
to the full pipeline with a browser view, and what each path needs.

| Goal | Needs | Time |
| --- | --- | --- |
| Run the tests | Python 3.11 | 1 minute |
| Run the whole pipeline on synthetic data and view the map | Python 3.11, Node.js 22 | 5 minutes |
| Collect and analyse real Twitch data | A Twitch application, a bot account, and its OAuth tokens | About 80 minutes per full survey |
| Deploy the scheduled AWS pipeline | AWS account, Docker, AWS CLI | See [DEPLOYMENT.md](../twitchiobot/docs/DEPLOYMENT.md) |

The production survey data is personal data under the project's
[data policy](../twitchiobot/docs/DATA_POLICY.md) and is not distributed.
**The published results therefore cannot be re-run from this repository.**
The code, the method, and the synthetic path below can.

## Prerequisites

- Python 3.11, the version used by CI and the Docker images. Dependencies are
  pinned in [`src/requirements.txt`](../twitchiobot/src/requirements.txt).
- Node.js 22 and npm for the frontend. CI uses Node 22; React Router 7 needs at
  least Node 20.

## Set up and test

```bash
git clone https://github.com/Von-Van/vieweratlas.git
cd vieweratlas/twitchiobot
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
pytest -q
```

The tests use fake Twitch clients, credential stores, and storage, so they
need no network access or credentials.

## Run the pipeline without Twitch

[`scripts/make_demo_data.py`](../twitchiobot/scripts/make_demo_data.py) writes
30 days of **synthetic** surveys in the exact private v2 layout: 90 surveys,
192 channels in 8 planted communities, plus two chat-bot services and a small
coordinated account farm. Output is deterministic for a given `--seed`. The
channels and chatters are invented, so results describe the pipeline, not
Twitch.

From `twitchiobot/`:

```bash
python scripts/make_demo_data.py
STORAGE_TYPE=file LOGS_DIR=demo_data python src/main.py analyze rigorous
```

The production preset then:

- publishes the 14- and 30-day windows and marks the 90-day window pending;
- removes 7 automated accounts (2 known bots and 5 concurrent);
- recovers the 8 planted communities of 24 channels.

It writes:

| Path | Contents |
| --- | --- |
| `demo_data/data/frontend-data.json`, `-14d.json`, `-30d.json` | Public payloads, one per window |
| `demo_data/processed/analysis_results.json` | Private run record |
| `demo_data/curated/analysis/<run date>/graph_*.csv`, `community_analysis/graph_*.csv` | Graph node and edge tables |
| `logs/pipeline.log` | Run log |

All of these paths are gitignored. To view the result in the frontend:

```bash
cd ../frontend
npm ci
mkdir -p public/data
cp ../twitchiobot/demo_data/data/frontend-data*.json public/data/
VITE_DATA_URL=/data/frontend-data.json npm run dev
```

Open the printed URL and go to **Community Map**. The time filter offers
14d and 30d, with 90d marked as not having enough history.
`frontend/public/data/` is gitignored; payloads never belong in the repository.

Running `npm run dev` without `VITE_DATA_URL` shows the same synthetic output,
bundled as `frontend/src/app/data/demoAtlasData.json`, under a "Demo data"
banner. After changing the exporter or the demo generator, refresh it from
`twitchiobot/`:

```bash
python -m json.tool --compact demo_data/data/frontend-data.json ../frontend/src/app/data/demoAtlasData.json
```

## Collect real data locally

A local survey uses the same code as production, with three differences:
credentials come from environment variables, there is no DynamoDB lease, and
output goes to `LOGS_DIR`.

1. Create a Twitch application and a dedicated bot account. Obtain a user
   access token and refresh token for the bot with **only** the
   `user:read:chat` scope, through Twitch's OAuth authorization-code flow. The
   survey refuses a token with any other scope. The repository's guided helper,
   `setup_twitch_auth.py`, writes only to AWS Secrets Manager, so for local
   runs you supply the tokens yourself.
2. `cp config/.env.example .env` and fill in the five `TWITCH_*` values.
3. Run a survey, then analyse:

   ```bash
   # A quick canary: 5 channels for 60 seconds.
   SURVEY_TOP_CHANNELS_LIMIT=5 SURVEY_WINDOW_SECONDS=60 python src/main.py survey config/config.yaml
   # The full survey: 1,200 channels, about 80 minutes.
   python src/main.py survey config/config.yaml
   python src/main.py analyze default
   ```

`default` analyses everything collected. `rigorous` publishes only a pending
payload until 14 days of surveys exist.

*This path was last run against live Twitch before 2026-09-22; it needs live
credentials. The code it uses is covered by
`tests/test_eventsub_survey.py`.*

## Analysis presets

`python src/main.py analyze <preset | path/to/config.yaml>`. Every preset
applies the automated-account filter.

| Preset | Window | Overlap threshold | Channel filters | Communities | Notes |
| --- | --- | --- | --- | --- | --- |
| `rigorous` (production) | 14/30/90 days, canonical 30 | 14d: 4, 30d: 5, 90d: 5, otherwise 2 | ≥ 3 observations, ≥ 10 chatters, isolated dropped | ≥ 10 channels, resolution 1.0 | PNG and HTML renders off |
| `default` | All retained data | 1 | none | ≥ 1, resolution 1.0 | Renders on |
| `explorer` | All retained data | 1 | none | ≥ 1, resolution 2.0 | Finer communities, DEBUG logging |
| `debug` | All retained data | 1 | none | ≥ 1, resolution 1.0 | Small collection limits, DEBUG logging |
| [`config/config.yaml`](../twitchiobot/config/config.yaml) | 14/30/90, canonical 30 | 10 (no per-window values) | none | ≥ 3, resolution 1.2 | Also the collection settings the production collector reads |

The YAML loader rejects unknown keys, so a misspelled option fails loudly
instead of being ignored.

## Environment variables

| Variable | Read by | Purpose |
| --- | --- | --- |
| `STORAGE_TYPE`, `LOGS_DIR` | storage | `file` (default) or `s3`. `LOGS_DIR` is the file-storage root (default `logs`). |
| `S3_BUCKET`, `S3_PREFIX`, `S3_REGION` | storage | S3 location when `STORAGE_TYPE=s3` |
| `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `TWITCH_BOT_USER_ID`, `TWITCH_OAUTH_TOKEN`, `TWITCH_REFRESH_TOKEN` | local survey | Bot credentials when no secret ID is set |
| `TWITCH_CREDENTIALS_SECRET_ID` | survey | Production: load and rotate credentials in Secrets Manager instead |
| `DYNAMODB_STATE_TABLE`, `SURVEY_LEASE_SECONDS` | survey | Lease table and duration. Used only with `STORAGE_TYPE=s3`. |
| `SURVEY_TOP_CHANNELS_LIMIT`, `SURVEY_BATCH_SIZE`, `SURVEY_WINDOW_SECONDS`, `SURVEY_TIMEOUT_SECONDS` | survey | Canary overrides. `run-survey-test.sh` sets them. |
| `SURVEY_SESSION_ID` | survey | Fix the session ID instead of using the start time |
| `OVERLAP_THRESHOLD`, `MIN_COMMUNITY_SIZE`, `RESOLUTION`, `LOG_LEVEL` | analysis | Overrides that apply to YAML configs only |
| `CHANNELS_FILE`, `TWITCH_CHANNELS` | VOD preprocessor | Channel list for the legacy local VOD path |
| `VITE_DATA_URL`, `VITE_SITE_URL` | frontend build | Payload URL (same origin), and the canonical site origin for meta tags |

Deployment variables are documented in
[`infrastructure/aws/.env.example`](../twitchiobot/infrastructure/aws/.env.example).

## Calibrating thresholds

With real surveys available locally or in S3:

```bash
twitchiobot/scripts/calibrate_windows.sh                 # reads S3 from infrastructure/aws/.env
twitchiobot/scripts/calibrate_windows.sh /path/to/root   # or a local storage root
```

The script sweeps each window using the production filters and prints a block
to paste into `get_rigorous_config()`. Take the knee, not the modularity
maximum; [methodology.md](methodology.md#calibrating-the-overlap-threshold)
explains why.

## Checks before committing

These are the checks CI runs:

```bash
cd twitchiobot
pytest -q
python -m compileall -q src scripts tests
for s in infrastructure/aws/*.sh scripts/*.sh ../frontend/deploy.sh; do bash -n "$s"; done
cd ../frontend
npm run typecheck
npm run build
```

There is no linter or formatter configuration yet (see technical debt below).

## Development process

ViewerAtlas is developed with AI assistance. AI coding assistants have been
used for implementation, debugging, refactoring, test writing,
documentation, exploratory analysis of the survey data, and code review.
Recent commits made with that help carry a `Co-Authored-By` trailer.

The project owner is responsible for:

- the research question, and what is and is not collected or published;
- the metrics and the analytical rules: windows, filters, thresholds, and how
  much of the map to publish;
- the architecture and the privacy boundary;
- interpreting results, validating them against real data, and deciding
  which proposed changes are accepted.

Decisions are recorded next to the code that implements them, together with
the measurement that justified them. The overlap thresholds in
`get_rigorous_config()` and the bot-filter rules in `chatter_filter.py` are
examples. They can be checked rather than taken on trust, and the tests pin
the behaviour those decisions depend on.

## Known technical debt

Known and not yet addressed, most important first.

1. **Legacy inputs are not windowed.** `DataAggregator` also loads flat JSON
   snapshots under `raw/snapshots/` and VOD presence snapshots under
   `curated/presence_snapshots/source=vod/`, and neither is filtered by the
   rolling window. If either exists in the production bucket, it is merged
   into every window. To check, the analysis log line `Loaded N JSON + N CSV +
   N VOD + N Parquet snapshots` should show 0 for everything except Parquet.
   Each run also downloads every survey `manifest.json` as a JSON candidate,
   and then ignores it.
2. **Provisional 90-day threshold.** It was measured on 50 days of surveys.
   Re-run `calibrate_windows.sh` once the window fills (about 2026-11-11).
3. **Identity by login.** Chatter IDs are collected but analysis still keys
   by lowercase login, so renames split one person into two.
4. **Misleading public field names.** `overallStats.totalViewers` counts
   chatters, and `channels[].modularityScore` is an in-community link share.
   Renaming them needs a coordinated schema change in the exporter, the
   validator, and the UI.
5. **"Most connected" saturates.** Rendered degree is capped at 25, so the
   ranking is mostly ties. Weighted or full-graph degree would be more
   informative.
6. **No lint or format tooling** for Python (for example ruff) or TypeScript
   (for example ESLint).
7. **`deploy-preflight.yml` asks for long-lived AWS keys** as secrets but only
   checks their format.
8. **Copied shell boilerplate.** Twelve AWS scripts each define their own
   `load_env_file`. Five let a variable already set in the shell override
   `.env` and seven do the reverse, so an override such as
   `IMAGE_TAG=… ./promote.sh` is silently replaced when `.env` sets the same
   key. The logging helpers and the `ENVIRONMENT` → `SERVICE_PREFIX` block are
   copied the same way. A shared file needs one precedence rule first, and
   `S3_BUCKET` means the frontend bucket in `frontend/deploy.sh` but the data
   lake everywhere else.
9. **Build and storage hygiene.** There is no `.dockerignore`, so build
   contexts include logs, outputs, and local `.env` files, although images
   copy only `src/` and the config. The legacy `raw/snapshots/` lifecycle
   rule (Standard-IA at 30 days, Glacier IR at 90) also matches the v2
   survey prefix.
