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
from dataclasses import dataclass, asdict, fields
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
    top_channels_limit: int = 5000  # Fetch top N channels from Twitch
    batch_size: int = 100  # Channels per batch
    duration_per_batch: int = 60  # Seconds to collect per batch
    
    # Scheduling
    wait_for_hour_alignment: bool = True  # Sync to top of hour
    collection_interval_minutes: int = 60  # Minutes between cycles
    
    # Cost Protection
    max_runtime_hours: Optional[int] = 24  # Auto-stop after N hours (None = unlimited)
    max_collection_cycles: Optional[int] = 100  # Auto-stop after N cycles (None = unlimited)
    
    # File settings
    logs_dir: str = "logs"
    
    def __post_init__(self):
        """Validate configuration."""
        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if self.duration_per_batch <= 0:
            raise ValueError("duration_per_batch must be positive")
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
    
    # Graph building
    overlap_threshold: int = 1  # Minimum shared viewers for an edge (TwitchAtlas used 300)
    weighting_mode: str = "shared_count"  # Edge weight formula: 'shared_count' (shared viewer intersection)
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
    
    def __post_init__(self):
        """Validate configuration."""
        if self.overlap_threshold < 0:
            raise ValueError("overlap_threshold cannot be negative")
        valid_weighting_modes = {"shared_count"}
        if self.weighting_mode not in valid_weighting_modes:
            raise ValueError(f"weighting_mode must be one of {valid_weighting_modes}")
        if self.resolution <= 0:
            raise ValueError("resolution must be positive")
        if self.min_community_size < 1:
            raise ValueError("min_community_size must be at least 1")
        if self.min_channel_viewers < 0:
            raise ValueError("min_channel_viewers cannot be negative")
        if self.frontend_max_channels < 1:
            raise ValueError("frontend_max_channels must be at least 1")
        if self.frontend_max_edges < 1:
            raise ValueError("frontend_max_edges must be at least 1")
        if self.frontend_top_edges_per_channel < 1:
            raise ValueError("frontend_top_edges_per_channel must be at least 1")
        
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
class SQSConfig:
    """Configuration for SQS-based distributed task queue and DynamoDB dedup."""

    enabled: bool = False
    queue_url: str = ""  # SQS FIFO queue URL
    dynamodb_state_table: str = "vieweratlas-collection-state"
    worker_concurrency: int = 1  # Number of concurrent worker tasks
    visibility_timeout_s: int = 900  # SQS message visibility timeout

    def __post_init__(self):
        env_queue = os.getenv("SQS_CHANNEL_QUEUE_URL")
        if env_queue:
            self.queue_url = env_queue
        env_table = os.getenv("DYNAMODB_STATE_TABLE")
        if env_table:
            self.dynamodb_state_table = env_table
        env_enabled = os.getenv("SQS_ENABLED")
        if env_enabled:
            self.enabled = env_enabled.lower() in ("true", "1", "yes")
        if self.enabled and not self.queue_url:
            raise ValueError("queue_url required when SQS is enabled")


@dataclass
class PipelineConfig:
    """Combined configuration for entire pipeline."""

    collection: CollectionConfig = None
    analysis: AnalysisConfig = None
    vod: VODConfig = None
    sqs: SQSConfig = None

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
        if self.sqs is None:
            self.sqs = SQSConfig()

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
    Get configuration matching TwitchAtlas parameters:
    - Focus on meaningful overlaps (300+ shared viewers)
    - Meaningful communities (10+ channels minimum)
    - English-speaking streamers (language filtering recommended)
    """
    return PipelineConfig(
        collection=CollectionConfig(
            top_channels_limit=5000,
            batch_size=100
        ),
        analysis=AnalysisConfig(
            min_channel_viewers=10,  # Only include channels with 10+ viewers
            overlap_threshold=300,  # TwitchAtlas threshold for meaningful connections
            resolution=1.0,
            min_community_size=10,  # Communities of 10+ channels
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
    "sqs": SQSConfig,
}

# Fields declared as tuples but naturally expressed as YAML lists.
_TUPLE_FIELDS = {"static_viz_figsize"}


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

    Every field of CollectionConfig, AnalysisConfig, VODConfig and SQSConfig is
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
