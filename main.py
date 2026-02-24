"""
Точка входа. Запуск: python main.py
"""
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
    print("Бот запущен. Нажми Ctrl+C для остановки.")
    app.run_polling()
