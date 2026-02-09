#!/bin/bash
# Quick setup and usage helpers

echo "ViewerAtlas Setup Helper"
echo "======================="
echo ""

# Check if we're in the right directory
if [ ! -f "twitchiobot/src/main.py" ]; then
    echo "❌ Error: Run this from the vieweratlas root directory"
    exit 1
fi

# Menu
echo "What would you like to do?"
echo ""
echo "1. Install dependencies"
echo "2. Analyze existing data (default config)"
echo "3. Analyze with rigorous config (TwitchAtlas-style)"
echo "4. Analyze with explorer config (fine-grained)"
echo "5. Check data quality"
echo "6. Start data collection"
echo ""

read -p "Enter choice (1-6): " choice

case $choice in
    1)
        echo "Installing dependencies..."
        pip install -r twitchiobot/src/requirements.txt
        echo "✓ Done!"
        ;;
    2)
        echo "Running analysis with default config..."
        cd twitchiobot
        python src/main.py analyze default
        echo ""
        echo "Results saved to: community_analysis/"
        ;;
    3)
        echo "Running analysis with rigorous config..."
        cd twitchiobot
        python src/main.py analyze rigorous
        echo ""
        echo "Results saved to: community_analysis/"
        ;;
    4)
        echo "Running analysis with explorer config..."
        cd twitchiobot
        python src/main.py analyze explorer
        echo ""
        echo "Results saved to: community_analysis/"
        ;;
    5)
        echo "Checking data quality..."
        cd twitchiobot
        python -c "
import sys
sys.path.append('src')
from data_aggregator import DataAggregator
agg = DataAggregator('logs')
agg.load_all()
quality = agg.get_data_quality_report()
print('\\nData Quality Report:')
print(f'  Total channels: {quality[\"total_channels\"]}')
print(f'  Total unique viewers: {quality[\"total_unique_viewers\"]}')
print(f'  Avg viewers/channel: {quality[\"avg_viewers_per_channel\"]:.1f}')
print(f'  One-off viewers: {quality[\"one_off_percentage\"]:.1f}%')
print(f'  Repeat visitors (2+): {quality[\"repeat_viewers_2plus\"]}')
"
        ;;
    6)
        echo "Starting continuous collection..."
        echo "This will run hourly collections."
        echo "Press Ctrl+C to stop."
        cd twitchiobot
        python src/main.py collect
        ;;
    *)
        echo "Invalid choice"
        exit 1
        ;;
esac
