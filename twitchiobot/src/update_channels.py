import os
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
OAUTH_TOKEN = os.getenv("TWITCH_OAUTH_TOKEN")
CHANNEL_FILE = os.getenv("CHANNELS_FILE", "channels.txt")

def fetch_top_channels(limit=5000):
    url = "https://api.twitch.tv/helix/streams"
    headers = {
        "Client-ID": CLIENT_ID,
        "Authorization": f"Bearer {OAUTH_TOKEN}"
    }

    channels = []
    cursor = None
    total_fetched = 0

    while total_fetched < limit:
        params = {
            "first": min(100, limit - total_fetched)
        }
        if cursor:
            params["after"] = cursor

        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            streams = data.get("data", [])
            if not streams:
                break

            for stream in streams:
                channels.append(stream["user_login"].lower())

            total_fetched += len(streams)
            cursor = data.get("pagination", {}).get("cursor")

        except Exception as e:
            print(f"Error fetching streams: {e}")
            break

    return channels

def update_channels_file(channels, file_path=CHANNEL_FILE):
    try:
        with open(file_path, "w") as f:
            for channel in channels:
                f.write(f"{channel}\n")
        print(f"Updated {file_path} with {len(channels)} channels.")
    except Exception as e:
        print(f"Failed to write to {file_path}: {e}")

def update_channel_list(limit=5000):
    print(f"📡 Fetching top {limit} Twitch channels...")
    channels = fetch_top_channels(limit)
    update_channels_file(channels)
