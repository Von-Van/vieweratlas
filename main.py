import asyncio
import os
from twitchio import ChatLogger, load_channels
from dotenv import load_dotenv

load_dotenv()

def main():
    OAUTH_TOKEN = os.getenv("TWITCH_OAUTH_TOKEN")
    CHANNELS = load_channels()

    if not OAUTH_TOKEN or not CHANNELS:
        print("Missing token or channels.")
        return

    async def run_logger():
        bot = ChatLogger(token=OAUTH_TOKEN, channels=CHANNELS)
        await bot.start()

        print(f"📡 Logging chatters in {', '.join(CHANNELS)}")
        await asyncio.sleep(60)  # Duration to listen
        await bot.log_results()
        await bot.close()

    asyncio.run(run_logger())

if __name__ == "__main__":
    main()
