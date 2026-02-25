# CHANGELOG

История изменений проекта. Ведётся вручную + обновляется в конце каждой сессии с Claude Code.

Формат: `[YYYY-MM-DD HH:MM] Описание — что изменилось и зачем.`

---

## [2026-02-25] Stage 6 — Engineer Agent, Manager upgrade, промпты экстерьера

### Изменения

**config/prompts.py**
- **ENGINEER_SYSTEM_PROMPT** полностью переписан для экстерьеров: структура 3–5 предложений, список запрещённых AI-слов (render, 8K, photorealistic, CGI...), стиль «Amateur RAW photo, unedited real life photography», умные правила ландшафта (3 ветки), умное правило фона (реальное фото → protection line / CAD → описываем реалистичный фон), отдельная защита архитектурной геометрии, 4 примера промптов
- **MANAGER_SYSTEM_PROMPT**: убран фиксированный вопрос о стиле → заменён условным правилом (спрашивать только если фото показывает пустую коробку/новостройку без материалов). Добавлен ШАГ 2.7 — вопрос об окружении и фоне (газон, кусты, небо, соседи) — только если клиент сам не упомянул. `description` в JSON-сигнале теперь включает окружение/фон

**src/integrations/telegram/bot.py**
- **Manager Agent** апгрейднут: `claude-haiku-4-5-20251001` → `claude-sonnet-4-6`, `max_tokens` 500→1024, добавлен `temperature=0.7`
- **`/start` сообщение** переписано: список типов проектов, схема работы (3 шага), пример хорошего и плохого описания
- **`_process_payment_confirmed()`** исправлен: теперь сначала загружается `ref_bytes` из Supabase, затем вызывается `_call_engineer(description, ref_bytes)` — Engineer видит фото при генерации промпта. Ранее вызывался устаревший `_generate_prompt()` без фото
- **`_call_engineer()`** добавлен в bot.py как отдельная функция: передаёт description + reference bytes в ENGINEER_SYSTEM_PROMPT
- **Caption при доставке** улучшен: теперь показывает краткое описание задачи клиента вместо стандартного «Ваш заказ готов»

### Стратегическое решение
MVP сфокусирован на **экстерьерах** (фасады, здания, ландшафт). Интерьеры, персонажи, фотошоп — отдельные этапы после стабилизации. Аудитория: архитекторы, проектировщики, риэлторы, частники.

### Затронутые файлы
- `config/prompts.py` — ENGINEER_SYSTEM_PROMPT, MANAGER_SYSTEM_PROMPT
- `src/integrations/telegram/bot.py` — Manager model, start message, _call_engineer(), _process_payment_confirmed(), delivery caption

---

## [2026-02-24 23:59] Stage 5 — Listener Agent, PicklePersistence, фикс промптов

**Коммит:** `8b0c23a`

### Изменения
- **Listener Agent** (`_call_listener`, `_listener_response`, `handle_unknown_message`): классифицирует сообщения вне ConversationHandler (NEW_ORDER / PAYMENT / QUESTION / FEEDBACK / CANCEL / OTHER) и даёт осмысленный ответ вместо тишины
- **PicklePersistence**: состояние диалога CHAT/PAYMENT теперь переживает рестарты бота. Требует `name="main"` и `persistent=True` на ConversationHandler — добавлено
- **MANAGER_SYSTEM_PROMPT**: добавлено правило «мы работаем с любым фото» — Manager больше не отказывает клиентам с ArchiCAD/CAD-моделями, а просит скриншот из программы
- **`_generate_prompt()`**: инструкция переписана на краткие промпты (2-3 предложения). Убрана многословная структура, которая давала мыльный AI-looking результат. Остался минимальный AUTO-IMPROVE для неготового участка
- **`_auto_deliver()` fallback**: пофиксили второй случай `Media_caption_too_long` — в ветке «файл не найден в памяти» не было усечения caption

### Затронутые файлы
- `src/integrations/telegram/bot.py` — Listener Agent, PicklePersistence, ConversationHandler, `_generate_prompt()`, `_auto_deliver()` fallback
- `config/prompts.py` — MANAGER_SYSTEM_PROMPT (правило CAD/ArchiCAD)
- `.gitignore` — добавлен `*.pkl`

---

## [2026-02-24 20:24] fix: стабилизация сети — таймауты, retry БД, caption overflow

**Коммит:** `80425eb`

### Изменения
- **Таймауты Telegram** увеличены (`connect/write=30с`, `read=60с`, `get_updates` тоже) — бот не умирал когда Claude отвечал с задержкой
- **Manager Agent → Haiku** (`claude-haiku-4-5-20251001`) постоянно — быстрее для простого диалога, Sonnet оставлен для Vision и генерации промпта
- **Retry для `db.create_order()`** при SSL-ошибке Supabase: `db.reset()` + 1 сек + повтор
- **`Media_caption_too_long` в `_auto_deliver()`** исправлен: caption усекается перед добавлением суффикса
- **`error_handler`** фильтр сужен до `TimedOut` only — `BadRequest` больше не глушится как сетевая ошибка
- **`Database.reset()`** — новый метод для принудительного пересоздания Supabase-клиента

### Затронутые файлы
- `src/core/database.py` — метод `reset()`
- `src/integrations/telegram/bot.py` — таймауты, retry, caption fix, error_handler

---

## [2026-02-23 18:37] Stage 4 — Улучшение диалога, промпта, хранения референсов

**Коммит:** `4a32078`

### Изменения
- **MANAGER_SYSTEM_PROMPT** переписан: теперь 3 чётких шага (что менять → материал → фото), убран вопрос про стиль — клиент не должен выбирать стиль отдельно
- **`_generate_prompt()`**: добавлено правило `DO NOT change sky/background` в конец каждого промпта — предотвращает нежелательные изменения фона в Midjourney
- **Vision Agent**: `max_tokens` увеличен с 500 до 1500 — JSON-ответ больше не обрезается при длинных описаниях
- **Референсы**: загрузка в Supabase перенесена на момент создания заказа (раньше — позже в флоу), путь сохранения: `{username}/{order_id}.jpg`

### Затронутые файлы
- `config/agents_config.yaml` — обновлены модели агентов
- `config/prompts.py` — MANAGER_SYSTEM_PROMPT
- `requirements.txt` — обновлены зависимости
- `src/core/database.py` — логика сохранения референсов
- `src/integrations/telegram/bot.py` — логика промпта и Vision

---

## [2026-02-22 23:05] Аудит — исправление расхождений документации с кодом

**Коммит:** `1a475b3`

### Изменения
- **Dockerfile**: `CMD` исправлен с несуществующего `run_bot.py` на `main.py` — Docker-образ теперь запускается корректно
- **`.env.example`**: добавлены недостающие переменные `PAYMENT_PHONE`, `PRICE_EXTERIOR`, `PRICE_INTERIOR`
- **`setup_database.py`**: дефолтный статус заказа исправлен с `'new'` на `'awaiting_payment'` — соответствует state machine бота
- **`CLAUDE.md`**: обновлена до MVP 0.2.0, задокументированы Vision Agent, динамическое ценообразование, правила `pending_deliveries`, добавлен раздел Known Issues

### Почему важно
Расхождения между кодом и документацией могут привести к невоспроизводимому деплою и ошибкам при онбординге.

### Затронутые файлы
- `.env.example`
- `CLAUDE.md`
- `deployment/Dockerfile`
- `scripts/setup_database.py`

---

## [2026-02-22 22:37] Stage 2 — Vision Agent для автоматической проверки оплаты

**Коммит:** `d3f1538`

### Изменения
- **Vision Agent**: новый метод `_verify_payment()` — анализирует скриншоты чеков через Claude Vision (`claude-sonnet-4-5`)
- **Три ветки обработки** по уровню уверенности:
  - `> 0.9` и `payment_confirmed = true` → авто-подтверждение, клиент получает уведомление мгновенно
  - `0.7–0.9` (или ошибка Vision) → ручная проверка админом, в подписи — заметки Vision
  - `< 0.7` и `payment_confirmed = false` → запрос более чёткого скриншота у клиента
- **Динамическое ценообразование** `_detect_price()`: экстерьер/фасад → 1500 ₽, интерьер/комната → 1000 ₽, остальное → 500 ₽
- **Новые env-переменные**: `PAYMENT_PHONE` (для сверки), `PRICE_EXTERIOR`, `PRICE_INTERIOR`
- **Безопасный fallback**: любое исключение в Vision → `vision_result = None` → стандартный ручной флоу

### Архитектурные решения
- Vision не блокирует создание заказа — это намеренно
- Проверяются: сумма, статус перевода, ФИО получателя (по фамилии, инициалы необязательны полностью), номер телефона (только цифры)

### Затронутые файлы
- `config/prompts.py` — VISION_SYSTEM_PROMPT
- `config/settings.py` — новые ценовые переменные
- `src/integrations/telegram/bot.py` — Vision Agent, динамические цены

---

## [2026-02-22 21:04] Stage 1 — MVP Stabilization

**Коммит:** `73703c0`

### Изменения
- **Безопасность**: реквизиты оплаты (карта, получатель) перенесены в `.env` через `settings` — убраны хардкодированные значения
- **Персистентность**: `pending_deliveries` (in-memory dict) теперь сохраняется в БД через колонку `delivery_admin_id`
- **Восстановление состояния**: `post_init()` при старте бота восстанавливает `pending_deliveries` из БД — рестарт бота больше не ломает активные доставки
- **История сообщений**: сообщения клиентов и промпты Claude сохраняются в таблицу `messages`
- **Обработка ошибок**: `error_handler` с уведомлениями админа в Telegram при критических ошибках
- **Совместимость**: исправлен `filters.User()` API для `python-telegram-bot 21.x`
- **Python 3.14**: исправлена проблема с `asyncio` event loop
- **Удалены**: `logs/example_usage.py`, `scripts/run_bot.py`, `src/utils/error_reporter.py` — неиспользуемый код

### Затронутые файлы
- `.env.example`, `config/settings.py` — новые переменные
- `main.py` — asyncio fix
- `requirements.txt` — совместимые версии зависимостей
- `scripts/setup_database.py` — добавлена колонка `delivery_admin_id`
- `src/core/database.py` — `post_init()`, сохранение сообщений
- `src/integrations/telegram/bot.py` — всё вышеперечисленное

---

## [2026-02-21 20:32] MVP 0.1.0 — Первый запуск

**Коммит:** `0d88b00`

### Создан проект с нуля

**Структура:**
- `main.py` — точка входа, запуск поллинга
- `src/integrations/telegram/bot.py` — основная логика бота
- `src/core/database.py` — Supabase singleton
- `config/settings.py` — Pydantic-settings, все настройки из `.env`
- `config/prompts.py` — системные промпты для агентов
- `config/agents_config.yaml` — конфиг планируемой multi-agent архитектуры
- `scripts/setup_database.py` — генерация SQL для создания таблиц
- `deployment/Dockerfile` — Docker-образ

**Флоу клиента:** `/start` → описание → стиль → оплата → скриншот → ожидание → получение изображения

**Флоу админа:** получает скриншот + ✅/❌ кнопки → подтверждает → получает промпт → доставляет изображение

**База данных:** таблицы `orders` и `messages`. Статусы: `awaiting_payment` → `prompt_ready` → `delivered`

**Аналитика рынка:** добавлены `competitors/` (7 конкурентов) и `audience/target_audience.md`

---

## Известные баги (история исправлений)

| Дата | Баг | Исправление |
|------|-----|-------------|
| 2026-02-22 | Двойное уведомление клиента при авто-подтверждении: `_process_payment_confirmed()` и `get_payment()` оба отправляли уведомление | Убрано уведомление из `_process_payment_confirmed()` — каждый вызывающий код делает это сам |
| 2026-02-22 | Vision слишком строгий к форматированию телефона: `+7-(911)-423-86-81` отклонялся | Vision инструктирован сравнивать только цифры |
| 2026-02-22 | Vision не засчитывал сокращённое ФИО: `Абрамов М.` при ожидаемом `Абрамов М.Е.` | Vision засчитывает совпадение если фамилия совпала, инициалы необязательны полностью |

---

## Шаблон для новых записей

```
## [YYYY-MM-DD HH:MM] Краткое описание изменений

**Коммит:** `<hash>`

### Изменения
- Что именно сделано и зачем

### Затронутые файлы
- `path/to/file.py` — что изменилось
```
