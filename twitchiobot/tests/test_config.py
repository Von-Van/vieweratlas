from pathlib import Path

import pytest

from config import (
    AnalysisConfig,
    CollectionConfig,
    PipelineConfig,
    get_debug_config,
    get_default_config,
    get_exploratory_config,
    get_rigorous_config,
    load_config_from_yaml,
)


class TestConfig:
    """Tests for configuration loading and validation."""

    def test_rigorous_config_is_calibrated_for_eventsub_surveys(self):
        """Production analysis preset, measured rather than inherited.

        The old TwitchAtlas threshold of 300 produced a graph with zero edges on
        real five-minute EventSub survey data, so these values come from
        scripts/sweep_threshold.py. They are expected to rise as data
        accumulates; this test exists so a change is deliberate, not accidental.
        """
        analysis = get_rigorous_config().analysis
        assert analysis.overlap_threshold == 2
        assert analysis.min_community_size == 10
        assert analysis.min_channel_observations == 3
        assert analysis.min_channel_viewers == 10
        assert analysis.weighting_mode == "shared_count"
        assert analysis.include_isolated_nodes is False
        assert analysis.exclude_known_bots is True
        assert analysis.max_concurrent_channels == 3
        assert analysis.analysis_window_days == 30

    def test_automated_chatter_filter_is_on_in_every_preset(self):
        """A chat bot is never audience, whatever the analysis is for."""
        for config in (get_default_config(), get_rigorous_config(),
                       get_exploratory_config(), get_debug_config()):
            assert config.analysis.exclude_known_bots is True
            assert config.analysis.max_concurrent_channels == 3
            assert config.analysis.excluded_chatters == ()

    def test_max_concurrent_channels_bounds(self):
        assert AnalysisConfig(max_concurrent_channels=None).max_concurrent_channels is None
        assert AnalysisConfig(max_concurrent_channels=1).max_concurrent_channels == 1
        for bad in (0, -1, True, 2.5):
            with pytest.raises(ValueError):
                AnalysisConfig(max_concurrent_channels=bad)

    def test_excluded_chatters_are_normalised(self):
        config = AnalysisConfig(excluded_chatters=(" HouseBot ", "housebot", "Other"))
        assert config.excluded_chatters == ("housebot", "other")

    @pytest.mark.parametrize("bad", ["housebot", ("",), (None,)])
    def test_excluded_chatters_must_be_a_list_of_logins(self, bad):
        """A bare string would otherwise be split into one-letter logins."""
        with pytest.raises(ValueError):
            AnalysisConfig(excluded_chatters=bad)

    def test_collection_config_validation(self):
        with pytest.raises(ValueError):
            CollectionConfig(batch_size=0)
        with pytest.raises(ValueError):
            CollectionConfig(duration_per_batch=-1)

    def test_collection_config_survey_env_overrides(self, monkeypatch):
        monkeypatch.setenv("SURVEY_TOP_CHANNELS_LIMIT", "7")
        monkeypatch.setenv("SURVEY_BATCH_SIZE", "4")
        monkeypatch.setenv("SURVEY_WINDOW_SECONDS", "12")
        monkeypatch.setenv("SURVEY_TIMEOUT_SECONDS", "90")

        config = CollectionConfig()
        assert config.top_channels_limit == 7
        assert config.batch_size == 4
        assert config.duration_per_batch == 12
        assert config.survey_timeout_seconds == 90

    def test_collection_config_rejects_invalid_survey_env_override(self, monkeypatch):
        monkeypatch.setenv("SURVEY_BATCH_SIZE", "101")
        with pytest.raises(ValueError, match="100-room"):
            CollectionConfig()

    def test_analysis_config_validation(self):
        with pytest.raises(ValueError):
            AnalysisConfig(overlap_threshold=-1)
        with pytest.raises(ValueError):
            AnalysisConfig(resolution=0)
        with pytest.raises(ValueError):
            AnalysisConfig(min_community_size=0)

    def test_pipeline_config_s3_requires_bucket(self):
        with pytest.raises(ValueError):
            PipelineConfig(storage_type="s3", s3_bucket=None)

    def test_yaml_loading(self, tmp_path):
        yaml_file = tmp_path / "test_config.yaml"
        yaml_file.write_text(
            "collection:\n"
            "  logs_dir: logs\n"
            "  batch_size: 50\n"
            "  top_channels_limit: 200\n"
            "  collection_interval_minutes: 30\n"
            "  wait_for_hour_alignment: false\n"
            "  max_runtime_hours: 8\n"
            "  max_collection_cycles: 12\n"
            "analysis:\n"
            "  overlap_threshold: 5\n"
            "  resolution: 1.5\n"
            "  min_community_size: 3\n"
            "vod:\n"
            "  persist_raw_chat: true\n"
            "  max_vods_per_run: 7\n"
            "  max_processing_hours: 2\n"
            "  rate_limit_delay_s: 3\n"
        )
        config = load_config_from_yaml(str(yaml_file))
        assert config.collection.batch_size == 50
        assert config.collection.top_channels_limit == 200
        assert not hasattr(config.collection, "collection_interval_minutes")
        assert config.analysis.overlap_threshold == 5
        assert config.analysis.resolution == 1.5
        assert config.analysis.min_community_size == 3
        assert config.vod.persist_raw_chat is True
        assert config.vod.max_vods_per_run == 7
        assert config.vod.max_processing_hours == 2
        assert config.vod.rate_limit_delay_s == 3

    def test_yaml_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config_from_yaml(str(tmp_path / "nonexistent.yaml"))

    def test_invalid_frontend_export_limits_raise(self):
        with pytest.raises(ValueError):
            AnalysisConfig(frontend_max_channels=0)
        with pytest.raises(ValueError):
            AnalysisConfig(frontend_max_edges=0)
        with pytest.raises(ValueError):
            AnalysisConfig(frontend_top_edges_per_channel=0)

    def test_invalid_weighting_mode_raises(self):
        with pytest.raises(ValueError):
            AnalysisConfig(weighting_mode="cosine")

    def test_normalized_threshold_bounds(self):
        assert AnalysisConfig(normalized_overlap_threshold=0.5)
        for bad in (-0.1, 1.1):
            with pytest.raises(ValueError):
                AnalysisConfig(normalized_overlap_threshold=bad)

    def test_min_channel_observations_bounds(self):
        assert AnalysisConfig(min_channel_observations=3)
        with pytest.raises(ValueError):
            AnalysisConfig(min_channel_observations=0)

    def test_yaml_loads_every_analysis_field(self, tmp_path):
        """Fields the hand-enumerated loader used to drop silently."""
        yaml_file = tmp_path / "full_config.yaml"
        yaml_file.write_text(
            "analysis:\n"
            "  enable_static_viz: false\n"
            "  enable_interactive_viz: false\n"
            "  export_graph_csv: false\n"
            "  save_analysis_json: false\n"
            "  label_top_n_nodes: 30\n"
            "  static_viz_dpi: 150\n"
            "  include_isolated_nodes: false\n"
            "  min_user_appearances: 2\n"
            "  show_node_labels: false\n"
        )
        config = load_config_from_yaml(str(yaml_file))
        assert config.analysis.enable_static_viz is False
        assert config.analysis.enable_interactive_viz is False
        assert config.analysis.export_graph_csv is False
        assert config.analysis.save_analysis_json is False
        assert config.analysis.label_top_n_nodes == 30
        assert config.analysis.static_viz_dpi == 150
        assert config.analysis.include_isolated_nodes is False
        assert config.analysis.min_user_appearances == 2
        assert config.analysis.show_node_labels is False

    def test_yaml_figsize_list_becomes_tuple(self, tmp_path):
        yaml_file = tmp_path / "figsize_config.yaml"
        yaml_file.write_text("analysis:\n  static_viz_figsize: [12, 9]\n")
        config = load_config_from_yaml(str(yaml_file))
        assert config.analysis.static_viz_figsize == (12, 9)

    def test_yaml_unknown_section_key_raises(self, tmp_path):
        yaml_file = tmp_path / "typo_config.yaml"
        yaml_file.write_text("analysis:\n  min_comunity_size: 3\n")
        with pytest.raises(ValueError, match="min_comunity_size"):
            load_config_from_yaml(str(yaml_file))

    def test_yaml_unknown_top_level_key_raises(self, tmp_path):
        yaml_file = tmp_path / "typo_top_config.yaml"
        yaml_file.write_text("stroage_type: s3\n")
        with pytest.raises(ValueError, match="stroage_type"):
            load_config_from_yaml(str(yaml_file))

    def test_yaml_vod_max_age_days_fallback(self, tmp_path):
        yaml_file = tmp_path / "vod_config.yaml"
        yaml_file.write_text("vod:\n  max_age_days: 3\n")
        config = load_config_from_yaml(str(yaml_file))
        assert config.vod.max_age_days == 3
        assert config.vod.max_age_hours == 72

    def test_yaml_explicit_max_age_hours_wins(self, tmp_path):
        yaml_file = tmp_path / "vod_hours_config.yaml"
        yaml_file.write_text("vod:\n  max_age_days: 3\n  max_age_hours: 6\n")
        config = load_config_from_yaml(str(yaml_file))
        assert config.vod.max_age_hours == 6

    def test_shipped_config_yaml_loads(self):
        """The config.yaml shipped in the repo must satisfy the strict loader."""
        shipped = Path(__file__).resolve().parent.parent / "config" / "config.yaml"
        config = load_config_from_yaml(str(shipped))
        assert config.analysis.overlap_threshold == 10
        assert config.analysis.min_community_size == 3
        assert config.analysis.exclude_known_bots is True
        assert config.analysis.excluded_chatters == ()
        assert config.analysis.max_concurrent_channels == 3

    def test_yaml_loads_chatter_filter_fields(self, tmp_path):
        yaml_file = tmp_path / "bots_config.yaml"
        yaml_file.write_text(
            "analysis:\n"
            "  exclude_known_bots: false\n"
            "  excluded_chatters: [HouseBot, relaybot]\n"
            "  max_concurrent_channels: null\n"
        )
        analysis = load_config_from_yaml(str(yaml_file)).analysis
        assert analysis.exclude_known_bots is False
        assert analysis.excluded_chatters == ("housebot", "relaybot")
        assert analysis.max_concurrent_channels is None

    def test_yaml_loading_weighting_mode(self, tmp_path):
        yaml_file = tmp_path / "wm_config.yaml"
        yaml_file.write_text(
            "analysis:\n"
            "  overlap_threshold: 1\n"
            '  weighting_mode: "shared_count"\n'
            "  frontend_max_channels: 500\n"
            "  frontend_max_edges: 10000\n"
            "  frontend_top_edges_per_channel: 10\n"
        )
        config = load_config_from_yaml(str(yaml_file))
        assert config.analysis.weighting_mode == "shared_count"
        assert config.analysis.frontend_max_channels == 500
        assert config.analysis.frontend_max_edges == 10000
        assert config.analysis.frontend_top_edges_per_channel == 10

    def test_window_is_configurable_from_yaml(self, tmp_path):
        yaml_file = tmp_path / "window.yaml"
        yaml_file.write_text("analysis:\n  analysis_window_days: 30\n")
        assert load_config_from_yaml(str(yaml_file)).analysis.analysis_window_days == 30

    def test_multi_window_config_requires_the_canonical_window(self):
        """The canonical window writes the unsuffixed public file.

        Publishing a set that excludes it would leave that file stale while the
        suffixed ones moved.
        """
        AnalysisConfig(analysis_window_days=30, analysis_windows=(14, 30, 90))
        with pytest.raises(ValueError, match="analysis_window_days must appear"):
            AnalysisConfig(analysis_window_days=30, analysis_windows=(14, 90))
        with pytest.raises(ValueError, match="at least 1"):
            AnalysisConfig(analysis_window_days=30, analysis_windows=(0, 30))

    def test_window_overlap_thresholds_are_validated(self):
        AnalysisConfig(window_overlap_thresholds={14: 1, 90: 4})
        with pytest.raises(ValueError, match="keys must be day counts"):
            AnalysisConfig(window_overlap_thresholds={0: 1})
        with pytest.raises(ValueError, match="values must be non-negative"):
            AnalysisConfig(window_overlap_thresholds={14: -1})

    def test_the_scheduled_profile_renders_nothing(self):
        """Neither artifact is uploaded anywhere, so the scheduled task must not
        spend memory or minutes producing them."""

        analysis = get_rigorous_config().analysis
        assert analysis.enable_static_viz is False
        assert analysis.enable_interactive_viz is False
