# ViewerAtlas – Twitch Community Detection

Discover and map streaming communities by analyzing viewer overlap patterns across Twitch channels. ViewerAtlas collects chat presence data (live + VOD), builds a weighted overlap graph, and uses community detection algorithms to reveal clusters of streamers that share audiences.

## 📂 Project Structure

```
twitchiobot/
├── src/                          # Core application code
│   ├── main.py                   # Pipeline orchestrator & entry point
│   ├── config.py                 # Dataclass config system (4 presets + YAML)
│   ├── storage.py                # Storage abstraction (FileStorage / S3Storage)
│   │
│   ├── get_viewers.py            # Live chat collection via TwitchIO IRC
│   ├── update_channels.py        # Channel discovery via Helix API
│   ├── vod_collector.py          # VOD chat replay via TwitchDownloaderCLI
│   │
│   ├── data_aggregator.py        # Snapshot loading & viewer set aggregation
│   ├── graph_builder.py          # Pairwise overlap → NetworkX graph
│   ├── community_detector.py     # Louvain community detection
│   ├── cluster_tagger.py         # Community labeling (game / language)
│   ├── visualizer.py             # Static PNG + interactive HTML output
│   │
│   └── requirements.txt          # Python dependencies
│
├── tests/                        # Pytest test suite (53 tests)
│   └── test_pipeline.py
│
├── config/                       # Configuration
│   └── config.yaml               # Pipeline configuration (YAML)
│
├── infrastructure/               # Deployment
│   ├── docker/                   # Container images
│   │   ├── Dockerfile.collector  # Live collection container
│   │   ├── Dockerfile.analysis   # Analysis pipeline container
│   │   ├── Dockerfile.vod        # VOD collection container
│   │   └── docker-compose.yml    # Local multi-service setup
│   │
│   └── aws/                      # AWS infrastructure
│       ├── deploy.sh             # Automated ECR + ECS deployment
│       ├── safe-deploy.sh        # Cost-protected deployment w/ guardrails
│       ├── ecs-task-*.json       # ECS Fargate task definitions
│       ├── iam-roles.json        # IAM role templates
│       ├── eventbridge-schedules.json  # Scheduled task configs
│       ├── athena-schema.sql     # Data lake query schema
│       ├── monitoring-dashboard.yaml   # CloudWatch dashboards + alarms
│       └── SNS_SETUP.md          # Alert notification setup
│
├── logs/                         # Runtime logs (gitignored)
├── channels.txt                  # Target channels list
└── docs/
    ├── DEVELOPER.md              # Detailed developer guide
    └── PRODUCTION_UPDATES.md     # Production change log
```

## 🏗️ Architecture

### Pipeline

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Collection   │     │  Aggregation  │     │ Graph Build   │
│              │     │              │     │              │
│ TwitchIO IRC │────▶│ Load JSON /  │────▶│ Pairwise set │
│ VOD Replay   │     │ CSV / Parquet │     │ intersection │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
                     ┌──────────────┐     ┌───────▼──────┐
                     │ Visualization │     │  Community   │
                     │              │◀────│  Detection   │
                     │ PNG + HTML   │     │  (Louvain)   │
                     │ CSV export   │     │  + Tagging   │
                     └──────────────┘     └──────────────┘
```

**Data Collection** — Two complementary sources:
- **Live:** IRC chat monitoring via TwitchIO joins channels and logs unique chatters per snapshot
- **VOD:** Historical chat replay via TwitchDownloaderCLI with time-bucketed presence snapshots

**Analysis** — Five-step pipeline orchestrated by `PipelineRunner`:
1. **Aggregate** viewer presence data from all snapshot sources
2. **Build** weighted overlap graph — edge weight = shared unique viewers (NetworkX)
3. **Detect** communities via Louvain modularity optimization (python-louvain)
4. **Tag** communities with human-readable labels from game/language metadata
5. **Visualize** as static bubble graph (Matplotlib) + interactive HTML (PyVis)

**Storage** — Abstracted via `BaseStorage` → `FileStorage` (local) or `S3Storage` (AWS)

### AWS Deployment

```
┌─────────────────────────────────────────────────────────────────────┐
│                        AWS ARCHITECTURE                            │
│                                                                    │
│  ┌──────────────┐   ┌───────────────┐   ┌──────────────────────┐  │
│  │ ECR          │   │ Secrets Mgr   │   │ EventBridge          │  │
│  │ 3 repos:     │   │ twitch/       │   │ Schedules:           │  │
│  │  -collector  │   │  oauth_token  │   │  -collector: always  │  │
│  │  -analysis   │   │  client_id    │   │  -analysis: daily    │  │
│  │  -vod        │   │               │   │  -vod: every 6h      │  │
│  └──────┬───────┘   └──────┬────────┘   └──────┬───────────────┘  │
│         │                  │                    │                  │
│         ▼                  ▼                    ▼                  │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                     ECS FARGATE CLUSTER                    │   │
│  │                                                            │   │
│  │  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐  │   │
│  │  │ Collector   │  │ Analysis     │  │ VOD Collector    │  │   │
│  │  │ 0.25 vCPU   │  │ 1 vCPU       │  │ 0.5 vCPU         │  │   │
│  │  │ 512 MB      │  │ 2 GB         │  │ 1 GB             │  │   │
│  │  │ Long-running│  │ Scheduled    │  │ Scheduled        │  │   │
│  │  └──────┬──────┘  └──────┬───────┘  └──────┬───────────┘  │   │
│  └─────────┼────────────────┼─────────────────┼──────────────┘   │
│            │                │                  │                  │
│            ▼                ▼                  ▼                  │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                      S3 DATA LAKE                          │   │
│  │                                                            │   │
│  │  raw/snapshots/              ← live chat JSON  (30-day TTL)│   │
│  │  raw/vod_chat/               ← VOD downloads    (7-day TTL)│   │
│  │  curated/presence_snapshots/ ← Parquet        (90d→Glacier)│   │
│  │  curated/analysis/           ← graph + partition results   │   │
│  └──────────────────────────┬──────────────────────────────────┘   │
│                             │                                     │
│            ┌────────────────┼──────────────────┐                  │
│            ▼                ▼                   ▼                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐    │
│  │ Athena       │  │ CloudWatch   │  │ SNS Alerts           │    │
│  │ (ad-hoc SQL) │  │ Logs + Dash  │  │ Budget + Task alarms │    │
│  └──────────────┘  │ + Metrics    │  └──────────────────────┘    │
│                    └──────────────┘                               │
└─────────────────────────────────────────────────────────────────────┘
```

**Estimated monthly cost** (100 channels, 4 h/day collection, Spot pricing):

| Service | Cost |
|---|---|
| S3 storage (10 GB) | ~$0.23 |
| ECS Fargate (Spot, 4 h/day) | ~$3–5 |
| CloudWatch Logs (1 GB) | ~$0.50 |
| Secrets Manager (3 secrets) | ~$1.20 |
| Data transfer | ~$0.50 |
| **Total** | **~$5–8 / month** |

Built-in cost guardrails: AWS Budget alert at $50/month, S3 lifecycle auto-deletion, 7-day log retention, task-level runtime caps.

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- A [Twitch application](https://dev.twitch.tv/console) with OAuth token and Client ID
- (Optional) Docker, AWS CLI for cloud deployment

### Local Development

```bash
# 1. Install dependencies
cd twitchiobot/src
pip install -r requirements.txt

# 2. Set Twitch credentials
export TWITCH_OAUTH_TOKEN=oauth:your_token
export TWITCH_CLIENT_ID=your_client_id

# 3. Collect live chat data
python main.py collect

# 4. Run the analysis pipeline
python main.py analyze

# 5. Run tests
cd ..
python -m pytest tests/ -v
```

### Docker (local)

```bash
cd twitchiobot/infrastructure/docker

# Copy and fill in credentials
cp ../../config/.env.example .env

# Build and start all services
docker-compose up -d

# View logs
docker-compose logs -f collector
```

### AWS Deployment

```bash
cd twitchiobot/infrastructure/aws

# Set required variables
export AWS_REGION=us-east-1
export S3_BUCKET=your-bucket-name
export ECS_CLUSTER=vieweratlas-cluster
export ALERT_EMAIL=you@example.com

# Cost-protected deployment (recommended)
./safe-deploy.sh

# Or direct deployment
./deploy.sh
```

See [infrastructure/aws/SNS_SETUP.md](infrastructure/aws/SNS_SETUP.md) for alert configuration.

## ⚙️ Configuration

ViewerAtlas uses a **dataclass-based config system** with four built-in presets:

| Preset | Channels | Overlap Threshold | Resolution | Use Case |
|---|---|---|---|---|
| `default` | 5,000 | 1 | 1.0 | Balanced general-purpose |
| `rigorous` | 5,000 | 300 | 1.0 | High-confidence edges only |
| `explorer` | 5,000 | 1 | 2.0 | Fine-grained sub-communities |
| `debug` | 100 | 1 | 1.0 | Fast local testing |

Configuration is loaded in order of precedence: **defaults → YAML file → environment variables**.

```bash
# Run with a preset
python main.py analyze rigorous

# Or use YAML config
python main.py analyze config.yaml
```

Key settings in `config/config.yaml`:

```yaml
collection:
  batch_size: 100           # Channels per IRC batch
  top_channels_limit: 500   # Max channels to monitor
  collection_interval_minutes: 60

analysis:
  overlap_threshold: 10     # Min shared viewers for an edge
  resolution: 1.0           # Louvain resolution (higher = more communities)
  min_community_size: 3

vod:
  bucket_len_s: 300         # Presence bucket window (seconds)
  max_age_days: 30          # Only process recent VODs
  min_views: 100            # Skip low-view VODs
```

## 🧪 Testing

53 tests across 6 test classes covering the full pipeline:

```bash
python -m pytest tests/test_pipeline.py -v
```

| Test Class | Tests | Coverage |
|---|---|---|
| `TestDataAggregator` | 11 | Snapshot loading, viewer sets, filtering, quality reports |
| `TestGraphBuilder` | 11 | Edge weights, thresholds, CSV export, neighbors |
| `TestCommunityDetector` | 12 | Partitions, modularity, resolution, attributes |
| `TestClusterTagger` | 7 | Game labels, language combos, fallback labels |
| `TestConfig` | 9 | Presets, validation, YAML loading |
| `TestIntegration` | 3 | Full pipeline end-to-end with fixture data |

## 📊 Output

Each analysis run produces:

- **Community graph** — static PNG (Matplotlib) + interactive HTML (PyVis)
- **Channel overlap statistics** — node/edge counts, density, modularity
- **Community labels** — auto-generated from dominant game/language patterns
- **Exportable data** — nodes CSV, edges CSV, Parquet snapshots

## 🗺️ Roadmap

### ✅ Completed
- Fix syntax errors and duplicate code in collector / pipeline
- Fix config YAML loader bugs (phantom fields, wrong key mappings)
- Synchronize schema document with actual codebase
- Add pytest test suite (53 tests)

### 🔧 Up Next — Deployment-Ready
- [ ] Normalize metadata keys across pipeline (game_name→game, viewer_count→viewers)
- [ ] Validate and harden Docker images for all three services
- [ ] Parameterize AWS templates + add `.env.example` for all variables
- [ ] Create step-by-step `DEPLOYMENT.md` guide
- [ ] Wire env var overrides for all config fields (containerized use)
- [ ] Integrate CloudWatch custom metrics into pipeline code

### 📈 Short-Term Improvements
- [ ] Helix "Get Chatters" endpoint as supplementary data source
- [ ] Repeat-viewer edge weighting (loyalty scoring)
- [ ] Application-layer data retention and cleanup
- [ ] Bot detection and filtering (blocklist + heuristics)

### 🔭 Medium-Term
- [ ] CI/CD pipeline (GitHub Actions → ECR → ECS)
- [ ] Web-based interactive visualization dashboard
- [ ] Temporal community tracking (evolution over time)
- [ ] Leiden algorithm option (faster, better-connected communities)
- [ ] Athena integration for ad-hoc SQL queries on the data lake

### 🌐 Long-Term Vision
- [ ] Multi-platform support (YouTube, Kick)
- [ ] Cross-platform community detection
- [ ] Overlapping community detection
- [ ] Scalability to full Twitch (MinHash, sparse matrices, distributed graph)

## 📖 Documentation

- [Developer Guide](docs/DEVELOPER.md) — Implementation details and module APIs
- [Production Updates](docs/PRODUCTION_UPDATES.md) — Production change log
- [AWS SNS Setup](infrastructure/aws/SNS_SETUP.md) — Alert notification configuration
- [Athena Schema](infrastructure/aws/athena-schema.sql) — Data lake query examples

## 📝 License

MIT License — See LICENSE file for details.
