import re

import pytest

from chatter_filter import KNOWN_BOT_LOGINS, remove_automated_chatters
from data_aggregator import DataAggregator
from graph_builder import GraphBuilder
from survey_fakes import survey_window_storage


class TestAutomatedChatterFilter:
    """A bot shared by N channels adds a false shared chatter to every pair."""

    @staticmethod
    def _row(channel, chatters, session="s1", batch=0):
        return {
            "channel": channel,
            "chatters": list(chatters),
            "survey_session_id": session,
            "batch": batch,
        }

    @staticmethod
    def _agg(tmp_path, rows):
        agg = DataAggregator(str(tmp_path))
        for row in rows:
            agg._ingest_snapshot(row)
        return agg

    def test_known_bot_is_removed_from_every_channel(self, tmp_path):
        agg = self._agg(tmp_path, [
            self._row("a", ["alice", "nightbot"], batch=0),
            self._row("b", ["bob", "nightbot"], batch=1),
        ])
        report = agg.exclude_automated_chatters()
        assert agg.get_channel_viewers() == {"a": {"alice"}, "b": {"bob"}}
        assert report["accounts"] == report["known_bots"] == 1
        assert report["memberships"] == 2
        assert report["channels"] == 2
        assert report["pair_overlaps"] == 1

    def test_shared_bots_no_longer_link_unrelated_channels(self, tmp_path):
        """The false connection this filter exists for: two audiences with
        nobody in common, joined only by the bots both channels run."""
        bots = ["nightbot", "streamelements", "fossabot"]
        rows = [
            self._row("cooking", ["ann", "ben", *bots], batch=0),
            self._row("speedrun", ["cat", "dan", *bots], batch=1),
        ]
        unfiltered = self._agg(tmp_path, rows).get_channel_viewers()
        assert GraphBuilder(overlap_threshold=3).build_graph(unfiltered).has_edge(
            "cooking", "speedrun"
        )

        agg = self._agg(tmp_path, rows)
        agg.exclude_automated_chatters()
        graph = GraphBuilder(overlap_threshold=1).build_graph(agg.get_channel_viewers())
        assert not graph.has_edge("cooking", "speedrun")

    def test_account_in_more_chats_than_the_limit_at_once_is_excluded(self, tmp_path):
        agg = self._agg(tmp_path, [
            self._row(f"ch{i}", [f"viewer{i}", "farm"]) for i in range(4)
        ])
        report = agg.exclude_automated_chatters(max_concurrent_channels=3)
        assert all("farm" not in viewers for viewers in agg.get_channel_viewers().values())
        assert report["concurrent"] == 1
        # Single-channel accounts are left out of the distribution entirely.
        assert report["peak_distribution"] == {4: 1}

    def test_account_at_the_limit_is_kept(self, tmp_path):
        agg = self._agg(tmp_path, [self._row(f"ch{i}", ["fan"]) for i in range(3)])
        assert agg.exclude_automated_chatters(max_concurrent_channels=3)["accounts"] == 0
        assert all(viewers == {"fan"} for viewers in agg.get_channel_viewers().values())

    def test_activity_spread_across_windows_is_not_concurrent(self, tmp_path):
        """Hopping between chats over a fortnight is what people do. The same
        batch number in another session is a different window."""
        agg = self._agg(tmp_path, [
            self._row("ch0", ["hopper"], session="s1", batch=0),
            self._row("ch1", ["hopper"], session="s1", batch=1),
            self._row("ch2", ["hopper"], session="s2", batch=0),
            self._row("ch3", ["hopper"], session="s2", batch=1),
        ])
        assert agg.exclude_automated_chatters(max_concurrent_channels=1)["accounts"] == 0

    def test_duplicated_row_counts_as_one_chat(self, tmp_path):
        agg = self._agg(tmp_path, [
            self._row("ch0", ["fan"]),
            self._row("ch0", ["fan"]),
            self._row("ch1", ["fan"]),
            self._row("ch2", ["fan"]),
        ])
        assert agg.exclude_automated_chatters(max_concurrent_channels=3)["accounts"] == 0

    def test_rows_without_a_survey_window_are_never_concurrent(self, tmp_path):
        """Legacy, CSV and VOD snapshots carry no shared window to judge."""
        legacy = [{"channel": f"old{i}", "chatters": ["regular"]} for i in range(3)]
        sparse = [
            {"channel": f"new{i}", "chatters": ["regular"],
             "survey_session_id": "s1", "batch": float("nan")}
            for i in range(3)
        ]
        agg = self._agg(tmp_path, legacy + sparse)
        assert agg.exclude_automated_chatters(max_concurrent_channels=1)["accounts"] == 0

    def test_listed_chatters_are_normalised_and_removed(self, tmp_path):
        agg = self._agg(tmp_path, [self._row("a", ["alice", "housebot"])])
        report = agg.exclude_automated_chatters(excluded_chatters=["  HouseBot "])
        assert agg.get_channel_viewers() == {"a": {"alice"}}
        assert report["listed"] == 1

    def test_each_account_is_credited_to_one_rule(self, tmp_path):
        """A known bot is usually concurrent too. Counting it twice would
        overstate what the behavioural rule adds beyond the lists."""
        agg = self._agg(tmp_path, [
            self._row(f"ch{i}", ["nightbot", "listedbot", "farm"]) for i in range(4)
        ])
        report = agg.exclude_automated_chatters(excluded_chatters=["listedbot", "nightbot"])
        assert (report["known_bots"], report["listed"], report["concurrent"]) == (1, 1, 1)
        assert report["accounts"] == 3

    def test_rules_can_be_disabled(self, tmp_path):
        agg = self._agg(tmp_path, [self._row(f"ch{i}", ["nightbot", "farm"]) for i in range(4)])
        report = agg.exclude_automated_chatters(
            exclude_known_bots=False, max_concurrent_channels=None
        )
        assert report["accounts"] == 0
        assert report["peak_distribution"] == {}
        assert all(
            viewers == {"nightbot", "farm"} for viewers in agg.get_channel_viewers().values()
        )

    def test_statistics_describe_the_filtered_data(self, tmp_path):
        agg = self._agg(tmp_path, [self._row("a", ["alice", "nightbot"])])
        assert agg.get_statistics()["automated_chatters"] is None
        agg.exclude_automated_chatters()
        stats = agg.get_statistics()
        assert stats["total_unique_viewers_across_all"] == 1
        assert stats["automated_chatters"]["known_bots"] == 1

    def test_invalid_concurrency_limit_rejected(self):
        with pytest.raises(ValueError):
            remove_automated_chatters({}, [], max_concurrent_channels=0)

    def test_v2_parquet_rows_carry_their_survey_window(self, tmp_path):
        """Session and batch must survive the Parquet round trip, including
        pandas' integer dtype for the batch column."""
        storage = survey_window_storage(
            {f"ch{i}": [f"viewer{i}", "farm"] for i in range(4)}
        )
        agg = DataAggregator(str(tmp_path), storage=storage)
        assert agg.load_parquet_snapshots() == 4
        assert agg.exclude_automated_chatters()["concurrent"] == 1
        assert all("farm" not in viewers for viewers in agg.get_channel_viewers().values())

    def test_known_bot_logins_are_matchable(self):
        """Analysis compares lowercase logins, so an entry in any other form
        would never match and would fail silently."""
        login = re.compile(r"[a-z0-9_]{1,25}")
        assert all(login.fullmatch(entry) for entry in KNOWN_BOT_LOGINS)
