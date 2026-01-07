# ViewerAtlas - Twitch Community Detection

Discover and analyze streaming communities through viewer overlap patterns.

## 📂 Project Structure

```
twitchiobot/
├── src/                          # Core application code
│   ├── main.py                   # Pipeline orchestrator
│   ├── config.py                 # Configuration system
│   ├── storage.py                # Storage abstraction (file/S3)
│   │
│   ├── get_viewers.py            # Live chat collection
│   ├── update_channels.py        # Channel discovery
│   ├── vod_collector.py          # VOD chat replay ingestion
│   │
│   ├── data_aggregator.py        # Data loading & aggregation
│   ├── graph_builder.py          # Overlap network construction
│   ├── community_detector.py     # Louvain community detection
│   ├── cluster_tagger.py         # Community labeling
│   ├── visualizer.py             # Visualization generation
│   │
│   └── requirements.txt          # Python dependencies
│
├── config/                       # Configuration templates
│   ├── config.yaml               # Pipeline configuration
│   └── .env.example              # Environment variables
│
├── infrastructure/               # AWS & Docker deployment
│   ├── docker/                   # Docker configurations
│   │   ├── Dockerfile.collector  # Live collection container
│   │   ├── Dockerfile.analysis   # Analysis pipeline container
│   │   ├── Dockerfile.vod        # VOD collection container
│   │   └── docker-compose.yml    # Local multi-service setup
│   │
│   └── aws/                      # AWS infrastructure configs
│       ├── deploy.sh             # Automated deployment script
│       ├── ecs-task-*.json       # ECS Fargate task definitions
│       ├── iam-roles.json        # IAM role templates
│       ├── eventbridge-schedules.json  # Scheduled task configs
│       ├── athena-schema.sql     # Data warehouse schema
│       └── monitoring-dashboard.yaml   # CloudWatch dashboards
│
├── logs/                         # Runtime logs (gitignored)
├── vod_raw/                      # Raw VOD chat files (gitignored)
├── community_analysis/           # Analysis outputs (gitignored)
│
├── channels.txt                  # Target channels list
├── README.md                     # This file
└── docs/                         # Additional documentation
    └── DEVELOPER.md              # Detailed developer guide
```

## 🚀 Quick Start

### Local Development

```bash
# 1. Install dependencies
cd src
pip install -r requirements.txt

# 2. Configure credentials
cp ../config/.env.example ../.env
# Edit .env with your Twitch credentials

# 3. Run data collection
python main.py collect

# 4. Run analysis
python main.py analyze
```

### Docker Deployment

```bash
# Build and run with Docker Compose
cd infrastructure/docker
docker-compose up -d

# View logs
docker-compose logs -f
```

### AWS Deployment

```bash
# Deploy to ECS
cd infrastructure/aws
export AWS_REGION=us-east-1
export S3_BUCKET=your-bucket-name
export ECS_CLUSTER=vieweratlas-cluster
export EFS_ID=fs-xxxxx
./deploy.sh
```

## 📖 Documentation

- **[Developer Guide](docs/DEVELOPER.md)** - Detailed technical documentation
- **[Configuration Guide](config/config.yaml)** - All configuration options
- **[AWS Setup](infrastructure/aws/)** - Cloud deployment instructions

## 🏗️ Architecture

**Data Collection:**
- Live: IRC chat monitoring via TwitchIO
- VOD: Historical chat replay via TwitchDownloaderCLI

**Storage:**
- Local: Filesystem (JSON/Parquet)
- Cloud: AWS S3 with encryption

**Analysis:**
1. Aggregate viewer presence data
2. Build weighted overlap graph (NetworkX)
3. Detect communities (Louvain algorithm)
4. Tag communities (game/language patterns)
5. Visualize (static PNG + interactive HTML)

**Deployment:**
- Docker containers for each component
- ECS Fargate tasks with auto-scaling
- EventBridge scheduling for periodic runs
- CloudWatch monitoring & alerting

## 🔧 Key Features

- **Multi-source ingestion:** Live chat + VOD replay
- **Storage flexibility:** Local files or S3
- **Scalable:** Handles 1000+ channels
- **Cloud-native:** Full AWS integration
- **Monitoring:** CloudWatch dashboards & alarms
- **Configurable:** Multiple analysis presets

## 📊 Output

- Community graph visualizations (PNG, HTML)
- Channel overlap statistics
- Community labels & metadata
- Exportable data (CSV, Parquet)

## 🤝 Contributing

See [docs/DEVELOPER.md](docs/DEVELOPER.md) for implementation details.

## 📝 License

MIT License - See LICENSE file for details.
