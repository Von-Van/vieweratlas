"""
SCHEMA COMPLIANCE AUDIT & CLEANUP PLAN

This document reviews the vieweratlas scheme.txt against current implementation
and identifies gaps, improvements, and cleanup opportunities.
"""

# ============================================================================
# 1. DATA COLLECTOR MODULE
# ============================================================================

STATUS: ✓ Implemented (get_viewers.py, update_channels.py)

CURRENT:
- Uses twitchio IRC library for chat collection
- Fetches top channels via Twitch Helix API
- Async/await pattern for concurrent collection
- Saves to JSON/CSV per batch

SCHEMA REQUIREMENTS:
✓ Use official Twitch Helix API
✓ Fetch chat participants (via IRC)
✓ Loop/schedule functionality
✓ Output {channel: set(users)} structure

GAPS/IMPROVEMENTS:
1. No error recovery on failed channels (should skip and continue)
2. No rate limiting handling
3. No duplicate detection (same channel in different batches)
4. No retry logic for failed connections
5. Limited logging of collection statistics
6. No metadata caching (game/title/viewers refetch each time)

RECOMMENDATION:
- Add error handling wrapper
- Add collection statistics tracking
- Add retry logic with exponential backoff
- Cache metadata between runs


# ============================================================================
# 2. DATA STORAGE
# ============================================================================

STATUS: ✓ Partial (JSON/CSV, no database option)

CURRENT:
- JSON snapshots per batch
- CSV logs of chatters
- File-based storage only

SCHEMA REQUIREMENTS:
✓ Can store {channel: set(users)} 
✓ Persistence across runs
- "Abstracted behind interface so switching storage doesn't affect pipeline"

GAPS/IMPROVEMENTS:
1. No SQLite option (mentioned in schema as alternative)
2. No database interface/abstraction layer
3. No data export/import utilities
4. No data cleanup/archival tools
5. No query interface for analysis

RECOMMENDATION:
- Create storage abstraction: `BaseStorage` interface
- Implement `FileStorage` (current)
- Implement `SQLiteStorage` as alternative
- Add data export utilities


# ============================================================================
# 3. GRAPH BUILDER MODULE
# ============================================================================

STATUS: ✓ Fully Implemented (graph_builder.py)

CURRENT:
✓ NetworkX graph creation
✓ Edge weight = shared viewers count
✓ Threshold filtering
✓ Node attributes (viewers, game, title)
✓ Statistics reporting
✓ CSV export for Gephi

SCHEMA REQUIREMENTS:
✓ Set intersection for overlap computation
✓ Weighted undirected graph
✓ Threshold filtering
✓ Node attributes
✓ Graph statistics

GAPS/IMPROVEMENTS:
1. Document mentions "weighting for repeat viewers" - not implemented
   (could weight edges by loyalty score, not just count)
2. No optimization for large graphs mentioned
3. Could add edge filtering by percentile (remove weakest X%)
4. No incremental graph updates (always rebuilds)

RECOMMENDATION:
- Optional: Add loyalty weighting mode (minimum visit days)
- Document current simplification from schema
- Add percentile-based edge filtering


# ============================================================================
# 4. COMMUNITY DETECTION MODULE
# ============================================================================

STATUS: ✓ Fully Implemented (community_detector.py)

CURRENT:
✓ Louvain algorithm via python-louvain
✓ Modularity calculation
✓ Resolution parameter tuning
✓ Greedy fallback if library unavailable
✓ Hard partition (non-overlapping)

SCHEMA REQUIREMENTS:
✓ Modularity-based clustering
✓ Resolution parameter
✓ Hard partition approach
✓ Community validation capability

GAPS/IMPROVEMENTS:
1. No validation checks mentioned in schema
   (do communities make sense? same game? language?)
2. Greedy fallback is naive (should be better)
3. No visualization of modularity over resolution ranges
4. No detection of degenerate communities (size 1)
5. Could warn if modularity is very low (poor community structure)

RECOMMENDATION:
- Add validation report (game/language coherence checks)
- Add modularity quality checks with warnings
- Filter out degenerate communities automatically


# ============================================================================
# 5. CLUSTER TAGGING MODULE
# ============================================================================

STATUS: ✓ Fully Implemented (cluster_tagger.py)

CURRENT:
✓ Dominant game detection (60% threshold)
✓ Language tagging
✓ Combination tags (game + language)
✓ Fallback to variety/uncategorized
✓ Reasoning output

SCHEMA REQUIREMENTS:
✓ Automated tagging by dominant attributes
✓ Game/category detection
✓ Language/region detection
✓ Human-readable labels
✓ No duplicate labels

GAPS/IMPROVEMENTS:
1. Schema mentions "Top Streamer or Team" labeling - not implemented
   (could detect clusters centered around big streamers)
2. No support for custom tag rules
3. No tag validation (ensure uniqueness)
4. Thresholds hardcoded (60%, 40%) - should be configurable
5. No handling of non-English games

RECOMMENDATION:
- Add top-streamer detection option
- Make tag thresholds configurable
- Add tag uniqueness validation
- Document language detection limitations


# ============================================================================
# 6. VISUALIZATION MODULE
# ============================================================================

STATUS: ✓ Fully Implemented (visualizer.py)

CURRENT:
✓ Matplotlib static visualization (PNG)
✓ PyVis interactive visualization (HTML)
✓ Force-directed layout (spring_layout)
✓ Color coding by community
✓ Node size by viewer count
✓ Edge thickness by weight
✓ Top-N node labeling
✓ Legend with community labels

SCHEMA REQUIREMENTS:
✓ Bubble graph with community coloring
✓ Force-directed layout
✓ Node size = audience size
✓ Edge weight visualization (thickness)
✓ Interactive visualization option
✓ Gephi export option (via CSV)
✓ Labeling strategy (large nodes only)

GAPS/IMPROVEMENTS:
1. Visualization sometimes cluttered with edges
   (schema mentions "apply edge threshold for visualization")
2. No edge percentile filtering (only weight threshold)
3. PyVis physics not fully configured (could improve separation)
4. No zoom/pan instructions in output
5. No legend placement optimization for large graphs
6. Schema suggests "ForceAtlas2 or Fruchterman-Reingold" - using spring_layout
7. No layout file export (for Gephi users)

RECOMMENDATION:
- Add edge percentile filtering mode
- Improve PyVis physics parameters
- Export layout coordinates to CSV
- Add Gephi GEXF export option
- Add instructions/README to visualizations


# ============================================================================
# 7. ORCHESTRATION & SCHEDULING
# ============================================================================

STATUS: ✓ Fully Implemented (main.py with PipelineRunner)

CURRENT:
✓ Three modes: collect, analyze, continuous
✓ Config-driven execution
✓ Pre-flight validation
✓ 6-step pipeline with logging
✓ Periodic analysis trigger (every 24 hours)

SCHEMA REQUIREMENTS:
✓ Initial/one-time analysis
✓ Continuous update capability
✓ Periodic recomputation
✓ Scheduler integration

GAPS/IMPROVEMENTS:
1. No actual cron/scheduler integration (just loop with wait)
2. No data collection history tracking (how much data collected so far?)
3. No pipeline state persistence (if interrupted, can't resume)
4. No notification/alert system
5. No performance metrics tracking (time per step)
6. Schema mentions "adjust frequency based on data volume"

RECOMMENDATION:
- Add pipeline state tracking/checkpointing
- Add performance metrics collection
- Document how to integrate with cron/systemd
- Add data volume tracking


# ============================================================================
# 8. LOGGING & MONITORING
# ============================================================================

STATUS: ✓ Partial (good logging, limited monitoring)

CURRENT:
✓ Structured logging with timestamps
✓ Multiple log levels
✓ Per-step logging in pipeline
✓ Statistics reporting
✓ Error tracking

SCHEMA REQUIREMENTS:
✓ Track progress
✓ Log issues
✓ Basic stats (channels, communities, time)

GAPS/IMPROVEMENTS:
1. No structured logging to file (only console)
2. No metrics export (JSON, Prometheus, etc.)
3. No performance benchmarks
4. No data quality monitoring over time
5. No alerts for anomalies
6. Limited debugging output for failures
7. No pipeline timing breakdown

RECOMMENDATION:
- Add file logging
- Add metrics JSON export
- Add performance profiling
- Add data quality trend tracking


# ============================================================================
# 9. CONFIGURATION
# ============================================================================

STATUS: ✓ Fully Implemented (config.py)

CURRENT:
✓ CollectionConfig
✓ AnalysisConfig
✓ PipelineConfig
✓ 4 preset configs (default, rigorous, explorer, debug)
✓ Validation in __post_init__
✓ Configuration as dataclass (clean, typed)

SCHEMA REQUIREMENTS:
✓ Single configuration object
✓ All parameters in one place
✓ Easy to adjust without code changes
✓ Preset configurations

GAPS/IMPROVEMENTS:
1. No config file loading (YAML/JSON) - all hardcoded in code
2. No environment variable overrides
3. No config validation rules (min/max values)
4. No config versioning (for compatibility)
5. No documentation of each config parameter

RECOMMENDATION:
- Add YAML config file support
- Add env var override capability
- Add comprehensive docstrings
- Add example config file


# ============================================================================
# 10. DOCUMENTATION & CODE QUALITY
# ============================================================================

STATUS: ✗ Limited

CURRENT:
- Module docstrings
- Function docstrings
- Inline comments
- README.md (minimal)

SCHEMA REQUIREMENTS:
- Clear documentation for users and developers

GAPS/IMPROVEMENTS:
1. No user guide/tutorial
2. No API documentation
3. No troubleshooting guide
4. No performance tuning guide
5. No architecture diagram
6. No examples of config usage
7. Limited README

RECOMMENDATION:
- Create comprehensive README
- Create USER_GUIDE.md
- Create DEVELOPER_GUIDE.md
- Create TROUBLESHOOTING.md
- Add CHANGELOG.md


# ============================================================================
# 11. EXTENSIBILITY (From Schema)
# ============================================================================

STATUS: ✗ Not Implemented

SCHEMA MENTIONS:
- Multi-platform support (BaseCollector interface)
- YouTube support mentioned
- Social media integration
- Overlapping communities
- Scaling considerations

CURRENT:
- Single platform (Twitch only)
- No extensibility hooks

RECOMMENDATION:
- Create `BaseCollector` abstract class
- Refactor `TwitchCollector` from get_viewers.py
- Design for YouTube support
- Document extension points


# ============================================================================
# 12. QUICK WINS (High Impact, Low Effort)
# ============================================================================

1. Add file logging (append to main.py logger setup)
2. Add config YAML loading (create config loader function)
3. Add comprehensive README with examples
4. Add shell scripts for common operations
5. Add metrics JSON export (after each analysis)
6. Add data quality report to CSV export
7. Fix main_new.py duplication (delete old file)
8. Add requirements.txt with versions
9. Add error recovery to collection
10. Add command help/usage in main.py

# ============================================================================
# 13. STRUCTURE CLEANUP
# ============================================================================

CURRENT ISSUES:
- main_new.py exists but not used (replaced by main.py)
- get_viewers.py, update_channels.py not integrated into main.py classes
- Tight coupling between modules
- No clear separation between collection and analysis

RECOMMENDATION:
- Delete main_new.py (main.py is better)
- Create collectors/ package with BaseCollector
- Create storage/ package with storage abstraction
- Create analysis/ package with pipeline
- Create utils/ package with helpers
- Reorganize into proper package structure

# ============================================================================
# 14. DEPENDENCIES
# ============================================================================

MISSING from requirements.txt:
- twitchio
- networkx
- python-louvain
- pyvis
- pandas (not used but useful)
- pyyaml (for config)
- pytest (for testing)

# ============================================================================
# PRIORITY FIXES (In Order)
# ============================================================================

CRITICAL:
1. Create requirements.txt
2. Delete main_new.py
3. Add comprehensive README
4. Add error handling in collection

HIGH:
5. Add file logging
6. Add config YAML support
7. Refactor into package structure
8. Add validation/quality checks

MEDIUM:
9. Add storage abstraction
10. Add metrics export
11. Add shell helper scripts
12. Add troubleshooting guide

NICE-TO-HAVE:
13. BaseCollector interface
14. YouTube support planning
15. Performance profiling
16. Overlapping communities support
"""
