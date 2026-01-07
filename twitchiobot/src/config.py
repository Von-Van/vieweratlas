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
from dataclasses import dataclass, asdict
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
    
    def __post_init__(self):
        """Validate configuration."""
        if self.overlap_threshold < 0:
            raise ValueError("overlap_threshold cannot be negative")
        if self.resolution <= 0:
            raise ValueError("resolution must be positive")
        if self.min_community_size < 1:
            raise ValueError("min_community_size must be at least 1")
        if self.min_channel_viewers < 0:
            raise ValueError("min_channel_viewers cannot be negative")
        
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
    
    # TwitchDownloaderCLI
    cli_path: str = "TwitchDownloaderCLI"  # Path to executable
    
    # Auto-discovery
    auto_discover: bool = False  # Automatically discover recent VODs
    vod_limit_per_channel: int = 5  # Number of recent VODs to queue per channel
    
    # Filtering
    max_age_days: int = 14  # Maximum VOD age in days (default 14)
    min_views: int = 0  # Minimum view count to process (default 0)
    
    def __post_init__(self):
        """Validate configuration."""
        if self.bucket_len_s <= 0:
            raise ValueError("bucket_len_s must be positive")
        if self.vod_limit_per_channel < 1:
            raise ValueError("vod_limit_per_channel must be at least 1")
        if self.max_age_days < 1:
            raise ValueError("max_age_days must be at least 1")
        if self.min_views < 0:
            raise ValueError("min_views cannot be negative")
        
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


def load_config_from_yaml(yaml_path: str) -> PipelineConfig:
    """
    Load configuration from YAML file with environment variable overrides.
    
    Args:
        yaml_path: Path to YAML config file
        
    Returns:
        PipelineConfig with loaded settings
        
    Raises:
        ImportError: If PyYAML not installed
        FileNotFoundError: If YAML file doesn't exist
        ValueError: If YAML config invalid
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
    
    # Create configs from dict
    collection_dict = config_dict.get("collection", {})
    analysis_dict = config_dict.get("analysis", {})
    vod_dict = config_dict.get("vod", {})
    
    collection_config = CollectionConfig(
        logs_dir=collection_dict.get("logs_dir", "logs"),
        collection_interval_minutes=collection_dict.get("collection_interval", 3600) // 60,
        batch_size=collection_dict.get("batch_size", 100),
        duration_per_batch=collection_dict.get("duration_per_batch", 60),
        top_channels_limit=analysis_dict.get("top_channels_limit", 5000)
    )
    
    analysis_config = AnalysisConfig(
        logs_dir=analysis_dict.get("logs_dir", "logs"),
        output_dir=analysis_dict.get("output_dir", "community_analysis"),
        min_channel_viewers=analysis_dict.get("min_channel_viewers", 1),
        overlap_threshold=analysis_dict.get("overlap_threshold", 1),
        resolution=analysis_dict.get("resolution", 1.0),
        game_threshold=analysis_dict.get("game_threshold", 60),
        language_threshold=analysis_dict.get("language_threshold", 40),
        min_community_size=analysis_dict.get("min_community_size", 2)
    )
    
    vod_config = VODConfig(
        enabled=vod_dict.get("enabled", False),
        bucket_len_s=vod_dict.get("bucket_len_s", 60),
        raw_dir=vod_dict.get("raw_dir", "vod_raw"),
        queue_file=vod_dict.get("queue_file", "vod_queue.json"),
        cli_path=vod_dict.get("cli_path", "TwitchDownloaderCLI"),
        auto_discover=vod_dict.get("auto_discover", False),
        vod_limit_per_channel=vod_dict.get("vod_limit_per_channel", 5),
        max_age_days=vod_dict.get("max_age_days", 14),
        min_views=vod_dict.get("min_views", 0)
    )
    
    return PipelineConfig(
        collection=collection_config,
        analysis=analysis_config,
        vod=vod_config,
        storage_type=config_dict.get("storage_type", "file"),
        s3_bucket=config_dict.get("s3_bucket"),
        s3_prefix=config_dict.get("s3_prefix", "vieweratlas/"),
        s3_region=config_dict.get("s3_region", "us-east-1"),
        log_level=config_dict.get("log_level", "INFO"),
        log_format=config_dict.get("log_format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"),
        verbose=config_dict.get("verbose", False)
    )


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
