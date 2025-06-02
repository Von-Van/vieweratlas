import os
import csv
import json
import requests
from datetime import datetime
from twitchio.ext import commands
from dotenv import load_dotenv

load_dotenv()

def load_channels_from_file(path="channels.txt"):
    if os.path.exists(path):
        with open(path, "r") as f:
            return [line.strip().lower() for line in f if line.strip()]
    return []

def load_channels():
    channels = load_channels_from_file()
    if channels:
        return channels
    env_channels = os.getenv("TWITCH_CHANNELS", "")
    return [c.strip().lower() for c in env_channels.split(",") if c.strip()]

class ChatLogger(commands.Bot):
    def __init__(self, token, channels, output_dir="logs"):
        super().__init__(
            token=token,
            prefix="!",
            initial_channels=channels
        )
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self.chatters = {channel: set() for channel in channels}
        self.start_time = None
        self.stream_data = {}

        self.client_id = os.getenv("TWITCH_CLIENT_ID")
        self.oauth_token = os.getenv("TWITCH_OAUTH_TOKEN")

    async def event_ready(self):
        print(f"✅ Bot ready. Logged in as: {self.nick}")

    async def event_message(self, message):
        if message.echo:
            return
        user = message.author.name.lower()
        channel = message.channel.name.lower()
        self.chatters[channel].add(user)

    def fetch_stream_info(self, channel_name):
        url = "https://api.twitch.tv/helix/streams"
        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.oauth_token}"
        }
        params = {"user_login": channel_name}

        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json().get("data", [])
            return data[0] if data else None
        except Exception as e:
            print(f"[{channel_name}] Failed to fetch stream info: {e}")
            return None

    async def log_results(self):
        timestamp = datetime.now().isoformat(timespec="seconds")

        for channel, users in self.chatters.items():
            stream_info = self.fetch_stream_info(channel)
            viewer_count = stream_info["viewer_count"] if stream_info else "Unavailable"
            game_name = stream_info["game_name"] if stream_info and "game_name" in stream_info else "Unknown"
            title = stream_info["title"] if stream_info else "Unavailable"
            started_at = stream_info["started_at"] if stream_info else "Unknown"

            print(f"\n #{channel} Stream Info:")
            print(f"  Title       : {title}")
            print(f"  Game        : {game_name}")
            print(f"  Viewers     : {viewer_count}")
            print(f"  Start Time  : {started_at}")
            print(f"  Chatters    : {len(users)}")

            filename_base = f"{channel}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            json_path = os.path.join(self.output_dir, f"{filename_base}.json")
            csv_path = os.path.join(self.output_dir, f"{filename_base}.csv")

            # Save JSON
            with open(json_path, "w") as f:
                json.dump({
                    "timestamp": timestamp,
                    "channel": channel,
                    "viewer_count": viewer_count,
                    "game_name": game_name,
                    "title": title,
                    "started_at": started_at,
                    "chatters": list(users)
                }, f, indent=2)

            # Save CSV
            with open(csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "channel", "viewer_count",
                    "game_name", "title", "started_at", "username"
                ])
                for user in sorted(users):
                    writer.writerow([
                        timestamp, channel, viewer_count,
                        game_name, title, started_at, user
                    ])

            print(f"📁 Saved log for #{channel}: {csv_path}, {json_path}")
