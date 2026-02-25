# Telegram Бот для Приёма Заказов на AI-Изображения

MVP-бот для автоматизации заказов на AI-визуализацию (экстерьеры, фасады, ландшафт).
Клиент описывает что хочет и платит — multi-agent pipeline автоматически проверяет оплату, генерирует промпт и изображение, затем адмнин доставляет результат одной кнопкой.

**Текущая версия:** MVP 0.3.0

## Как это работает

```
Клиент: /start → описание → скрин оплаты
                                   ↓
              Vision Agent проверяет оплату (Claude Sonnet)
                                   ↓
         confidence > 0.9 → авто-подтверждение    |   ниже → ручная проверка админом
                                   ↓
              Engineer Agent генерирует промпт (Claude Sonnet)
                                   ↓
              Gemini Imagen 3 Pro генерирует изображение
                                   ↓
         Админ видит готовое изображение → [✅ Доставить клиенту] → Клиент получает результат
```

## Быстрый старт

### 1. Установить зависимости

```bash
pip install -r requirements.txt
```

### 2. Создать .env файл

```bash
cp .env.example .env
```

Заполни переменные:

| Переменная | Описание |
|-----------|----------|
| `SUPABASE_URL`, `SUPABASE_KEY` | Supabase Dashboard → Settings → API |
| `TELEGRAM_BOT_TOKEN` | От @BotFather |
| `TELEGRAM_ADMIN_IDS` | Твой Telegram ID (через запятую, узнать у @userinfobot) |
| `ANTHROPIC_API_KEY` | console.anthropic.com |
| `PAYMENT_CARD` | Реквизиты карты (e.g. `Сбербанк: 1234 5678 9012 3456`) |
| `PAYMENT_RECIPIENT` | ФИО получателя (e.g. `Иванов Иван Иванович`) |
| `PAYMENT_PHONE` | Телефон получателя для сверки Vision Agent |
| `GEMINI_API_KEY` | Google AI Studio (опционально — авто-генерация изображений) |

### 3. Создать таблицы в Supabase

```bash
python scripts/setup_database.py
```

Скопируй выведенный SQL → Supabase Dashboard → SQL Editor → Run.

### 4. Запустить

```bash
python main.py
```

---

## Стек

| Компонент | Технология |
|-----------|-----------|
| Бот | python-telegram-bot 21.x |
| Listener / Manager Agent | Claude Haiku / Sonnet (Anthropic) |
| Vision Agent | Claude Sonnet (Anthropic) |
| Engineer Agent | Claude Sonnet (Anthropic) |
| Генерация изображений | Gemini Imagen 3 Pro (`gemini-3-pro-image-preview`) |
| База данных | Supabase (PostgreSQL) |
| Язык | Python 3.11+ |

## Агенты

| Агент | Модель | Роль |
|-------|--------|------|
| Listener | `claude-haiku-4-5-20251001` | Классифицирует сообщения вне диалога, отвечает на вопросы |
| Manager | `claude-sonnet-4-6` | Ведёт диалог с клиентом, собирает детали заказа |
| Vision | `claude-sonnet-4-6` | Проверяет скриншоты оплаты |
| Engineer | `claude-sonnet-4-6` | Генерирует детальный промпт для Gemini Imagen |
| Generator | Gemini Imagen 3 Pro | Авто-генерирует изображение из промпта + референса |

## Структура проекта

```
main.py                              ← точка входа
config/
  settings.py                        ← настройки из .env
  prompts.py                         ← системные промпты всех агентов
  agents_config.yaml                 ← конфиг агентов (документация)
src/
  core/database.py                   ← Supabase singleton
  integrations/telegram/bot.py       ← весь бот (~920 строк)
scripts/
  setup_database.py                  ← SQL для создания таблиц
deployment/
  Dockerfile                         ← Docker-образ
```

## Команды и кнопки

**Для клиентов:**
- `/start` — начать новый заказ
- `/cancel` — отменить текущий заказ

**Для админа:**
- `/orders` — последние 10 заказов
- **✅ Подтвердить** — ручное подтверждение оплаты (если Vision не уверен)
- **❌ Отклонить** — отклонить оплату, уведомить клиента
- **✅ Доставить клиенту** — одна кнопка для доставки готового изображения
- **📤 Заменить вручную** — заменить авто-сгенерированное изображение своим

## Ценообразование

Динамическое, по ключевым словам в описании:
- Экстерьер / фасад / рендеринг → `PRICE_EXTERIOR` (по умолчанию 1500 ₽)
- Интерьер / комната → `PRICE_INTERIOR` (по умолчанию 1000 ₽)
- Всё остальное → `BASE_PRICE_IMAGE` (по умолчанию 500 ₽)

## Docker

```bash
docker build -t ai-viz-bot .
docker run --env-file .env ai-viz-bot
```

## Версия

**MVP 0.3.0** — multi-agent pipeline: Listener + Manager + Vision + Engineer, авто-генерация изображений через Gemini Imagen 3 Pro, хранение референсов в Supabase Storage.
