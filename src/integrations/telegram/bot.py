"""
Telegram бот для приёма заказов на AI-изображения.

Флоу клиента:
  /start → описание → стиль → скрин оплаты → ожидание

Флоу админа (ты):
  Получаешь уведомление с кнопками → подтверждаешь оплату →
  получаешь промпт от Claude → генеришь картинку →
  жмёшь "Доставить" → отправляешь фото → клиент получает результат
"""

import logging
import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
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

# ─── Состояния диалога с клиентом ────────────────────────────────────────────
DESCRIPTION, STYLE, PAYMENT = range(3)

# ─── Отслеживаем какой заказ доставляем {admin_id: order_id} ─────────────────
pending_deliveries: dict[int, int] = {}


# ─── Клиентский флоу ─────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Привет! 👋 Я помогу вам заказать AI-изображение.\n\n"
        "Напишите что хотите получить — как можно подробнее:"
    )
    return DESCRIPTION


async def get_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["description"] = update.message.text
    await update.message.reply_text(
        "Понял! Теперь уточните стиль.\n\n"
        "Например: реализм, аниме, акварель, 3D, портрет, пейзаж, абстракция..."
    )
    return STYLE


async def get_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    description = context.user_data["description"]
    style = update.message.text
    context.user_data["full_description"] = f"{description}\nСтиль: {style}"

    price = settings.base_price_image
    await update.message.reply_text(
        f"Стоимость: {price} ₽\n\n"
        f"Реквизиты для оплаты:\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{settings.payment_card}\n"
        f"Получатель: {settings.payment_recipient}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"После оплаты пришлите скриншот чека:"
    )
    return PAYMENT


async def get_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text(
            "Пожалуйста, пришлите скриншот оплаты как фото."
        )
        return PAYMENT

    user = update.message.from_user
    full_description = context.user_data.get("full_description", "")

    try:
        order = db.create_order(
            user_id=user.id,
            username=user.username or user.first_name or str(user.id),
            description=full_description,
        )
        db.save_message(order["id"], "user", full_description)
    except Exception as e:
        logger.error(f"Ошибка создания заказа в БД: {e}")
        await update.message.reply_text(
            "⚠️ Технический сбой — попробуйте отправить скриншот ещё раз.\n"
            "Если проблема повторяется, напишите нам напрямую."
        )
        return PAYMENT

    await update.message.reply_text(
        "✅ Скриншот получен!\n"
        "Проверяем оплату — обычно занимает до 30 минут.\n"
        "Как подтвердим, сразу напишем."
    )

    # Уведомляем всех админов
    photo_file_id = update.message.photo[-1].file_id
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_{order['id']}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{order['id']}"),
    ]])
    # Telegram ограничение: caption не более 1024 символов
    short_desc = (full_description[:400] + "...") if len(full_description) > 400 else full_description
    caption = (
        f"💰 Новая оплата!\n\n"
        f"Заказ #{order['id']}\n"
        f"Клиент: @{user.username or user.first_name} (ID: {user.id})\n\n"
        f"Описание:\n{short_desc}\n\n"
        f"Сумма: {settings.base_price_image} ₽"
    )
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


# ─── Кнопки для админа ───────────────────────────────────────────────────────

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("confirm_"):
        await _confirm_payment(query, context, int(data.split("_")[1]))
    elif data.startswith("reject_"):
        await _reject_payment(query, context, int(data.split("_")[1]))
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

    # Генерируем промпт через Claude
    try:
        prompt = await _generate_prompt(order["description"])
        db.update_prompt(order_id, prompt)
        db.update_status(order_id, "prompt_ready")
        db.save_message(order_id, "assistant", prompt)
    except Exception as e:
        logger.error(f"Ошибка генерации промпта: {e}")
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=f"❌ Не удалось сгенерировать промпт для заказа #{order_id}: {e}",
        )
        return

    # Отправляем промпт админу с кнопкой доставки
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📤 Доставить клиенту", callback_data=f"deliver_{order_id}"),
    ]])
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=(
            f"🎨 Промпт для заказа #{order_id}:\n\n"
            f"{prompt}\n\n"
            f"Сгенерируй изображение и нажми кнопку ниже."
        ),
        reply_markup=keyboard,
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
    pending_deliveries[admin_id] = order_id
    db.set_delivery_admin(order_id, admin_id)
    # Убираем кнопку и просим прислать фото
    await query.edit_message_text(
        query.message.text + "\n\n📤 Отправь изображение следующим сообщением:",
        reply_markup=InlineKeyboardMarkup([]),
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


# ─── Claude: генерация промпта ────────────────────────────────────────────────

async def _generate_prompt(description: str) -> str:
    client = _get_anthropic_client()
    message = await client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=800,
        messages=[{
            "role": "user",
            "content": (
                "You are an expert at writing prompts for Nano Banana Pro (Gemini 3 Pro Image by Google DeepMind).\n\n"
                "Nano Banana Pro understands natural language — do NOT use keyword spam like '4K, masterpiece, trending on artstation'.\n"
                "Use this structure: [Subject] + [Pose/Action] + [Setting] + [Style] + [Technical details]\n"
                "Include: specific lighting (e.g. soft morning light, golden hour), camera details (85mm lens, f/2.8, shallow depth of field), "
                "realistic textures and imperfections, composition details.\n"
                "Write in flowing descriptive English, like a professional photography brief.\n\n"
                f"Client request: {description}\n\n"
                "Output only the prompt, no explanations."
            ),
        }],
    )
    return message.content[0].text


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


# ─── Обработчик необработанных ошибок ────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Логирует необработанные исключения и уведомляет всех админов."""
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
    app = Application.builder().token(settings.telegram_bot_token).post_init(post_init).build()

    # Диалог с клиентом
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_description)],
            STYLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_style)],
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

    app.add_error_handler(error_handler)

    return app
