# ViewerAtlas: Quick Reference - Session 2 Enhancements

## 🚀 Quick Start with New Features

### File Logging (Automatic)
```bash
python main.py analyze
# Logs automatically written to:
# - Console (immediate feedback)
# - logs/pipeline.log (persistent)
```

View logs:
```bash
tail -f logs/pipeline.log        # Watch live
less logs/pipeline.log           # Browse
grep ERROR logs/pipeline.log     # Search
```

---

### YAML Configuration

**Create custom config:**
```bash
cp twitchiobot/config.yaml my_analysis.yaml
```

**Edit settings:**
```yaml
analysis:
  overlap_threshold: 300    # Increase for stricter filtering
  resolution: 1.5           # Adjust community size
  output_dir: "results"     # Custom output location
```

**Use it:**
```bash
python main.py analyze my_analysis.yaml
```

**Override with environment:**
```bash
export OVERLAP_THRESHOLD=500
export LOG_LEVEL=DEBUG
python main.py analyze my_analysis.yaml
```

---

### Error Recovery

Collection now handles failures gracefully:
```bash
python main.py collect
# Output shows:
# ✓ Successful:  92 channels
# ✗ Failed:      5 channels
# ⊘ Skipped:     3 channels
```

Failed channels are logged with reasons:
- `NOT_FOUND` - Channel doesn't exist
- `AUTH_ERROR` - Token invalid
- `MAX_RETRIES_EXHAUSTED` - Network issues after 3 retries

---

## 📁 Log Structure

```
logs/
├── pipeline.log              # Main logs (rotated at 10MB)
├── pipeline.log.1            # Backup 1
├── pipeline.log.2            # Backup 2
├── snapshots/                # JSON stream data
└── chatter_logs/             # CSV chatter data
```

---

## 🔄 Configuration Options

### Via YAML File
```yaml
collection:
  logs_dir: "logs"
  collection_interval: 3600      # Seconds

analysis:
  overlap_threshold: 1           # Min overlap
  min_community_size: 2
  resolution: 1.0                # Modularity resolution
  game_threshold: 60             # % for game label
  output_dir: "community_analysis"

log_level: "INFO"
log_format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
```

### Via Environment Variables
```bash
# Override threshold
export OVERLAP_THRESHOLD=300

# Override resolution
export RESOLUTION=2.0

# Override log level
export LOG_LEVEL=DEBUG

# Then run
python main.py analyze config.yaml
```

### Via Preset Config Names
```bash
python main.py analyze default      # Balanced
python main.py analyze rigorous     # TwitchAtlas-style
python main.py analyze explorer     # Fine-grained
python main.py analyze debug        # Verbose + small dataset
```

---

## 📊 Monitoring Pipeline

### Watch logs in real-time:
```bash
tail -f logs/pipeline.log
```

### Check collection statistics:
```bash
grep "Collection Statistics" logs/pipeline.log -A 5
```

### Monitor file size:
```bash
du -h logs/pipeline.log
# Rotates automatically at 10MB
```

### Count log files:
```bash
ls -1 logs/pipeline.log* | wc -l
# Up to 5 backup files kept
```

---

## 🛠️ Troubleshooting

### No logs appearing
```bash
# Check if logs directory exists
ls -la logs/

# Check log level
export LOG_LEVEL=DEBUG
python main.py analyze

# Check file permissions
chmod 755 logs/
```

### YAML config not found
```bash
# Ensure file exists
ls -la config.yaml

# Use absolute path
python main.py analyze /full/path/to/config.yaml

# Or relative from script directory
cd twitchiobot
python main.py analyze config.yaml
```

### API retries exhausted
```bash
# Check OAuth token
echo $TWITCH_OAUTH_TOKEN

# Verify in .env
cat .env | grep TWITCH_OAUTH_TOKEN

# Try with longer timeout (manual retry)
# System will retry up to 3 times with backoff
```

### Log file too large
```bash
# Automatic rotation happens at 10MB
# Backups kept: pipeline.log.1 through pipeline.log.5

# Manual cleanup (keep only recent)
rm logs/pipeline.log.[3-9]

# Or archive and clean
gzip logs/pipeline.log.* 
```

---

## 📈 Performance Tips

### For Large Datasets
```bash
# Use rigorous config (fewer, stronger communities)
python main.py analyze rigorous

# Or custom YAML:
analysis:
  overlap_threshold: 300
  min_community_size: 10
  resolution: 0.8
```

### For Exploration
```bash
# Use explorer config (many fine-grained communities)
python main.py analyze explorer

# Or custom YAML:
analysis:
  overlap_threshold: 1
  resolution: 2.0
```

### For Development
```bash
# Use debug config (verbose, small dataset)
python main.py analyze debug

# Or run with debug log level
export LOG_LEVEL=DEBUG
python main.py analyze default
```

---

## 🔗 File Locations

| File | Purpose | Location |
|------|---------|----------|
| Pipeline logs | Execution trace | `logs/pipeline.log` |
| Config template | Example settings | `twitchiobot/config.yaml` |
| Stream snapshots | JSON data | `logs/snapshots/*.json` |
| Chatter logs | CSV records | `logs/chatter_logs/*.csv` |
| Analysis output | Visualizations | `community_analysis/` |

---

## ✨ Examples

### Complete Analysis Workflow
```bash
# 1. Create custom config
cp twitchiobot/config.yaml analysis_config.yaml

# 2. Edit config (set thresholds)
nano analysis_config.yaml

# 3. Run analysis
python main.py analyze analysis_config.yaml

# 4. Monitor progress
tail -f logs/pipeline.log

# 5. Check results
ls -la community_analysis/
```

### Continuous Collection + Analysis
```bash
# Collect data every hour, analyze daily
python main.py continuous rigorous

# Logs go to logs/pipeline.log (rotates at 10MB)
# Results go to community_analysis/

# Stop with Ctrl+C when done
```

### Debug Mode (Development)
```bash
# Verbose logging + small dataset
export LOG_LEVEL=DEBUG
python main.py analyze debug

# Watch logs as they write
tail -f logs/pipeline.log
```

---

## 📞 Quick Commands

```bash
# Run with default settings
python main.py analyze

# Run with YAML config
python main.py analyze config.yaml

# Run with custom log level
LOG_LEVEL=DEBUG python main.py analyze

# Run with custom thresholds
OVERLAP_THRESHOLD=300 python main.py analyze

# View latest logs
tail -50 logs/pipeline.log

# Monitor real-time
tail -f logs/pipeline.log

# Search logs
grep "ERROR\|WARNING" logs/pipeline.log

# Count lines in log
wc -l logs/pipeline.log
```

---

**Version**: Session 2 Enhancements  
**Updated**: January 5, 2026  
**Status**: ✅ Production Ready
