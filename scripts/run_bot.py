"""
Запуск бота. Это то же самое что: python main.py из корня проекта.
"""
import sys
from pathlib import Path

# Добавляем корень проекта в path чтобы работали импорты
sys.path.insert(0, str(Path(__file__).parent.parent))

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
