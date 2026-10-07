import asyncio
import logging
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import networkx as nx
import pytest

import main as app_main
from config import AnalysisConfig, CollectionConfig, PipelineConfig, get_rigorous_config
from storage import FileStorage
from survey_fakes import InMemoryStorage, survey_window_storage


class TestAutomatedChatterLogging:
    """Analysis logs how many accounts were excluded, never which."""

    def test_analysis_excludes_automated_accounts_and_logs_counts_only(
        self, tmp_path, caplog
    ):
        """Logs sit outside the private data boundary, and a behavioural rule
        can misjudge a person, so no excluded account may ever be named."""
        runner = app_main.PipelineRunner.__new__(app_main.PipelineRunner)
        runner.config = PipelineConfig(
            analysis=AnalysisConfig(logs_dir=str(tmp_path), output_dir=str(tmp_path / "out"))
        )
        runner.logger = logging.getLogger("test")
        runner.storage = survey_window_storage(
            {f"ch{i}": [f"viewer{i}", "farmaccount", "nightbot"] for i in range(4)}
        )
        caplog.set_level("INFO")

        aggregator = runner._step_aggregate(None)

        assert aggregator.get_channel_viewers() == {
            f"ch{i}": {f"viewer{i}"} for i in range(4)
        }
        assert (
            "AUTOMATED_CHATTERS_EXCLUDED accounts=2 known_bots=1 listed=0 concurrent=1"
            in caplog.text
        )
        assert "farmaccount" not in caplog.text
        assert "nightbot" not in caplog.text


class TestWindowPlan:
    """Which windows are analysed, published, or left pending."""

    @staticmethod
    def _plan(window_days, windows, covered_days):
        """window_plan() for a deployment holding `covered_days` of surveys."""
        runner = app_main.PipelineRunner.__new__(app_main.PipelineRunner)
        runner.config = PipelineConfig(
            collection=CollectionConfig(),
            analysis=AnalysisConfig(analysis_window_days=window_days, analysis_windows=windows),
        )
        runner.logger = logging.getLogger("test")
        runner.storage = MagicMock()
        end = date(2026, 8, 26)
        span = (end - timedelta(days=covered_days - 1), end) if covered_days else (None, None)
        with patch("main.survey_date_span", return_value=span):
            return runner.window_plan()

    def test_windows_short_of_data_are_pending_not_analysed(self):
        """A 90-day window over 14 days of surveys is not a 90-day window.

        Analysing it would spend a full extra pass republishing the 14-day
        graph, so it waits and the browser shows PENDING instead.
        """
        plan = self._plan(30, (14, 30, 90), covered_days=14)
        assert plan["available"] == [14]
        assert plan["pending"] == [30, 90]
        # 30 is configured canonical but not ready, so the widest ready window
        # becomes the default rather than opening on a window with no file.
        assert plan["default"] == 14

    def test_windows_promote_themselves_as_surveys_accumulate(self):
        """No redeploy should be needed for a window to start working."""
        before_14 = self._plan(30, (14, 30, 90), 13)
        assert before_14["available"] == []
        assert before_14["pending"] == [14, 30, 90]
        assert before_14["default"] == 14

        at_30 = self._plan(30, (14, 30, 90), 30)
        assert sorted(at_30["available"]) == [14, 30]
        assert at_30["pending"] == [90]
        # The configured canonical is ready now, so it reclaims the default.
        assert at_30["default"] == 30

        at_90 = self._plan(30, (14, 30, 90), 90)
        assert sorted(at_90["available"]) == [14, 30, 90]
        assert at_90["pending"] == []
        assert at_90["default"] == 30

    def test_default_window_leads_so_it_seeds_community_colors(self):
        plan = self._plan(30, (14, 30, 90), 90)
        assert plan["available"][0] == plan["default"] == 30

    def test_every_window_is_pending_before_the_narrowest_is_full(self):
        """A short graph must never be published under a longer label."""
        plan = self._plan(30, (14, 30, 90), covered_days=3)
        assert plan["available"] == []
        assert plan["pending"] == [14, 30, 90]
        assert plan["default"] == 14

    def test_unwindowed_deployment_declares_no_windows(self):
        runner = app_main.PipelineRunner.__new__(app_main.PipelineRunner)
        runner.config = PipelineConfig(collection=CollectionConfig(), analysis=AnalysisConfig())
        runner.logger = logging.getLogger("test")
        runner.storage = MagicMock()
        plan = runner.window_plan()
        assert plan["available"] == [None] and plan["pending"] == []

    def test_duplicate_windows_are_analysed_once(self):
        """A repeat entry would cost a full extra aggregate/graph/detect pass."""
        plan = self._plan(30, (14, 14, 30, 90), covered_days=90)
        assert sorted(plan["available"]) == [14, 30, 90]

    def test_frontend_key_is_suffixed_per_window(self):
        assert app_main.PipelineRunner._frontend_key(14) == "data/frontend-data-14d.json"
        assert app_main.PipelineRunner._frontend_key(90) == "data/frontend-data-90d.json"
        # An unwindowed run keeps the original key untouched.
        assert app_main.PipelineRunner._frontend_key(None) == "data/frontend-data.json"

    def test_unwindowed_run_does_not_publish_the_same_key_twice(self):
        """A single-window deployment writes data/frontend-data.json as its own
        output key; also_write must not duplicate that PUT."""
        runner = app_main.PipelineRunner.__new__(app_main.PipelineRunner)
        runner.config = PipelineConfig(collection=CollectionConfig(), analysis=AnalysisConfig())
        runner.logger = logging.getLogger("test")
        runner.storage = MagicMock()

        graph = nx.Graph()
        graph.add_edge("a", "b", weight=9)
        aggregator = MagicMock()
        aggregator.get_statistics.return_value = {}
        aggregator.get_channel_metadata.return_value = {}
        saved = []

        with patch.object(app_main.PipelineRunner, "_step_aggregate", return_value=aggregator), \
             patch.object(app_main.PipelineRunner, "_step_build_graph", return_value=graph), \
             patch.object(app_main.PipelineRunner, "_step_detect_communities",
                          return_value=({"a": 0, "b": 0}, {0: {"a", "b"}},
                                        {"num_communities": 1, "modularity": 0.5}, graph)), \
             patch.object(app_main.PipelineRunner, "_step_tag_communities",
                          return_value=({0: "Label"}, {})), \
             patch.object(app_main.PipelineRunner, "_step_visualize"), \
             patch.object(app_main.PipelineRunner, "_step_save_results",
                          side_effect=lambda *a, **kw: saved.append(kw)):
            assert app_main.PipelineRunner.run_analysis_pipeline(runner)["status"] == "success"

        assert len(saved) == 1
        assert saved[0]["output_key"] == "data/frontend-data.json"
        assert saved[0]["also_write"] == ()

    def test_published_windows_are_declared_to_the_browser(self):
        """The filter renders from the payload, so it shows exactly the windows
        that exist and PENDING for the ones still filling up."""
        def run(analysis_kwargs, covered_days):
            runner = app_main.PipelineRunner.__new__(app_main.PipelineRunner)
            runner.config = PipelineConfig(
                collection=CollectionConfig(), analysis=AnalysisConfig(**analysis_kwargs)
            )
            runner.logger = logging.getLogger("test")
            runner.storage = MagicMock()
            graph = nx.Graph()
            graph.add_edge("a", "b", weight=9)
            aggregator = MagicMock()
            aggregator.get_statistics.return_value = {}
            aggregator.get_channel_metadata.return_value = {}
            saved = []
            end_day = date(2026, 8, 26)
            span = (end_day - timedelta(days=covered_days - 1), end_day)
            with patch("main.survey_date_span", return_value=span), \
                 patch.object(app_main.PipelineRunner, "_step_aggregate", return_value=aggregator), \
                 patch.object(app_main.PipelineRunner, "_step_build_graph", return_value=graph), \
                 patch.object(app_main.PipelineRunner, "_step_detect_communities",
                              return_value=({"a": 0, "b": 0}, {0: {"a", "b"}},
                                            {"num_communities": 1, "modularity": 0.5}, graph)), \
                 patch.object(app_main.PipelineRunner, "_step_tag_communities",
                              return_value=({0: "Label"}, {})), \
                 patch.object(app_main.PipelineRunner, "_step_visualize"), \
                 patch.object(app_main.PipelineRunner, "_step_save_results",
                              side_effect=lambda *a, **kw: saved.append(kw)):
                app_main.PipelineRunner.run_analysis_pipeline(runner)
            return saved

        # Only 14 days exist: one pass runs, the other two are declared pending.
        early = run({"analysis_window_days": 30, "analysis_windows": (14, 30, 90)}, 14)
        assert len(early) == 1
        assert early[0]["available_windows"] == (14,)
        assert early[0]["pending_windows"] == (30, 90)
        assert early[0]["default_window"] == 14

        # Once every window is full, all three run and nothing is pending.
        mature = run({"analysis_window_days": 30, "analysis_windows": (14, 30, 90)}, 90)
        assert len(mature) == 3
        assert all(kw["available_windows"] == (14, 30, 90) for kw in mature)
        assert all(kw["pending_windows"] == () for kw in mature)
        assert all(kw["default_window"] == 30 for kw in mature)

        # A single-window run advertises nothing, which hides the control.
        single = run({"analysis_window_days": 30}, 90)
        assert single[0]["available_windows"] == (30,)
        assert single[0]["pending_windows"] == ()

    def test_all_pending_plan_refreshes_both_status_outputs(self):
        """Daily analysis is still a successful, freshness-checkable run."""
        runner = app_main.PipelineRunner.__new__(app_main.PipelineRunner)
        runner.config = PipelineConfig(
            collection=CollectionConfig(),
            analysis=AnalysisConfig(
                analysis_window_days=30,
                analysis_windows=(14, 30, 90),
            ),
        )
        runner.logger = logging.getLogger("test")
        runner.storage = InMemoryStorage()
        end_day = date(2026, 8, 26)

        with patch(
            "main.survey_date_span",
            return_value=(end_day - timedelta(days=2), end_day),
        ):
            result = app_main.PipelineRunner.run_analysis_pipeline(runner)

        assert result["status"] == "success"
        assert result["windows_published"] == 0
        assert result["windows_pending"] == [14, 30, 90]
        private = runner.storage._json_uploads["processed/analysis_results.json"]
        public = runner.storage._json_uploads["data/frontend-data.json"]
        assert private["status"] == "pending"
        assert private["partition"] == {}
        assert public["pendingWindows"] == [14, 30, 90]

    def test_each_window_is_analysed_and_published_once(self):
        """The canonical window leads, carries the private artifacts, and seeds
        the anchor the other windows reuse."""
        runner = app_main.PipelineRunner.__new__(app_main.PipelineRunner)
        runner.config = get_rigorous_config()
        # Stated here rather than inherited, so the test keeps meaning if the
        # preset's window list changes.
        runner.config.analysis.analysis_windows = (14, 30, 90)
        runner.config.analysis.window_overlap_thresholds = {14: 1, 90: 5}
        runner.logger = logging.getLogger("test")
        runner.storage = MagicMock()

        graph = nx.Graph()
        graph.add_edge("a", "b", weight=9)
        aggregator = MagicMock()
        aggregator.get_statistics.return_value = {}
        aggregator.get_channel_metadata.return_value = {}

        aggregated, built, saved, visualized = [], [], [], []

        end_day = date(2026, 8, 26)
        with patch("main.survey_date_span",
                   return_value=(end_day - timedelta(days=89), end_day)), \
             patch.object(
            app_main.PipelineRunner, "_step_aggregate",
            side_effect=lambda window_days=None: (aggregated.append(window_days), aggregator)[1],
        ), patch.object(
            app_main.PipelineRunner, "_step_build_graph",
            side_effect=lambda agg, overlap_threshold=None, export_csv=True: (
                built.append((overlap_threshold, export_csv)), graph)[1],
        ), patch.object(
            app_main.PipelineRunner, "_step_detect_communities",
            return_value=({"a": 0, "b": 0}, {0: {"a", "b"}},
                          {"num_communities": 1, "modularity": 0.5}, graph),
        ), patch.object(
            app_main.PipelineRunner, "_step_tag_communities",
            return_value=({0: "Label"}, {}),
        ), patch.object(
            app_main.PipelineRunner, "_step_visualize",
            side_effect=lambda *a: visualized.append(True),
        ), patch.object(
            app_main.PipelineRunner, "_step_save_results",
            side_effect=lambda *a, **kw: saved.append(kw),
        ):
            result = app_main.PipelineRunner.run_analysis_pipeline(runner)

        assert result["status"] == "success"
        assert result["windows_published"] == 3

        # Canonical first, then the rest in ascending order.
        assert aggregated == [30, 14, 90]
        # Each window uses its own calibrated threshold; only the canonical run
        # writes the graph CSVs, which share one dated key.
        assert built == [(2, True), (1, False), (5, False)]

        assert [kw["output_key"] for kw in saved] == [
            "data/frontend-data-30d.json",
            "data/frontend-data-14d.json",
            "data/frontend-data-90d.json",
        ]
        # Only the canonical window refreshes the unsuffixed file and the
        # private artifacts, and visualization runs once rather than per window.
        assert [kw["also_write"] for kw in saved] == [("data/frontend-data.json",), (), ()]
        assert [kw["publish_private"] for kw in saved] == [True, False, False]
        # The saved record names the window and the threshold that built it.
        assert [kw["window_days"] for kw in saved] == [30, 14, 90]
        assert [kw["overlap_threshold"] for kw in saved] == [2, 1, 5]
        assert len(visualized) == 1
        # One anchor object threads through every window, which is what keeps
        # community colours stable across the filter.
        assert len({id(kw["anchor"]) for kw in saved}) == 1

    def test_results_are_published_before_the_render(self):
        """The render is the only step that can kill the task without raising:
        a wide canonical window exhausts the container and the kernel SIGKILLs
        the process, so nothing after it runs. Publishing first is what keeps an
        unrenderable graph from costing the public dataset — it froze for three
        days when the 30d window promoted and the PNG stopped fitting in 2 GB."""
        runner = app_main.PipelineRunner.__new__(app_main.PipelineRunner)
        runner.config = PipelineConfig(collection=CollectionConfig(), analysis=AnalysisConfig())
        runner.logger = logging.getLogger("test")
        runner.storage = MagicMock()

        graph = nx.Graph()
        graph.add_edge("a", "b", weight=9)
        aggregator = MagicMock()
        aggregator.get_statistics.return_value = {}
        aggregator.get_channel_metadata.return_value = {}
        calls = []

        with patch.object(app_main.PipelineRunner, "_step_aggregate", return_value=aggregator), \
             patch.object(app_main.PipelineRunner, "_step_build_graph", return_value=graph), \
             patch.object(app_main.PipelineRunner, "_step_detect_communities",
                          return_value=({"a": 0, "b": 0}, {0: {"a", "b"}},
                                        {"num_communities": 1, "modularity": 0.5}, graph)), \
             patch.object(app_main.PipelineRunner, "_step_tag_communities",
                          return_value=({0: "Label"}, {})), \
             patch.object(app_main.PipelineRunner, "_step_visualize",
                          side_effect=lambda *a, **kw: calls.append("visualize")), \
             patch.object(app_main.PipelineRunner, "_step_save_results",
                          side_effect=lambda *a, **kw: calls.append("save")):
            assert app_main.PipelineRunner.run_analysis_pipeline(runner)["status"] == "success"

        assert calls == ["save", "visualize"]

    def test_a_failed_render_still_leaves_the_results_published(self):
        """Whatever the render does on its way down, the payload is already in
        S3 by the time it runs."""
        runner = app_main.PipelineRunner.__new__(app_main.PipelineRunner)
        runner.config = PipelineConfig(collection=CollectionConfig(), analysis=AnalysisConfig())
        runner.logger = logging.getLogger("test")
        runner.storage = MagicMock()

        graph = nx.Graph()
        graph.add_edge("a", "b", weight=9)
        aggregator = MagicMock()
        aggregator.get_statistics.return_value = {}
        aggregator.get_channel_metadata.return_value = {}
        saved = []

        with patch.object(app_main.PipelineRunner, "_step_aggregate", return_value=aggregator), \
             patch.object(app_main.PipelineRunner, "_step_build_graph", return_value=graph), \
             patch.object(app_main.PipelineRunner, "_step_detect_communities",
                          return_value=({"a": 0, "b": 0}, {0: {"a", "b"}},
                                        {"num_communities": 1, "modularity": 0.5}, graph)), \
             patch.object(app_main.PipelineRunner, "_step_tag_communities",
                          return_value=({0: "Label"}, {})), \
             patch.object(app_main.PipelineRunner, "_step_visualize",
                          side_effect=MemoryError("render blew up")), \
             patch.object(app_main.PipelineRunner, "_step_save_results",
                          side_effect=lambda *a, **kw: saved.append(kw)):
            app_main.PipelineRunner.run_analysis_pipeline(runner)

        assert [kw["output_key"] for kw in saved] == ["data/frontend-data.json"]


class TestAnalysisInputValidation:
    """The analyze gate must accept exactly what collection writes.

    Live EventSub collection writes Parquet under raw/snapshots/, so a
    JSON-only check rejects a perfectly healthy dataset.
    """

    def _runner(self, storage, storage_type="file", logs_dir="logs"):
        config = PipelineConfig(
            analysis=AnalysisConfig(logs_dir=logs_dir, output_dir=logs_dir),
            storage_type=storage_type,
            s3_bucket="test-bucket" if storage_type == "s3" else None,
        )
        with patch("main.get_storage", return_value=storage):
            return app_main.PipelineRunner(config)

    def test_s3_parquet_only_dataset_is_accepted(self):
        storage = InMemoryStorage(parquet_data={"raw/snapshots/2026/03/11/cycle_1.parquet": b"x"})
        runner = self._runner(storage, storage_type="s3")
        assert runner._validate_analysis_inputs() is True

    def test_s3_empty_dataset_is_rejected(self):
        runner = self._runner(InMemoryStorage(), storage_type="s3")
        assert runner._validate_analysis_inputs() is False

    def test_s3_empty_dataset_allowed_when_data_not_required(self):
        runner = self._runner(InMemoryStorage(), storage_type="s3")
        assert runner._validate_analysis_inputs(require_data=False) is True

    def test_s3_vod_only_dataset_is_accepted(self):
        storage = InMemoryStorage(vod_snapshots=[{"channel": "a", "chatters": ["u1"]}])
        runner = self._runner(storage, storage_type="s3")
        assert runner._validate_analysis_inputs() is True

    def test_local_nested_parquet_dataset_is_accepted(self, tmp_path):
        snapshot_dir = tmp_path / "raw" / "snapshots" / "2026" / "03" / "11"
        snapshot_dir.mkdir(parents=True)
        (snapshot_dir / "cycle_20260311_120000.parquet").write_bytes(b"placeholder")

        runner = self._runner(
            FileStorage(base_dir=str(tmp_path)),
            storage_type="file",
            logs_dir=str(tmp_path),
        )
        assert runner._validate_analysis_inputs() is True

    def test_local_legacy_flat_json_dataset_is_accepted(self, tmp_path):
        (tmp_path / "snapshot.json").write_text('{"channel": "a", "chatters": ["u1"]}')

        runner = self._runner(
            FileStorage(base_dir=str(tmp_path)),
            storage_type="file",
            logs_dir=str(tmp_path),
        )
        assert runner._validate_analysis_inputs() is True

    def test_local_empty_dataset_is_rejected(self, tmp_path):
        runner = self._runner(
            FileStorage(base_dir=str(tmp_path)),
            storage_type="file",
            logs_dir=str(tmp_path),
        )
        assert runner._validate_analysis_inputs() is False


class TestScheduledAnalysisOutcome:
    """A scheduled task must fail closed when analysis or persistence fails."""

    @staticmethod
    def _runner_with_storage(storage):
        runner = object.__new__(app_main.PipelineRunner)
        runner.config = PipelineConfig(
            analysis=AnalysisConfig(
                output_dir="out",
                enable_static_viz=False,
                enable_interactive_viz=False,
                export_graph_csv=False,
            )
        )
        runner.storage = storage
        runner.logger = MagicMock()
        return runner

    @staticmethod
    def _save_args():
        graph = nx.Graph()
        graph.add_node("channel-a", viewer_count=10)
        aggregator = MagicMock()
        aggregator.get_statistics.return_value = {
            "total_channels": 1,
            "total_unique_viewers_across_all": 1,
        }
        return {
            "partition": {"channel-a": 0},
            "labels": {0: "Test"},
            "graph": graph,
            "aggregator": aggregator,
            "detection_stats": {"num_communities": 1, "modularity": 0.0},
            "tagging_stats": {},
            "communities": {0: ["channel-a"]},
        }

    def test_private_analysis_result_write_failure_is_fatal(self):
        class FailedStorage(InMemoryStorage):
            def upload_json(self, key, data, **kwargs):
                return False

        runner = self._runner_with_storage(FailedStorage())

        with pytest.raises(IOError, match="private analysis results"):
            runner._step_save_results(**self._save_args())

    def test_private_results_record_the_threshold_the_window_used(self):
        """A calibrated window is not built with the fallback overlap_threshold,
        so recording the fallback would make the run impossible to reproduce."""
        storage = InMemoryStorage()
        runner = self._runner_with_storage(storage)
        assert runner.config.analysis.overlap_threshold != 3

        runner._step_save_results(**self._save_args(), window_days=14, overlap_threshold=3)

        recorded = storage._json_uploads["processed/analysis_results.json"]["config"]
        assert recorded["analysis_window_days"] == 14
        assert recorded["overlap_threshold"] == 3

    def test_public_frontend_write_failure_is_fatal(self):
        runner = self._runner_with_storage(InMemoryStorage())

        with patch("main.export_frontend_data", return_value=False):
            with pytest.raises(IOError, match="public frontend data"):
                runner._step_save_results(**self._save_args())

    def test_mode_analyze_reports_success_and_failure_milestones(self, caplog):
        caplog.set_level("INFO")

        successful = MagicMock()
        successful._validate_prerequisites.return_value = True
        successful.run_analysis_pipeline.return_value = {
            "status": "success",
            "num_channels": 2,
            "num_communities": 1,
            "num_edges": 1,
        }
        failed = MagicMock()
        failed._validate_prerequisites.return_value = True
        failed.run_analysis_pipeline.return_value = {"status": "error"}

        with patch("main.PipelineRunner", return_value=successful):
            assert asyncio.run(app_main.mode_analyze(PipelineConfig())) is True
        with patch("main.PipelineRunner", return_value=failed):
            assert asyncio.run(app_main.mode_analyze(PipelineConfig())) is False

        assert "ANALYSIS_COMPLETED" in caplog.text
        assert "ANALYSIS_FAILED" in caplog.text


def test_survey_cli_never_logs_raw_exception_or_token(monkeypatch, capsys, tmp_path):
    sentinel = "secret-access-token-that-must-never-reach-logs"

    async def fail_survey(_config):
        raise RuntimeError(f'Invalid or expired token: "{sentinel}"')

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app_main, "mode_survey", fail_survey)
    monkeypatch.setattr(sys, "argv", ["main.py", "survey", "default"])

    assert app_main.main() == 1
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "SURVEY_TASK_FAILED" in combined
    assert sentinel not in combined
    assert "Invalid or expired token" not in combined

    log_text = (tmp_path / "logs" / "pipeline.log").read_text(encoding="utf-8")
    assert "SURVEY_TASK_FAILED" in log_text
    assert sentinel not in log_text


def test_analysis_cli_returns_nonzero_when_scheduled_analysis_fails(
    monkeypatch, tmp_path
):
    async def fail_analysis(_config):
        return False

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app_main, "mode_analyze", fail_analysis)
    monkeypatch.setattr(sys, "argv", ["main.py", "analyze", "default"])

    assert app_main.main() == 1
