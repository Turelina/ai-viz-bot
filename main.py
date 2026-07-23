"""Application entry point. Run with: python main.py."""
import asyncio
import logging
from dotenv import load_dotenv
load_dotenv()
from src.integrations.telegram.bot import build_app

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)

if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = build_app()
    print("Bot started. Press Ctrl+C to stop.")
    app.run_polling()
