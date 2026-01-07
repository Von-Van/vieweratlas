# ViewerAtlas: Developer Guide

This directory contains the ViewerAtlas application code.

**For comprehensive documentation, see [../README.md](../README.md)**

This guide provides technical implementation details for developers.

---

## 📁 Module Overview

```
twitchiobot/
├── main.py                 # Pipeline orchestrator (collect → analyze → visualize)
├── config.py              # Configuration system with 4 presets
├── storage.py             # Storage abstraction (local files + AWS S3)
│
├── get_viewers.py         # Twitch IRC chat collection with retry logic
├── update_channels.py     # Fetch top channels via Helix API
├── vod_collector.py       # VOD chat replay ingestion (TwitchDownloaderCLI)
├── data_aggregator.py     # Load & aggregate viewer data from storage
├── graph_builder.py       # Build overlap network with NetworkX
├── community_detector.py  # Louvain community detection
├── cluster_tagger.py      # Generate community labels (game/language)
├── visualizer.py          # Create PNG & HTML visualizations
│
├── Dockerfile.collector   # Container for data collection
├── Dockerfile.analysis    # Container for analysis pipeline
├── Dockerfile.vod         # Container for VOD preprocessing
├── docker-compose.yml     # Local Docker testing setup
├── ecs-task-*.json        # AWS ECS Fargate task definitions
├── deploy.sh              # Automated AWS deployment script
│
├── config.yaml            # Configuration template
├── .env.example           # Environment variables template
└── requirements.txt       # Python dependencies
```

---

## 🔧 Implementation Details

### Storage Abstraction (storage.py)

The storage module provides a unified interface for both local file system and AWS S3:

**Base Interface:**
```python
class BaseStorage(ABC):
    @abstractmethod
    def upload_json(self, key: str, data: dict) -> str:
        """Upload JSON data to storage"""
    
    @abstractmethod
    def download_json(self, key: str) -> dict:
        """Download JSON data from storage"""
    
    @abstractmethod
    def list_files(self, prefix: str = "", suffix: str = "") -> list[str]:
        """List files matching prefix and suffix"""
```

**Implementations:**
- `FileStorage`: Local filesystem (backward compatible)
- `S3Storage`: AWS S3 with encryption, pagination, retry logic

**Usage:**
```python
from storage import get_storage

# Auto-detect from environment
storage = get_storage()

# Upload with date partitioning
storage.upload_json('raw/snapshots/2026/01/07/channel_123.json', data)

# List files
files = storage.list_files(prefix='raw/snapshots', suffix='.json')
```

---

## 📊 Pipeline Implementation

The analysis pipeline executes in 6 steps:

**1. Aggregation** (`data_aggregator.py`)
- Loads JSON/CSV logs from storage backend
- Loads VOD presence snapshots (Parquet or JSON)
- Builds `{channel: set(viewers)}` structure
- Generates data quality report with source breakdown

**2. Graph Building** (`graph_builder.py`)
- Computes overlaps (shared viewers between channels)
- Creates weighted graph with NetworkX
- Exports nodes/edges CSVs for Gephi

**3. Community Detection** (`community_detector.py`)
- Applies Louvain modularity optimization
- Detects tightly-connected groups
- Reports modularity score

**4. Tagging** (`cluster_tagger.py`)
- Analyzes dominant game per community
- Detects language/region patterns
- Generates human-readable labels

**5. Visualization** (`visualizer.py`)
- Creates static PNG with force-directed layout
- Generates interactive HTML (PyVis)
- Color-codes nodes by community

**6. Results** (`main.py`)
- Saves results to storage backend (local or S3)
- Exports graph data for Gephi

---

## 🎥 VOD Collection (Optional)

VOD (Video On Demand) collection supplements live data with historical chat replay.

### Overview

**Purpose:** Ingest historical VOD chat messages as time-bucketed presence snapshots compatible with live collection.

**Pipeline:** VOD Queue → Download (TwitchDownloaderCLI) → Parse → Bucketize (60s windows) → Write Parquet Snapshots

**Output Format:** Canonical `PresenceSnapshot` records with `source=vod`, stored as Parquet under:
```
curated/presence_snapshots/source=vod/channel=<login>/vod=<id>/part-0000.parquet
```

### Setup

1. **Install TwitchDownloaderCLI:**
   ```bash
   # macOS/Linux
   wget https://github.com/lay295/TwitchDownloader/releases/latest/download/TwitchDownloaderCLI-LinuxX64.zip
   unzip TwitchDownloaderCLI-LinuxX64.zip
   chmod +x TwitchDownloaderCLI
   sudo mv TwitchDownloaderCLI /usr/local/bin/
   ```

2. **Enable in config.yaml:**
   ```yaml
   vod:
     enabled: true
     bucket_len_s: 60  # Must match live collection window
     cli_path: "/usr/local/bin/TwitchDownloaderCLI"
     auto_discover: true
     vod_limit_per_channel: 5
   ```

3. **Set Twitch credentials:**
   ```bash
   export TWITCH_CLIENT_ID=your_client_id
   export TWITCH_OAUTH_TOKEN=oauth:your_token
   ```

### Usage

**Automatic discovery and processing:**
```bash
# Auto-discover VODs from channels.txt and process queue
python main.py preprocess_vods config.yaml

# Process up to 10 VODs
python main.py preprocess_vods config.yaml 10
```

**Manual VOD management:**
```bash
# Add specific VOD
python vod_collector.py add --vod-id 1234567890 --channel xqc

# Discover recent VODs
python vod_collector.py discover --channels-file channels.txt --vod-limit 5

# Process queue
python vod_collector.py process --max-vods 10

# Check queue status
python vod_collector.py stats
```

### PresenceSnapshot Format

VOD snapshots are bucketized into 60-second windows and stored with canonical fields:

```json
{
  "platform": "twitch",
  "source": "vod",
  "channel": "xqc",
  "content_id": "vod:1234567890",
  "bucket_len_s": 60,
  "offset_s": 120,
  "chatters": ["user1", "user2", "user3"],
  "viewer_count": 3,
  "timestamp": "vod:1234567890_offset_120"
}
```

### Filtering

VOD processing includes automatic filtering to focus on relevant content:
- **Age filter:** Only processes VODs from the last 14 days (configurable via `max_age_days`)
- **View count filter:** Optional minimum view count threshold (default: 0)
- **Channel filter:** Only discovers VODs from channels in your collection list (channels.txt or top channels)

```yaml
vod:
  max_age_days: 14  # Only VODs from last 2 weeks
  min_views: 100    # Only VODs with 100+ views
```

### Retry & Backoff

VOD processing includes exponential backoff for transient failures:
- **Max attempts:** 5
- **Backoff:** 30s, 60s, 120s, 240s, 480s
- **Lease duration:** 15 minutes (prevents duplicate processing)
- **Stale lease recovery:** Automatically releases expired leases

### Integration

VOD snapshots are automatically loaded during analysis:
```python
aggregator = DataAggregator("logs", storage=storage)
json_count, csv_count, vod_count = aggregator.load_all()
# VOD data merged with live data seamlessly
```

---

## 🔬 Configuration System

### Configuration Classes

```python
@dataclass
class CollectionConfig:
    logs_dir: str = "logs"
    collection_interval: int = 3600  # seconds
    top_channels_limit: int = 1000
    batch_size: int = 100

@dataclass
class AnalysisConfig:
    overlap_threshold: int = 1
    min_channel_viewers: int = 10
    min_community_size: int = 2
    resolution: float = 1.0
    game_threshold: float = 0.60
    language_threshold: float = 0.40
    output_dir: str = "community_analysis"

@dataclass
class PipelineConfig:
    collection: CollectionConfig
    analysis: AnalysisConfig
    storage_type: str = "file"  # "file" or "s3"
    s3_bucket: Optional[str] = None
    s3_prefix: str = "vieweratlas/"
    s3_region: str = "us-east-1"
    log_level: str = "INFO"
```

### Configuration Presets

**Default** - Balanced approach (overlap_threshold=1)  
**Rigorous** - TwitchAtlas-style (overlap_threshold=300, min_community_size=10)  
**Explorer** - Fine-grained (resolution=2.0, verbose logging)  
**Debug** - Small dataset (100 channels, very verbose)

### YAML Configuration

```yaml
collection:
  logs_dir: "logs"
  collection_interval: 3600

analysis:
  overlap_threshold: 300
  resolution: 1.0
  min_community_size: 5

storage:
  storage_type: "s3"
  s3_bucket: "vieweratlas-data-lake"
  s3_prefix: "vieweratlas/"
  s3_region: "us-east-1"

log_level: "INFO"
```

Load from YAML:
```python
from config import load_config_from_yaml
config = load_config_from_yaml("config.yaml")
```

---

## 🐳 Docker Implementation

### Collector Container

**Dockerfile.collector:**
- Base: `python:3.11-slim`
- Runs `get_viewers.py` continuously
- Health check: Verifies logs directory

**Build and run:**
```bash
docker build -t vieweratlas-collector -f Dockerfile.collector .
docker run -d \
  -e TWITCH_OAUTH_TOKEN=oauth:token \
  -e STORAGE_TYPE=s3 \
  vieweratlas-collector
```

### Analysis Container

**Dockerfile.analysis:**
- Base: `python:3.11-slim`
- Includes matplotlib/networkx dependencies
- Runs `main.py analyze` pipeline

**Build and run:**
```bash
docker build -t vieweratlas-analysis -f Dockerfile.analysis .
docker run -d \
  -e STORAGE_TYPE=s3 \
  vieweratlas-analysis
```

### Docker Compose

Local testing with both services:
```bash
cp .env.example .env
# Edit .env with credentials
docker-compose up -d
docker-compose logs -f
```

**Services:**
- `collector`: Live chat collection
- `analysis`: Community detection pipeline
- `vod-collector`: VOD preprocessing (optional)

---

## ☁️ AWS Deployment

### IAM Roles

VOD collector requires two IAM roles (see [iam-roles.json](iam-roles.json)):

**Task Role** (`vieweratlas-vod-collector-task-role`):
- S3 read/write access to vieweratlas/* prefix
- CloudWatch Logs write access

**Execution Role** (`vieweratlas-vod-collector-execution-role`):
- ECR image pull permissions
- Secrets Manager access for Twitch credentials
- CloudWatch Logs creation

```bash
# Create roles using provided templates
aws iam create-role --role-name vieweratlas-vod-collector-task-role \
  --assume-role-policy-document file://iam-roles.json

aws iam create-role --role-name vieweratlas-vod-collector-execution-role \
  --assume-role-policy-document file://iam-roles.json
```

### EventBridge Scheduling

Schedule VOD processing runs (see [eventbridge-schedules.json](eventbridge-schedules.json)):

**Daily at 2 AM UTC:**
```bash
aws events put-rule --name vieweratlas-vod-daily \
  --schedule-expression 'cron(0 2 * * ? *)' \
  --description 'Run VOD collector daily'
```

**Every 6 hours:**
```bash
aws events put-rule --name vieweratlas-vod-hourly \
  --schedule-expression 'rate(6 hours)'
```

### Athena/Glue Queries

Query curated VOD snapshots using Athena (see [athena-schema.sql](athena-schema.sql)):

```sql
-- Create external table
CREATE EXTERNAL TABLE vieweratlas_vod_snapshots (...)
STORED AS PARQUET
LOCATION 's3://your-bucket/vieweratlas/curated/presence_snapshots/';

-- Query VOD statistics
SELECT channel, COUNT(*) as vod_count
FROM vieweratlas_vod_snapshots
WHERE source = 'vod'
GROUP BY channel;
```

### Monitoring

CloudWatch dashboard configuration in [monitoring-dashboard.yaml](monitoring-dashboard.yaml):

**Key Metrics:**
- VOD queue status (pending/processing/completed/failed)
- Processing rate and duration
- Backoff attempt distribution
- ECS task CPU/memory utilization

**Alarms:**
- High queue backlog (>1000 pending)
- High failure rate (>20%)
- Processing stalled (no VODs in 6 hours)

### ECS Task Definitions

**ecs-task-collector.json:**
- Fargate task for continuous collection
- Secrets Manager integration for Twitch credentials
- CloudWatch Logs integration

**ecs-task-analysis.json:**
- Fargate task for analysis pipeline
- Larger resources (1024 CPU, 2048 MB)
- S3 read/write permissions

**ecs-task-vod-collector.json:**
- Fargate task for VOD preprocessing
- TwitchDownloaderCLI bundled in image
- EFS mount for shared VOD queue
- EventBridge scheduling (hourly/daily)

### Deployment Script

**deploy.sh** automates:
1. ECR repository creation (collector, analysis, vod)
2. Docker image builds
3. Image push to ECR
4. ECS task definition registration
5. ECS service updates

**Usage:**
```bash
export AWS_REGION=us-east-1
export S3_BUCKET=vieweratlas-data-lake
export ECS_CLUSTER=vieweratlas-cluster
export EFS_ID=fs-12345678  # For VOD queue sharing
./deploy.sh
```

---

## 🧪 Testing

### Storage Backend Tests

```bash
python storage.py
```

### Module Tests

```python
from data_aggregator import DataAggregator
from graph_builder import GraphBuilder

# Test aggregation
agg = DataAggregator("logs")
agg.load_all()
print(agg.get_data_quality_report())

# Test graph building
builder = GraphBuilder(overlap_threshold=50)
graph = builder.build_graph(agg.channel_viewers)
print(f"Nodes: {graph.number_of_nodes()}")
print(f"Edges: {graph.number_of_edges()}")
```

### Integration Test

```bash
python main.py analyze debug
ls -lh community_analysis/
```

---

## 📈 Performance Considerations

### Data Collection
- **Batch size**: 100 channels per batch (configurable)
- **Retry logic**: 3 attempts with exponential backoff (1s, 2s, 4s)
- **Collection interval**: 3600s (1 hour) default

### Analysis
- **Memory usage**: ~500MB for 1000 channels
- **Computation time**: ~2-5 minutes for 1000 channels
- **Graph density**: Target 0.01-0.10 for good performance
- **Community detection**: O(n log n) with Louvain

### S3 Storage
- **Uploads**: SSE-AES256 encryption automatic
- **Date partitioning**: Enables efficient Athena queries
- **List operations**: Paginated (1000 objects per page)
- **Retry logic**: 3 attempts for transient failures

---

## 🛠️ Extending ViewerAtlas

### Adding New Storage Backends

Implement the `BaseStorage` interface:

```python
from storage import BaseStorage

class MyStorage(BaseStorage):
    def upload_json(self, key: str, data: dict) -> str:
        # Implementation
        pass
    
    def download_json(self, key: str) -> dict:
        # Implementation
        pass
    
    # ... implement other required methods
```

Register in `get_storage()`:
```python
def get_storage(storage_type=None, **kwargs):
    if storage_type == 'mystorage':
        return MyStorage(**kwargs)
    # ... existing logic
```

### Custom Community Detection

Replace Louvain with your algorithm:

```python
from community_detector import CommunityDetector

class MyCommunityDetector(CommunityDetector):
    def detect_communities(self, graph):
        partition = {}  # {node: community_id}
        modularity = self.calculate_modularity(graph, partition)
        return partition, modularity
```

### Custom Visualization

```python
from visualizer import Visualizer

class MyVisualizer(Visualizer):
    def visualize_3d(self, graph, partition, labels):
        # 3D visualization implementation
        pass
```

---

## 📚 API Reference

### PipelineRunner

```python
class PipelineRunner:
    def __init__(self, config: PipelineConfig):
        """Initialize pipeline with configuration"""
    
    def run_collection_cycle(self) -> None:
        """Run one collection cycle"""
    
    def run_analysis_pipeline(self, config_name: str = "default") -> dict:
        """Run full analysis pipeline, return results"""
    
    def run_continuous(self, config_name: str = "default") -> None:
        """Run continuous collection + analysis"""
```

### DataAggregator

```python
class DataAggregator:
    def __init__(self, logs_dir: str, storage: Optional[BaseStorage] = None):
        """Initialize with logs directory or storage backend"""
    
    def load_all(self, filter_by_repeat: bool = False) -> None:
        """Load all snapshots and chatter logs"""
    
    def filter_by_repeat_viewers(self, min_appearances: int = 2) -> dict:
        """Filter for viewers appearing in N+ channels"""
    
    def get_data_quality_report(self) -> dict:
        """Get statistics about data quality"""
```

### GraphBuilder

```python
class GraphBuilder:
    def __init__(self, overlap_threshold: int = 1):
        """Initialize with minimum overlap threshold"""
    
    def build_graph(self, channel_viewers: dict) -> nx.Graph:
        """Build weighted overlap graph"""
    
    def export_for_gephi(self, graph: nx.Graph, output_dir: str) -> None:
        """Export nodes and edges CSVs for Gephi"""
```

---

## 🔄 Change Log

### Session 4 (January 7, 2026) - VOD Collection
- ✅ Implemented VOD chat replay ingestion (vod_collector.py)
- ✅ Added PresenceSnapshot canonical format for live/VOD parity
- ✅ Parquet-based curated snapshot storage
- ✅ Aggregator support for VOD snapshots (JSON + Parquet)
- ✅ Exponential backoff + lease-based queue management
- ✅ TwitchDownloaderCLI integration
- ✅ Dockerfile.vod + ECS task definition
- ✅ Deploy script updated for VOD infrastructure
- ✅ Config validation for bucket window consistency
- ✅ VOD age filtering (14-day default) + view count threshold
- ✅ IAM roles and EventBridge scheduling templates
- ✅ Athena schema for querying curated snapshots
- ✅ CloudWatch monitoring dashboard configuration

### Session 3 (January 7, 2026)
- ✅ Added storage abstraction layer (storage.py)
- ✅ Implemented S3 backend with encryption
- ✅ Added Docker containerization
- ✅ Created ECS task definitions
- ✅ Built automated deployment script
- ✅ Added date partitioning for S3

### Session 2 (January 5, 2026)
- ✅ Added file logging with rotation
- ✅ Implemented error recovery with retry logic
- ✅ Added YAML configuration support
- ✅ Environment variable overrides

### Session 1 (Original Implementation)
- ✅ Core pipeline implementation
- ✅ Twitch API integration
- ✅ Community detection
- ✅ Visualization
- ✅ Configuration presets

---

## 📖 Additional Resources

- **Main README**: [../README.md](../README.md) - Complete user guide
- **Original Spec**: [../vieweratlas scheme.txt](../vieweratlas%20scheme.txt) - Project specification
- **NetworkX**: https://networkx.org/
- **Louvain Algorithm**: https://python-louvain.readthedocs.io/
- **TwitchIO**: https://twitchio.dev/

---

**For usage instructions and troubleshooting, see [../README.md](../README.md)**
