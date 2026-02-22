# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram bot for accepting orders on AI-generated images. The client describes what they want and pays → Vision Agent automatically verifies the payment screenshot → if confirmed, bot generates a Claude prompt, admin manually creates the image in Midjourney/DALL-E/Imagen3, and delivers it.

**Current version:** MVP 0.2.0 — automatic payment verification via Vision Agent, dynamic pricing by order type.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Fill in variables: SUPABASE_URL, SUPABASE_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_IDS, ANTHROPIC_API_KEY
# Payment details: PAYMENT_CARD, PAYMENT_RECIPIENT, PAYMENT_PHONE
# Prices (optional): BASE_PRICE_IMAGE, PRICE_EXTERIOR, PRICE_INTERIOR

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

**Admin flow (auto):** Vision Agent confirms automatically → admin immediately receives prompt with "📤 Доставить" button → sends image → client receives it

**Admin flow (manual):** admin receives screenshot with Vision notes + ✅/❌ buttons → clicks Подтвердить → receives prompt → delivers image

**Key components:**

| File | Role |
|------|------|
| `main.py` | Entry point, starts polling |
| `src/integrations/telegram/bot.py` | Entire bot logic (~550 lines) |
| `src/core/database.py` | Lazy-initialized Supabase singleton (`db`). Uses `orders` and `messages` tables |
| `config/settings.py` | Pydantic-settings, reads from `.env`. All payment details and prices here |
| `config/prompts.py` | System prompts. `VISION_SYSTEM_PROMPT` is active; others are stubs for future agents |
| `config/agents_config.yaml` | Agent configuration for the planned multi-agent system (not yet wired up) |
| `scripts/setup_database.py` | Prints SQL to create tables — paste into Supabase SQL Editor |

**Claude is used in two places:**
1. `_verify_payment()` — Vision Agent, analyzes payment screenshot (model: `claude-sonnet-4-5`)
2. `_generate_prompt()` — converts client description into English image prompt (model: `claude-sonnet-4-5`)

**Dynamic pricing** via keyword matching in `_detect_price()`:
- Exterior/facade/rendering keywords → `settings.price_exterior` (default 1500 ₽)
- Interior/room keywords → `settings.price_interior` (default 1000 ₽)
- Anything else → `settings.base_price_image` (default 500 ₽)

**Vision Agent** — three confidence branches:
- `confidence > 0.9` and `payment_confirmed = true` → auto-confirm, client gets instant notification
- `0.7 ≤ confidence ≤ 0.9` (or Vision error) → manual admin review with Vision notes in caption
- `confidence < 0.7` and `payment_confirmed = false` → ask client for clearer screenshot (order not created)

**In-memory state:** `pending_deliveries: dict[int, int]` maps `admin_id → order_id`. Persisted in DB via `delivery_admin_id` column. Restored from DB on restart via `post_init()`.

### Planned Multi-Agent Architecture (not yet implemented)

`config/agents_config.yaml` and `config/prompts.py` define a 6-agent pipeline. The stubs in `src/core/agents/`, `src/core/orchestrator/`, `src/core/services/`, `src/integrations/llm/`, etc. are empty `__init__.py` files reserved for future architecture:

| Agent | Model | Role | Status |
|-------|-------|------|--------|
| Listener | claude-sonnet-4-5 | Classifies incoming messages | Not implemented |
| Manager | claude-opus-4-6 | Communicates with clients | Not implemented |
| Vision | claude-sonnet-4-5 | Verifies payment screenshots | **Implemented** in `bot.py` |
| Engineer | claude-opus-4-6 | Generates detailed image prompts | Not implemented (uses `_generate_prompt()`) |
| Generator | manual | Coordinates image generation | Manual only |
| Delivery | claude-sonnet-4-5 | Delivers results, collects feedback | Not implemented |

### Database

**Current:** two tables — `orders` and `messages`.

`orders` columns: `id`, `user_id`, `username`, `description`, `status`, `prompt`, `delivery_admin_id`, `created_at`.

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
