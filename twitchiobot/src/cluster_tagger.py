"""
Cluster Tagger Module

Generates human-readable labels for detected communities.
Analyzes dominant game categories, languages, and other attributes
to create intuitive community descriptions.
"""

from typing import Dict, Set, List, Tuple
from collections import Counter
import logging

logger = logging.getLogger(__name__)

# A single game outright characterises the community; its name alone is the label.
DOMINANT_GAME_SHARE = 60.0
# Below this share the plurality game is merely the most common of many, and
# naming the community after it is wrong rather than merely vague: a cluster of
# 27 Japanese channels of which 4 play Valorant is not "VALORANT (ja)". Such a
# community is named for the signal it actually has — its language.
GAME_LABEL_MIN_SHARE = 40.0
# Share of a community speaking one language before that language is worth naming.
LANGUAGE_LABEL_MIN_SHARE = 40.0


class ClusterTagger:
    """
    Assigns descriptive labels to detected communities based on streamer metadata.
    """
    
    def __init__(self):
        """Initialize the tagger."""
        self.community_labels: Dict[int, str] = {}
        self.community_reasons: Dict[int, dict] = {}
        
    def tag_communities(self,
                       communities: Dict[int, Set[str]],
                       channel_metadata: Dict[str, dict]) -> Dict[int, str]:
        """
        Generate labels for each community.
        
        Tagging strategy:
        1. Count game categories in each community
        2. Count languages if available
        3. Find dominant attributes
        4. Generate human-readable label
        
        Args:
            communities: Dict mapping community_id -> set of channels
            channel_metadata: Dict mapping channel -> metadata dict
        
        Returns:
            Dict mapping community_id -> label
        """
        self.community_labels = {}
        self.community_reasons = {}
        
        logger.info(f"Tagging {len(communities)} communities")
        
        for comm_id, channels in communities.items():
            label, reason = self._generate_label(comm_id, channels, channel_metadata)
            self.community_labels[comm_id] = label
            self.community_reasons[comm_id] = reason
            
            logger.debug(f"Community {comm_id}: {label} ({reason['reasoning']})")
        
        return dict(self.community_labels)
    
    def _generate_label(self, 
                       comm_id: int,
                       channels: Set[str],
                       channel_metadata: Dict[str, dict]) -> Tuple[str, dict]:
        """
        Generate a label for a single community.
        
        Args:
            comm_id: Community ID
            channels: Set of channel names in community
            channel_metadata: Metadata dict for all channels
        
        Returns:
            Tuple of (label, reason_dict)
        """
        # Extract metadata for channels in this community
        games = []
        languages = []
        viewer_counts = []
        
        for channel in channels:
            if channel in channel_metadata:
                meta = channel_metadata[channel]
                game = meta.get("game_name", meta.get("game", "Unknown"))
                if game and game != "Unknown":
                    games.append(game)
                
                lang = meta.get("language", "Unknown")
                if lang and lang != "Unknown":
                    languages.append(lang)
                
                viewers = meta.get("viewer_count", meta.get("viewers", 0))
                if viewers:
                    viewer_counts.append(viewers)
        
        # Find dominant attributes
        reason = {"reasoning": ""}
        total = len(channels)

        # Shares are taken over every channel in the community, not only those
        # carrying the attribute, so a community whose metadata is mostly
        # unknown cannot be named after its handful of known members.
        game_counts = Counter(games)
        top_game, game_share = None, 0.0
        if game_counts and total:
            top_game, game_freq = game_counts.most_common(1)[0]
            game_share = (game_freq / total) * 100

        lang_counts = Counter(languages)
        top_lang, lang_share = None, 0.0
        if lang_counts and total:
            top_lang, lang_freq = lang_counts.most_common(1)[0]
            lang_share = (lang_freq / total) * 100

        # One game characterises the whole community.
        if top_game and game_share >= DOMINANT_GAME_SHARE:
            reason["dominant_game"] = top_game
            reason["game_percentage"] = game_share
            reason["reasoning"] = f"{top_game} ({game_share:.0f}% of channels)"
            return top_game, reason

        # A game worth naming, spoken in one language.
        if (
            top_game
            and game_share >= GAME_LABEL_MIN_SHARE
            and top_lang
            and lang_share >= LANGUAGE_LABEL_MIN_SHARE
        ):
            # Deliberately no game_percentage key: get_statistics() counts a
            # community as game-labelled or language-labelled, never both.
            reason["dominant_game"] = top_game
            reason["game_share"] = game_share
            reason["dominant_language"] = top_lang
            reason["language_percentage"] = lang_share
            reason["reasoning"] = (
                f"{top_game} ({game_share:.0f}%) / {top_lang}-speaking"
            )
            return f"{top_game} ({top_lang})", reason

        # A language, and no game with a real claim on the name.
        if top_lang and lang_share >= LANGUAGE_LABEL_MIN_SHARE:
            reason["dominant_language"] = top_lang
            reason["language_percentage"] = lang_share
            if top_game:
                reason["plurality_game"] = top_game
                reason["plurality_game_share"] = game_share
            reason["reasoning"] = (
                f"{top_lang}-speaking, no game above "
                f"{GAME_LABEL_MIN_SHARE:.0f}% (top: {top_game or 'none'} "
                f"{game_share:.0f}%)"
            )
            return f"Variety ({top_lang})", reason

        # No language signal: name the games that are there.
        if game_counts:
            top_games = game_counts.most_common(3)
            if len(top_games) >= 2:
                game_names = [g[0] for g in top_games]
                reason["top_games"] = game_names
                reason["reasoning"] = f"Mixed: {', '.join(game_names[:2])}"
                return f"{game_names[0]} / {game_names[1]} Mix", reason
            reason["dominant_game"] = top_games[0][0]
            reason["game_percentage"] = game_share
            reason["reasoning"] = f"{top_games[0][0]} ({game_share:.0f}% of channels)"
            return top_games[0][0], reason

        # Fallback: use size or just generic label
        num_channels = total
        avg_viewers = int(sum(viewer_counts) / len(viewer_counts)) if viewer_counts else 0

        if avg_viewers > 0:
            reason["reasoning"] = "Variety / Mixed genres"
            reason["num_channels"] = num_channels
            reason["avg_viewers"] = avg_viewers
            return f"Variety Community ({num_channels} channels)", reason

        reason["reasoning"] = "Uncategorized"
        reason["num_channels"] = num_channels
        return f"Community {comm_id}", reason
    
    def get_labels(self) -> Dict[int, str]:
        """
        Get all community labels.
        
        Returns:
            Dict mapping community_id -> label
        """
        return dict(self.community_labels)
    
    def get_label_for_community(self, comm_id: int) -> str:
        """
        Get the label for a specific community.
        
        Args:
            comm_id: Community ID
        
        Returns:
            Label string
        """
        return self.community_labels.get(comm_id, f"Community {comm_id}")
    
    def get_label_reasoning(self, comm_id: int) -> dict:
        """
        Get the reasoning/metadata for why a community was labeled a certain way.
        
        Args:
            comm_id: Community ID
        
        Returns:
            Dict with reasoning details
        """
        return self.community_reasons.get(comm_id, {})
    
    def get_statistics(self) -> dict:
        """
        Get tagging statistics.
        
        Returns:
            Dict with info about labeled communities
        """
        labeled_count = len(self.community_labels)
        
        # Count how many communities have clear dominant attribute
        clear_game = sum(1 for r in self.community_reasons.values() 
                        if "game_percentage" in r and r.get("game_percentage", 0) >= 60)
        
        clear_language = sum(1 for r in self.community_reasons.values() 
                           if "language_percentage" in r and r.get("language_percentage", 0) >= 40)
        
        return {
            "total_labeled": labeled_count,
            "with_clear_game": clear_game,
            "with_clear_language": clear_language,
            "uncategorized": labeled_count - clear_game - clear_language
        }


class LabeledCommunity:
    """
    Utility class representing a labeled community.
    """
    
    def __init__(self, comm_id: int, channels: Set[str], label: str, reasoning: dict = None):
        self.comm_id = comm_id
        self.channels = channels
        self.label = label
        self.reasoning = reasoning or {}
    
    def __repr__(self) -> str:
        return f"Community {self.comm_id}: '{self.label}' ({len(self.channels)} channels)"


if __name__ == "__main__":
    # Test with sample data
    from data_aggregator import DataAggregator
    from graph_builder import GraphBuilder
    from community_detector import CommunityDetector
    
    logging.basicConfig(level=logging.INFO)
    
    # Load and process data
    aggregator = DataAggregator("logs")
    aggregator.load_all()
    
    builder = GraphBuilder(overlap_threshold=1)
    graph = builder.build_graph(
        aggregator.get_channel_viewers(),
        aggregator.get_channel_metadata()
    )
    
    try:
        detector = CommunityDetector()
        detector.detect_communities(graph)
        communities = detector.get_communities()
        
        # Tag communities
        tagger = ClusterTagger()
        labels = tagger.tag_communities(communities, aggregator.get_channel_metadata())
        
        print("\nCommunity Labels:")
        for comm_id, label in labels.items():
            channels = communities[comm_id]
            print(f"  [{comm_id}] {label} ({len(channels)} channels)")
            
            # Show reasoning
            reasoning = tagger.get_label_reasoning(comm_id)
            print(f"       Reasoning: {reasoning.get('reasoning', 'N/A')}")
        
        # Show stats
        print("\nTagging Statistics:")
        stats = tagger.get_statistics()
        for key, value in stats.items():
            print(f"  {key}: {value}")
    
    except ImportError as e:
        print(f"Cannot test: {e}")
