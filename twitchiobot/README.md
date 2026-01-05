# ViewerAtlas: Twitch Community Detection System

A sophisticated tool for analyzing Twitch streamer communities by detecting viewer overlaps and generating beautiful network visualizations.

## 🎯 Overview

ViewerAtlas automatically:
1. **Collects** real-time chat data from top Twitch channels
2. **Aggregates** viewer information across channels
3. **Builds** a network graph based on shared audiences
4. **Detects** communities of streamers with similar viewer bases
5. **Labels** communities by game, language, and content type
6. **Visualizes** results as interactive bubble graphs

The result is a beautiful, data-driven map of Twitch's streaming ecosystem.

## 🏗️ Architecture

```
twitchiobot/
├── main.py                 # Orchestrator (collect → analyze → visualize)
├── config.py              # Configuration (4 presets: default, rigorous, explorer, debug)
├── get_viewers.py         # Twitch IRC chat collection
├── update_channels.py     # Fetch top channels via Helix API
├── data_aggregator.py     # Load & aggregate viewer data
├── graph_builder.py       # Build overlap network
├── community_detector.py  # Louvain community detection
├── cluster_tagger.py      # Generate community labels
├── visualizer.py          # Create PNG & HTML visualizations
└── requirements.txt       # Python dependencies
```

## 🚀 Quick Start

### 1. Install Dependencies

```bash
cd twitchiobot
pip install -r requirements.txt
```

### 2. Set Up Twitch API

Create a `.env` file in `twitchiobot/`:

```env
TWITCH_OAUTH_TOKEN=your_oauth_token_here
TWITCH_CLIENT_ID=your_client_id_here
```

Get tokens from [Twitch Dev Console](https://dev.twitch.tv/console).

### 3. Run Analysis

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

## 📊 Operating Modes

### Analyze (Analysis-Only)
```bash
python main.py analyze [config]
```
Processes existing data from `logs/` directory. No collection required.

**Perfect for:**
- Testing with existing data
- Tuning parameters
- Quick iterations

### Collect (Data Collection)
```bash
python main.py collect
```
Runs continuous data collection hourly. Requires Twitch API tokens.

**Perfect for:**
- Building datasets over time
- Running as a background service

### Continuous (Collection + Analysis)
```bash
python main.py continuous [config]
```
Collects data hourly, runs analysis every 24 hours.

**Perfect for:**
- Automated monthly reports
- Keeping community maps current

## ⚙️ Configuration

### Preset Configurations

**Default** (`default`)
- Balanced approach
- Low threshold (1 shared viewer = edge)
- Good for initial exploration

**Rigorous** (`rigorous`)
- TwitchAtlas-style parameters
- 300+ shared viewers required
- 10+ channel minimum per community
- Better for meaningful overlaps

**Explorer** (`explorer`)
- Fine-grained communities (resolution 2.0)
- Low thresholds
- Verbose logging
- All data included

**Debug** (`debug`)
- Small dataset (100 channels)
- Very verbose logging
- Quick testing

### Custom Configuration

Edit `config.py` to create your own:

```python
from config import PipelineConfig, CollectionConfig, AnalysisConfig

my_config = PipelineConfig(
    collection=CollectionConfig(
        top_channels_limit=1000,
        batch_size=50
    ),
    analysis=AnalysisConfig(
        overlap_threshold=50,
        resolution=1.5,
        min_community_size=5
    )
)
```

## 📈 Pipeline Steps

### [1/6] Aggregation
- Loads JSON/CSV logs from `logs/`
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
- Saves `analysis_results.json` with full output
- Exports graph data for Gephi
- Creates bubble graph visualizations

## 📁 Output Files

After analysis, check `community_analysis/`:

```
community_analysis/
├── community_graph.png           # Static visualization
├── community_graph.html          # Interactive visualization
├── analysis_results.json         # Full results
├── graph_nodes.csv              # Node attributes
└── graph_edges.csv              # Edge weights
```

## 🔍 Understanding Results

### analysis_results.json

```json
{
  "timestamp": "2026-01-05T...",
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
    "graph": {...},
    "detection": {...},
    "tagging": {...}
  }
}
```

### Interpreting Communities

Each detected community typically represents:
- **Game-based**: Streamers of same game (e.g., "Valorant (NA)")
- **Language-based**: Streamers speaking same language
- **Niche communities**: Art, music, variety, etc.
- **Cross-game**: Streamers with overlapping audiences despite different games

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

### Exporting for Gephi

```bash
# CSV files are automatically exported to community_analysis/
# Import graph_nodes.csv and graph_edges.csv into Gephi for advanced layout
```

### Custom Visualization

```python
from visualizer import Visualizer
from graph_builder import GraphBuilder

viz = Visualizer(figsize=(24, 20))
viz.visualize_static(
    graph, partition, labels,
    output_file="custom_viz.png",
    show_labels=True
)
```

## 📊 Data Collection Tips

### For Best Results:

1. **Collect over 3-5 days minimum**
   - Captures different viewing patterns
   - Identifies loyal vs. casual viewers

2. **Run hourly collections**
   - Picks up peak and off-peak viewers
   - Follows the config schedule

3. **Aim for 1000+ channels**
   - Top channels often sufficient
   - More channels = richer overlaps

4. **Exclude obvious bots**
   - Filter if you know bot usernames
   - Check data quality report

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

### python-louvain not installed

**Problem:** "python-louvain not installed" error

**Solution:**
```bash
pip install python-louvain
```

Falls back to greedy algorithm if unavailable.

## 📖 Documentation

- **[SCHEMA_AUDIT.md](../SCHEMA_AUDIT.md)** - What was built vs. original spec
- **[requirements.txt](requirements.txt)** - Python dependencies
- **[config.py](config.py)** - Configuration options and presets

## 🔮 Future Enhancements

- [ ] YouTube support (BaseCollector interface)
- [ ] SQLite storage backend
- [ ] Overlapping communities detection
- [ ] Social media integration (Twitter, Discord)
- [ ] Custom tag rules
- [ ] Performance profiling
- [ ] Scheduled cron integration
- [ ] Prometheus metrics export

## 📝 License

This project documents and implements the Streaming Community Detection approach described in the ViewerAtlas schema.

## 🙏 Acknowledgments

Based on research from:
- [TwitchAtlas](https://github.com/KiranGershenfeld/VisualizingTwitchCommunities)
- [Twitch Official Blog](https://blog.twitch.tv/en/2015/02/04/visual-mapping-of-twitch-and-our-communities-cause-science-2f5ad212c3da/)
- NetworkX and community detection libraries

---

**Questions?** Check the code docstrings or create an issue.