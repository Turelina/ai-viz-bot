"""
Telegram бот для приёма заказов на AI-изображения.

Флоу клиента:
  /start → описание → стиль → скрин оплаты → ожидание

Флоу админа (ты):
  Получаешь уведомление с кнопками → подтверждаешь оплату →
  получаешь промпт от Claude → генеришь картинку →
  жмёшь "Доставить" → отправляешь фото → клиент получает результат
"""

import asyncio
import os
import httpx
import base64
import io
import json
import logging
import re
import anthropic
from google import genai
from google.genai import types as genai_types
from config.prompts import get_agent_prompt, LISTENER_SYSTEM_PROMPT
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
    PicklePersistence,
)
from config.settings import settings
from src.core.database import db

logger = logging.getLogger(__name__)

# ─── Claude клиент (синглтон) ─────────────────────────────────────────────────
_anthropic_client: anthropic.AsyncAnthropic | None = None

def _get_anthropic_client() -> anthropic.AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _anthropic_client

# ─── Gemini клиент (синглтон) ─────────────────────────────────────────────────
_gemini_client: genai.Client | None = None

def _get_gemini_client() -> genai.Client | None:
    """None если ключ не задан — авто-генерация отключена."""
    global _gemini_client
    if not settings.gemini_api_key:
        return None
    if _gemini_client is None:
        if settings.gemini_proxy:
            os.environ['HTTPS_PROXY'] = settings.gemini_proxy
        try:
            _gemini_client = genai.Client(api_key=settings.gemini_api_key)
        finally:
            os.environ.pop('HTTPS_PROXY', None)
    return _gemini_client

# ─── Состояния диалога с клиентом ────────────────────────────────────────────
CHAT, PAYMENT = range(2)

# ─── Отслеживаем какой заказ доставляем {admin_id: order_id} ─────────────────
pending_deliveries: dict[int, int] = {}

# ─── file_id авто-сгенерированного фото {order_id: telegram_file_id} ─────────
pending_auto_images: dict[int, str] = {}


def _price_from_category(category: str) -> int:
    """Возвращает цену по категории из Manager-сигнала."""
    if category == "exterior":
        return settings.price_exterior
    if category == "interior":
        return settings.price_interior
    return settings.base_price_image


# ─── Клиентский флоу ─────────────────────────────────────────────────────────

async def _call_manager(history: list[dict]) -> str:
    """Вызывает Manager Agent с историей диалога."""
    client = _get_anthropic_client()
    system_prompt = get_agent_prompt(
        "manager",
        base_price=settings.base_price_image,
        price_exterior=settings.price_exterior,
        price_interior=settings.price_interior,
    )
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        system=system_prompt,
        messages=history,
    )
    return response.content[0].text


async def _call_listener(text: str) -> dict | None:
    """Классифицирует входящее сообщение через Listener Agent (Haiku).
    Возвращает dict с message_type и confidence или None при ошибке."""
    client = _get_anthropic_client()
    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=3000,
            temperature=0.3,
            system=LISTENER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        raw = response.content[0].text
        match = re.search(r'\{[\s\S]+\}', raw)
        if not match:
            return None
        return json.loads(match.group(0))
    except Exception as e:
        logger.error(f"Listener Agent ошибка: {e}")
        return None


def _listener_response(message_type: str) -> str:
    """Возвращает ответ на сообщение по классификации Listener Agent."""
    if message_type == "NEW_ORDER":
        return (
            "Чтобы оформить заказ, нажмите /start — "
            "задам несколько вопросов и рассчитаю стоимость."
        )
    if message_type == "PAYMENT":
        return (
            "Оплата происходит в процессе оформления заказа. "
            "Начните с /start."
        )
    if message_type == "QUESTION":
        return (
            "Мы делаем AI-визуализации недвижимости:\n\n"
            f"• Экстерьер / фасад — от {settings.price_exterior} ₽\n"
            f"• Интерьер / комната — от {settings.price_interior} ₽\n"
            f"• Другие изображения — от {settings.base_price_image} ₽\n\n"
            "Нажмите /start чтобы оформить заказ."
        )
    if message_type == "FEEDBACK":
        return "Спасибо за обратную связь! Если хотите сделать заказ — /start."
    if message_type == "CANCEL":
        return "Активных заказов нет. Чтобы создать новый — /start."
    return (
        "Привет! Я бот для заказа AI-визуализаций. "
        "Чтобы начать — нажмите /start."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    context.user_data["history"] = []
    await update.message.reply_text(
        "Привет! 👋 Я помогу вам заказать AI-изображение.\n\n"
        "Расскажите, что хотите создать — уточним детали вместе."
    )
    return CHAT


async def _handle_manager_response(
    response_text: str,
    history: list[dict],
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Обрабатывает ответ Manager Agent: JSON-сигнал → PAYMENT, текст → CHAT."""
    try:
        match = re.search(r'\{[\s\S]+\}', response_text)
        if match:
            data = json.loads(match.group(0))
            if data.get("action") == "ready_for_payment":
                category = data.get("price_category", "base")
                description = data.get("description", "")
                price = _price_from_category(category)
                context.user_data["full_description"] = description
                context.user_data["price"] = price
                history.append({"role": "assistant", "content": response_text})
                context.user_data["history"] = history
                await update.message.reply_text(
                    f"Отлично! Стоимость заказа: {price} ₽\n\n"
                    f"Реквизиты для оплаты:\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"{settings.payment_card}\n"
                    f"Получатель: {settings.payment_recipient}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"После оплаты пришлите скриншот чека:"
                )
                return PAYMENT
    except (json.JSONDecodeError, KeyError, TypeError):
        pass  # Не JSON-сигнал — отправляем как обычный текст

    history.append({"role": "assistant", "content": response_text})
    context.user_data["history"] = history
    await update.message.reply_text(response_text)
    return CHAT


async def manager_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ведёт текстовый диалог с клиентом через Manager Agent."""
    user_text = update.message.text
    history = context.user_data.get("history", [])
    history.append({"role": "user", "content": user_text})

    try:
        response_text = await _call_manager(history)
    except Exception as e:
        logger.error(f"Manager Agent ошибка: {e}")
        await update.message.reply_text("Произошла ошибка, попробуйте ещё раз.")
        return CHAT

    return await _handle_manager_response(response_text, history, update, context)


async def manager_chat_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает фото-референс клиента в диалоге с Manager Agent."""
    caption = update.message.caption or ""
    history = context.user_data.get("history", [])

    try:
        tg_file = await context.bot.get_file(update.message.photo[-1].file_id)
        photo_bytes = bytes(await tg_file.download_as_bytearray())
        image_b64 = base64.standard_b64encode(photo_bytes).decode("utf-8")
    except Exception as e:
        logger.error(f"Ошибка скачивания фото-референса: {e}")
        await update.message.reply_text("Не удалось загрузить фото, попробуйте ещё раз.")
        return CHAT

    # Сохраняем байты в памяти — загрузим в Storage после создания заказа (нужен order_id)
    context.user_data.setdefault("reference_photo_bytes", []).append(photo_bytes)
    logger.info(f"Фото-референс #{len(context.user_data['reference_photo_bytes'])} сохранён в памяти")

    # Сообщение с изображением только для текущего вызова API
    image_content: list = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
        {"type": "text", "text": caption if caption else "Вот фото референса."},
    ]
    call_history = history + [{"role": "user", "content": image_content}]

    try:
        response_text = await _call_manager(call_history)
    except Exception as e:
        logger.error(f"Manager Agent ошибка при обработке фото: {e}")
        await update.message.reply_text("Произошла ошибка, попробуйте ещё раз.")
        return CHAT

    # В историю сохраняем текстовый placeholder, а не base64 — экономим токены
    placeholder = f"[📎 Фото референса]{': ' + caption if caption else ''}"
    history.append({"role": "user", "content": placeholder})

    return await _handle_manager_response(response_text, history, update, context)


async def get_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text(
            "Пожалуйста, пришлите скриншот оплаты как фото."
        )
        return PAYMENT

    user = update.message.from_user
    full_description = context.user_data.get("full_description", "")
    price = context.user_data.get("price", settings.base_price_image)
    photo_file_id = update.message.photo[-1].file_id

    # ── Vision Agent: проверяем скриншот перед созданием заказа ──────────────
    vision_result: dict | None = None
    try:
        tg_file = await context.bot.get_file(photo_file_id)
        photo_bytes = bytes(await tg_file.download_as_bytearray())
        vision_result = await _verify_payment(
            photo_bytes,
            price,
            settings.payment_recipient,
            settings.payment_card,
            settings.payment_phone,
        )
        logger.info(f"Vision Agent результат: {vision_result}")
    except Exception as e:
        logger.error(f"Vision Agent ошибка (fallback на ручную проверку): {e}")

    # Если Vision уверен, что это не чек — просим прислать чёткий скриншот
    if (
        vision_result is not None
        and not vision_result.get("payment_confirmed", True)
        and vision_result.get("confidence", 1.0) < 0.7
    ):
        await update.message.reply_text(
            "⚠️ Не удалось распознать чек оплаты на этом изображении.\n"
            "Пожалуйста, пришлите более чёткий скриншот с суммой и статусом платежа."
        )
        return PAYMENT

    # ── Создаём заказ в БД (retry при SSL-ошибке) ────────────────────────────
    order = None
    ref_urls: list[str] = []
    for attempt in range(2):
        try:
            order = db.create_order(
                user_id=user.id,
                username=user.username or user.first_name or str(user.id),
                description=full_description,
            )
            db.save_message(order["id"], "user", full_description)
            # Загружаем фото-референсы в Storage с именами {username}/{order_id}.jpg
            ref_bytes_list = context.user_data.get("reference_photo_bytes", [])
            if ref_bytes_list:
                safe_username = re.sub(r"[^\w.-]", "_", user.username or user.first_name or str(user.id))
                for idx, ref_bytes in enumerate(ref_bytes_list, start=1):
                    try:
                        url = db.upload_reference_photo(ref_bytes, safe_username, order["id"], index=idx)
                        ref_urls.append(url)
                        logger.info(f"Референс #{idx} загружен: {safe_username}/{order['id']}.jpg")
                    except Exception as ref_e:
                        logger.error(f"Не удалось загрузить референс #{idx} заказа #{order['id']}: {ref_e}")
                if ref_urls:
                    try:
                        db.update_reference_photo(order["id"], ref_urls[0])
                    except Exception as ref_e:
                        logger.error(f"Не удалось сохранить reference_photo_url заказа #{order['id']}: {ref_e}")
            break  # успех
        except Exception as e:
            if attempt == 0:
                logger.warning(f"db.create_order попытка 1 не удалась: {e} — пересоздаём клиент")
                db.reset()
                await asyncio.sleep(1)
            else:
                logger.error(f"Ошибка создания заказа в БД: {e}")
                await update.message.reply_text(
                    "⚠️ Технический сбой — попробуйте отправить скриншот ещё раз.\n"
                    "Если проблема повторяется, напишите нам напрямую."
                )
                return PAYMENT
    context.user_data["reference_photo_urls"] = ref_urls

    # ── Авто-подтверждение при высокой уверенности Vision ────────────────────
    if (
        vision_result is not None
        and vision_result.get("payment_confirmed", False)
        and vision_result.get("confidence", 0) > 0.9
    ):
        await update.message.reply_text(
            "✅ Оплата подтверждена автоматически!\n"
            "Готовим ваш заказ — скоро пришлём результат."
        )
        try:
            await _process_payment_confirmed(context, order["id"], settings.admin_ids_list)
        except Exception as e:
            logger.error(f"Ошибка авто-подтверждения заказа #{order['id']}: {e}")
            for admin_id in settings.admin_ids_list:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=(
                            f"⚠️ Заказ #{order['id']} авто-подтверждён, но возникла ошибка "
                            f"генерации промпта: {e}\nОбработайте вручную."
                        ),
                    )
                except Exception:
                    pass
        return ConversationHandler.END

    # ── Стандартный флоу: уведомляем админов вручную ─────────────────────────
    await update.message.reply_text(
        "✅ Скриншот получен!\n"
        "Проверяем оплату — обычно занимает до 30 минут.\n"
        "Как подтвердим, сразу напишем."
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_{order['id']}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{order['id']}"),
    ]])

    # Telegram ограничение: caption не более 1024 символов
    short_desc = (full_description[:300] + "...") if len(full_description) > 300 else full_description
    caption = (
        f"💰 Новая оплата!\n\n"
        f"Заказ #{order['id']}\n"
        f"Клиент: @{user.username or user.first_name} (ID: {user.id})\n\n"
        f"Описание:\n{short_desc}\n\n"
        f"Сумма: {price} ₽"
    )

    # Добавляем заметки Vision Agent (если есть результат)
    if vision_result is not None:
        confidence = vision_result.get("confidence", 0)
        amount = vision_result.get("amount")
        status = vision_result.get("status", "")
        notes = vision_result.get("notes", "")
        confidence_emoji = "🟡" if confidence < 0.9 else "🟢"
        vision_block = f"\n\n🤖 Vision Agent: {confidence_emoji} {int(confidence * 100)}%"
        if amount:
            vision_block += f" | {amount} ₽"
        if status:
            vision_block += f" | {status}"
        if notes:
            vision_block += f"\n📝 {notes}"
        # Не превышаем 1024 символа
        if len(caption) + len(vision_block) <= 1024:
            caption += vision_block

    # Добавляем ссылку на фото-референс (если клиент присылал)
    ref_urls = context.user_data.get("reference_photo_urls", [])
    if ref_urls:
        ref_block = f"\n\n🖼 Референс: {ref_urls[0]}"
        if len(caption) + len(ref_block) <= 1024:
            caption += ref_block

    for admin_id in settings.admin_ids_list:
        try:
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=photo_file_id,
                caption=caption,
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить админа {admin_id}: {e}")

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Заказ отменён. Напишите /start чтобы начать снова."
    )
    return ConversationHandler.END


# ─── Команды для админа ──────────────────────────────────────────────────────

async def cmd_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает последние 10 заказов. Только для админа."""
    if update.effective_user.id not in settings.admin_ids_list:
        return

    orders = db.get_recent_orders(limit=10)
    if not orders:
        await update.message.reply_text("Заказов пока нет.")
        return

    STATUS_LABELS = {
        "awaiting_payment": "⏳ ждёт оплаты",
        "prompt_ready":     "🎨 промпт готов",
        "delivered":        "✅ доставлен",
        "cancelled":        "❌ отменён",
    }

    lines = ["Последние заказы:\n"]
    for o in orders:
        status = STATUS_LABELS.get(o["status"], o["status"])
        desc = o["description"] or ""
        short_desc = (desc[:60] + "...") if len(desc) > 60 else desc
        lines.append(
            f"#{o['id']} | {status}\n"
            f"  @{o['username']} — {short_desc}\n"
        )

    await update.message.reply_text("\n".join(lines))


# ─── Общая логика подтверждения оплаты (ручное и авто) ───────────────────────

async def _process_payment_confirmed(context, order_id: int, admin_ids: list[int]) -> None:
    """Генерирует промпт, пробует авто-генерацию и отправляет результат админам.
    Уведомление клиента — ответственность вызывателя."""
    order = db.get_order(order_id)
    prompt = await _generate_prompt(order["description"])
    db.update_prompt(order_id, prompt)
    db.update_status(order_id, "prompt_ready")
    db.save_message(order_id, "assistant", prompt)

    ref_bytes: bytes | None = None
    ref_url = order.get("reference_photo_url")
    if ref_url:
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.get(ref_url)
                resp.raise_for_status()
                ref_bytes = resp.content
        except Exception as ref_e:
            logger.warning(f"Не удалось скачать референс заказа #{order_id}: {ref_e}")

    image_bytes = await _generate_image(prompt, reference_bytes=ref_bytes)

    if image_bytes is not None:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Доставить клиенту", callback_data=f"autodeliver_{order_id}"),
            InlineKeyboardButton("📤 Заменить вручную", callback_data=f"deliver_{order_id}"),
        ]])
        caption = (
            f"🤖 NanaBananaPro сгенерировал изображение для заказа #{order_id}.\n\n"
            f"Промпт:\n{prompt[:950]}"
        )
        for admin_id in admin_ids:
            try:
                sent = await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=io.BytesIO(image_bytes),
                    caption=caption,
                    reply_markup=keyboard,
                )
                if order_id not in pending_auto_images:
                    pending_auto_images[order_id] = sent.photo[-1].file_id
            except Exception as e:
                logger.error(f"Не удалось отправить авто-изображение админу {admin_id}: {e}")
    else:
        # Fallback — прежний ручной флоу
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("📤 Доставить клиенту", callback_data=f"deliver_{order_id}"),
        ]])
        for admin_id in admin_ids:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"🎨 Промпт для заказа #{order_id}:\n\n"
                        f"{prompt}\n\n"
                        f"Сгенерируй изображение и нажми кнопку ниже."
                    ),
                    reply_markup=keyboard,
                )
            except Exception as e:
                logger.error(f"Не удалось отправить промпт админу {admin_id}: {e}")


# ─── Кнопки для админа ───────────────────────────────────────────────────────

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("confirm_"):
        await _confirm_payment(query, context, int(data.split("_")[1]))
    elif data.startswith("reject_"):
        await _reject_payment(query, context, int(data.split("_")[1]))
    elif data.startswith("autodeliver_"):
        await _auto_deliver(query, context, int(data.split("_")[1]))
    elif data.startswith("deliver_"):
        await _start_delivery(query, context, int(data.split("_")[1]))


async def _confirm_payment(query, context, order_id: int) -> None:
    order = db.get_order(order_id)
    if not order:
        await query.edit_message_caption("❌ Заказ не найден", reply_markup=InlineKeyboardMarkup([]))
        return

    # Убираем кнопки сразу чтобы нельзя было нажать дважды
    await query.edit_message_caption(
        query.message.caption + "\n\n⏳ Оплата подтверждена! Генерирую промпт...",
        reply_markup=InlineKeyboardMarkup([]),
    )

    # Уведомляем клиента
    await context.bot.send_message(
        chat_id=order["user_id"],
        text="✅ Оплата подтверждена! Готовим ваш заказ — скоро пришлём результат.",
    )

    try:
        await _process_payment_confirmed(context, order_id, [query.from_user.id])
    except Exception as e:
        logger.error(f"Ошибка генерации промпта: {e}")
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=f"❌ Не удалось сгенерировать промпт для заказа #{order_id}: {e}",
        )


async def _reject_payment(query, context, order_id: int) -> None:
    order = db.get_order(order_id)
    if not order:
        await query.edit_message_caption("❌ Заказ не найден", reply_markup=InlineKeyboardMarkup([]))
        return

    db.update_status(order_id, "cancelled")
    await query.edit_message_caption(
        query.message.caption + "\n\n❌ Оплата отклонена",
        reply_markup=InlineKeyboardMarkup([]),
    )
    await context.bot.send_message(
        chat_id=order["user_id"],
        text=(
            "❌ К сожалению, не смогли подтвердить оплату.\n"
            "Пожалуйста, свяжитесь с нами или попробуйте ещё раз /start"
        ),
    )


async def _start_delivery(query, context, order_id: int) -> None:
    admin_id = query.from_user.id
    pending_auto_images.pop(order_id, None)  # очищаем авто-изображение если было
    pending_deliveries[admin_id] = order_id
    db.set_delivery_admin(order_id, admin_id)
    suffix = "\n\n📤 Отправь изображение следующим сообщением:"
    if query.message.photo:
        # Кнопка "Заменить вручную" была на фото-сообщении — редактируем caption
        await query.edit_message_caption(
            (query.message.caption or "") + suffix,
            reply_markup=InlineKeyboardMarkup([]),
        )
    else:
        await query.edit_message_text(
            (query.message.text or "") + suffix,
            reply_markup=InlineKeyboardMarkup([]),
        )


async def _auto_deliver(query, context, order_id: int) -> None:
    """Доставляет авто-сгенерированное изображение клиенту."""
    order = db.get_order(order_id)
    if not order:
        await query.edit_message_caption("❌ Заказ не найден", reply_markup=InlineKeyboardMarkup([]))
        return

    file_id = pending_auto_images.pop(order_id, None)
    if not file_id:
        # После рестарта бота — fallback на ручной флоу
        suffix = "\n\n⚠️ Изображение не найдено в памяти. Доставь вручную."
        base = (query.message.caption or "")
        if len(base) + len(suffix) > 1024:
            base = base[:1024 - len(suffix)]
        await query.edit_message_caption(
            base + suffix,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📤 Доставить вручную", callback_data=f"deliver_{order_id}"),
            ]]),
        )
        return

    try:
        await context.bot.send_photo(
            chat_id=order["user_id"],
            photo=file_id,
            caption="🎉 Ваш заказ готов! Спасибо, что выбрали нас 😊",
        )
        db.update_status(order_id, "delivered")
        db.clear_delivery_admin(order_id)
        suffix = f"\n\n✅ Заказ #{order_id} доставлен клиенту (авто)."
        base = (query.message.caption or "")
        if len(base) + len(suffix) > 1024:
            base = base[:1024 - len(suffix)]
        await query.edit_message_caption(base + suffix, reply_markup=InlineKeyboardMarkup([]))
    except Exception as e:
        logger.error(f"Ошибка авто-доставки заказа #{order_id}: {e}")
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📤 Доставить вручную", callback_data=f"deliver_{order_id}"),
            ]]))
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=f"❌ Ошибка авто-доставки заказа #{order_id}: {e}",
        )


# ─── Доставка: админ присылает фото ──────────────────────────────────────────

async def handle_admin_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_id = update.message.from_user.id

    if admin_id not in pending_deliveries:
        return  # Фото не для доставки — игнорируем

    order_id = pending_deliveries.pop(admin_id)
    order = db.get_order(order_id)
    if not order:
        await update.message.reply_text("❌ Заказ не найден")
        return

    photo_file_id = update.message.photo[-1].file_id
    await context.bot.send_photo(
        chat_id=order["user_id"],
        photo=photo_file_id,
        caption="🎉 Ваш заказ готов! Спасибо, что выбрали нас 😊",
    )
    db.update_status(order_id, "delivered")
    db.clear_delivery_admin(order_id)
    await update.message.reply_text(f"✅ Заказ #{order_id} доставлен клиенту!")


# ─── Claude: Vision Agent — проверка скриншота оплаты ────────────────────────

async def _verify_payment(
    image_bytes: bytes,
    expected_amount: int,
    payment_recipient: str,
    payment_card: str,
    payment_phone: str,
) -> dict | None:
    """Анализирует скриншот оплаты через Claude Vision.
    Проверяет сумму, статус и реквизиты получателя.
    Возвращает dict с результатом или None при любой ошибке."""
    client = _get_anthropic_client()
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    system_prompt = get_agent_prompt(
        "vision",
        expected_amount=expected_amount,
        payment_recipient=payment_recipient,
        payment_card=payment_card,
        payment_phone=payment_phone,
    )
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64},
                },
                {"type": "text", "text": "Проанализируй скриншот и верни JSON."},
            ],
        }],
    )
    text = response.content[0].text.strip()
    match = re.search(r'\{[\s\S]+\}', text)
    if not match:
        logger.warning(f"Vision Agent не вернул JSON. Ответ модели: {text[:300]!r}")
        return None
    return json.loads(match.group(0))


# ─── Claude: генерация промпта ────────────────────────────────────────────────

async def _generate_prompt(description: str) -> str:
    client = _get_anthropic_client()
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": (
                "You are an expert at writing short, focused prompts for Nano Banana Pro (Gemini 3 Pro Image by Google DeepMind).\n\n"
                "RULES:\n"
                "1. Write maximum 2-3 short sentences total\n"
                "2. Describe ONLY what the client wants to change — materials, colors, specific elements\n"
                "3. If the immediate yard/plot looks unfinished (mud, bare ground, construction debris) — add one short phrase with minimal neat landscaping that matches the building style (e.g. 'neat lawn and simple path'). Skip this if the surroundings already look finished\n"
                "4. Do NOT describe sky, distant background, trees, lighting, or neighbors\n"
                "5. End every prompt with: DO NOT change sky, background, distant surroundings, or any element not mentioned — keep identical to reference.\n\n"
                f"Client request: {description}\n\n"
                "Output only the prompt, nothing else."
            ),
        }],
    )
    return message.content[0].text


# ─── Gemini: генерация изображения (NanaBananaPro) ───────────────────────────

async def _generate_image(prompt: str, reference_bytes: bytes | None = None) -> bytes | None:
    """NanaBananaPro (gemini-3-pro-image-preview) → bytes изображения.
    None при любой ошибке или отсутствии ключа."""
    client = _get_gemini_client()
    if client is None:
        return None
    try:
        if reference_bytes is not None:
            contents = [
                genai_types.Part(
                    inline_data=genai_types.Blob(mime_type="image/jpeg", data=reference_bytes)
                ),
                genai_types.Part(text=prompt),
            ]
        else:
            contents = prompt
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-3-pro-image-preview",
            contents=contents,
            config=genai_types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=genai_types.ImageConfig(
                    image_size="2K",
                ),
            ),
        )
        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                return part.inline_data.data
        return None
    except Exception as e:
        logger.error(f"Gemini image generation ошибка: {e}")
        return None


# ─── Очистка старых фото-референсов ──────────────────────────────────────────

async def _cleanup_old_reference_photos(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Удаляет фото-референсы из Supabase Storage старше 7 дней."""
    try:
        old_orders = db.get_orders_with_old_reference_photos(days=7)
        if not old_orders:
            return
        for order in old_orders:
            url = order.get("reference_photo_url", "") or ""
            match = re.search(r'/reference-photos/([^?]+)', url)
            if not match:
                continue
            filename = match.group(1)
            try:
                db.delete_reference_photo_from_storage(filename)
                db.update_reference_photo(order["id"], None)
                logger.info(f"Удалён референс заказа #{order['id']}: {filename}")
            except Exception as e:
                logger.error(f"Ошибка удаления референса #{order['id']}: {e}")
    except Exception as e:
        logger.error(f"Ошибка плановой очистки референсов: {e}")


# ─── Инициализация при старте ─────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    """Устанавливает меню команд и восстанавливает состояние после рестарта."""
    await application.bot.set_my_commands([
        BotCommand("start",  "Заказать AI-изображение"),
        BotCommand("orders", "Последние заказы (для админа)"),
        BotCommand("cancel", "Отменить текущий заказ"),
    ])

    # Восстанавливаем pending_deliveries из БД после рестарта
    try:
        recovered = db.get_pending_deliveries()
        if recovered:
            pending_deliveries.update(recovered)
            logger.info(f"Восстановлено {len(recovered)} доставок из БД: {recovered}")
    except Exception as e:
        logger.error(f"Не удалось восстановить состояние доставок: {e}", exc_info=True)

    # Запускаем ежедневную очистку фото-референсов старше 7 дней
    if application.job_queue:
        application.job_queue.run_repeating(
            _cleanup_old_reference_photos,
            interval=86400,   # каждые 24 часа
            first=60,         # первый запуск через 60 секунд после старта
        )


# ─── Listener Agent: сообщения вне активного диалога ─────────────────────────

async def handle_unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отвечает на сообщения клиента вне ConversationHandler через Listener Agent."""
    text = update.message.text or ""
    result = await _call_listener(text)

    if result and result.get("confidence", 0) >= 0.8:
        msg_type = result.get("message_type", "OTHER")
    else:
        msg_type = "OTHER"

    await update.message.reply_text(_listener_response(msg_type))


# ─── Обработчик необработанных ошибок ────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Логирует необработанные исключения и уведомляет всех админов."""
    import telegram.error as tg_err
    if isinstance(context.error, tg_err.TimedOut):
        logger.warning(f"Сетевая ошибка (игнорируем): {context.error}")
        return
    logger.error("Необработанная ошибка:", exc_info=context.error)
    error_text = (
        f"⚠️ Критическая ошибка бота:\n"
        f"{type(context.error).__name__}: {context.error}"
    )
    for admin_id in settings.admin_ids_list:
        try:
            await context.bot.send_message(chat_id=admin_id, text=error_text)
        except Exception:
            pass  # Не падаем, если и уведомление не прошло


# ─── Сборка приложения ────────────────────────────────────────────────────────

def build_app() -> Application:
    persistence = PicklePersistence(filepath="bot_persistence.pkl")
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .persistence(persistence)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(30)
        .get_updates_connect_timeout(30)
        .get_updates_read_timeout(30)
        .post_init(post_init)
        .build()
    )

    # Диалог с клиентом
    conv = ConversationHandler(
        name="main",
        persistent=True,
        entry_points=[CommandHandler("start", start)],
        states={
            CHAT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, manager_chat),
                MessageHandler(filters.PHOTO, manager_chat_photo),
            ],
            PAYMENT: [
                MessageHandler(filters.PHOTO, get_payment),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_payment),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)

    # Команды для админа
    app.add_handler(CommandHandler("orders", cmd_orders))

    # Кнопки для админа
    app.add_handler(CallbackQueryHandler(button_callback))

    # Фото от админа (доставка)
    app.add_handler(
        MessageHandler(
            filters.PHOTO & filters.User(user_id=settings.admin_ids_list),
            handle_admin_photo,
        )
    )

    # Listener Agent: catch-all для сообщений вне активного диалога
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unknown_message))

    app.add_error_handler(error_handler)

    return app
