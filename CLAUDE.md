# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram bot for accepting orders on AI-generated images. The client describes what they want and pays → the admin gets a Claude-generated prompt, manually generates the image in Midjourney/DALL-E/Imagen3, and delivers it.

**Current version:** MVP 0.1.0 — manual payment confirmation and manual image generation.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Fill in 5 variables: SUPABASE_URL, SUPABASE_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_IDS, ANTHROPIC_API_KEY

# Create database tables (outputs SQL to paste into Supabase SQL Editor)
python scripts/setup_database.py

# Run the bot
python main.py
# or
python scripts/run_bot.py

# Docker
docker build -t ai-viz-bot .
docker run --env-file .env ai-viz-bot
```

## Architecture

### Current Implementation (MVP)

All bot logic lives in one file: `src/integrations/telegram/bot.py`.

**Client flow:** `/start` → description → style → payment screenshot → wait

**Admin flow:** receives notification with payment screenshot → clicks **Подтвердить** (confirm) → bot calls Claude to generate an image prompt → admin copies prompt into Midjourney/DALL-E → clicks **Доставить клиенту** → sends the generated image → client receives it

**Key components:**

| File | Role |
|------|------|
| `main.py` | Entry point, starts polling |
| `src/integrations/telegram/bot.py` | Entire bot: ConversationHandler for clients, InlineKeyboard callbacks for admin |
| `src/core/database.py` | Lazy-initialized Supabase singleton (`db`). Currently only uses the `orders` table |
| `config/settings.py` | Pydantic-settings, reads from `.env`. `TELEGRAM_ADMIN_IDS` is comma-separated |
| `config/prompts.py` | System prompts for the planned multi-agent system (not yet wired up) |
| `config/agents_config.yaml` | Agent configuration for the planned system (not yet wired up) |
| `scripts/setup_database.py` | Prints SQL to create tables — paste into Supabase SQL Editor |

**In-memory state:** `pending_deliveries: dict[int, int]` maps `admin_id → order_id` while waiting for the admin to send the generated image. This is not persisted — restarts will lose delivery-in-progress state.

**Claude is used only in `_generate_prompt()`** (`bot.py:289`) to convert the client's description into an English image prompt for Midjourney/DALL-E. Model: `claude-sonnet-4-5`.

### Planned Multi-Agent Architecture (not yet implemented)

`config/agents_config.yaml` and `config/prompts.py` define a 6-agent pipeline that has not been built yet. The stubs in `src/core/agents/`, `src/core/orchestrator/`, `src/core/services/`, `src/integrations/llm/`, etc. are empty `__init__.py` files reserved for this future architecture:

| Agent | Model | Role |
|-------|-------|------|
| Listener | claude-sonnet-4-5 | Classifies incoming messages |
| Manager | claude-opus-4-6 | Communicates with clients, gathers requirements |
| Vision | gemini-3-pro | Verifies payment screenshots automatically |
| Engineer | claude-opus-4-6 | Generates detailed image prompts |
| Generator | manual | Coordinates image generation (manual in MVP) |
| Delivery | claude-sonnet-4-5 | Delivers results, collects feedback |

### Database

**Current:** `src/core/database.py` uses only the `orders` table with columns: `id`, `user_id`, `username`, `description`, `status`, `prompt`, `created_at`.

Order statuses: `awaiting_payment` → `prompt_ready` → `delivered` (or `cancelled`).

**Planned:** `docs/database-schema.md` documents a full 7-table schema (`users`, `orders`, `messages`, `payments`, `generation_jobs`, `state_transitions`, `token_usage`) for the multi-agent system. This schema is not yet implemented.

## Configuration

All settings go in `.env` (see `.env.example`). Key variables:

- `TELEGRAM_ADMIN_IDS` — comma-separated Telegram user IDs with admin access
- `BASE_PRICE_IMAGE` — base price in rubles (default 500)
- `ENVIRONMENT` — `development` or `production`

**Payment details** are hardcoded in `bot.py:66-67` — replace `Сбербанк: 1234 5678 9012 3456` and `Иванов И.И.` with real payment details before going live.
