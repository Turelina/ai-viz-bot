# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram bot for accepting orders on AI-generated images. The client describes what they want and pays → Vision Agent automatically verifies the payment screenshot → if confirmed, Engineer Agent generates a detailed prompt → Gemini Imagen 3 Pro (Nano Banana Pro) auto-generates the image → admin delivers it to the client.

**Current version:** MVP 0.3.0 — multi-agent pipeline (Listener + Manager + Vision + Engineer), Gemini auto-generation, reference photo storage with cleanup.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Fill in variables: SUPABASE_URL, SUPABASE_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_IDS, ANTHROPIC_API_KEY
# Payment details: PAYMENT_CARD, PAYMENT_RECIPIENT, PAYMENT_PHONE
# Prices (optional): BASE_PRICE_IMAGE, PRICE_EXTERIOR, PRICE_INTERIOR
# Gemini (optional): GEMINI_API_KEY (if empty — auto-generation disabled), GEMINI_PROXY

# Create database tables (outputs SQL to paste into Supabase SQL Editor)
python scripts/setup_database.py

# Run the bot
python main.py

# Docker
docker build -t ai-viz-bot .
docker run --env-file .env ai-viz-bot
```

## Architecture

### Current Implementation (MVP 0.2.0)

All bot logic lives in one file: `src/integrations/telegram/bot.py`.

**Client flow:** `/start` → description → style → price shown → payment screenshot → Vision Agent verifies → auto-confirm or wait for admin

**Admin flow (auto):** Vision Agent confirms → Engineer Agent generates prompt → Gemini auto-generates image → admin receives image with "✅ Доставить клиенту" button → one click → client receives it

**Admin flow (manual):** admin receives screenshot with Vision notes + ✅/❌ buttons → clicks Подтвердить → receives prompt → delivers image

**Key components:**

| File | Role |
|------|------|
| `main.py` | Entry point, starts polling |
| `src/integrations/telegram/bot.py` | Entire bot logic (~920 lines) |
| `src/core/database.py` | Lazy-initialized Supabase singleton (`db`). Uses `orders` and `messages` tables. `reset()` for SSL recovery |
| `config/settings.py` | Pydantic-settings, reads from `.env`. All payment details, prices, and API keys here |
| `config/prompts.py` | System prompts. All 4 active agent prompts live here (Vision, Manager, Listener, Engineer) |
| `config/agents_config.yaml` | Agent configuration reference (not wired up in code — used as documentation) |
| `scripts/setup_database.py` | Prints SQL to create tables — paste into Supabase SQL Editor |

**Claude is used in four places:**
1. `_call_listener()` — Listener Agent, classifies messages outside ConversationHandler (model: `claude-haiku-4-5-20251001`)
2. `_call_manager()` — Manager Agent, multi-turn client dialogue, collects order requirements (model: `claude-sonnet-4-6`)
3. `_verify_payment()` — Vision Agent, analyzes payment screenshot (model: `claude-sonnet-4-6`)
4. `_call_engineer()` — Engineer Agent, generates detailed English prompt for Nano Banana Pro (model: `claude-sonnet-4-6`)

**Gemini is used in one place:**
- `_generate_image()` — Nano Banana Pro (Gemini Imagen 3 Pro, `gemini-3-pro-image-preview`). Auto-generates image from prompt + reference photo. Disabled if `GEMINI_API_KEY` is not set.

**Dynamic pricing** via keyword matching in `_detect_price()`:
- Exterior/facade/rendering keywords → `settings.price_exterior` (default 1500 ₽)
- Interior/room keywords → `settings.price_interior` (default 1000 ₽)
- Anything else → `settings.base_price_image` (default 500 ₽)

**Vision Agent** — three confidence branches:
- `confidence > 0.9` and `payment_confirmed = true` → auto-confirm, client gets instant notification
- `0.7 ≤ confidence ≤ 0.9` (or Vision error) → manual admin review with Vision notes in caption
- `confidence < 0.7` and `payment_confirmed = false` → ask client for clearer screenshot (order not created)

**In-memory state:**
- `pending_deliveries: dict[int, int]` — `admin_id → order_id`. Persisted in DB via `delivery_admin_id` column. Restored on restart via `post_init()`.
- `pending_auto_images: dict[int, int]` — `order_id → file_id` of Gemini-generated image. Used for one-click delivery button "✅ Доставить клиенту". Not persisted (admin can always use "📤 Заменить вручную").

**Scheduled jobs** (started in `post_init()`):
- `_cleanup_old_reference_photos()` — runs every 24h. Deletes reference photos > 7 days old from Supabase Storage to control costs.

**ConversationHandler** is persistent via `PicklePersistence` (`bot_persistence.pkl`). `name="main"` and `persistent=True` required — state survives bot restarts.

### Multi-Agent Architecture

All agents live directly in `bot.py`. No separate orchestrator yet — `_process_payment_confirmed()` acts as the implicit pipeline coordinator.

| Agent | Model | Role | Status |
|-------|-------|------|--------|
| Listener | `claude-haiku-4-5-20251001` | Classifies messages outside ConversationHandler | **Implemented** (`_call_listener()`) |
| Manager | `claude-sonnet-4-6` | Multi-turn client dialogue, requirements gathering | **Implemented** (`_call_manager()`) |
| Vision | `claude-sonnet-4-6` | Verifies payment screenshots | **Implemented** (`_verify_payment()`) |
| Engineer | `claude-sonnet-4-6` | Generates detailed prompts for Nano Banana Pro | **Implemented** (`_call_engineer()`) |
| Generator | Gemini Imagen 3 Pro | Auto-generates image from prompt + reference | **Implemented** (`_generate_image()`), manual fallback if no key |
| Delivery | — | Delivers results, collects feedback | Not implemented (manual button flow) |

### Database

**Current:** two tables — `orders` and `messages`.

`orders` columns: `id`, `user_id`, `username`, `description`, `status`, `prompt`, `delivery_admin_id`, `reference_photo_url`, `created_at`.

`messages` columns: `id`, `order_id`, `role`, `content`, `created_at`.

Order statuses: `awaiting_payment` → `prompt_ready` → `delivered` (or `cancelled`).

**Planned:** `docs/database-schema.md` documents a full 7-table schema for the multi-agent system. Not yet implemented.

## Configuration

All settings go in `.env` (see `.env.example`). Key variables:

- `TELEGRAM_ADMIN_IDS` — comma-separated Telegram user IDs with admin access
- `PAYMENT_CARD` — card/bank details shown to client (e.g. `Сбербанк: 1234 5678 9012 3456`)
- `PAYMENT_RECIPIENT` — full name of payment recipient (e.g. `Иванов Иван Иванович`)
- `PAYMENT_PHONE` — recipient phone number for Vision verification (e.g. `+7 999 123 45 67`)
- `BASE_PRICE_IMAGE` — base price in rubles (default 500)
- `PRICE_EXTERIOR` — price for exterior/facade/rendering orders (default 1500)
- `PRICE_INTERIOR` — price for interior/room orders (default 1000)
- `ENVIRONMENT` — `development` or `production` (currently unused in logic, reserved)
- `GEMINI_API_KEY` — Google Gemini API key for Nano Banana Pro auto-generation (optional; if empty, auto-generation is disabled and admin generates manually)
- `GEMINI_PROXY` — HTTP proxy URL for Gemini API if geo-blocked (e.g. `http://user:pass@host:port`)
- `LOG_LEVEL` — logging level (default: `INFO`)

## Rules for Claude Code

These rules govern how Claude Code should behave when working in this repository. Follow them strictly.

### Secrets & Environment

- **Never read, print, or expose `.env` contents.** If the user asks to debug env variables, suggest they check values themselves.
- **Never hardcode secrets** (tokens, API keys, Supabase URLs, admin IDs) directly in source code. All secrets must come from `.env` via `config/settings.py`.
- **Never commit `.env`** — it is in `.gitignore` and must stay there.

### Admin Security

- **Always validate admin access** via `TELEGRAM_ADMIN_IDS` before executing any admin action. Never trust `user_id` from message context alone without this check.
- **Never add new admin commands** without updating the admin ID check. The check lives in `bot.py` — find it before adding new handlers.

### Database Safety

- **Never construct raw SQL strings with user input.** Supabase client uses parameterized queries — keep it that way.
- **Never delete or modify orders without a status check.** Orders follow a strict state machine: `awaiting_payment` → `prompt_ready` → `delivered` (or `cancelled`). Skipping states breaks consistency.

### Bot Architecture

- **Do not break the `ConversationHandler` state machine.** It is the core of the client flow. Adding or removing states without tracing all transitions will cause silent failures.
- **`pending_deliveries` is in-memory but also persisted in DB** via `delivery_admin_id` column. `post_init()` restores it on restart. Do not bypass the DB persistence.
- **Do not add new Telegram handlers** without checking for conflicts with existing `ConversationHandler` states.
- **Vision Agent is a safe fallback** — any exception in `_verify_payment()` sets `vision_result = None` and falls back to manual admin review. Never let Vision block order creation.
- **Engineer Agent wraps `_call_engineer()`** — if it fails, the exception bubbles up to `_process_payment_confirmed()`. Always keep error handling there. `_generate_prompt()` is left in the file as a fallback reference but is not called.
- **PicklePersistence file is `bot_persistence.pkl`** — do not delete it in production; it holds active ConversationHandler states. ConversationHandler must keep `name="main"` and `persistent=True` or state is lost on restart.

### Git & File Operations

- **Never force-push** (`git push --force`) without explicit user confirmation.
- **Never delete files or branches** without explicit user confirmation.
- **Never commit changes** unless the user explicitly asks. Propose the commit message and wait for approval.
- **Never skip pre-commit hooks** (`--no-verify`) unless the user explicitly requests it.
- **Never amend published commits** — create a new commit instead.

### Code Quality

- **Read the file before editing it.** Never suggest changes to code you haven't read.
- **Do not create new files** unless they are strictly necessary. Prefer editing existing files.
- **Do not over-engineer.** Only implement what was explicitly requested. No extra features, no premature abstractions, no unnecessary refactoring.
- **Do not add comments or docstrings** to code you did not change.
- **Do not add error handling for scenarios that cannot happen** in this codebase.

### Known Issues & Past Mistakes

> This section is updated as bugs are found and fixed. Each entry prevents the same mistake from being reintroduced.

- [2026-02-22] **Двойное уведомление клиента при авто-подтверждении**: `_process_payment_confirmed()` отправляла уведомление клиенту, и `get_payment()` тоже. Исправлено: уведомление клиента убрано из `_process_payment_confirmed()` — каждый вызывающий код делает это сам.
- [2026-02-22] **Vision слишком строгий к форматированию телефона**: `+7-(911)-423-86-81` и `+7 911 423 86 81` — одинаковые номера. Исправлено в промпте: Vision явно инструктирован сравнивать только цифры.
- [2026-02-22] **Vision не засчитывал сокращённое ФИО**: `Абрамов М.` при ожидаемом `Абрамов М.Е.` отклонялся. Исправлено: Vision засчитывает совпадение если фамилия совпала, инициалы необязательны полностью.
- [2026-02-22] **Хардкод реквизитов оплаты в bot.py**: Строки `"Сбербанк: 1234 5678 9012 3456"` и `"Иванов И.И."` были прямо в коде — нельзя сменить без правки исходника. Исправлено: вынесено в `.env` → `settings.payment_card`, `settings.payment_recipient`.
- [2026-02-22] **`filters.User(user_ids=...)` — неверный параметр**: python-telegram-bot принимает `user_id=` (без 's'). При `user_ids=` фото от админов молча игнорировались — `handle_admin_photo` не вызывался вообще. Исправлено: `filters.User(user_id=settings.admin_ids_list)`.
- [2026-02-22] **Anthropic-клиент создавался заново при каждом вызове**: `_generate_prompt()` делал `anthropic.AsyncAnthropic(...)` каждый раз — лишние соединения, лишняя память. Исправлено: синглтон `_get_anthropic_client()`, клиент создаётся один раз.
- [2026-02-22] **`pending_deliveries` терялся при рестарте**: Словарь `{admin_id: order_id}` жил только в RAM. После крэша незавершённые доставки "подвисали" без возможности продолжить. Исправлено: добавлена колонка `delivery_admin_id` в `orders`, `post_init()` восстанавливает словарь из БД при старте.
- [2026-02-22] **Необработанные исключения не видны админам**: Любой неперехваченный exception — бот падал молча, без уведомления. Исправлено: добавлен глобальный `error_handler` → все критические ошибки рассылаются всем admin_ids.
- [2026-02-22] **Dockerfile CMD указывал на несуществующий файл**: `CMD ["python", "scripts/run_bot.py"]` — файл был удалён, Docker-контейнер не стартовал. Исправлено: `CMD ["python", "main.py"]`.
- [2026-02-22] **`setup_database.py` создавал заказы со статусом `'new'`**: Стейт-машина бота ожидает `'awaiting_payment'` — заказ со статусом `'new'` никакой ветки не проходит. Исправлено: дефолт исправлен на `'awaiting_payment'`.
- [2026-02-22] **Vision Agent не обрабатывал ответ без JSON**: `json.loads(text)` без проверки — при любом нечисловом ответе модели бросал исключение и блокировал создание заказа. Исправлено: если `re.search(r'\{...\}', text)` не нашёл совпадения — возвращаем `None` (fallback на ручную проверку).
- [2026-02-22] **`max_tokens=500` для Vision Agent — JSON обрезался**: Подробный `VISION_SYSTEM_PROMPT` + JSON-ответ превышали 500 токенов, ответ truncated → `json.loads` падал. Исправлено: `max_tokens=1500`.
- [2026-02-24] **Фото-референс сохранялся в истории диалога как base64**: `context.user_data["history"]` хранил сырой base64 изображения. При каждом следующем сообщении клиента эта история (с многокилобайтным base64) шла в API — лишние токены и риск превысить контекст. Исправлено: в историю пишется текстовый placeholder `[📎 Фото референса]`, base64 используется только для текущего вызова API.
- [2026-02-24] **`Media_caption_too_long` в `_auto_deliver()`**: caption с `{prompt[:950]}` (~1010 символов) + суффикс `✅ Заказ доставлен` превышал 1024. Затем except-блок пытался добавить текст ошибки к тому же caption — тоже падал. Исправлено: base усекается перед добавлением суффикса; при ошибке — `edit_message_reply_markup` + отдельное сообщение админу вместо редактирования caption.
- [2026-02-24] **`BadRequest` глушился как сетевая ошибка**: в `error_handler` фильтр `isinstance(error, NetworkError)` в ptb v21 захватывал `BadRequest` (400), ошибки типа `Media_caption_too_long` тихо игнорировались вместо уведомления админа. Исправлено: фильтр сужен до `isinstance(error, TimedOut)` — только таймауты считаются транзиентными.
- [2026-02-24] **`db.create_order()` не делал retry при SSL-ошибке Supabase**: singleton-клиент держал битое SSL-соединение и не пересоздавался — повторный вызов с тем же клиентом тоже падал. Исправлено: добавлен `db.reset()` + один retry с задержкой 1с в `get_payment()`.
- [2026-02-24] **Дефолтный `write_timeout` Telegram = 5 сек**: при задержке Claude API 10–15 сек бот не успевал отправить ответ в Telegram → `TimedOut`. Исправлено: `connect_timeout=30`, `write_timeout=30`, `read_timeout=60`, `get_updates_connect_timeout=30`, `get_updates_read_timeout=30` в `build_app()`.
- [2026-02-24] **`ConversationHandler` без `name`/`persistent=True` не сохранял состояние через PicklePersistence**: PTB не регистрировал ConversationHandler в persistence-слое — состояние CHAT/PAYMENT терялось при рестарте, Listener перехватывал сообщения вместо продолжения диалога. Исправлено: добавлены `name="main"` и `persistent=True` в ConversationHandler.
- [2026-02-24] **Manager Agent отказывал клиентам с ArchiCAD/CAD-моделями**: модель не знала как обрабатывать запросы с упоминанием CAD и говорила «нужен специализированный 3D-софт» — потеря заказа. Исправлено: добавлено правило в MANAGER_SYSTEM_PROMPT: работаем с любым фото/скриншотом, просим прислать скриншот из программы.
- [2026-02-24] **`_generate_prompt()` создавал многословные промпты (~300 слов)**: структура из 4 секций + AUTO-IMPROVE ландшафта давала мыльный AI-looking результат и пустой/размытый фон. Исправлено: инструкция переписана на max 2-3 предложения, описываем только запрошенные изменения, AUTO-IMPROVE — одна короткая фраза только если участок выглядит неготовым.
- [2026-02-24] **`Media_caption_too_long` в `_auto_deliver()` fallback-ветке**: ветка «file_id не найден в памяти» (строка 629) вызывала `edit_message_caption` без усечения caption — повторный `BadRequest`. Исправлено: добавлена та же логика усечения что и в ветке успешной доставки.
- [2026-02-25] **`_process_payment_confirmed()` вызывал `_generate_prompt()` до загрузки ref_bytes**: Engineer не получал фото-референс, генерировал промпт вслепую только по тексту описания. Исправлено: порядок изменён — сначала грузится `ref_bytes` из Supabase, затем вызывается `_call_engineer(description, ref_bytes)`.
- [2026-02-25] **Manager Agent работал на Haiku с `max_tokens=500`**: для сложных диалогов о деталях экстерьера (материалы, окружение, фон) Haiku не хватало контекста и токенов. Исправлено: апгрейд на `claude-sonnet-4-6`, `max_tokens=1024`, `temperature=0.7`.
- [2026-02-25] **ENGINEER_SYSTEM_PROMPT использовал AI-buzzwords**: слова `photorealistic`, `8K`, `HDR`, `render` давали Gemini Imagen сигнал генерировать CGI-картинки вместо реалистичных фото. Исправлено: banned words list, заменено на `Amateur RAW photo, unedited real life photography`.

## Session End Checklist

> Когда пользователь говорит **"завершаем сессию"** (или "закрываем", "session end") — выполни все пункты по порядку.

1. **CHANGELOG.md** — добавь запись о сессии: что делали, что изменилось, дата. Формат: `## [YYYY-MM-DD HH:MM] Краткое описание`.
2. **Known Issues** — были найдены или исправлены баги в этой сессии? Добавь записи в `### Known Issues & Past Mistakes` выше.
3. **Roadmap** — завершён какой-то этап или появились новые задачи? Обнови `## Roadmap` ниже.
4. **Коммит** — после обновления всех файлов: проверь `git status`, предложи один коммит со всем сразу в формате `feat/fix/chore: описание`.

## Roadmap — Следующие шаги

> Список запланированных задач в порядке приоритета. Обновляется в конце каждой сессии.

### В работе / ближайшие

- [x] **Stage 5 — Listener Agent**: ✅ Реализован. Классифицирует сообщения вне ConversationHandler через Haiku, отвечает статичными шаблонами по типу. PicklePersistence добавлен — состояние диалога переживает рестарты.
- [x] **Stage 6 — Manager Agent**: ✅ Реализован. Апгрейд с Haiku на `claude-sonnet-4-6`. Запрет markdown в ответах. Температура 0.7.
- [x] **Stage 7 — Engineer Agent**: ✅ Реализован. `_call_engineer()` заменил `_generate_prompt()`. Принимает фото-референс, генерирует детальный промпт для Nano Banana Pro (`claude-sonnet-4-6`, `temperature=0.5`). `ENGINEER_SYSTEM_PROMPT` переписан под Gemini Imagen 3 Pro.

### Среднесрочные

- [ ] **Stage 8 — Delivery Agent**: автоматическая доставка изображения клиенту + сбор обратной связи (модель: `claude-sonnet-4-6`).

### Инфраструктура

- [ ] **Полная схема БД**: реализовать 7-табличную схему из `docs/database-schema.md` (сейчас работают только `orders` и `messages`).
- [ ] **Оркестратор**: подключить `config/agents_config.yaml` — сейчас файл описывает архитектуру, но не используется в коде.
