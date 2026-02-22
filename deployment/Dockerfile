# Базовый образ Python
FROM python:3.11-slim

# Установка рабочей директории
WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Копирование файлов зависимостей
COPY requirements.txt .

# Установка Python зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода приложения
COPY . .

# Создание папки для логов
RUN mkdir -p logs

# Переменные окружения
ENV PYTHONUNBUFFERED=1
ENV ENVIRONMENT=production

# Открыть порт (если нужен FastAPI)
EXPOSE 8000

# Команда запуска
CMD ["python", "main.py"]
