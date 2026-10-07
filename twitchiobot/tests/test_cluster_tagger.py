from cluster_tagger import ClusterTagger


class TestClusterTagger:
    """Tests for community label generation."""

    def test_dominant_game_label(self):
        """Community where 80% play the same game should get that game as label."""
        communities = {0: {"ch1", "ch2", "ch3", "ch4", "ch5"}}
        metadata = {
            "ch1": {"game_name": "Valorant", "viewer_count": 100},
            "ch2": {"game_name": "Valorant", "viewer_count": 200},
            "ch3": {"game_name": "Valorant", "viewer_count": 150},
            "ch4": {"game_name": "Valorant", "viewer_count": 300},
            "ch5": {"game_name": "CS2", "viewer_count": 50},
        }
        tagger = ClusterTagger()
        labels = tagger.tag_communities(communities, metadata)
        assert "Valorant" in labels[0]

    def test_language_game_combo_label(self):
        """Community with clear language + game combo."""
        communities = {0: {"ch1", "ch2", "ch3", "ch4", "ch5"}}
        metadata = {
            "ch1": {"game_name": "Minecraft", "language": "es", "viewer_count": 100},
            "ch2": {"game_name": "Fortnite", "language": "es", "viewer_count": 200},
            "ch3": {"game_name": "Minecraft", "language": "es", "viewer_count": 150},
            "ch4": {"game_name": "Roblox", "language": "en", "viewer_count": 300},
            "ch5": {"game_name": "Minecraft", "language": "es", "viewer_count": 50},
        }
        tagger = ClusterTagger()
        labels = tagger.tag_communities(communities, metadata)
        label = labels[0]
        # Should reference game and/or language
        assert "Minecraft" in label or "es" in label

    def test_mixed_games_label(self):
        """Community with no dominant game should get a mixed label."""
        communities = {0: {"ch1", "ch2", "ch3"}}
        metadata = {
            "ch1": {"game_name": "Valorant", "viewer_count": 100},
            "ch2": {"game_name": "Fortnite", "viewer_count": 200},
            "ch3": {"game_name": "Minecraft", "viewer_count": 150},
        }
        tagger = ClusterTagger()
        labels = tagger.tag_communities(communities, metadata)
        label = labels[0]
        # Should contain "Mix" or multiple game names
        assert "Mix" in label or "/" in label

    def test_all_communities_get_labels(self):
        communities = {
            0: {"ch1", "ch2"},
            1: {"ch3", "ch4"},
            2: {"ch5"},
        }
        metadata = {
            "ch1": {"game_name": "Valorant", "viewer_count": 100},
            "ch2": {"game_name": "Valorant", "viewer_count": 200},
            "ch3": {"game_name": "LoL", "viewer_count": 300},
            "ch4": {"game_name": "LoL", "viewer_count": 400},
            "ch5": {"game_name": "Art", "viewer_count": 50},
        }
        tagger = ClusterTagger()
        labels = tagger.tag_communities(communities, metadata)
        assert len(labels) == 3
        assert 0 in labels
        assert 1 in labels
        assert 2 in labels

    def test_empty_metadata_fallback(self):
        """Channels with no metadata should still get a label."""
        communities = {0: {"ch1", "ch2"}}
        metadata = {}
        tagger = ClusterTagger()
        labels = tagger.tag_communities(communities, metadata)
        assert 0 in labels
        assert len(labels[0]) > 0

    def test_statistics(self):
        communities = {
            0: {"ch1", "ch2", "ch3"},
            1: {"ch4", "ch5"},
        }
        metadata = {
            "ch1": {"game_name": "Valorant", "viewer_count": 100},
            "ch2": {"game_name": "Valorant", "viewer_count": 200},
            "ch3": {"game_name": "Valorant", "viewer_count": 150},
            "ch4": {"game_name": "Art", "viewer_count": 50},
            "ch5": {"game_name": "Music", "viewer_count": 50},
        }
        tagger = ClusterTagger()
        tagger.tag_communities(communities, metadata)
        stats = tagger.get_statistics()

        assert stats["total_labeled"] == 2
        assert stats["with_clear_game"] >= 1  # The Valorant community

    def test_tied_games_are_named_the_same_whatever_the_channel_order(self):
        """Community members arrive as a set, whose order changes with the
        process's hash seed. A tie must not make the label depend on it."""
        metadata = {
            "z1": {"game_name": "Zelda", "language": "en"},
            "z2": {"game_name": "Zelda", "language": "en"},
            "a1": {"game_name": "Asteroids", "language": "en"},
            "a2": {"game_name": "Asteroids", "language": "en"},
        }
        labels = {
            ClusterTagger().tag_communities({0: order}, metadata)[0]
            for order in (["z1", "z2", "a1", "a2"], ["a1", "a2", "z1", "z2"])
        }
        assert labels == {"Asteroids (en)"}
