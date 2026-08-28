"""
Configuration Module

Centralized configuration for the streaming community detection pipeline.
Separates collection config from analysis config for flexibility.

Supports:
- Dataclass-based config with validation
- Four preset configurations (default, rigorous, explorer, debug)
- YAML file loading with environment variable overrides
"""

import os
from dataclasses import dataclass, asdict, field, fields
from pathlib import Path
from typing import Optional

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


@dataclass
class CollectionConfig:
    """Configuration for data collection phase."""
    
    # Twitch API settings
    oauth_token: Optional[str] = None
    client_id: Optional[str] = None
    
    # Channel discovery
    top_channels_limit: int = 1200  # Freeze the approximate top N streams per survey
    batch_size: int = 100  # Twitch chat rooms concurrently joined by the bot
    duration_per_batch: int = 300  # Equal active-listening window per batch
    survey_timeout_seconds: int = 7200  # Hard safety limit for a one-shot survey
    subscription_retries: int = 2  # Retries after an individual subscription failure
    batch_retries: int = 2  # Full restarts after an unrecoverable websocket loss
    
    # Scheduling
    wait_for_hour_alignment: bool = True  # Sync to top of hour
    collection_interval_minutes: int = 60  # Minutes between cycles
    
    # Cost Protection
    max_runtime_hours: Optional[int] = 24  # Auto-stop after N hours (None = unlimited)
    max_collection_cycles: Optional[int] = 100  # Auto-stop after N cycles (None = unlimited)
    
    # File settings
    logs_dir: str = "logs"
    
    def __post_init__(self):
        """Apply survey canary overrides and validate configuration."""
        integer_overrides = {
            "SURVEY_TOP_CHANNELS_LIMIT": "top_channels_limit",
            "SURVEY_BATCH_SIZE": "batch_size",
            "SURVEY_WINDOW_SECONDS": "duration_per_batch",
            "SURVEY_TIMEOUT_SECONDS": "survey_timeout_seconds",
        }
        for env_name, field_name in integer_overrides.items():
            raw = os.getenv(env_name)
            if raw is None or not raw.strip():
                continue
            try:
                value = int(raw)
            except ValueError:
                raise ValueError(f"{env_name} must be an integer") from None
            setattr(self, field_name, value)

        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if self.batch_size > 100:
            raise ValueError("batch_size cannot exceed Twitch's 100-room chat limit")
        if self.duration_per_batch <= 0:
            raise ValueError("duration_per_batch must be positive")
        if self.survey_timeout_seconds <= 0:
            raise ValueError("survey_timeout_seconds must be positive")
        if self.subscription_retries < 0:
            raise ValueError("subscription_retries cannot be negative")
        if self.batch_retries < 0:
            raise ValueError("batch_retries cannot be negative")
        if self.top_channels_limit <= 0:
            raise ValueError("top_channels_limit must be positive")
        if self.max_runtime_hours is not None and self.max_runtime_hours <= 0:
            raise ValueError("max_runtime_hours must be positive or None")
        if self.max_collection_cycles is not None and self.max_collection_cycles <= 0:
            raise ValueError("max_collection_cycles must be positive or None")
        
        # Create logs directory if it doesn't exist
        Path(self.logs_dir).mkdir(exist_ok=True)


@dataclass
class AnalysisConfig:
    """Configuration for analysis phase (aggregation, graph, detection, visualization)."""
    
    # Input/output
    logs_dir: str = "logs"
    output_dir: str = "community_analysis"
    
    # Data filtering
    min_channel_viewers: int = 1  # Minimum unique viewers for a channel to be included
    min_user_appearances: int = 1  # Minimum channels a user must appear in
    # Survey cohorts churn, so many channels are sampled only once or twice. A
    # channel seen once contributes a single 5-minute window and cannot produce
    # reliable overlap at any threshold.
    min_channel_observations: int = 1
    # Rolling window of survey days to analyse (30/60/90 are the intended values).
    # None unions every retained snapshot, which makes overlap_threshold drift as
    # data accumulates: viewer sets only grow, so graph density climbs over time.
    analysis_window_days: Optional[int] = None
    # Extra windows to publish alongside the canonical one, each a full analysis
    # pass exported to data/frontend-data-<N>d.json. The entry equal to
    # analysis_window_days is canonical: it is the one that also writes the
    # unsuffixed data/frontend-data.json, produces the private artifacts, and
    # anchors community colours for the other windows. Empty means single-window.
    analysis_windows: tuple = ()
    # Per-window overlap_threshold overrides, keyed by window length in days.
    # Observed overlap grows super-linearly with survey count, so one threshold
    # cannot serve 14 and 90 days at once: the value calibrated for 30 days is
    # too strict at 14 and admits an edge explosion at 90. Measure each with
    # `scripts/sweep_threshold.py --window-days N` and record it here. Windows
    # absent from this map fall back to overlap_threshold.
    window_overlap_thresholds: dict = field(default_factory=dict)
    
    # Graph building
    overlap_threshold: int = 1  # Minimum shared viewers for an edge (TwitchAtlas used 300)
    # 'shared_count' (raw intersection), 'jaccard' (|A n B| / |A u B|) or
    # 'overlap_coef' (|A n B| / min(|A|,|B|)). Raw counts reward channels that
    # were simply sampled more often; the normalised modes do not.
    weighting_mode: str = "shared_count"
    # Minimum normalised score (0-1) for an edge. Ignored by 'shared_count'.
    normalized_overlap_threshold: float = 0.0
    include_isolated_nodes: bool = True  # Include channels with no overlaps
    
    # Community detection
    resolution: float = 1.0  # Louvain resolution (higher = more communities)
    min_community_size: int = 1  # Minimum channels in a community to include
    
    # Continuous mode
    analysis_interval_cycles: int = 24  # Run analysis every N collection cycles
    
    # Visualization
    enable_static_viz: bool = True  # Generate PNG
    enable_interactive_viz: bool = True  # Generate HTML
    static_viz_dpi: int = 300  # PNG resolution
    static_viz_figsize: tuple = (20, 16)  # Figure size (width, height) in inches
    show_node_labels: bool = True  # Label large nodes on PNG
    label_top_n_nodes: int = 15  # Number of largest nodes to label
    
    # Export
    export_graph_csv: bool = True  # Export nodes/edges CSV
    save_analysis_json: bool = True  # Save full results JSON
    frontend_max_channels: int = 1000  # Public frontend node cap
    frontend_max_edges: int = 25000  # Public frontend edge cap
    frontend_top_edges_per_channel: int = 25  # Public per-node edge cap
    # Communities this small *after* the public cap are remnants of the cap
    # rather than structure, and each one costs the map a legend entry and a
    # colour. Applied only while some community still clears it.
    frontend_min_community_size: int = 4
    
    def __post_init__(self):
        """Validate configuration."""
        if self.overlap_threshold < 0:
            raise ValueError("overlap_threshold cannot be negative")
        valid_weighting_modes = {"shared_count", "jaccard", "overlap_coef"}
        if self.weighting_mode not in valid_weighting_modes:
            raise ValueError(f"weighting_mode must be one of {valid_weighting_modes}")
        if self.resolution <= 0:
            raise ValueError("resolution must be positive")
        if self.min_community_size < 1:
            raise ValueError("min_community_size must be at least 1")
        if self.min_channel_viewers < 0:
            raise ValueError("min_channel_viewers cannot be negative")
        if self.analysis_window_days is not None and self.analysis_window_days < 1:
            raise ValueError("analysis_window_days must be at least 1 or None")
        if any(days < 1 for days in self.analysis_windows):
            raise ValueError("every entry in analysis_windows must be at least 1")
        if self.analysis_windows and self.analysis_window_days not in self.analysis_windows:
            # The canonical window is the one that writes the unsuffixed public
            # artifact. Publishing a set that excludes it would leave that file
            # stale while the suffixed ones move.
            raise ValueError(
                "analysis_window_days must appear in analysis_windows when "
                "multiple windows are published"
            )
        for days, threshold in self.window_overlap_thresholds.items():
            if not isinstance(days, int) or days < 1:
                raise ValueError("window_overlap_thresholds keys must be day counts of at least 1")
            if not isinstance(threshold, int) or threshold < 0:
                raise ValueError("window_overlap_thresholds values must be non-negative integers")
        if self.min_channel_observations < 1:
            raise ValueError("min_channel_observations must be at least 1")
        if not 0.0 <= self.normalized_overlap_threshold <= 1.0:
            raise ValueError("normalized_overlap_threshold must be between 0 and 1")
        if self.frontend_max_channels < 1:
            raise ValueError("frontend_max_channels must be at least 1")
        if self.frontend_max_edges < 1:
            raise ValueError("frontend_max_edges must be at least 1")
        if self.frontend_top_edges_per_channel < 1:
            raise ValueError("frontend_top_edges_per_channel must be at least 1")
        if self.frontend_min_community_size < 1:
            raise ValueError("frontend_min_community_size must be at least 1")
        
        # Create output directory if it doesn't exist
        Path(self.output_dir).mkdir(exist_ok=True)


@dataclass
class VODConfig:
    """Configuration for VOD (Video On Demand) chatter collection."""
    
    # Enable/disable VOD collection
    enabled: bool = False
    
    # Time bucketing
    bucket_len_s: int = 60  # Bucket window size in seconds (must match live collection)
    
    # Storage
    raw_dir: str = "vod_raw"  # Directory for raw VOD chat JSON
    queue_file: str = "vod_queue.json"  # VOD processing queue
    persist_raw_chat: bool = False  # Opt-in: retain downloaded VOD chat JSON
    
    # TwitchDownloaderCLI
    cli_path: str = "TwitchDownloaderCLI"  # Path to executable
    
    # Auto-discovery
    auto_discover: bool = False  # Automatically discover recent VODs
    vod_limit_per_channel: int = 5  # Number of recent VODs to queue per channel
    
    # Filtering
    max_age_hours: int = 24  # Maximum VOD age in hours (default 24)
    max_age_days: int = 14  # Maximum VOD age in days (default 14)
    min_views: int = 0  # Minimum view count to process (default 0)
    
    # Cost Protection
    max_vods_per_run: Optional[int] = 50  # Max VODs to process per execution (None = unlimited)
    max_processing_hours: Optional[int] = 4  # Auto-stop after N hours (None = unlimited)
    rate_limit_delay_s: int = 2  # Delay between API calls to avoid rate limits
    
    def __post_init__(self):
        """Validate configuration."""
        if self.bucket_len_s <= 0:
            raise ValueError("bucket_len_s must be positive")
        if self.vod_limit_per_channel < 1:
            raise ValueError("vod_limit_per_channel must be at least 1")
        if self.max_age_hours < 1:
            raise ValueError("max_age_hours must be at least 1")
        if self.max_age_days < 1:
            raise ValueError("max_age_days must be at least 1")
        if self.min_views < 0:
            raise ValueError("min_views cannot be negative")
        if self.max_vods_per_run is not None and self.max_vods_per_run <= 0:
            raise ValueError("max_vods_per_run must be positive or None")
        if self.max_processing_hours is not None and self.max_processing_hours <= 0:
            raise ValueError("max_processing_hours must be positive or None")
        if self.rate_limit_delay_s < 0:
            raise ValueError("rate_limit_delay_s cannot be negative")
        
        # Create directories if they don't exist
        if self.enabled:
            Path(self.raw_dir).mkdir(exist_ok=True)


@dataclass
class PipelineConfig:
    """Combined configuration for entire pipeline."""

    collection: CollectionConfig = None
    analysis: AnalysisConfig = None
    vod: VODConfig = None

    # Storage backend
    storage_type: str = "file"  # 'file' or 's3'
    s3_bucket: Optional[str] = None  # Required if storage_type='s3'
    s3_prefix: str = "vieweratlas/"  # S3 key prefix
    s3_region: str = "us-east-1"  # AWS region
    
    # Logging
    log_level: str = "INFO"  # DEBUG, INFO, WARNING, ERROR
    log_format: str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    # Pipeline mode
    dry_run: bool = False  # If True, don't actually write files
    verbose: bool = False  # Extra debug output
    
    def __post_init__(self):
        """Initialize defaults if not provided."""
        if self.collection is None:
            self.collection = CollectionConfig()
        if self.analysis is None:
            self.analysis = AnalysisConfig()
        if self.vod is None:
            self.vod = VODConfig()

        # Allow runtime environment to control storage backend selection.
        env_storage_type = os.getenv("STORAGE_TYPE")
        if env_storage_type:
            self.storage_type = env_storage_type.lower()

        env_s3_bucket = os.getenv("S3_BUCKET")
        if env_s3_bucket:
            self.s3_bucket = env_s3_bucket

        env_s3_prefix = os.getenv("S3_PREFIX")
        if env_s3_prefix:
            self.s3_prefix = env_s3_prefix

        env_s3_region = os.getenv("S3_REGION")
        if env_s3_region:
            self.s3_region = env_s3_region
        
        # Validate S3 config
        if self.storage_type == 's3' and not self.s3_bucket:
            raise ValueError("s3_bucket required when storage_type='s3'")


# Default configurations for different use cases

def get_default_config() -> PipelineConfig:
    """Get default configuration for normal operation."""
    return PipelineConfig(
        collection=CollectionConfig(),
        analysis=AnalysisConfig(
            overlap_threshold=1,  # Start lenient, can raise later
            resolution=1.0
        ),
        log_level="INFO",
        verbose=False
    )


def get_rigorous_config() -> PipelineConfig:
    """
    Production analysis preset, calibrated against real EventSub survey data.

    The scheduled task runs ``main.py analyze rigorous``, so these are the values
    that actually shape the published graph. They were measured with
    ``scripts/sweep_threshold.py`` over a four-day, 15-survey sample, not
    inherited from TwitchAtlas: five-minute EventSub windows produce a very
    different overlap distribution from continuous IRC collection.

    Re-run the sweep as data accumulates. Observed overlap grows super-linearly
    with survey count, so these thresholds will need raising.
    """
    return PipelineConfig(
        collection=CollectionConfig(
            top_channels_limit=1200,
            batch_size=100
        ),
        analysis=AnalysisConfig(
            analysis_window_days=30,  # Bound the union; without it density climbs daily
            # Windows offered by the map's time filter. Listing one here does
            # not commit to analysing it: each run publishes only the windows
            # the retained surveys can actually fill, and declares the rest to
            # the browser as PENDING. They promote themselves as history
            # accumulates, so this list needs no revisiting.
            analysis_windows=(14, 30, 90),
            # Survey cohorts churn: 7,098 distinct channels appeared in four
            # days, most of them once or twice. Requiring three observations and
            # ten authors leaves ~1,969 channels worth comparing.
            min_channel_observations=3,
            min_channel_viewers=10,
            # Measured: median and p90 overlap are both 1, so a threshold of 1
            # admits ~36,000 single-chatter coincidences. Moving to 2 drops 93%
            # of edges and lifts modularity from 0.641 to 0.772.
            #
            # That measurement was taken over a four-day, 15-survey sample and
            # is the fallback for any window without its own entry below. It is
            # now known to be far too loose once a window holds real history:
            # re-swept over 14 days (47,864 rows) the same procedure returns 10,
            # not 2. Treat this as the floor for an unmeasured window, not as a
            # calibrated value, and sweep every window that promotes.
            overlap_threshold=2,
            # Measured 2026-08-28 with scripts/calibrate_windows.sh over
            # 2026-08-13..08-26. The sweep suggests 10, which is where its
            # modularity peaks (0.840). This is deliberately 3 instead.
            #
            # Modularity is nearly flat from 3 to 10 (0.830 -> 0.840) while the
            # connected share of channels falls from 64% to 27%. Measured
            # end-to-end, threshold 10 publishes 654 channels against 899 at 3 —
            # a third of the map given up for 0.010 of modularity. Modularity
            # rewards sparsity on its own, so the sweep's argmax overshoots what
            # an overlap map is for. 3 keeps every edge at 3+ shared chatters,
            # which is still above the p90 of 3 for the window's pair overlaps.
            #
            # The 30 and 90-day entries are absent because no window that long
            # exists yet; sweep each one as it promotes out of PENDING, and
            # weigh coverage against modularity the same way rather than taking
            # the suggested value unread.
            window_overlap_thresholds={14: 3},
            # Normalised modes lose to the raw count while overlaps are this
            # thin (best jaccard 0.699, best overlap_coef 0.726). Revisit once
            # overlaps carry real magnitude.
            weighting_mode="shared_count",
            # At threshold 2 roughly 44% of channels have no edge; showing them
            # would fill the public map with unconnected dots.
            include_isolated_nodes=False,
            resolution=1.0,
            min_community_size=10,  # Verified: 21 communities survive this floor
            label_top_n_nodes=20
        ),
        log_level="INFO",
        verbose=False
    )


def get_exploratory_config() -> PipelineConfig:
    """
    Get configuration for exploratory analysis:
    - Lower thresholds to see more patterns
    - Finer-grained communities
    - All data included
    """
    return PipelineConfig(
        collection=CollectionConfig(),
        analysis=AnalysisConfig(
            min_channel_viewers=1,  # Include all channels
            overlap_threshold=1,  # All overlaps
            resolution=2.0,  # Fine-grained communities
            min_community_size=1,  # Include all communities
            label_top_n_nodes=30
        ),
        log_level="DEBUG",
        verbose=True
    )


def get_debug_config() -> PipelineConfig:
    """Get configuration for debugging (small dataset, verbose output)."""
    return PipelineConfig(
        collection=CollectionConfig(
            top_channels_limit=100,  # Just 100 channels
            batch_size=10
        ),
        analysis=AnalysisConfig(
            min_channel_viewers=1,
            overlap_threshold=1,
            resolution=1.0
        ),
        log_level="DEBUG",
        verbose=True,
        dry_run=False
    )


_SECTION_CLASSES = {
    "collection": CollectionConfig,
    "analysis": AnalysisConfig,
    "vod": VODConfig,
}

# Fields declared as tuples but naturally expressed as YAML lists.
_TUPLE_FIELDS = {"static_viz_figsize", "analysis_windows"}


def _build_section(section_name: str, config_class, values: dict):
    """Instantiate a config dataclass from YAML values, rejecting unknown keys.

    Silently ignoring an unrecognised key means a typo like `min_comunity_size`
    reads as "configured" while doing nothing, so we fail loudly instead.
    """
    if not isinstance(values, dict):
        raise ValueError(f"Config section '{section_name}' must be a mapping, got {type(values).__name__}")

    known = {f.name for f in fields(config_class)}
    unknown = set(values) - known
    if unknown:
        raise ValueError(
            f"Unknown key(s) in '{section_name}' config section: {', '.join(sorted(unknown))}. "
            f"Valid keys: {', '.join(sorted(known))}"
        )

    coerced = {
        key: tuple(value) if key in _TUPLE_FIELDS and isinstance(value, list) else value
        for key, value in values.items()
    }
    return config_class(**coerced)


def load_config_from_yaml(yaml_path: str) -> PipelineConfig:
    """
    Load configuration from YAML file with environment variable overrides.

    Every field of CollectionConfig, AnalysisConfig and VODConfig is
    settable from YAML; unrecognised keys raise rather than being dropped.

    Args:
        yaml_path: Path to YAML config file

    Returns:
        PipelineConfig with loaded settings

    Raises:
        ImportError: If PyYAML not installed
        FileNotFoundError: If YAML file doesn't exist
        ValueError: If YAML config invalid or contains unknown keys
    """
    if not HAS_YAML:
        raise ImportError(
            "PyYAML required for YAML config loading. "
            "Install with: pip install pyyaml"
        )
    
    yaml_file = Path(yaml_path)
    if not yaml_file.exists():
        raise FileNotFoundError(f"Config file not found: {yaml_path}")
    
    # Load YAML
    with open(yaml_file) as f:
        config_dict = yaml.safe_load(f) or {}
    
    # Override with environment variables
    if os.getenv("OVERLAP_THRESHOLD"):
        if "analysis" not in config_dict:
            config_dict["analysis"] = {}
        config_dict["analysis"]["overlap_threshold"] = int(os.getenv("OVERLAP_THRESHOLD"))
    
    if os.getenv("MIN_COMMUNITY_SIZE"):
        if "analysis" not in config_dict:
            config_dict["analysis"] = {}
        config_dict["analysis"]["min_community_size"] = int(os.getenv("MIN_COMMUNITY_SIZE"))
    
    if os.getenv("RESOLUTION"):
        if "analysis" not in config_dict:
            config_dict["analysis"] = {}
        config_dict["analysis"]["resolution"] = float(os.getenv("RESOLUTION"))
    
    if os.getenv("LOG_LEVEL"):
        config_dict["log_level"] = os.getenv("LOG_LEVEL")
    
    if not isinstance(config_dict, dict):
        raise ValueError(f"Config file {yaml_path} must contain a top-level mapping")

    # Legacy fallback: derive max_age_hours from max_age_days when only the
    # latter is given, before the section is validated against VODConfig.
    vod_dict = dict(config_dict.get("vod") or {})
    if "max_age_hours" not in vod_dict and "max_age_days" in vod_dict:
        vod_dict["max_age_hours"] = vod_dict["max_age_days"] * 24
    if vod_dict:
        config_dict["vod"] = vod_dict

    sections = {
        name: _build_section(name, config_class, config_dict.get(name) or {})
        for name, config_class in _SECTION_CLASSES.items()
    }

    # Whatever is not a section is a top-level PipelineConfig field.
    top_level = {k: v for k, v in config_dict.items() if k not in _SECTION_CLASSES}
    pipeline_fields = {f.name for f in fields(PipelineConfig)} - set(_SECTION_CLASSES)
    unknown = set(top_level) - pipeline_fields
    if unknown:
        raise ValueError(
            f"Unknown top-level key(s) in config: {', '.join(sorted(unknown))}. "
            f"Valid keys: {', '.join(sorted(pipeline_fields | set(_SECTION_CLASSES)))}"
        )

    return PipelineConfig(**sections, **top_level)


if __name__ == "__main__":
    # Test configuration loading and validation
    print("Default Config:")
    default = get_default_config()
    print(f"  Logs dir: {default.analysis.logs_dir}")
    print(f"  Output dir: {default.analysis.output_dir}")
    print(f"  Overlap threshold: {default.analysis.overlap_threshold}")
    
    print("\nRigorous Config (TwitchAtlas-style):")
    rigorous = get_rigorous_config()
    print(f"  Min channel viewers: {rigorous.analysis.min_channel_viewers}")
    print(f"  Overlap threshold: {rigorous.analysis.overlap_threshold}")
    print(f"  Min community size: {rigorous.analysis.min_community_size}")
    
    print("\nExplorer Config:")
    explorer = get_exploratory_config()
    print(f"  Resolution: {explorer.analysis.resolution}")
    print(f"  Overlap threshold: {explorer.analysis.overlap_threshold}")
    
    print("\nAll configs loaded successfully!")
