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
                game = meta.get("game", "Unknown")
                if game and game != "Unknown":
                    games.append(game)
                
                lang = meta.get("language", "Unknown")
                if lang and lang != "Unknown":
                    languages.append(lang)
                
                viewers = meta.get("viewers", 0)
                if viewers:
                    viewer_counts.append(viewers)
        
        # Find dominant attributes
        reason = {"reasoning": ""}
        
        # Dominant game
        game_counts = Counter(games)
        if game_counts:
            top_game, game_freq = game_counts.most_common(1)[0]
            game_percentage = (game_freq / len(channels)) * 100
            
            if game_percentage >= 60:  # Clear dominant game
                label = f"{top_game}"
                reason["dominant_game"] = top_game
                reason["game_percentage"] = game_percentage
                reason["reasoning"] = f"{top_game} ({game_percentage:.0f}% of channels)"
                return label, reason
        
        # Dominant language + game combo
        if languages and games:
            lang_counts = Counter(languages)
            top_lang, lang_freq = lang_counts.most_common(1)[0]
            lang_percentage = (lang_freq / len(channels)) * 100
            
            if lang_percentage >= 40 and game_counts:
                top_game = game_counts.most_common(1)[0][0]
                label = f"{top_game} ({top_lang})"
                reason["dominant_game"] = top_game
                reason["dominant_language"] = top_lang
                reason["language_percentage"] = lang_percentage
                reason["reasoning"] = f"{top_game} / {top_lang}-speaking"
                return label, reason
        
        # Only language available
        if languages:
            lang_counts = Counter(languages)
            top_lang, lang_freq = lang_counts.most_common(1)[0]
            lang_percentage = (lang_freq / len(channels)) * 100
            
            if lang_percentage >= 50:
                label = f"{top_lang}-speaking Variety"
                reason["dominant_language"] = top_lang
                reason["language_percentage"] = lang_percentage
                reason["reasoning"] = f"{top_lang}-speaking community"
                return label, reason
        
        # Top 2-3 games if no clear dominant
        if game_counts:
            top_games = game_counts.most_common(3)
            if len(top_games) >= 2:
                game_names = [g[0] for g in top_games]
                label = f"{game_names[0]} / {game_names[1]} Mix"
                reason["top_games"] = game_names
                reason["reasoning"] = f"Mixed: {', '.join(game_names[:2])}"
                return label, reason
        
        # Fallback: use size or just generic label
        num_channels = len(channels)
        avg_viewers = int(sum(viewer_counts) / len(viewer_counts)) if viewer_counts else 0
        
        if avg_viewers > 0:
            label = f"Variety Community ({num_channels} channels)"
            reason["reasoning"] = "Variety / Mixed genres"
            reason["num_channels"] = num_channels
            reason["avg_viewers"] = avg_viewers
            return label, reason
        
        label = f"Community {comm_id}"
        reason["reasoning"] = "Uncategorized"
        reason["num_channels"] = num_channels
        return label, reason
    
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
