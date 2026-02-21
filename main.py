"""
Точка входа. Запуск: python main.py
"""
import logging
from src.integrations.telegram.bot import build_app

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)

if __name__ == "__main__":
    app = build_app()
    print("Бот запущен. Нажми Ctrl+C для остановки.")
    app.run_polling()
