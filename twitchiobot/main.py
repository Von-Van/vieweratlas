"""
Main Orchestrator Module (Refactored)

Unified entry point for the complete streaming community detection pipeline.
Uses PipelineRunner class for clean orchestration and config-driven execution.

Pipeline flow:
1. Collect data (existing chat logger)
2. Aggregate viewer data
3. Build overlap graph
4. Detect communities
5. Tag communities
6. Visualize results
"""

import asyncio
import os
import sys
import time
import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv

from get_viewers import ChatLogger, load_channels
from update_channels import update_channel_list
from data_aggregator import DataAggregator
from graph_builder import GraphBuilder
from community_detector import CommunityDetector
from cluster_tagger import ClusterTagger
from visualizer import Visualizer
from config import (
    PipelineConfig, 
    get_default_config, 
    get_rigorous_config,
    get_exploratory_config,
    get_debug_config,
    load_config_from_yaml
)
from storage import get_storage

load_dotenv()


def setup_logging(config: PipelineConfig):
    """
    Configure logging with both console and file handlers.
    
    Args:
        config: PipelineConfig with log settings
    """
    log_level = getattr(logging, config.log_level)
    log_format = logging.Formatter(config.log_format)
    
    # Create logs directory if it doesn't exist
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Remove existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(log_format)
    root_logger.addHandler(console_handler)
    
    # File handler with rotation (max 10MB per file, keep 5 backups)
    file_handler = RotatingFileHandler(
        logs_dir / "pipeline.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(log_format)
    root_logger.addHandler(file_handler)


class PipelineRunner:
    """
    Orchestrates the complete pipeline: collection → analysis → visualization.
    """
    
    def __init__(self, config: PipelineConfig):
        """
        Initialize runner with configuration.
        
        Args:
            config: PipelineConfig object with all settings
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"PipelineRunner initialized with log level {config.log_level}")
        
        # Initialize storage backend
        self.storage = get_storage(
            storage_type=config.storage_type,
            bucket=config.s3_bucket,
            prefix=config.s3_prefix,
            region=config.s3_region
        )
        self.logger.info(f"Storage backend: {config.storage_type}")
        
        # Initialize storage backend
        self.storage = get_storage(
            storage_type=config.storage_type,
            bucket=config.s3_bucket,
            prefix=config.s3_prefix,
            region=config.s3_region
        )
        self.logger.info(f"Storage backend: {config.storage_type}")
    
    def _validate_prerequisites(self, mode: str) -> bool:
        """
        Validate that all prerequisites are met.
        
        Args:
            mode: 'collect', 'analyze', or 'continuous'
        
        Returns:
            True if valid, False otherwise
        """
        self.logger.info("Validating prerequisites...")
        
        if mode in ['collect', 'continuous']:
            oauth_token = os.getenv("TWITCH_OAUTH_TOKEN")
            if not oauth_token:
                self.logger.error("❌ TWITCH_OAUTH_TOKEN not set in .env")
                return False
            self.logger.info("✓ OAuth token found")
        
        if mode in ['analyze', 'continuous']:
            logs_path = Path(self.config.analysis.logs_dir)
            if not logs_path.exists():
                self.logger.error(f"❌ Logs directory {logs_path} does not exist")
                return False
            
            json_files = list(logs_path.glob("*.json"))
            csv_files = list(logs_path.glob("*.csv"))
            if not json_files and not csv_files:
                self.logger.error(f"❌ No data found in {logs_path}")
                return False
            
            self.logger.info(f"✓ Found {len(json_files)} JSON and {len(csv_files)} CSV files")
        
        # Check for required libraries
        try:
            import community
            self.logger.info("✓ python-louvain available")
        except ImportError:
            self.logger.warning("⚠ python-louvain not installed. Community detection will fail.")
            self.logger.info("   Install with: pip install python-louvain")
        
        self.logger.info("Prerequisites validation complete ✓\n")
        return True
    
    async def run_collection_cycle(self):
        """Execute a single data collection cycle."""
        oauth_token = os.getenv("TWITCH_OAUTH_TOKEN")
        
        self.logger.info("Starting data collection cycle...")
        self.logger.info(f"Fetching top {self.config.collection.top_channels_limit} channels...")
        
        update_channel_list(limit=self.config.collection.top_channels_limit)
        all_channels = load_channels()
        
        if not all_channels:
            self.logger.error("No channels loaded")
            return
        
        self.logger.info(f"Found {len(all_channels)} channels to monitor")
        
        # Process in batches
        for i, batch in enumerate(self._split_batches(all_channels), 1):
            self.logger.info(f"Processing batch {i}/{-(-len(all_channels)//self.config.collection.batch_size)}...")
            bot = ChatLogger(token=oauth_token, channels=batch, storage=self.storage)
            await bot.start()
            self.logger.info(f"  📡 Logging chatters in {len(batch)} channels")
            await asyncio.sleep(self.config.collection.duration_per_batch)
            await bot.log_results()
            await bot.close()
        
        self.logger.info("✓ Collection cycle complete\n")
    
    def run_analysis_pipeline(self) -> dict:
        """
        Execute the complete analysis pipeline.
        
        Returns:
            Dict with analysis results and status
        """
        self.logger.info("=" * 70)
        self.logger.info("ANALYSIS PIPELINE START")
        self.logger.info("=" * 70 + "\n")
        
        try:
            # Step 1: Aggregate
            self.logger.info("[1/6] AGGREGATING VIEWER DATA")
            self.logger.info("-" * 70)
            aggregator = self._step_aggregate()
            if not aggregator:
                return {"status": "error", "message": "Aggregation failed"}
            
            # Step 2: Build graph
            self.logger.info("\n[2/6] BUILDING OVERLAP GRAPH")
            self.logger.info("-" * 70)
            graph = self._step_build_graph(aggregator)
            if graph is None:
                return {"status": "error", "message": "Graph building failed"}
            
            # Step 3: Detect communities
            self.logger.info("\n[3/6] DETECTING COMMUNITIES")
            self.logger.info("-" * 70)
            partition, communities, detection_stats = self._step_detect_communities(graph)
            if partition is None:
                return {"status": "error", "message": "Community detection failed"}
            
            # Step 4: Tag communities
            self.logger.info("\n[4/6] TAGGING COMMUNITIES")
            self.logger.info("-" * 70)
            labels, tagging_stats = self._step_tag_communities(
                communities,
                aggregator.get_channel_metadata()
            )
            
            # Step 5: Visualize
            self.logger.info("\n[5/6] CREATING VISUALIZATIONS")
            self.logger.info("-" * 70)
            self._step_visualize(graph, partition, labels)
            
            # Step 6: Save results
            self.logger.info("\n[6/6] SAVING RESULTS")
            self.logger.info("-" * 70)
            self._step_save_results(
                partition, labels, 
                graph, aggregator, 
                detection_stats, tagging_stats
            )
            
            self.logger.info("\n" + "=" * 70)
            self.logger.info("✅ ANALYSIS PIPELINE COMPLETE")
            self.logger.info("=" * 70 + "\n")
            
            return {
                "status": "success",
                "num_communities": detection_stats['num_communities'],
                "num_channels": graph.number_of_nodes(),
                "num_edges": graph.number_of_edges(),
                "modularity": detection_stats['modularity'],
                "output_dir": self.config.analysis.output_dir
            }
        
        except Exception as e:
            self.logger.error(f"Pipeline failed: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}
    
    def _step_aggregate(self) -> Optional[DataAggregator]:
        """Aggregation step."""
        aggregator = DataAggregator(self.config.analysis.logs_dir, storage=self.storage)
        json_count, csv_count = aggregator.load_all()
        self.logger.info(f"Loaded {json_count} JSON snapshots + {csv_count} CSV records")
        
        stats = aggregator.get_statistics()
        self.logger.info(f"Total channels: {stats['total_channels']}")
        self.logger.info(f"Total unique viewers: {stats['total_unique_viewers_across_all']}")
        if stats['top_channels_by_viewers']:
            top = stats['top_channels_by_viewers'][0]
            self.logger.info(f"Top channel: {top[0]} ({top[1]} viewers)")
        
        # Print data quality report
        quality = aggregator.get_data_quality_report()
        self.logger.info(f"\nData Quality Report:")
        self.logger.info(f"  Avg viewers/channel: {quality['avg_viewers_per_channel']:.1f}")
        self.logger.info(f"  Repeat viewers (2+): {quality['repeat_viewers_2plus']}")
        self.logger.info(f"  One-off viewers: {quality['one_off_viewers']} ({quality['one_off_percentage']:.1f}%)")
        
        if not aggregator.get_channel_viewers():
            self.logger.error("No viewer data found")
            return None
        
        return aggregator
    
    def _step_build_graph(self, aggregator: DataAggregator) -> Optional[object]:
        """Graph building step."""
        channel_viewers = aggregator.get_channel_viewers()
        channel_metadata = aggregator.get_channel_metadata()
        
        # Apply filtering if configured
        if self.config.analysis.min_channel_viewers > 1:
            original_count = len(channel_viewers)
            channel_viewers = aggregator.filter_channels_by_size(
                self.config.analysis.min_channel_viewers
            )
            self.logger.info(f"Filtered channels: {original_count} → {len(channel_viewers)} "
                           f"(min {self.config.analysis.min_channel_viewers} viewers)")
        
        builder = GraphBuilder(overlap_threshold=self.config.analysis.overlap_threshold)
        graph = builder.build_graph(channel_viewers, channel_metadata)
        
        stats = builder.get_statistics()
        self.logger.info(f"Graph created:")
        self.logger.info(f"  Nodes: {stats['num_nodes']}")
        self.logger.info(f"  Edges: {stats['num_edges']}")
        self.logger.info(f"  Avg edge weight: {stats['avg_edge_weight']:.2f}")
        self.logger.info(f"  Max edge weight: {stats['max_edge_weight']}")
        self.logger.info(f"  Density: {stats['density']:.4f}")
        
        if graph.number_of_edges() == 0:
            self.logger.error("Graph has no edges. Try lowering overlap_threshold.")
            return None
        
        if self.config.analysis.export_graph_csv:
            builder.export_nodes_csv(f"{self.config.analysis.output_dir}/graph_nodes.csv")
            builder.export_edges_csv(f"{self.config.analysis.output_dir}/graph_edges.csv")
            self.logger.info(f"Exported graph data to {self.config.analysis.output_dir}/")
        
        return graph
    
    def _step_detect_communities(self, graph) -> tuple:
        """Community detection step."""
        detector = CommunityDetector(resolution=self.config.analysis.resolution)
        
        try:
            partition = detector.detect_communities(graph)
        except ImportError as e:
            self.logger.error(f"Community detection failed: {e}")
            return None, None, None
        
        communities = detector.get_communities()
        stats = detector.get_statistics()
        
        self.logger.info(f"Communities detected:")
        self.logger.info(f"  Count: {stats['num_communities']}")
        self.logger.info(f"  Modularity: {stats['modularity']:.4f}")
        self.logger.info(f"  Largest: {stats['largest_community_size']} channels")
        self.logger.info(f"  Smallest: {stats['smallest_community_size']} channels")
        
        return partition, communities, stats
    
    def _step_tag_communities(self, communities, channel_metadata) -> tuple:
        """Tagging step."""
        tagger = ClusterTagger()
        labels = tagger.tag_communities(communities, channel_metadata)
        stats = tagger.get_statistics()
        
        self.logger.info(f"Communities tagged:")
        self.logger.info(f"  With clear game: {stats['with_clear_game']}")
        self.logger.info(f"  With clear language: {stats['with_clear_language']}")
        self.logger.info(f"  Uncategorized: {stats['uncategorized']}")
        
        self.logger.info(f"\nCommunity Labels:")
        for comm_id, label in sorted(labels.items()):
            size = len(communities[comm_id])
            self.logger.info(f"  [{comm_id}] {label} ({size} channels)")
        
        return labels, stats
    
    def _step_visualize(self, graph, partition, labels):
        """Visualization step."""
        viz = Visualizer(figsize=self.config.analysis.static_viz_figsize)
        
        if self.config.analysis.enable_static_viz:
            viz.visualize_static(
                graph,
                partition,
                labels,
                output_file=f"{self.config.analysis.output_dir}/community_graph.png",
                show_labels=self.config.analysis.show_node_labels,
                edge_threshold=None
            )
            self.logger.info("✓ Static visualization saved")
        
        if self.config.analysis.enable_interactive_viz:
            try:
                viz.visualize_interactive(
                    graph,
                    partition,
                    labels,
                    output_file=f"{self.config.analysis.output_dir}/community_graph.html"
                )
                self.logger.info("✓ Interactive visualization saved")
            except Exception as e:
                self.logger.warning(f"Interactive visualization failed: {e}")
    
    def _step_save_results(self, partition, labels, graph, aggregator, 
                          detection_stats, tagging_stats):
        """Save results to files."""
        graph_stats = {
            "num_nodes": graph.number_of_nodes(),
            "num_edges": graph.number_of_edges(),
            "density": graph.number_of_edges() / (graph.number_of_nodes() * (graph.number_of_nodes() - 1) / 2) if graph.number_of_nodes() > 1 else 0
        }
        
        results = {
            "timestamp": datetime.now().isoformat(),
            "config": {
                "overlap_threshold": self.config.analysis.overlap_threshold,
                "resolution": self.config.analysis.resolution,
                "min_channel_viewers": self.config.analysis.min_channel_viewers
            },
            "partition": partition,
            "labels": labels,
            "statistics": {
                "graph": graph_stats,
                "detection": detection_stats,
                "tagging": tagging_stats,
                "aggregator": aggregator.get_statistics()
            }
        }
        
        if self.config.analysis.save_analysis_json:
            results_key = "processed/analysis_results.json"
            self.storage.upload_json(results_key, results)
            self.logger.info(f"✓ Results saved to {self.storage.get_uri(results_key)}")
    
    def wait_until_next_hour(self):
        """Wait until top of hour (or configured interval)."""
        now = datetime.now()
        next_trigger = now.replace(minute=0, second=0, microsecond=0)
        if now.minute != 0 or now.second != 0:
            next_trigger = next_trigger.replace(hour=(now.hour + 1) % 24)
        
        wait_seconds = (next_trigger - now).total_seconds()
        self.logger.info(f"⏰ Waiting {int(wait_seconds)}s until next cycle...")
        time.sleep(wait_seconds)
    
    @staticmethod
    def _split_batches(lst, batch_size):
        """Split list into batches."""
        for i in range(0, len(lst), batch_size):
            yield lst[i:i + batch_size]


# Entry point modes

async def mode_collect(config: PipelineConfig):
    """Data collection only mode."""
    runner = PipelineRunner(config)
    if not runner._validate_prerequisites('collect'):
        return
    
    while True:
        await runner.run_collection_cycle()
        if config.collection.wait_for_hour_alignment:
            runner.wait_until_next_hour()


async def mode_analyze(config: PipelineConfig):
    """Analysis only mode."""
    runner = PipelineRunner(config)
    if not runner._validate_prerequisites('analyze'):
        return
    
    result = runner.run_analysis_pipeline()
    print(f"\nResult: {result}")


async def mode_continuous(config: PipelineConfig):
    """Continuous collection + periodic analysis."""
    runner = PipelineRunner(config)
    if not runner._validate_prerequisites('continuous'):
        return
    
    collection_cycles = 0
    analysis_interval = 24  # Analyze every N cycles
    
    while True:
        await runner.run_collection_cycle()
        collection_cycles += 1
        
        if collection_cycles % analysis_interval == 0:
            runner.logger.info(f"Running periodic analysis (cycle {collection_cycles})...")
            runner.run_analysis_pipeline()
        
        if config.collection.wait_for_hour_alignment:
            runner.wait_until_next_hour()


def main():
    """Main entry point. Supports preset configs or YAML file."""
    # Parse arguments
    if len(sys.argv) < 2:
        mode = "analyze"
        config_arg = "default"
    else:
        mode = sys.argv[1]
        config_arg = sys.argv[2] if len(sys.argv) > 2 else "default"
    
    # Check if it's a YAML file
    if config_arg.endswith(".yaml") or config_arg.endswith(".yml"):
        try:
            config = load_config_from_yaml(config_arg)
            logger_msg = f"Loaded config from {config_arg}"
        except FileNotFoundError:
            print(f"✗ Config file not found: {config_arg}")
            return
        except ImportError as e:
            print(f"✗ {e}")
            return
        except Exception as e:
            print(f"✗ Error loading config: {e}")
            return
    else:
        # Use preset config
        config_map = {
            "default": get_default_config,
            "rigorous": get_rigorous_config,
            "explorer": get_exploratory_config,
            "debug": get_debug_config
        }
        
        if config_arg not in config_map:
            print(f"Unknown config: {config_arg}")
            print(f"Available: {', '.join(config_map.keys())}")
            return
        
        config = config_map[config_arg]()
        logger_msg = f"Using '{config_arg}' config"
    
    # Setup logging
    setup_logging(config)
    
    logger = logging.getLogger(__name__)
    logger.info(f"Starting in '{mode}' mode")
    logger.info(logger_msg)
    logger.info(f"Output directory: {config.analysis.output_dir}\n")
    
    # Run mode
    if mode == "collect":
        asyncio.run(mode_collect(config))
    elif mode == "analyze":
        asyncio.run(mode_analyze(config))
    elif mode == "continuous":
        asyncio.run(mode_continuous(config))
    else:
        print(f"Usage: python main.py [collect|analyze|continuous] [config_name_or_yaml_file]")
        print(f"\nModes: collect, analyze, continuous")
        print(f"\nPreset Configs: default, rigorous, explorer, debug")
        print(f"\nExamples:")
        print(f"  python main.py analyze                    # Default config")
        print(f"  python main.py analyze rigorous           # TwitchAtlas-style")
        print(f"  python main.py analyze config.yaml        # Custom YAML config")


if __name__ == "__main__":
    main()
