# ViewerAtlas

ViewerAtlas collects Twitch audience-presence data and builds community maps
from shared viewers.

## What It Does

1. Discovers channels (top Twitch streams via Helix API)
2. Collects live chatters in timed batches
3. Optionally ingests VOD chat replay data
4. Aggregates snapshots into channel -> unique viewers
5. Builds a weighted overlap graph (shared viewers = edge weight)
6. Runs Louvain community detection
7. Tags communities using dominant game/language metadata
8. Exports visualizations and analysis artifacts

## Scope and Features

- Live Twitch chat collection with retry logic and daily per-channel dedupe
- Optional VOD preprocessing pipeline (TwitchDownloaderCLI)
- Analysis pipeline (aggregation, graphing, community detection, tagging)
- PNG and HTML network visualizations
- Graph CSV exports for external tooling (for example Gephi)
- Storage abstraction: local file mode or AWS S3 mode
- Local development workflow plus AWS deployment scripts/runbooks

## Runtime Model

- Local: run directly with `python src/main.py <mode> [config]`
- AWS collector: `vieweratlas-collector` (always-on ECS service)
- AWS analysis: `vieweratlas-analysis` (scheduled ECS task)
- AWS VOD collector: `vieweratlas-vod-collector` (scheduled ECS task)

## Repository Structure

- `twitchiobot/`: application code, tests, config, infra scripts, docs
- `setup.sh`: interactive local helper script
- `.github/workflows/`: CI, security, and deploy preflight checks

## Quick Start (Local)

```bash
cd twitchiobot
python -m pip install -r src/requirements.txt
cp config/.env.example .env
cp channels.example.txt channels.txt
pytest -q
```

Set at least these environment variables (for collection modes):

- `TWITCH_CLIENT_ID`
- `TWITCH_OAUTH_TOKEN`

## Runtime Modes

Run from `twitchiobot/`:

```bash
python src/main.py analyze [default|rigorous|explorer|debug|config.yaml]
python src/main.py collect [default|rigorous|explorer|debug|config.yaml]
python src/main.py continuous [default|rigorous|explorer|debug|config.yaml]
python src/main.py preprocess_vods config.yaml [max_vods]
```

Notes:

- `analyze` requires existing snapshot data in local `logs/` or configured S3.
- `collect` and `continuous` require Twitch credentials.
- `collect` refreshes `channels.txt` from top Twitch channels each cycle.
- `preprocess_vods` is optional and requires `TwitchDownloaderCLI`.

## Configuration

- Preset configs: `default`, `rigorous`, `explorer`, `debug`
- Custom config: pass a YAML file (for example `config/config.yaml`)
- Env vars can override storage settings:
  - `STORAGE_TYPE=file|s3`
  - `S3_BUCKET`, `S3_PREFIX`, `S3_REGION`

## Outputs (Default Local File Mode)

When `STORAGE_TYPE=file`, outputs are written under `twitchiobot/`:

- `logs/raw/snapshots/...`: live snapshot JSON
- `logs/raw/chatter_logs/...`: per-channel chatter CSV
- `logs/curated/presence_snapshots/source=vod/...`: VOD parquet snapshots
- `community_analysis/community_graph.png`: static graph visualization
- `community_analysis/community_graph.html`: interactive graph visualization
- `community_analysis/graph_nodes.csv` and `community_analysis/graph_edges.csv`
- `logs/processed/analysis_results.json`: final analysis summary
- `logs/pipeline.log`: rotating pipeline log file

## AWS Production Flow

```bash
cd twitchiobot/infrastructure/aws
./safe-deploy.sh
./create-schedules.sh
./apply-monitoring.sh
./smoke-test.sh
```

See `twitchiobot/docs/DEPLOYMENT.md` for full setup and verification steps.

## Documentation

- Developer guide: `twitchiobot/docs/DEVELOPER.md`
- Deployment guide: `twitchiobot/docs/DEPLOYMENT.md`
- Runbook: `twitchiobot/docs/RUNBOOK.md`
- Data policy: `twitchiobot/docs/DATA_POLICY.md`

## CI and Security Gates

- `.github/workflows/ci.yml`: tests and parser/syntax checks
- `.github/workflows/security.yml`: dependency and static security checks
- `.github/workflows/deploy-preflight.yml`: manual deploy-variable validation
