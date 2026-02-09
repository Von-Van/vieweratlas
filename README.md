# ViewerAtlas: Twitch Community Detection System

A sophisticated, cloud-native tool for analyzing Twitch streamer communities by detecting viewer overlaps and generating beautiful network visualizations. Now with AWS S3 integration and containerized deployment support.

**Status**: In Progess 
**Last Updated**: February 9, 2026

---

## Project Overview

ViewerAtlas is a Twitch community mapping system. It collects live chatters from top channels, builds a viewer-overlap graph, detects streamer communities, and produces both data outputs and visualizations. The project is designed to run locally for experiments and scale up to cloud deployments for larger, repeatable analyses.

## Project Goals

- Reveal meaningful streamer communities by shared audiences.
- Provide repeatable, configurable analysis with clear presets.
- Support both local workflows and cloud-scale runs (S3 + containers).
- Produce outputs that are easy to share: graphs, labels, and visuals.

## 🎯 Overview

ViewerAtlas automatically:
1. **Collects** real-time chat data from top Twitch channels
2. **Aggregates** viewer information across channels
3. **Builds** a network graph based on shared audiences
4. **Detects** communities of streamers with similar viewer bases
5. **Labels** communities by game, language, and content type
6. **Visualizes** results as interactive bubble graphs
7. **Deploys** to AWS for scalable, cloud-native operation

The result is a beautiful, data-driven map of Twitch's streaming ecosystem.

---

## 🏗️ Architecture

```
vieweratlas/
├── twitchiobot/                    # Main application
│   ├── main.py                     # Pipeline orchestrator (3 modes)
│   ├── config.py                   # Configuration (4 presets + YAML)
│   ├── config.yaml                 # Configuration template
│   ├── storage.py                  # Storage abstraction (local + S3)
│   │
│   ├── get_viewers.py              # Twitch IRC chat collection
│   ├── update_channels.py          # Fetch top channels via Helix API
│   ├── data_aggregator.py          # Load & aggregate viewer data
│   ├── graph_builder.py            # Build overlap network
│   ├── community_detector.py       # Louvain community detection
│   ├── cluster_tagger.py           # Generate community labels
│   ├── visualizer.py               # Create PNG & HTML visualizations
│   │
│   ├── Dockerfile.collector        # Collector container
│   ├── Dockerfile.analysis         # Analysis container
│   ├── docker-compose.yml          # Local testing setup
│   ├── ecs-task-collector.json     # AWS ECS task definition
│   ├── ecs-task-analysis.json      # AWS ECS task definition
│   ├── deploy.sh                   # AWS deployment script
│   │
│   └── requirements.txt            # Python dependencies
│
├── README.md                       # This file
└── vieweratlas scheme.txt          # Original specification
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Docker (optional, for containerized deployment)
- AWS CLI (optional, for cloud deployment)
- Twitch API credentials

### Local Installation

```bash
cd twitchiobot
pip install -r requirements.txt
```

### Twitch API Setup

1. Create a [Twitch Developer Application](https://dev.twitch.tv/console/apps)
2. Get your OAuth token and Client ID
3. Create `.env` file:

```bash
# Copy example
cp .env.example .env

# Edit with your credentials
TWITCH_OAUTH_TOKEN=oauth:your_token_here
TWITCH_CLIENT_ID=your_client_id_here
STORAGE_TYPE=file  # Use 'file' for local, 's3' for AWS
```

### Run Your First Analysis

```bash
# Analyze existing log data (default config)
python main.py analyze

# TwitchAtlas-style rigorous analysis (300+ overlap threshold)
python main.py analyze rigorous

# Fine-grained exploratory analysis
python main.py analyze explorer

# Debug mode (small dataset, verbose)
python main.py analyze debug
```

---

## 📊 Operating Modes

### 1. Analyze (Analysis-Only)
```bash
python main.py analyze [config]
```
Processes existing data from `logs/` directory. No collection required.

**Perfect for:**
- Testing with existing data
- Tuning parameters
- Quick iterations

### 2. Collect (Data Collection)
```bash
python main.py collect
```
Runs continuous data collection hourly. Requires Twitch API tokens.

**Perfect for:**
- Building datasets over time
- Running as a background service

### 3. Continuous (Collection + Analysis)
```bash
python main.py continuous [config]
```
Collects data hourly, runs analysis every 24 hours.

**Perfect for:**
- Automated monthly reports
- Keeping community maps current

---

## ⚙️ Configuration

### Configuration Presets

**Default** (`default`)
- Balanced approach for initial exploration
- Low threshold (1 shared viewer = edge)
- Good starting point

**Rigorous** (`rigorous`)
- TwitchAtlas-style parameters
- 300+ shared viewers required
- 10+ channel minimum per community
- Best for meaningful overlaps

**Explorer** (`explorer`)
- Fine-grained communities (resolution 2.0)
- Low thresholds, verbose logging
- All data included

**Debug** (`debug`)
- Small dataset (100 channels)
- Very verbose logging
- Quick testing

### YAML Configuration

Create custom configurations:

```bash
cp config.yaml my_analysis.yaml
```

Edit settings:

```yaml
collection:
  logs_dir: "logs"
  collection_interval: 3600
  top_channels_limit: 1000

analysis:
  overlap_threshold: 300
  resolution: 1.0
  min_community_size: 5
  game_threshold: 60
  language_threshold: 40
  output_dir: "community_analysis"

storage:
  storage_type: "file"  # or "s3"
  s3_bucket: "vieweratlas-data-lake"
  s3_prefix: "vieweratlas/"
  s3_region: "us-east-1"

log_level: "INFO"
```

Use your config:

```bash
python main.py analyze my_analysis.yaml
```

Override with environment variables:

```bash
export OVERLAP_THRESHOLD=500
export LOG_LEVEL=DEBUG
python main.py analyze my_analysis.yaml
```

---

## ☁️ AWS Cloud Deployment

### Storage Abstraction

ViewerAtlas supports both local file storage and AWS S3:

**Local Storage (Default)**
```bash
STORAGE_TYPE=file python main.py analyze
```

**AWS S3 Storage**
```bash
STORAGE_TYPE=s3 \
S3_BUCKET=vieweratlas-data-lake \
S3_PREFIX=vieweratlas/ \
S3_REGION=us-east-1 \
python main.py analyze
```

### S3 Data Lake Structure

```
s3://your-bucket/vieweratlas/
├── raw/
│   ├── snapshots/YYYY/MM/DD/          # Date-partitioned JSON snapshots
│   └── chatter_logs/YYYY/MM/DD/       # Date-partitioned CSV logs
├── processed/
│   └── analysis_results.json          # Analysis outputs
└── outputs/
    ├── community_graph.png
    └── community_graph.html
```

### Docker Deployment

**Local Testing with Docker Compose:**

```bash
# Set up environment
cp .env.example .env
# Edit .env with your credentials

# Build and run
docker-compose up

# Run in background
docker-compose up -d

# View logs
docker-compose logs -f collector
docker-compose logs -f analysis

# Stop services
docker-compose down
```

**Build Individual Containers:**

```bash
# Collector
docker build -t vieweratlas-collector -f Dockerfile.collector .
docker run -d \
  -e TWITCH_OAUTH_TOKEN=oauth:token \
  -e TWITCH_CLIENT_ID=client_id \
  -e STORAGE_TYPE=s3 \
  -e S3_BUCKET=my-bucket \
  vieweratlas-collector

# Analysis
docker build -t vieweratlas-analysis -f Dockerfile.analysis .
docker run -d \
  -e STORAGE_TYPE=s3 \
  -e S3_BUCKET=my-bucket \
  vieweratlas-analysis
```

### AWS ECS Deployment

**Automated Deployment:**

```bash
# Configure AWS credentials
aws configure

# Set environment variables
export AWS_REGION=us-east-1
export S3_BUCKET=vieweratlas-data-lake
export ECS_CLUSTER=vieweratlas-cluster  # Optional

# Deploy everything (ECR + ECS)
./deploy.sh
```

The deployment script automatically:
1. Creates ECR repositories
2. Builds Docker images
3. Pushes images to ECR
4. Registers ECS task definitions
5. Updates ECS services (if cluster specified)

**Manual ECS Setup:**

1. Create ECR repositories
2. Build and push Docker images
3. Create IAM roles with policies:
   - S3 read/write access
   - Secrets Manager access (for Twitch credentials)
   - CloudWatch Logs write access
4. Register task definitions from `ecs-task-*.json`
5. Create ECS services with Fargate

---

## 📈 Pipeline Steps

### [1/6] Aggregation
- Loads JSON/CSV logs from storage backend
- Builds `{channel: set(viewers)}` structure
- Generates data quality report
- Shows one-off vs. repeat viewers

### [2/6] Graph Building
- Computes overlaps (shared viewers between channels)
- Creates weighted graph with NetworkX
- Exports nodes/edges CSVs for Gephi
- Reports network density and statistics

### [3/6] Community Detection
- Applies Louvain modularity optimization
- Detects tightly-connected groups
- Reports modularity score
- Validates community coherence

### [4/6] Tagging
- Analyzes dominant game per community
- Detects language/region patterns
- Generates human-readable labels
- Ensures unique community names

### [5/6] Visualization
- Creates static PNG with force-directed layout
- Generates interactive HTML (PyVis)
- Color-codes nodes by community
- Sizes nodes by audience
- Thickens edges by overlap strength

### [6/6] Results
- Saves results to storage backend (local or S3)
- Exports graph data for Gephi
- Creates bubble graph visualizations

---

## 📁 Output Files

After analysis, check `community_analysis/` (or S3):

```
community_analysis/
├── community_graph.png           # Static visualization
├── community_graph.html          # Interactive visualization
├── analysis_results.json         # Full results
├── graph_nodes.csv              # Node attributes (for Gephi)
└── graph_edges.csv              # Edge weights (for Gephi)
```

### Understanding analysis_results.json

```json
{
  "timestamp": "2026-01-07T...",
  "config": {
    "overlap_threshold": 300,
    "resolution": 1.0,
    "min_channel_viewers": 10
  },
  "partition": {
    "channel_name": 0,
    ...
  },
  "labels": {
    "0": "League of Legends (English)",
    "1": "Just Chatting (Spanish)",
    ...
  },
  "statistics": {
    "graph": {
      "nodes": 250,
      "edges": 1532,
      "density": 0.049
    },
    "detection": {
      "num_communities": 12,
      "modularity": 0.73
    },
    "tagging": {
      "labeled_communities": 12
    }
  }
}
```

### Interpreting Communities

Each detected community typically represents:
- **Game-based**: Streamers of same game (e.g., "Valorant (NA)")
- **Language-based**: Streamers speaking same language
- **Niche communities**: Art, music, variety streamers
- **Cross-game**: Streamers with overlapping audiences despite different games

---

## 🛠️ Advanced Usage

### Data Filtering

Filter for repeat viewers only:

```python
from data_aggregator import DataAggregator

agg = DataAggregator("logs")
agg.load_all()

# Only include viewers who appear in 3+ channels
filtered = agg.filter_by_repeat_viewers(min_appearances=3)
```

### Quality Report

```python
quality = agg.get_data_quality_report()
print(f"One-off viewers: {quality['one_off_percentage']:.1f}%")
print(f"Repeat viewers (2+): {quality['repeat_viewers_2plus']}")
```

### Custom Visualization

```python
from visualizer import Visualizer

viz = Visualizer(figsize=(24, 20))
viz.visualize_static(
    graph, partition, labels,
    output_file="custom_viz.png",
    show_labels=True
)
```

### Storage Backends

```python
from storage import get_storage

# Auto-detect from environment
storage = get_storage()

# Or explicitly
from storage import S3Storage
storage = S3Storage(
    bucket='vieweratlas-data-lake',
    prefix='vieweratlas/',
    region='us-east-1'
)

# Upload data
storage.upload_json('processed/results.json', data)

# List files
files = storage.list_files(prefix='raw/snapshots', suffix='.json')

# Get URI
uri = storage.get_uri('outputs/graph.png')
# Returns: s3://bucket/vieweratlas/outputs/graph.png
```

---

## 📊 Best Practices

### For Best Results:

1. **Collect over 3-5 days minimum**
   - Captures different viewing patterns
   - Identifies loyal vs. casual viewers

2. **Run hourly collections**
   - Picks up peak and off-peak viewers
   - Follows the config schedule

3. **Aim for 1000+ channels**
   - Top channels usually sufficient
   - More channels = richer overlaps

4. **Exclude obvious bots**
   - Filter if you know bot usernames
   - Check data quality report

5. **Use S3 for production**
   - Scalable, durable storage
   - Enables Athena queries
   - Automatic date partitioning

---

## 🐛 Troubleshooting

### No data found in logs/

**Problem:** Analyze mode fails with "No data found"

**Solution:**
1. Run collection first: `python main.py collect`
2. Wait at least 1 hour for data
3. Check `logs/` directory exists
4. Verify JSON/CSV files were created

### Graph has no edges

**Problem:** Overlap threshold too high, no edges created

**Solution:**
1. Lower `overlap_threshold` in config
2. Start with `overlap_threshold=1`
3. Check if enough viewer overlap exists

### Poor community structure (low modularity)

**Problem:** Modularity < 0.4 indicates weak communities

**Possible causes:**
- Overlap threshold too low (includes noise)
- Insufficient data collection period
- Channels too diverse (no natural grouping)

**Solutions:**
1. Increase `overlap_threshold`
2. Increase `min_channel_viewers`
3. Filter for repeat viewers only
4. Collect more data over longer period

### S3 Access Denied

**Problem:** Cannot upload/download from S3

**Solution:**
1. Check AWS credentials are configured
2. Verify IAM role has S3 permissions
3. Confirm bucket name and region are correct
4. For ECS: Ensure task role has S3 policy attached

### Docker container exits immediately

**Problem:** Container stops right after starting

**Solution:**
1. Check logs: `docker logs <container_id>`
2. Verify environment variables are set
3. Ensure Twitch credentials are valid
4. Check health check command

---

## 📖 Features & Enhancements

### Core Features ✅
- ✅ Twitch IRC + Helix API integration
- ✅ File-based and S3 storage backends
- ✅ Network graph construction (NetworkX)
- ✅ Louvain community detection
- ✅ Automated game/language labeling
- ✅ PNG + interactive HTML visualizations
- ✅ 4 configuration presets
- ✅ YAML configuration with env overrides
- ✅ File logging with rotation (10MB, 5 backups)
- ✅ Error recovery with retry logic
- ✅ Data quality reporting
- ✅ Docker containerization
- ✅ AWS ECS deployment support
- ✅ S3 data lake with date partitioning

### Optional Future Enhancements ⏳
- [ ] YouTube support (BaseCollector interface)
- [ ] SQLite storage backend
- [ ] Lambda deployment for serverless analysis
- [ ] Terraform/CloudFormation IaC templates
- [ ] Overlapping communities detection
- [ ] Social media integration (Twitter, Discord)
- [ ] Athena query templates
- [ ] QuickSight dashboard
- [ ] Custom tag rules
- [ ] Performance profiling
- [ ] Prometheus metrics export

---

## 📚 Project Status

**Overall Status**: ✅ **PRODUCTION READY**

| Component | Status | Description |
|-----------|--------|-------------|
| Data Collector | ✅ | Twitch IRC + Helix API integration |
| Storage Layer | ✅ | Local files + AWS S3 support |
| Aggregator | ✅ | Load/filter logs, user-channel mapping |
| Graph Builder | ✅ | NetworkX overlap computation |
| Community Detector | ✅ | Louvain algorithm + greedy fallback |
| Tagger | ✅ | Automated game/language labeling |
| Visualizer | ✅ | PNG + interactive HTML output |
| Orchestrator | ✅ | PipelineRunner class (3 modes) |
| Configuration | ✅ | 4 presets + YAML + env overrides |
| File Logging | ✅ | Persistent logs with rotation |
| Error Recovery | ✅ | Retry logic, graceful failures |
| Docker Support | ✅ | Collector + analysis containers |
| AWS Deployment | ✅ | ECS task definitions + deploy script |

---

## 🔒 Security Notes

### Credentials Management

**Local Development:**
- Use `.env` file (never commit to git)
- `.env` is in `.gitignore` by default

**AWS Deployment:**
- Store Twitch credentials in AWS Secrets Manager
- Use IAM roles for service authentication
- Never hardcode credentials in task definitions
- Enable S3 encryption (SSE-AES256 by default)

### IAM Policies

**Collector Task Role:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:PutObjectAcl"
      ],
      "Resource": "arn:aws:s3:::your-bucket/vieweratlas/raw/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": "arn:aws:secretsmanager:*:*:secret:vieweratlas/*"
    }
  ]
}
```

**Analysis Task Role:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::your-bucket",
        "arn:aws:s3:::your-bucket/vieweratlas/raw/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject"
      ],
      "Resource": "arn:aws:s3:::your-bucket/vieweratlas/processed/*"
    }
  ]
}
```

---

## 🙏 Acknowledgments

Based on research from:
- [TwitchAtlas](https://github.com/KiranGershenfeld/VisualizingTwitchCommunities)
- [Twitch Official Blog](https://blog.twitch.tv/en/2015/02/04/visual-mapping-of-twitch-and-our-communities-cause-science-2f5ad212c3da/)
- NetworkX and community detection libraries

---

## 📝 License

This project documents and implements the Streaming Community Detection approach described in the ViewerAtlas schema.

---
