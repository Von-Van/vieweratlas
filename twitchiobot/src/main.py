"""ViewerAtlas one-shot EventSub survey and analysis entry point.

Production collection is launched by EventBridge Scheduler with the ``survey``
command. The former Twitch IRC ``collect`` and ``continuous`` commands are
deliberately retired.
"""

import asyncio
import os
import signal
import sys
import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv

from get_viewers import load_channels
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
from vod_collector import VODCollector
from frontend_exporter import FrontendExportConfig, export_frontend_data
from eventsub_survey import (
    EventSubSurveyRunner,
    TwitchEventSubClient,
    make_survey_session_id,
)
from survey_lease import get_survey_lease
from twitch_credentials import get_credential_store
from update_channels import TopStreamsProvider

load_dotenv()

# Graceful shutdown support (ECS sends SIGTERM before SIGKILL)
_shutdown_requested = False

def _handle_sigterm(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    logging.getLogger(__name__).info("SIGTERM received, finishing current cycle before exit...")

signal.signal(signal.SIGTERM, _handle_sigterm)


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

        if config.vod and config.vod.enabled:
            if config.vod.bucket_len_s != config.collection.duration_per_batch:
                self.logger.warning(
                    "VOD bucket_len_s (%s) differs from live collection duration_per_batch (%s). "
                    "Presence bucket windows should be consistent system-wide.",
                    config.vod.bucket_len_s,
                    config.collection.duration_per_batch
                )
    
    def _validate_prerequisites(self, mode: str) -> bool:
        """
        Validate that all prerequisites are met.
        
        Args:
            mode: Pipeline mode. Only analysis requires dataset validation.
        
        Returns:
            True if valid, False otherwise
        """
        self.logger.info("Validating prerequisites...")
        
        if mode == 'analyze':
            if not self._validate_analysis_inputs():
                return False
        
        # Check for required libraries
        try:
            import community
            self.logger.info("✓ python-louvain available")
        except ImportError:
            self.logger.warning("⚠ python-louvain not installed. Community detection will fail.")
            self.logger.info("   Install with: pip install python-louvain")
        
        self.logger.info("Prerequisites validation complete ✓\n")
        return True

    def _validate_analysis_inputs(self, require_data: bool = True) -> bool:
        """
        Validate that analysis inputs are accessible for the configured storage backend.

        Args:
            require_data: If True, fail when no input data is found.
                         If False, log warnings and continue.
        """
        # Count every input format DataAggregator.load_all() actually reads.
        # EventSub surveys and legacy collectors write Parquet under
        # raw/snapshots/, so a JSON-only check would reject a healthy dataset.
        snapshot_parquet = self.storage.list_files(prefix="raw/snapshots", suffix=".parquet")
        snapshot_json = self.storage.list_files(prefix="raw/snapshots", suffix=".json")
        vod_json = self.storage.list_files(
            prefix="curated/presence_snapshots/source=vod",
            suffix=".json"
        )
        vod_parquet = self.storage.list_files(
            prefix="curated/presence_snapshots/source=vod",
            suffix=".parquet"
        )
        counts = {
            "live parquet": len(snapshot_parquet),
            "live JSON": len(snapshot_json),
            "VOD parquet": len(vod_parquet),
            "VOD JSON": len(vod_json),
        }

        if self.config.storage_type != "s3":
            # Legacy local layout: flat JSON/CSV snapshots directly in logs_dir.
            logs_path = Path(self.config.analysis.logs_dir)
            if logs_path.exists():
                counts["legacy JSON"] = len(list(logs_path.glob("*.json")))
                counts["legacy CSV"] = len(list(logs_path.glob("*.csv")))

        if sum(counts.values()) == 0:
            location = (
                f"s3://{self.config.s3_bucket}/{self.config.s3_prefix}"
                if self.config.storage_type == "s3"
                else self.config.analysis.logs_dir
            )
            msg = (
                f"No analysis input data found under {location} "
                "('raw/snapshots' or 'curated/presence_snapshots/source=vod')"
            )
            if require_data:
                self.logger.error(f"❌ {msg}")
                return False
            self.logger.warning(f"⚠ {msg}. Continuing because data was not required.")
            return True

        summary = ", ".join(f"{count} {name}" for name, count in counts.items() if count)
        self.logger.info(f"✓ Found analysis inputs: {summary}")
        return True
    
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
            partition, communities, detection_stats, graph = self._step_detect_communities(graph)
            if partition is None:
                return {"status": "error", "message": "Community detection failed"}
            if not partition:
                return {
                    "status": "error",
                    "message": (
                        f"No community met min_community_size="
                        f"{self.config.analysis.min_community_size}"
                    ),
                }

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
                detection_stats, tagging_stats,
                communities
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
        aggregator = DataAggregator(
            self.config.analysis.logs_dir,
            storage=self.storage,
            window_days=self.config.analysis.analysis_window_days,
        )
        json_count, csv_count, vod_count, parquet_count = aggregator.load_all()
        self.logger.info(
            f"Loaded {json_count} JSON + {csv_count} CSV + {vod_count} VOD + {parquet_count} Parquet snapshots"
        )
        self.logger.info(
            f"Viewer set memory estimate: {aggregator.get_viewer_memory_estimate_mb():.1f} MB"
        )
        
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
        
        # Drop one-off viewers before sizing channels, so the size filter sees the
        # same viewer sets the graph will be built from.
        if self.config.analysis.min_user_appearances > 1:
            original_viewers = sum(len(v) for v in channel_viewers.values())
            channel_viewers = aggregator.filter_by_repeat_viewers(
                self.config.analysis.min_user_appearances
            )
            remaining_viewers = sum(len(v) for v in channel_viewers.values())
            self.logger.info(
                f"Filtered viewers: {original_viewers} → {remaining_viewers} "
                f"(seen in min {self.config.analysis.min_user_appearances} channels)"
            )

        # Channels sampled once contribute a single window; drop them before
        # any size or overlap filtering so thin cohorts do not distort weights.
        if self.config.analysis.min_channel_observations > 1:
            original_count = len(channel_viewers)
            keep = aggregator.filter_channels_by_observations(
                self.config.analysis.min_channel_observations
            )
            channel_viewers = {c: v for c, v in channel_viewers.items() if c in keep}
            self.logger.info(
                f"Filtered channels: {original_count} -> {len(channel_viewers)} "
                f"(min {self.config.analysis.min_channel_observations} observations)"
            )

        # Apply filtering if configured
        if self.config.analysis.min_channel_viewers > 1:
            original_count = len(channel_viewers)
            channel_viewers = {
                ch: viewers for ch, viewers in channel_viewers.items()
                if len(viewers) >= self.config.analysis.min_channel_viewers
            }
            self.logger.info(f"Filtered channels: {original_count} → {len(channel_viewers)} "
                           f"(min {self.config.analysis.min_channel_viewers} viewers)")

        builder = GraphBuilder(
            overlap_threshold=self.config.analysis.overlap_threshold,
            include_isolated_nodes=self.config.analysis.include_isolated_nodes,
            weighting_mode=self.config.analysis.weighting_mode,
            normalized_overlap_threshold=self.config.analysis.normalized_overlap_threshold,
        )
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
            nodes_path = f"{self.config.analysis.output_dir}/graph_nodes.csv"
            edges_path = f"{self.config.analysis.output_dir}/graph_edges.csv"
            builder.export_nodes_csv(nodes_path)
            builder.export_edges_csv(edges_path)
            self.logger.info(f"Exported graph data to {self.config.analysis.output_dir}/")
            # Upload to S3 so outputs survive container exit
            if self.storage:
                date_str = datetime.now().strftime("%Y-%m-%d")
                self.storage.upload_file(f"curated/analysis/{date_str}/graph_nodes.csv", nodes_path)
                self.storage.upload_file(f"curated/analysis/{date_str}/graph_edges.csv", edges_path)
                self.logger.info(f"Uploaded graph CSVs to S3 curated/analysis/{date_str}/")
        
        return graph
    
    def _step_detect_communities(self, graph) -> tuple:
        """Community detection step.

        Returns (partition, communities, stats, graph). The graph is narrowed to
        the retained channels when min_community_size discards any, so every
        downstream stage sees the same node set as the partition.
        """
        detector = CommunityDetector(
            resolution=self.config.analysis.resolution,
            min_community_size=self.config.analysis.min_community_size,
        )

        try:
            partition = detector.detect_communities(graph)
        except ImportError as e:
            self.logger.error(f"Community detection failed: {e}")
            return None, None, None, graph

        if detector.discarded_channels:
            self.logger.info(
                f"Dropped {len(detector.discarded_channels)} channels in communities "
                f"smaller than {self.config.analysis.min_community_size}"
            )
            graph = graph.subgraph(partition.keys()).copy()

        communities = detector.get_communities()
        stats = detector.get_statistics()

        self.logger.info(f"Communities detected:")
        self.logger.info(f"  Count: {stats['num_communities']}")
        self.logger.info(f"  Modularity: {stats['modularity']:.4f}")
        self.logger.info(f"  Largest: {stats['largest_community_size']} channels")
        self.logger.info(f"  Smallest: {stats['smallest_community_size']} channels")

        return partition, communities, stats, graph
    
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
                edge_threshold=None,
                label_top_n=self.config.analysis.label_top_n_nodes,
                dpi=self.config.analysis.static_viz_dpi,
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
                          detection_stats, tagging_stats, communities=None) -> None:
        """Persist both required analysis artifacts or fail the scheduled run."""
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
            if not self.storage.upload_json(results_key, results):
                raise IOError("Could not persist private analysis results")
            self.logger.info(f"✓ Results saved to {self.storage.get_uri(results_key)}")

        # Export frontend-ready JSON for S3/CloudFront serving
        if communities is not None:
            exported = export_frontend_data(
                graph=graph,
                partition=partition,
                communities=communities,
                labels=labels,
                detection_stats=detection_stats,
                aggregator_stats=aggregator.get_statistics(),
                storage=self.storage,
                config=FrontendExportConfig(
                    max_channels=self.config.analysis.frontend_max_channels,
                    max_edges=self.config.analysis.frontend_max_edges,
                    top_edges_per_channel=self.config.analysis.frontend_top_edges_per_channel,
                ),
            )
            if not exported:
                raise IOError("Could not persist public frontend data")
            self.logger.info("✓ Frontend data exported")
    
# Entry point modes


async def mode_survey(config: PipelineConfig):
    """Run exactly one EventSub survey and exit for EventBridge Scheduler."""
    session_id = os.getenv("SURVEY_SESSION_ID") or make_survey_session_id()
    lease = get_survey_lease()
    if not lease.acquire(session_id):
        logging.getLogger(__name__).warning(
            "A survey is already running; this scheduled invocation will exit without collecting"
        )
        return {"status": "skipped", "reason": "lease_held"}

    try:
        credential_store = get_credential_store()
        credentials = credential_store.load()
        runner = PipelineRunner(config)
        provider = TopStreamsProvider(
            client_id=credentials.client_id,
            access_token=credentials.access_token,
        )
        client = TwitchEventSubClient(credentials, credential_store)
        survey = EventSubSurveyRunner(
            storage=runner.storage,
            target_provider=provider,
            client=client,
            top_channels_limit=config.collection.top_channels_limit,
            batch_size=config.collection.batch_size,
            window_seconds=config.collection.duration_per_batch,
            timeout_seconds=config.collection.survey_timeout_seconds,
            subscription_retries=config.collection.subscription_retries,
            batch_retries=config.collection.batch_retries,
            should_stop=lambda: _shutdown_requested,
            on_batch_complete=lambda: lease.renew(session_id),
            secrets=(
                credentials.client_secret,
                credentials.access_token,
                credentials.refresh_token,
            ),
        )
        result = await survey.run(session_id=session_id)
        Path(os.getenv("LOGS_DIR", "logs"), ".heartbeat").touch()
        return result
    finally:
        try:
            lease.release(session_id)
        except Exception:
            logging.getLogger(__name__).exception("Failed to release the survey lease")


async def mode_analyze(config: PipelineConfig) -> bool:
    """Run one analysis task and report a scheduler-safe success value."""
    logger = logging.getLogger(__name__)
    runner = PipelineRunner(config)
    if not runner._validate_prerequisites('analyze'):
        logger.error("ANALYSIS_FAILED reason=prerequisites")
        return False
    
    result = runner.run_analysis_pipeline()
    if result.get("status") != "success":
        logger.error("ANALYSIS_FAILED reason=pipeline")
        return False
    logger.info(
        "ANALYSIS_COMPLETED channels=%s communities=%s edges=%s",
        result.get("num_channels", 0),
        result.get("num_communities", 0),
        result.get("num_edges", 0),
    )
    return True


async def mode_preprocess_vods(config: PipelineConfig, max_vods: Optional[int] = None):
    """VOD preprocessing mode: discover, queue, and process VOD chats."""
    logger = logging.getLogger(__name__)

    if not config.vod.enabled:
        logger.warning("VOD collection is disabled in config but preprocess_vods was requested. Proceeding anyway.")

    storage = get_storage(
        storage_type=config.storage_type,
        bucket=config.s3_bucket,
        prefix=config.s3_prefix,
        region=config.s3_region
    )

    collector = VODCollector(
        storage=storage,
        queue_file=config.vod.queue_file,
        raw_dir=config.vod.raw_dir,
        persist_raw_chat=config.vod.persist_raw_chat,
        bucket_len_s=config.vod.bucket_len_s,
        cli_path=config.vod.cli_path,
        max_age_hours=config.vod.max_age_hours,
        min_views=config.vod.min_views,
        max_processing_hours=config.vod.max_processing_hours,
        rate_limit_delay_s=config.vod.rate_limit_delay_s
    )

    # Cost protection: limit VODs to process
    effective_max = max_vods or config.vod.max_vods_per_run
    if effective_max:
        logger.warning(f"⚠️  Cost Protection: Max {effective_max} VODs will be processed this run")

    if config.vod.auto_discover:
        channels = load_channels()
        if channels:
            logger.info(f"Auto-discovering VODs for {len(channels)} channels (limit {config.vod.vod_limit_per_channel} each)")
            collector.add_vods_for_channels(channels, vod_limit=config.vod.vod_limit_per_channel)
        else:
            logger.warning("No channels found for auto-discovery; skipping queue population")

    collector.process_all_pending(max_vods=effective_max)


def main() -> int:
    """Main entry point. Supports preset configs or YAML file."""
    # Parse arguments
    if len(sys.argv) < 2:
        mode = "analyze"
        config_arg = "default"
    else:
        mode = sys.argv[1]
        config_arg = sys.argv[2] if len(sys.argv) > 2 else "default"
    max_vods = None
    if mode == "preprocess_vods" and len(sys.argv) > 3:
        try:
            max_vods = int(sys.argv[3])
        except ValueError:
            print("Warning: max_vods argument must be an integer; ignoring")
    
    # Check if it's a YAML file
    if config_arg.endswith(".yaml") or config_arg.endswith(".yml"):
        try:
            config = load_config_from_yaml(config_arg)
            logger_msg = f"Loaded config from {config_arg}"
        except FileNotFoundError:
            print(f"✗ Config file not found: {config_arg}")
            return 1
        except ImportError as e:
            print(f"✗ {e}")
            return 1
        except Exception as e:
            print(f"✗ Error loading config: {e}")
            return 1
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
            return 1
        
        config = config_map[config_arg]()
        logger_msg = f"Using '{config_arg}' config"
    
    # Setup logging
    setup_logging(config)
    
    logger = logging.getLogger(__name__)
    logger.info(f"Starting in '{mode}' mode")
    logger.info(logger_msg)
    logger.info(f"Output directory: {config.analysis.output_dir}\n")
    
    # Run mode
    if mode == "survey":
        try:
            asyncio.run(mode_survey(config))
        except KeyboardInterrupt:
            logger.error("SURVEY_TASK_FAILED reason=operator_interrupt")
            return 130
        except Exception:
            # OAuth libraries may embed the rejected token in an exception's
            # text. Never interpolate or attach the traceback at this outer
            # boundary; the redacted survey manifest carries safe diagnostics.
            logger.error(
                "SURVEY_TASK_FAILED reason=collection_error; review the redacted survey manifest"
            )
            return 1
    elif mode == "analyze":
        if not asyncio.run(mode_analyze(config)):
            return 1
    elif mode == "preprocess_vods":
        asyncio.run(mode_preprocess_vods(config, max_vods=max_vods))
    else:
        print(f"Usage: python main.py [survey|analyze|preprocess_vods] [config_name_or_yaml_file]")
        print(f"\nModes: survey, analyze, preprocess_vods")
        print(f"\nPreset Configs: default, rigorous, explorer, debug")
        print(f"\nExamples:")
        print(f"  python main.py analyze                    # Default config")
        print(f"  python main.py survey config.yaml         # One EventSub survey")
        print(f"  python main.py analyze rigorous           # TwitchAtlas-style")
        print(f"  python main.py analyze config.yaml        # Custom YAML config")
        print(f"  python main.py preprocess_vods config.yaml 5  # Process up to 5 queued VODs")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
