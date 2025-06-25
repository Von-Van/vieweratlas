import asyncio
import os
import time
from dotenv import load_dotenv
from datetime import datetime
from get_viewers import ChatLogger, load_channels
from update_channels import update_channel_list

load_dotenv()

BATCH_SIZE = 100
DURATION_PER_BATCH = 60  # seconds

def split_batches(lst, batch_size):
    for i in range(0, len(lst), batch_size):
        yield lst[i:i + batch_size]

async def run_logger_batch(oauth_token, batch):
    bot = ChatLogger(token=oauth_token, channels=batch)
    await bot.start()
    print(f"📡 Logging chatters in {len(batch)} channels")
    await asyncio.sleep(DURATION_PER_BATCH)
    await bot.log_results()
    await bot.close()

def wait_until_next_hour():
    now = datetime.now()
    next_hour = now.replace(minute=0, second=0, microsecond=0)
    if now.minute != 0:
        next_hour = next_hour.replace(hour=(now.hour + 1) % 24)
    wait_seconds = (next_hour - now).total_seconds()
    print(f"⏳ Waiting {int(wait_seconds)} seconds until the top of the hour...")
    time.sleep(wait_seconds)

def main():
    OAUTH_TOKEN = os.getenv("TWITCH_OAUTH_TOKEN")
    if not OAUTH_TOKEN:
        print("Missing OAuth token.")
        return

    while True:
        print("\n⏱️ Starting new logging cycle...")
        update_channel_list(limit=5000)
        all_channels = load_channels()

        for batch in split_batches(all_channels, BATCH_SIZE):
            asyncio.run(run_logger_batch(OAUTH_TOKEN, batch))

        print("✅ Finished all batches.")
        wait_until_next_hour()

if __name__ == "__main__":
    main()
