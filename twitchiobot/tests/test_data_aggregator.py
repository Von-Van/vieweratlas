import json
from datetime import date

import pytest

from community_detector import LOUVAIN_AVAILABLE, CommunityDetector
from config import AnalysisConfig
from data_aggregator import DataAggregator, survey_date_span
from graph_builder import GraphBuilder
from survey_fakes import InMemoryStorage, parquet_bytes, survey_storage, v2_batch_bytes


class TestDataAggregator:
    """Tests for data loading, viewer set building, and statistics."""

    def test_load_json_snapshots_count(self, tmp_logs_dir):
        agg = DataAggregator(str(tmp_logs_dir))
        agg.storage = None
        json_count = agg.load_json_snapshots()
        assert json_count == 5, f"Expected 5 snapshots loaded, got {json_count}"

    def test_channel_viewers_keys(self, aggregator):
        viewers = aggregator.get_channel_viewers()
        expected_channels = {"streamer_a", "streamer_b", "streamer_c", "streamer_d", "streamer_e"}
        assert set(viewers.keys()) == expected_channels

    def test_channel_viewers_sets(self, aggregator):
        viewers = aggregator.get_channel_viewers()
        assert viewers["streamer_a"] == {"alice", "bob", "carol", "dave", "eve"}
        assert viewers["streamer_b"] == {"alice", "bob", "carol", "frank", "grace"}
        assert viewers["streamer_c"] == {"dave", "hank", "iris", "jose"}

    def test_unique_viewers_across_all(self, aggregator):
        stats = aggregator.get_statistics()
        # All unique: alice bob carol dave eve frank grace hank iris jose kate leo zara yolanda = 14
        assert stats["total_unique_viewers_across_all"] == 14

    def test_channel_metadata_game(self, aggregator):
        meta = aggregator.get_channel_metadata()
        assert meta["streamer_a"]["game_name"] == "Valorant"
        assert meta["streamer_c"]["game_name"] == "League of Legends"

    def test_filter_channels_by_size(self, aggregator):
        filtered = aggregator.filter_channels_by_size(min_viewers=4)
        # streamer_a=5, streamer_b=5, streamer_c=4, streamer_d=6 pass; streamer_e=2 fails
        assert "streamer_e" not in filtered
        assert "streamer_a" in filtered
        assert "streamer_d" in filtered

    def test_user_channel_map(self, aggregator):
        ucm = aggregator.get_user_channel_map()
        # alice appears in streamer_a, streamer_b, streamer_d
        assert ucm["alice"] == {"streamer_a", "streamer_b", "streamer_d"}
        # zara only in streamer_e
        assert ucm["zara"] == {"streamer_e"}

    def test_filter_by_repeat_viewers(self, aggregator):
        filtered = aggregator.filter_by_repeat_viewers(min_appearances=2)
        # streamer_e has only zara+yolanda who each appear in 1 channel => excluded
        assert "streamer_e" not in filtered
        # streamer_a should still be present (alice, bob, carol, dave, eve — most appear in 2+ channels)
        assert "streamer_a" in filtered

    def test_data_quality_report(self, aggregator):
        report = aggregator.get_data_quality_report()
        assert report["total_channels"] == 5
        assert report["total_unique_viewers"] == 14
        assert report["total_snapshots"] == 5
        assert report["one_off_viewers"] == 7

    def test_load_empty_directory(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        agg = DataAggregator(str(empty_dir))
        agg.storage = None
        json_count, csv_count, vod_count, parquet_count = agg.load_all()
        assert json_count == 0
        assert csv_count == 0
        assert agg.get_channel_viewers() == {}

    def test_csv_username_column_loads_correctly(self, tmp_path):
        """Regression: CSV written with 'username' header must be read correctly."""
        csv_dir = tmp_path / "csv_logs"
        csv_dir.mkdir()
        csv_file = csv_dir / "test_channel_20250101_120000.csv"
        csv_file.write_text(
            "timestamp,channel,viewer_count,game_name,title,started_at,username\n"
            "2025-01-01T12:00:00,test_ch,1000,Valorant,Test,2025-01-01T10:00:00,alice\n"
            "2025-01-01T12:00:00,test_ch,1000,Valorant,Test,2025-01-01T10:00:00,bob\n"
        )
        agg = DataAggregator(str(csv_dir))
        agg.storage = None
        csv_count = agg.load_csv_snapshots()
        assert csv_count == 2
        viewers = agg.get_channel_viewers()
        assert "test_ch" in viewers
        assert viewers["test_ch"] == {"alice", "bob"}


@pytest.fixture
def tmp_vod_snapshots_dir(tmp_path):
    """Create a temporary logs/vod_snapshots directory with VOD JSON snapshot files."""
    vod_dir = tmp_path / "logs" / "vod_snapshots"
    vod_dir.mkdir(parents=True)

    vod_snap_1 = {
        "channel": "streamer_v1",
        "timestamp": "2025-01-01T14:00:00",
        "viewer_count": 1200,
        "game_name": "Minecraft",
        "title": "VOD: Sunday session",
        "started_at": "2025-01-01T12:00:00",
        "chatters": ["alpha", "beta", "gamma"],
        "_source": "vod",
    }
    vod_snap_2 = {
        "channel": "streamer_v2",
        "timestamp": "2025-01-01T14:05:00",
        "viewer_count": 800,
        "game_name": "Minecraft",
        "title": "VOD: Monday highlights",
        "started_at": "2025-01-01T13:00:00",
        "chatters": ["beta", "delta", "epsilon"],
        "_source": "vod",
    }

    for i, snap in enumerate([vod_snap_1, vod_snap_2]):
        filepath = vod_dir / f"snapshot_{i:03d}.json"
        with open(filepath, "w") as f:
            json.dump(snap, f)

    return tmp_path / "logs"


class TestVODSnapshotLoading:
    """Tests for VOD snapshot ingestion from local filesystem."""

    def test_load_vod_snapshots_count(self, tmp_vod_snapshots_dir):
        agg = DataAggregator(str(tmp_vod_snapshots_dir))
        agg.storage = None
        count = agg.load_vod_snapshots()
        assert count == 2, f"Expected 2 VOD snapshots loaded, got {count}"

    def test_vod_viewer_sets_correct(self, tmp_vod_snapshots_dir):
        agg = DataAggregator(str(tmp_vod_snapshots_dir))
        agg.storage = None
        agg.load_vod_snapshots()
        viewers = agg.get_channel_viewers()
        assert viewers["streamer_v1"] == {"alpha", "beta", "gamma"}
        assert viewers["streamer_v2"] == {"beta", "delta", "epsilon"}

    def test_vod_source_count_tracked(self, tmp_vod_snapshots_dir):
        agg = DataAggregator(str(tmp_vod_snapshots_dir))
        agg.storage = None
        agg.load_vod_snapshots()
        assert agg.snapshot_source_counts.get("vod", 0) == 2

    def test_load_all_vod_count(self, tmp_vod_snapshots_dir):
        agg = DataAggregator(str(tmp_vod_snapshots_dir))
        agg.storage = None
        json_count, csv_count, vod_count, parquet_count = agg.load_all()
        assert vod_count == 2

    def test_vod_metadata_stored(self, tmp_vod_snapshots_dir):
        agg = DataAggregator(str(tmp_vod_snapshots_dir))
        agg.storage = None
        agg.load_vod_snapshots()
        meta = agg.get_channel_metadata()
        assert meta["streamer_v1"]["game_name"] == "Minecraft"
        assert meta["streamer_v1"]["viewer_count"] == 1200

    def test_empty_vod_dir_returns_zero(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        agg = DataAggregator(str(logs_dir))
        agg.storage = None
        count = agg.load_vod_snapshots()
        assert count == 0

    def test_vod_graph_edges_on_overlap(self, tmp_vod_snapshots_dir):
        """VOD data flows into the graph with correct overlap weights."""
        agg = DataAggregator(str(tmp_vod_snapshots_dir))
        agg.storage = None
        agg.load_vod_snapshots()
        viewers = agg.get_channel_viewers()

        builder = GraphBuilder(overlap_threshold=1)
        g = builder.build_graph(viewers)

        # streamer_v1 and streamer_v2 share 'beta' => weight 1
        assert g.has_edge("streamer_v1", "streamer_v2")
        assert g["streamer_v1"]["streamer_v2"]["weight"] == 1


class TestS3Integration:
    """Tests for S3 storage backend path through DataAggregator (mocked)."""

    @pytest.fixture
    def live_snapshots(self):
        return [
            {
                "channel": "s3_streamer_a",
                "timestamp": "2025-02-01T10:00:00",
                "viewer_count": 500,
                "game_name": "Apex Legends",
                "title": "S3 live stream",
                "chatters": ["user1", "user2", "user3"],
            },
            {
                "channel": "s3_streamer_b",
                "timestamp": "2025-02-01T10:00:00",
                "viewer_count": 300,
                "game_name": "Apex Legends",
                "title": "S3 live stream B",
                "chatters": ["user2", "user3", "user4"],
            },
        ]

    @pytest.fixture
    def vod_snapshots(self):
        return [
            {
                "channel": "s3_vod_channel",
                "timestamp": "2025-02-01T08:00:00",
                "viewer_count": 200,
                "game_name": "Apex Legends",
                "title": "VOD replay",
                "chatters": ["user1", "user5"],
                "_source": "vod",
            }
        ]

    def test_s3_load_json_snapshots(self, live_snapshots, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=InMemoryStorage(live_snapshots=live_snapshots))
        count = agg.load_json_snapshots()
        assert count == 2

    def test_s3_channels_populated(self, live_snapshots, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=InMemoryStorage(live_snapshots=live_snapshots))
        agg.load_json_snapshots()
        viewers = agg.get_channel_viewers()
        assert "s3_streamer_a" in viewers
        assert "s3_streamer_b" in viewers
        assert viewers["s3_streamer_a"] == {"user1", "user2", "user3"}

    def test_s3_load_vod_snapshots(self, vod_snapshots, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=InMemoryStorage(vod_snapshots=vod_snapshots))
        count = agg.load_vod_snapshots()
        assert count == 1
        viewers = agg.get_channel_viewers()
        assert "s3_vod_channel" in viewers
        assert viewers["s3_vod_channel"] == {"user1", "user5"}

    def test_s3_vod_source_tracked(self, vod_snapshots, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=InMemoryStorage(vod_snapshots=vod_snapshots))
        agg.load_vod_snapshots()
        assert agg.snapshot_source_counts.get("vod", 0) == 1

    def test_s3_load_all_returns_correct_counts(self, live_snapshots, vod_snapshots, tmp_path):
        storage = InMemoryStorage(live_snapshots=live_snapshots, vod_snapshots=vod_snapshots)
        agg = DataAggregator(str(tmp_path), storage=storage)
        json_count, csv_count, vod_count, parquet_count = agg.load_all()
        assert json_count == 2
        assert vod_count == 1

    def test_s3_graph_from_live_data(self, live_snapshots, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=InMemoryStorage(live_snapshots=live_snapshots))
        agg.load_json_snapshots()
        viewers = agg.get_channel_viewers()

        builder = GraphBuilder(overlap_threshold=1)
        g = builder.build_graph(viewers)

        # s3_streamer_a and s3_streamer_b share user2 and user3 => weight 2
        assert g.has_edge("s3_streamer_a", "s3_streamer_b")
        assert g["s3_streamer_a"]["s3_streamer_b"]["weight"] == 2


class TestParquetSnapshots:
    """Tests for Parquet-based snapshot loading via storage backend."""

    @pytest.fixture
    def parquet_storage(self):
        rows = [
            {
                "channel": "pq_streamer_a",
                "timestamp": "2025-03-01T10:00:00",
                "viewer_count": 500,
                "game_name": "Valorant",
                "title": "Ranked",
                "started_at": "2025-03-01T08:00:00",
                "language": "en",
                "chatters_json": '["alice", "bob", "carol"]',
            },
            {
                "channel": "pq_streamer_b",
                "timestamp": "2025-03-01T10:00:00",
                "viewer_count": 300,
                "game_name": "Fortnite",
                "title": "Squads",
                "started_at": "2025-03-01T09:00:00",
                "language": "en",
                "chatters_json": '["bob", "carol", "dave"]',
            },
        ]
        return InMemoryStorage(
            parquet_data={"raw/snapshots/2025/03/01/cycle_20250301_100000.parquet": parquet_bytes(rows)}
        )

    def test_load_parquet_snapshots_count(self, parquet_storage, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=parquet_storage)
        count = agg.load_parquet_snapshots()
        assert count == 2

    def test_parquet_viewer_sets_correct(self, parquet_storage, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=parquet_storage)
        agg.load_parquet_snapshots()
        viewers = agg.get_channel_viewers()
        assert viewers["pq_streamer_a"] == {"alice", "bob", "carol"}
        assert viewers["pq_streamer_b"] == {"bob", "carol", "dave"}

    def test_parquet_source_count_tracked(self, parquet_storage, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=parquet_storage)
        agg.load_parquet_snapshots()
        assert agg.snapshot_source_counts.get("live", 0) == 2

    def test_parquet_graph_edge_on_overlap(self, parquet_storage, tmp_path):
        """Parquet data flows into the graph with correct overlap weights."""
        agg = DataAggregator(str(tmp_path), storage=parquet_storage)
        agg.load_parquet_snapshots()
        viewers = agg.get_channel_viewers()

        builder = GraphBuilder(overlap_threshold=1)
        g = builder.build_graph(viewers)

        # pq_streamer_a and pq_streamer_b share bob, carol => weight 2
        assert g.has_edge("pq_streamer_a", "pq_streamer_b")
        assert g["pq_streamer_a"]["pq_streamer_b"]["weight"] == 2

    def test_parquet_metadata_stored(self, parquet_storage, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=parquet_storage)
        agg.load_parquet_snapshots()
        meta = agg.get_channel_metadata()
        assert meta["pq_streamer_a"]["game_name"] == "Valorant"
        assert meta["pq_streamer_a"]["viewer_count"] == 500

    def test_load_all_includes_parquet_count(self, parquet_storage, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=parquet_storage)
        result = agg.load_all()
        assert len(result) == 4
        json_count, csv_count, vod_count, parquet_count = result
        assert parquet_count == 2

    def test_v2_loader_keeps_completed_empty_rows_and_skips_failures(self, tmp_path):
        rows = [
            {
                "schema_version": 2,
                "channel": "completed_with_authors",
                "collection_status": "completed",
                "chatters_json": '["ALICE", "bob"]',
                "chatter_ids_json": '["1", "2"]',
            },
            {
                "schema_version": 2,
                "channel": "completed_empty",
                "collection_status": "completed",
                "chatters_json": "[]",
                "chatter_ids_json": "[]",
            },
            {
                "schema_version": 2,
                "channel": "failed_channel",
                "collection_status": "subscription_failed",
                "chatters_json": "[]",
                "chatter_ids_json": "[]",
            },
        ]
        storage = InMemoryStorage(
            parquet_data={
                "raw/snapshots/v2/date=2026-08-12/session=test/batch=01.parquet": parquet_bytes(rows)
            }
        )
        storage._json_uploads[
            "raw/snapshots/v2/date=2026-08-12/session=test/manifest.json"
        ] = {"status": "complete_with_errors"}

        agg = DataAggregator(str(tmp_path), storage=storage)
        assert agg.load_parquet_snapshots() == 2
        viewers = agg.get_channel_viewers()
        assert viewers["completed_with_authors"] == {"alice", "bob"}
        assert viewers["completed_empty"] == set()
        assert "failed_channel" not in viewers

    @pytest.mark.parametrize("manifest_status", [None, "running", "partial"])
    def test_v2_loader_skips_session_without_terminal_manifest(
        self, tmp_path, manifest_status
    ):
        prefix = "raw/snapshots/v2/date=2026-08-12/session=unfinished"
        storage = InMemoryStorage(
            parquet_data={f"{prefix}/batch=01.parquet": v2_batch_bytes("must_not_be_loaded", ["alice"])}
        )
        if manifest_status is not None:
            storage._json_uploads[f"{prefix}/manifest.json"] = {"status": manifest_status}

        agg = DataAggregator(str(tmp_path), storage=storage)
        assert agg.load_parquet_snapshots() == 0
        assert "must_not_be_loaded" not in agg.get_channel_viewers()


class TestObservationFiltering:
    """Channels sampled once cannot produce reliable overlap at any threshold."""

    def _agg_with(self, tmp_path, counts):
        agg = DataAggregator(str(tmp_path))
        for channel, n in counts.items():
            for i in range(n):
                agg._ingest_snapshot({"channel": channel, "chatters": [f"u{i}", "shared"]})
        return agg

    def test_observations_counted_per_channel(self, tmp_path):
        agg = self._agg_with(tmp_path, {"often": 5, "once": 1})
        assert agg.get_channel_observations() == {"often": 5, "once": 1}

    def test_filter_keeps_only_well_sampled_channels(self, tmp_path):
        agg = self._agg_with(tmp_path, {"often": 5, "twice": 2, "once": 1})
        kept = agg.filter_channels_by_observations(2)
        assert set(kept) == {"often", "twice"}

    def test_filter_of_one_is_a_no_op(self, tmp_path):
        agg = self._agg_with(tmp_path, {"often": 5, "once": 1})
        assert set(agg.filter_channels_by_observations(1)) == {"often", "once"}


class TestMixedLiveVOD:
    """Tests for pipelines combining live snapshots and VOD snapshots."""

    @pytest.fixture
    def mixed_logs_dir(self, tmp_path):
        """Create logs dir with both live JSON snapshots and vod_snapshots/ subfolder."""
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()

        # Live snapshot
        live_snap = {
            "channel": "live_channel",
            "timestamp": "2025-03-01T10:00:00",
            "viewer_count": 1000,
            "game_name": "Fortnite",
            "title": "Live now",
            "chatters": ["viewer_a", "viewer_b", "viewer_c"],
        }
        with open(logs_dir / "snapshot_live.json", "w") as f:
            json.dump(live_snap, f)

        # VOD snapshot (overlaps 1 viewer with live)
        vod_dir = logs_dir / "vod_snapshots"
        vod_dir.mkdir()
        vod_snap = {
            "channel": "vod_channel",
            "timestamp": "2025-03-01T09:00:00",
            "viewer_count": 500,
            "game_name": "Fortnite",
            "title": "VOD replay",
            "chatters": ["viewer_b", "viewer_d"],
            "_source": "vod",
        }
        with open(vod_dir / "snapshot_vod.json", "w") as f:
            json.dump(vod_snap, f)

        return logs_dir

    def test_load_all_counts_both_sources(self, mixed_logs_dir):
        agg = DataAggregator(str(mixed_logs_dir))
        agg.storage = None
        json_count, csv_count, vod_count, parquet_count = agg.load_all()
        assert json_count == 1
        assert vod_count == 1

    def test_source_counts_separate(self, mixed_logs_dir):
        agg = DataAggregator(str(mixed_logs_dir))
        agg.storage = None
        agg.load_all()
        assert agg.snapshot_source_counts.get("live", 0) == 1
        assert agg.snapshot_source_counts.get("vod", 0) == 1

    def test_mixed_graph_edge_on_shared_viewer(self, mixed_logs_dir):
        """Live and VOD channels sharing a viewer should produce a graph edge."""
        agg = DataAggregator(str(mixed_logs_dir))
        agg.storage = None
        agg.load_all()
        viewers = agg.get_channel_viewers()

        builder = GraphBuilder(overlap_threshold=1)
        g = builder.build_graph(viewers)

        # live_channel and vod_channel share viewer_b => weight 1
        assert g.has_edge("live_channel", "vod_channel")
        assert g["live_channel"]["vod_channel"]["weight"] == 1

    def test_mixed_pipeline_full_run(self, mixed_logs_dir):
        """Full pipeline run on mixed live+VOD data produces valid community output."""
        if not LOUVAIN_AVAILABLE:
            pytest.skip("python-louvain not installed")

        agg = DataAggregator(str(mixed_logs_dir))
        agg.storage = None
        agg.load_all()

        viewers = agg.get_channel_viewers()
        metadata = agg.get_channel_metadata()

        builder = GraphBuilder(overlap_threshold=1)
        g = builder.build_graph(viewers, metadata)

        detector = CommunityDetector(resolution=1.0)
        partition = detector.detect_communities(g)

        assert len(partition) == 2  # live_channel and vod_channel
        assert "live_channel" in partition
        assert "vod_channel" in partition


class TestAnalysisWindow:
    """Without a window, viewer sets only grow and graph density climbs over time."""

    ALL_DAYS = ["2026-05-01", "2026-06-15", "2026-08-10", "2026-08-11", "2026-08-12"]

    def test_no_window_loads_every_retained_survey(self, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=survey_storage(self.ALL_DAYS))
        assert agg.load_parquet_snapshots() == 5
        assert agg.window_start is None and agg.window_end is None

    def test_window_anchors_to_newest_snapshot_not_wall_clock(self, tmp_path):
        """Replays and backfills must reproduce the original graph."""
        agg = DataAggregator(
            str(tmp_path), storage=survey_storage(self.ALL_DAYS), window_days=3
        )
        assert agg.load_parquet_snapshots() == 3
        assert agg.window_end == date(2026, 8, 12)
        assert agg.window_start == date(2026, 8, 10)
        channels = agg.get_channel_viewers()
        assert set(channels) == {"ch_2026-08-10", "ch_2026-08-11", "ch_2026-08-12"}

    def test_window_history_ignores_unfinished_v2_surveys(self):
        """An early Parquet PUT is not data until its manifest commits it."""
        storage = survey_storage(["2026-08-12"])
        unfinished = "raw/snapshots/v2/date=2026-01-01/session=unfinished"
        storage._parquet[f"{unfinished}/batch=01.parquet"] = v2_batch_bytes("must_not_extend_history", ["alice"])
        storage._json_uploads[f"{unfinished}/manifest.json"] = {"status": "partial"}

        assert survey_date_span(storage) == (date(2026, 8, 12), date(2026, 8, 12))

    def test_window_boundary_is_inclusive(self, tmp_path):
        """window_days=N spans N distinct days, anchor included."""
        agg = DataAggregator(
            str(tmp_path), storage=survey_storage(self.ALL_DAYS), window_days=1
        )
        assert agg.load_parquet_snapshots() == 1
        assert agg.window_start == agg.window_end == date(2026, 8, 12)

    def test_window_larger_than_data_keeps_everything(self, tmp_path):
        agg = DataAggregator(
            str(tmp_path), storage=survey_storage(self.ALL_DAYS), window_days=3650
        )
        assert agg.load_parquet_snapshots() == 5

    def test_window_applies_to_legacy_date_partitioned_keys(self, tmp_path):
        parquet = {
            f"raw/snapshots/{day}/cycle_1.parquet": parquet_bytes(
                [{"channel": f"legacy_{day.replace('/', '')}", "chatters_json": '["bob"]'}]
            )
            for day in ["2026/08/01", "2026/08/12"]
        }

        agg = DataAggregator(
            str(tmp_path), storage=InMemoryStorage(parquet_data=parquet), window_days=2
        )
        assert agg.load_parquet_snapshots() == 1
        assert "legacy_20260812" in agg.get_channel_viewers()

    def test_undated_keys_are_never_dropped_by_the_window(self, tmp_path):
        """A window must not silently discard data whose date it cannot parse."""
        storage = survey_storage(["2026-08-12"])
        storage._parquet["raw/snapshots/oddball.parquet"] = v2_batch_bytes("undated_channel", ["carol"])
        agg = DataAggregator(str(tmp_path), storage=storage, window_days=1)
        agg.load_parquet_snapshots()
        assert "undated_channel" in agg.get_channel_viewers()

    def test_window_reported_in_statistics_and_collection_period(self, tmp_path):
        agg = DataAggregator(
            str(tmp_path), storage=survey_storage(self.ALL_DAYS), window_days=3
        )
        agg.load_parquet_snapshots()
        stats = agg.get_statistics()
        assert stats["analysis_window_days"] == 3
        assert stats["window_start"] == "2026-08-10"
        assert stats["window_end"] == "2026-08-12"
        assert stats["collection_period"] == "Aug 10 – Aug 12, 2026"

    def test_window_reports_days_covered_not_days_requested(self, tmp_path):
        """collection_period goes straight onto the public site.

        A 90-day window over 60 days of surveys must not advertise a month of
        data that was never collected.
        """
        agg = DataAggregator(
            str(tmp_path), storage=survey_storage(["2026-08-10", "2026-08-11", "2026-08-12"]),
            window_days=90,
        )
        agg.load_parquet_snapshots()
        stats = agg.get_statistics()

        # Requested 90 days; only three exist, so that is what is reported.
        assert stats["window_start"] == "2026-08-10"
        assert stats["window_end"] == "2026-08-12"
        assert stats["collection_period"] == "Aug 10 – Aug 12, 2026"
        # The requested length is still recorded, so the shortfall is visible.
        assert stats["analysis_window_days"] == 90

    def test_invalid_window_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            DataAggregator(str(tmp_path), window_days=0)
        with pytest.raises(ValueError):
            AnalysisConfig(analysis_window_days=0)
