"""Telegram bot for accepting AI image orders.

Client flow:
  /start → description → style → payment screenshot → waiting

Admin flow:
  Receives a notification with buttons → confirms payment → receives a Claude
  prompt → generates an image → selects Deliver → sends the image to the client.
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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, InputMediaPhoto
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

# ─── Claude client (singleton) ────────────────────────────────────────────────
_anthropic_client: anthropic.AsyncAnthropic | None = None

def _get_anthropic_client() -> anthropic.AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            max_retries=4,
        )
    return _anthropic_client

# ─── Gemini client (singleton) ────────────────────────────────────────────────
_gemini_client: genai.Client | None = None

def _get_gemini_client() -> genai.Client | None:
    """Return None when no API key is configured and auto-generation is disabled."""
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

# ─── Client conversation states ───────────────────────────────────────────────
CHAT, PAYMENT = range(2)

# ─── Track the order being delivered: {admin_id: order_id} ────────────────────
pending_deliveries: dict[int, int] = {}

# ─── Auto-generated photo file_id: {order_id: telegram_file_id} ──────────────
pending_auto_images: dict[int, str] = {}

# ─── Full-quality bytes for the "Download in 2K" button: {order_id: bytes} ───
pending_full_quality: dict[int, bytes] = {}

# ─── Client regeneration attempt count: {order_id: count} ─────────────────────
MAX_CLIENT_RETRIES = 3
_pipeline_retry_counts: dict[int, int] = {}


def _detect_image_media_type(image_bytes: bytes) -> str:
    """Determine an image MIME type from its leading-byte signature."""
    if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        return "image/png"
    if image_bytes[:3] == b'\xff\xd8\xff':
        return "image/jpeg"
    if image_bytes[:6] in (b'GIF87a', b'GIF89a'):
        return "image/gif"
    if image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
        return "image/webp"
    return "image/jpeg"


def _price_from_category(category: str) -> int:
    """Return the price for a category from the Manager signal."""
    if category == "exterior":
        return settings.price_exterior
    if category == "interior":
        return settings.price_interior
    return settings.base_price_image


# ─── Client flow ──────────────────────────────────────────────────────────────

async def _call_manager(history: list[dict]) -> str:
    """Call the Manager Agent with the conversation history."""
    client = _get_anthropic_client()
    system_prompt = get_agent_prompt(
        "manager",
        base_price=settings.base_price_image,
        price_exterior=settings.price_exterior,
        price_interior=settings.price_interior,
    )
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        temperature=0.7,
        system=system_prompt,
        messages=history,
    )
    return response.content[0].text


async def _call_listener(text: str) -> dict | None:
    """Classify an incoming message with the Listener Agent (Haiku).

    Return a dictionary with message_type and confidence, or None on error.
    """
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
        logger.error(f"Listener Agent error: {e}")
        return None


def _listener_response(message_type: str) -> str:
    """Return a response based on the Listener Agent classification."""
    if message_type == "NEW_ORDER":
        return (
            "To place an order, select /start — "
            "I will ask a few questions and calculate the price."
        )
    if message_type == "PAYMENT":
        return (
            "Payment is made while placing an order. "
            "Start with /start."
        )
    if message_type == "QUESTION":
        return (
            "We create AI visualizations for properties:\n\n"
            f"• Exterior / facade — from {settings.price_exterior} RUB\n"
            f"• Interior / room — from {settings.price_interior} RUB\n"
            f"• Other images — from {settings.base_price_image} RUB\n\n"
            "Select /start to place an order."
        )
    if message_type == "FEEDBACK":
        return "Thank you for the feedback! To place an order, select /start."
    if message_type == "CANCEL":
        return "There are no active orders. To create one, select /start."
    return (
        "Hello! I am a bot for ordering AI visualizations. "
        "To begin, select /start."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    context.user_data["history"] = []
    await update.message.reply_text(
        "🏠 Welcome! I will help bring your ideas to life with AI visualization.\n\n"
        "I work with:\n"
        "• 🏢 Exteriors and facades\n"
        "• 🛋️ Interiors and rooms\n"
        "• 🌿 Landscapes and plots\n"
        "• 🎨 Any other ideas\n\n"
        "How it works:\n"
        "1️⃣ Tell me what you want to change\n"
        "2️⃣ Send a photo of your property\n"
        "3️⃣ Receive the finished AI visualization\n\n"
        "👇 Describe your request. The more precise the description, the better the result!\n\n"
        "✅ Great: Make the facade white plaster and the roof dark asphalt shingles. Add a green lawn, shrubs, and a sunny sky around the house.\n"
        "❌ Not enough: Just make it realistic."
    )
    return CHAT


async def _handle_manager_response(
    response_text: str,
    history: list[dict],
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Handle a Manager response: JSON signal → PAYMENT; text → CHAT."""
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
                    f"Great! Order price: {price} RUB\n\n"
                    f"Payment details:\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"{settings.payment_card}\n"
                    f"Recipient: {settings.payment_recipient}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"After payment, send a screenshot of the receipt:"
                )
                return PAYMENT
    except (json.JSONDecodeError, KeyError, TypeError):
        pass  # Not a JSON signal: send it as regular text.

    history.append({"role": "assistant", "content": response_text})
    context.user_data["history"] = history
    await update.message.reply_text(response_text)
    return CHAT


async def manager_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Run a text conversation with the client through the Manager Agent."""
    user_text = update.message.text
    history = context.user_data.get("history", [])
    history.append({"role": "user", "content": user_text})

    try:
        task = asyncio.create_task(_call_manager(history))
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except asyncio.TimeoutError:
            await update.message.reply_text(
                "⏳ The bot is currently busy. Your response is being prepared; please wait a moment."
            )
        response_text = await task
    except Exception as e:
        logger.error(f"Manager Agent error: {e}")
        await update.message.reply_text("An error occurred. Please try again.")
        return CHAT

    return await _handle_manager_response(response_text, history, update, context)


async def manager_chat_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle a client's reference photo in the Manager Agent conversation."""
    caption = update.message.caption or ""
    history = context.user_data.get("history", [])

    try:
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
        else:
            file_id = update.message.document.file_id
        tg_file = await context.bot.get_file(file_id)
        photo_bytes = bytes(await tg_file.download_as_bytearray())
        image_b64 = base64.standard_b64encode(photo_bytes).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to download reference photo: {e}")
        await update.message.reply_text("Unable to upload the photo. Please try again.")
        return CHAT

    # Keep bytes in memory; upload to Storage after creating the order (requires order_id).
    context.user_data.setdefault("reference_photo_bytes", []).append(photo_bytes)
    logger.info(f"Reference photo #{len(context.user_data['reference_photo_bytes'])} stored in memory")

    # Use the image message only for the current API call.
    image_content: list = [
        {"type": "image", "source": {"type": "base64", "media_type": _detect_image_media_type(photo_bytes), "data": image_b64}},
        {"type": "text", "text": caption if caption else "Here is the reference photo."},
    ]
    call_history = history + [{"role": "user", "content": image_content}]

    try:
        task = asyncio.create_task(_call_manager(call_history))
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except asyncio.TimeoutError:
            await update.message.reply_text(
                "⏳ The bot is currently busy. Your response is being prepared; please wait a moment."
            )
        response_text = await task
    except Exception as e:
        logger.error(f"Manager Agent photo-processing error: {e}")
        await update.message.reply_text("An error occurred. Please try again.")
        return CHAT

    # Store a text placeholder rather than base64 in history to save tokens.
    placeholder = f"[📎 Reference photo]{': ' + caption if caption else ''}"
    history.append({"role": "user", "content": placeholder})

    return await _handle_manager_response(response_text, history, update, context)


async def get_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo and not update.message.document:
        await update.message.reply_text(
            "Please send the payment screenshot as a photo."
        )
        return PAYMENT

    user = update.message.from_user
    full_description = context.user_data.get("full_description", "")
    price = context.user_data.get("price", settings.base_price_image)
    photo_file_id = update.message.photo[-1].file_id if update.message.photo else update.message.document.file_id

    # ── Vision Agent: verify the screenshot before creating an order ──────────
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
        logger.info(f"Vision Agent result: {vision_result}")
    except Exception as e:
        logger.error(f"Vision Agent error (falling back to manual review): {e}")

    # If Vision is confident this is not a receipt, ask for a clearer screenshot.
    if (
        vision_result is not None
        and not vision_result.get("payment_confirmed", True)
        and vision_result.get("confidence", 1.0) < 0.7
    ):
        await update.message.reply_text(
            "⚠️ Unable to recognize a payment receipt in this image.\n"
            "Please send a clearer screenshot showing the amount and payment status."
        )
        return PAYMENT

    # ── Create the database order (retry after an SSL error) ──────────────────
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
            # Upload reference photos to Storage as {username}/{order_id}.jpg.
            ref_bytes_list = context.user_data.get("reference_photo_bytes", [])
            if ref_bytes_list:
                safe_username = re.sub(r"[^\w.-]", "_", user.username or user.first_name or str(user.id))
                for idx, ref_bytes in enumerate(ref_bytes_list, start=1):
                    try:
                        url = db.upload_reference_photo(ref_bytes, safe_username, order["id"], index=idx)
                        ref_urls.append(url)
                        logger.info(f"Reference #{idx} uploaded: {safe_username}/{order['id']}.jpg")
                    except Exception as ref_e:
                        logger.error(f"Failed to upload reference #{idx} for order #{order['id']}: {ref_e}")
                if ref_urls:
                    try:
                        db.update_reference_photo(order["id"], ref_urls[0])
                    except Exception as ref_e:
                        logger.error(f"Failed to save reference_photo_url for order #{order['id']}: {ref_e}")
            break  # Success.
        except Exception as e:
            if attempt == 0:
                logger.warning(f"db.create_order attempt 1 failed: {e}; recreating client")
                db.reset()
                await asyncio.sleep(1)
            else:
                logger.error(f"Database order-creation error: {e}")
                await update.message.reply_text(
                    "⚠️ A technical problem occurred. Please send the screenshot again.\n"
                    "If the issue persists, contact us directly."
                )
                return PAYMENT
    context.user_data["reference_photo_urls"] = ref_urls

    # ── Automatically confirm payment when Vision has high confidence ─────────
    if (
        vision_result is not None
        and vision_result.get("payment_confirmed", False)
        and vision_result.get("confidence", 0) > 0.9
    ):
        await update.message.reply_text(
            "✅ Payment was confirmed automatically!\n"
            "We are preparing your order and will send the result shortly."
        )
        try:
            await _process_payment_confirmed(context, order["id"], settings.admin_ids_list)
        except Exception as e:
            logger.error(f"Auto-confirmation error for order #{order['id']}: {e}")
            await _notify_pipeline_failure(context, order["id"])
        return ConversationHandler.END

    # ── Standard flow: notify administrators for manual review ────────────────
    await update.message.reply_text(
        "✅ Screenshot received!\n"
        "We are checking the payment; this usually takes up to 30 minutes.\n"
        "We will message you as soon as it is confirmed."
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_{order['id']}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"reject_{order['id']}"),
    ]])

    # Telegram limit: captions must not exceed 1,024 characters.
    short_desc = (full_description[:300] + "...") if len(full_description) > 300 else full_description
    caption = (
        f"💰 New payment!\n\n"
        f"Order #{order['id']}\n"
        f"Client: @{user.username or user.first_name} (ID: {user.id})\n\n"
        f"Description:\n{short_desc}\n\n"
        f"Amount: {price} RUB"
    )

    # Add Vision Agent notes when a result is available.
    if vision_result is not None:
        confidence = vision_result.get("confidence", 0)
        amount = vision_result.get("amount")
        status = vision_result.get("status", "")
        notes = vision_result.get("notes", "")
        confidence_emoji = "🟡" if confidence < 0.9 else "🟢"
        vision_block = f"\n\n🤖 Vision Agent: {confidence_emoji} {int(confidence * 100)}%"
        if amount:
            vision_block += f" | {amount} RUB"
        if status:
            vision_block += f" | {status}"
        if notes:
            vision_block += f"\n📝 {notes}"
        # Keep within the 1,024-character limit.
        if len(caption) + len(vision_block) <= 1024:
            caption += vision_block

    # Add a reference-photo link when the client supplied one.
    ref_urls = context.user_data.get("reference_photo_urls", [])
    if ref_urls:
        ref_block = f"\n\n🖼 Reference: {ref_urls[0]}"
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
            logger.error(f"Failed to notify administrator {admin_id}: {e}")

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Order cancelled. Select /start to begin again."
    )
    return ConversationHandler.END


# ─── Administrator commands ──────────────────────────────────────────────────

async def cmd_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the latest 10 orders. Administrators only."""
    if update.effective_user.id not in settings.admin_ids_list:
        return

    orders = db.get_recent_orders(limit=10)
    if not orders:
        await update.message.reply_text("There are no orders yet.")
        return

    STATUS_LABELS = {
        "awaiting_payment": "⏳ awaiting payment",
        "prompt_ready":     "🎨 prompt ready",
        "delivered":        "✅ delivered",
        "cancelled":        "❌ cancelled",
    }

    lines = ["Latest orders:\n"]
    for o in orders:
        status = STATUS_LABELS.get(o["status"], o["status"])
        desc = o["description"] or ""
        short_desc = (desc[:60] + "...") if len(desc) > 60 else desc
        lines.append(
            f"#{o['id']} | {status}\n"
            f"  @{o['username']} — {short_desc}\n"
        )

    await update.message.reply_text("\n".join(lines))


# ─── Shared payment-confirmation logic (manual and automatic) ────────────────

async def _send_batch_to_admin(
    context,
    admin_id: int,
    images: list[bytes],
    prompt: str,
    order_id: int,
) -> None:
    """Send generated images and control buttons to one administrator."""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Deliver to client", callback_data=f"deliver_{order_id}")],
        [InlineKeyboardButton("🔄 Generate again", callback_data=f"regen_{order_id}")],
        [InlineKeyboardButton("❌ Cancel order", callback_data=f"cancel_order_{order_id}")],
    ])

    if not images:
        # Fallback: no images, only the prompt.
        fallback_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("📤 Deliver to client", callback_data=f"deliver_{order_id}"),
        ]])
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    f"🎨 Prompt for order #{order_id}:\n\n"
                    f"{prompt}\n\n"
                    f"Generate the image and select the button below."
                ),
                reply_markup=fallback_keyboard,
            )
        except Exception as e:
            logger.error(f"Failed to send prompt to administrator {admin_id}: {e}")
        return

    if len(images) == 1:
        # One image: send_photo with buttons directly below it.
        caption = f"🤖 NanaBananaPro — order #{order_id}.\n\nPrompt:\n{prompt[:900]}"
        try:
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=io.BytesIO(images[0]),
                caption=caption,
                reply_markup=keyboard,
                write_timeout=120,
                read_timeout=120,
            )
        except Exception as e:
            logger.error(f"Failed to send image to administrator {admin_id}: {e}")
        return

    # Two or more images: MediaGroup plus a separate button message.
    media = [
        InputMediaPhoto(media=io.BytesIO(img_bytes), caption=f"Option {i + 1}")
        for i, img_bytes in enumerate(images)
    ]
    try:
        await context.bot.send_media_group(
            chat_id=admin_id,
            media=media,
            write_timeout=120,
            read_timeout=120,
        )
    except Exception as e:
        logger.error(f"Failed to send MediaGroup to administrator {admin_id}: {e}")
        return

    try:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                f"🤖 NanaBananaPro generated {len(images)} options for order #{order_id}.\n\n"
                f"Prompt:\n{prompt[:3000]}"
            ),
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"Failed to send control buttons to administrator {admin_id}: {e}")


async def _process_payment_confirmed(context, order_id: int, admin_ids: list[int]) -> None:
    """Generate a prompt, attempt auto-generation, and send the result to admins.

    The caller is responsible for notifying the client.
    """
    order = db.get_order(order_id)

    ref_bytes: bytes | None = None
    ref_url = order.get("reference_photo_url")
    if ref_url:
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.get(ref_url)
                resp.raise_for_status()
                ref_bytes = resp.content
        except Exception as ref_e:
            logger.warning(f"Failed to download reference for order #{order_id}: {ref_e}")

    try:
        prompt = await _call_engineer(order["description"], ref_bytes)
        db.update_prompt(order_id, prompt)
        db.update_status(order_id, "prompt_ready")
        db.save_message(order_id, "assistant", prompt)
    except Exception as eng_e:
        logger.warning(f"Engineer Agent failed (order #{order_id}); using client description as fallback: {eng_e}")
        prompt = order["description"]

    images = await _generate_image(prompt, reference_bytes=ref_bytes)
    for admin_id in admin_ids:
        await _send_batch_to_admin(context, admin_id, images, prompt, order_id)


async def _notify_pipeline_failure(context, order_id: int) -> None:
    """Notify the client about a generation error and escalate after retries."""
    order = db.get_order(order_id)
    if not order:
        return

    count = _pipeline_retry_counts.get(order_id, 0) + 1
    _pipeline_retry_counts[order_id] = count

    if count <= MAX_CLIENT_RETRIES:
        retry_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Try again", callback_data=f"client_retry_{order_id}"),
        ]])
        await context.bot.send_message(
            chat_id=order["user_id"],
            text=(
                "⏳ Generation is delayed because the servers are under heavy load.\n"
                f"Attempt {count} of {MAX_CLIENT_RETRIES} — select the button to retry."
            ),
            reply_markup=retry_keyboard,
        )
    else:
        # All attempts are exhausted: notify the client and escalate to an admin.
        _pipeline_retry_counts.pop(order_id, None)
        await context.bot.send_message(
            chat_id=order["user_id"],
            text=(
                "⚠️ Unfortunately, the image could not be generated because of a technical error.\n"
                "We are already investigating and will contact you personally soon."
            ),
        )
        description_short = (order.get("description") or "")[:300]
        prompt = order.get("prompt") or "not generated"
        ref_url = order.get("reference_photo_url") or "none"
        manual_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("📤 Deliver to client", callback_data=f"deliver_{order_id}"),
        ]])
        for admin_id in settings.admin_ids_list:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"⚠️ Order #{order_id}: generation failed and requires manual handling.\n\n"
                        f"Client: @{order.get('username', '?')} (ID: {order['user_id']})\n\n"
                        f"Description:\n{description_short}\n\n"
                        f"Prompt:\n{prompt[:500]}\n\n"
                        f"Reference: {ref_url}"
                    ),
                    reply_markup=manual_keyboard,
                )
            except Exception as e:
                logger.error(f"Failed to notify administrator {admin_id} about order #{order_id} escalation: {e}")


async def _client_retry_pipeline(query, context, order_id: int) -> None:
    """Handle a client's repeated request for image generation."""
    order = db.get_order(order_id)
    if not order or order["user_id"] != query.from_user.id:
        await query.answer("This button is unavailable.", show_alert=True)
        return

    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([]))
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="🔄 Retrying generation...",
    )
    try:
        await _process_payment_confirmed(context, order_id, settings.admin_ids_list)
        _pipeline_retry_counts.pop(order_id, None)  # Reset the count after success.
    except Exception as e:
        logger.error(f"Repeated generation error for order #{order_id}: {e}")
        await _notify_pipeline_failure(context, order_id)


# ─── Administrator buttons ───────────────────────────────────────────────────

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
    elif data.startswith("download_"):
        await _send_full_quality(query, context, int(data.split("_")[1]))
    elif data.startswith("regen_"):
        await _regenerate_image(query, context, int(data.split("_")[1]))
    elif data.startswith("cancel_order_"):
        await _cancel_order_admin(query, context, int(data.split("_", 2)[2]))
    elif data.startswith("client_retry_"):
        await _client_retry_pipeline(query, context, int(data.split("_", 2)[2]))


async def _confirm_payment(query, context, order_id: int) -> None:
    order = db.get_order(order_id)
    if not order:
        await query.edit_message_caption("❌ Order not found", reply_markup=InlineKeyboardMarkup([]))
        return

    # Remove buttons immediately to prevent double selection.
    await query.edit_message_caption(
        query.message.caption + "\n\n⏳ Payment confirmed! Generating prompt...",
        reply_markup=InlineKeyboardMarkup([]),
    )

    # Notify the client.
    await context.bot.send_message(
        chat_id=order["user_id"],
        text="✅ Payment confirmed! We are preparing your order and will send the result shortly.",
    )

    try:
        await _process_payment_confirmed(context, order_id, [query.from_user.id])
    except Exception as e:
        logger.error(f"Prompt-generation error: {e}")
        await _notify_pipeline_failure(context, order_id)


async def _reject_payment(query, context, order_id: int) -> None:
    order = db.get_order(order_id)
    if not order:
        await query.edit_message_caption("❌ Order not found", reply_markup=InlineKeyboardMarkup([]))
        return

    db.update_status(order_id, "cancelled")
    await query.edit_message_caption(
        query.message.caption + "\n\n❌ Payment rejected",
        reply_markup=InlineKeyboardMarkup([]),
    )
    await context.bot.send_message(
        chat_id=order["user_id"],
        text=(
            "❌ Unfortunately, we could not confirm the payment.\n"
            "Please contact us or try again with /start"
        ),
    )


async def _start_delivery(query, context, order_id: int) -> None:
    admin_id = query.from_user.id
    pending_auto_images.pop(order_id, None)  # Clear the auto-generated image if present.
    pending_deliveries[admin_id] = order_id
    db.set_delivery_admin(order_id, admin_id)
    suffix = "\n\n📤 Send the image in your next message:"
    if query.message.photo:
        # The manual-replacement button was on a photo message, so edit its caption.
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
    """Deliver an automatically generated image to the client."""
    order = db.get_order(order_id)
    if not order:
        await query.edit_message_caption("❌ Order not found", reply_markup=InlineKeyboardMarkup([]))
        return

    file_id = pending_auto_images.pop(order_id, None)
    full_quality_bytes = pending_full_quality.pop(order_id, None)
    if not file_id:
        # After a bot restart, fall back to manual delivery.
        suffix = "\n\n⚠️ Image is not available in memory. Deliver it manually."
        base = (query.message.caption or "")
        if len(base) + len(suffix) > 1024:
            base = base[:1024 - len(suffix)]
        await query.edit_message_caption(
            base + suffix,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📤 Deliver manually", callback_data=f"deliver_{order_id}"),
            ]]),
        )
        return

    try:
        description_short = (order.get("description") or "")[:200]
        if len(order.get("description") or "") > 200:
            description_short += "…"
        auto_delivery_caption = (
            f"🎨 Done! Your visualization is ready.\n\n"
            f"✏️ Request: {description_short}\n\n"
            f"Want a new order? Select /start 🚀"
        )
        client_keyboard = None
        if full_quality_bytes:
            pending_full_quality[order_id] = full_quality_bytes
            client_keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("📥 Download in full 2K", callback_data=f"download_{order_id}"),
            ]])
        await context.bot.send_photo(
            chat_id=order["user_id"],
            photo=file_id,
            caption=auto_delivery_caption,
            reply_markup=client_keyboard,
        )
        db.update_status(order_id, "delivered")
        db.clear_delivery_admin(order_id)
        suffix = f"\n\n✅ Order #{order_id} delivered to the client automatically."
        base = (query.message.caption or "")
        if len(base) + len(suffix) > 1024:
            base = base[:1024 - len(suffix)]
        await query.edit_message_caption(base + suffix, reply_markup=InlineKeyboardMarkup([]))
    except Exception as e:
        logger.error(f"Automatic-delivery error for order #{order_id}: {e}")
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📤 Deliver manually", callback_data=f"deliver_{order_id}"),
            ]]))
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=f"❌ Automatic-delivery error for order #{order_id}: {e}",
        )


async def _send_full_quality(query, context, order_id: int) -> None:
    """Send a full-quality image to the client after selecting the button."""
    data = pending_full_quality.pop(order_id, None)
    if data is None:
        await query.answer("The file is unavailable. Please contact an administrator.", show_alert=True)
        return
    await query.answer()
    try:
        if isinstance(data, bytes):
            await context.bot.send_document(
                chat_id=query.from_user.id,
                document=io.BytesIO(data),
                filename=f"order_{order_id}_2K.jpg",
                caption="📎 Your visualization in full 2K quality.",
                write_timeout=120,
                read_timeout=120,
            )
        else:
            # Document file_id (manual delivery).
            await context.bot.send_document(
                chat_id=query.from_user.id,
                document=data,
                caption="📎 Your visualization in full quality.",
            )
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([]))
    except Exception as e:
        logger.error(f"Full-quality delivery error for order #{order_id}: {e}")
        await query.answer("Unable to send the file. Please try again later.", show_alert=True)


async def _regenerate_image(query, context, order_id: int) -> None:
    """Regenerate three new options with the stored order prompt."""
    # Remove buttons immediately to prevent double selection.
    try:
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([]))
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="🔄 Generating new options...",
    )

    order = db.get_order(order_id)
    if not order:
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=f"❌ Order #{order_id} was not found.",
        )
        return

    prompt = order.get("prompt") or ""
    ref_bytes: bytes | None = None
    ref_url = order.get("reference_photo_url")
    if ref_url:
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.get(ref_url)
                resp.raise_for_status()
                ref_bytes = resp.content
        except Exception as e:
            logger.warning(f"Failed to download reference to regenerate order #{order_id}: {e}")

    images = await _generate_image(prompt, reference_bytes=ref_bytes)
    await _send_batch_to_admin(context, query.from_user.id, images, prompt, order_id)


async def _cancel_order_admin(query, context, order_id: int) -> None:
    """Cancel an order and notify the client."""
    order = db.get_order(order_id)
    if order and order.get("status") != "cancelled":
        db.update_status(order_id, "cancelled")
        db.clear_delivery_admin(order_id)
        pending_auto_images.pop(order_id, None)
        pending_full_quality.pop(order_id, None)
        try:
            await context.bot.send_message(
                chat_id=order["user_id"],
                text="❌ Unfortunately, your order was cancelled. Select /start to create a new order.",
            )
        except Exception as e:
            logger.error(f"Failed to notify client about cancellation of order #{order_id}: {e}")
    try:
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([]))
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=f"❌ Order #{order_id} cancelled.",
    )


# ─── Delivery: administrator sends an image ───────────────────────────────────

async def handle_admin_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_id = update.message.from_user.id

    if admin_id not in pending_deliveries:
        return  # This photo is not for delivery; ignore it.

    order_id = pending_deliveries.pop(admin_id)
    order = db.get_order(order_id)
    if not order:
        await update.message.reply_text("❌ Order not found")
        return

    is_document = update.message.document is not None
    photo_file_id = update.message.document.file_id if is_document else update.message.photo[-1].file_id
    description_short = (order.get("description") or "")[:200]
    if len(order.get("description") or "") > 200:
        description_short += "…"
    delivery_caption = (
        f"🎨 Done! Your visualization is ready.\n\n"
        f"✏️ Request: {description_short}\n\n"
        f"Want a new order? Select /start 🚀"
    )
    if is_document:
        await context.bot.send_document(
            chat_id=order["user_id"],
            document=photo_file_id,
            caption=delivery_caption,
        )
    else:
        await context.bot.send_photo(
            chat_id=order["user_id"],
            photo=photo_file_id,
            caption=delivery_caption,
        )
    db.update_status(order_id, "delivered")
    db.clear_delivery_admin(order_id)
    await update.message.reply_text(f"✅ Order #{order_id} delivered to the client!")


# ─── Claude: Vision Agent — payment-screenshot verification ──────────────────

async def _verify_payment(
    image_bytes: bytes,
    expected_amount: int,
    payment_recipient: str,
    payment_card: str,
    payment_phone: str,
) -> dict | None:
    """Analyze a payment screenshot with Claude Vision.

    Check the amount, status, and recipient details. Return a result dictionary
    or None if any error occurs.
    """
    client = _get_anthropic_client()
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    image_media_type = _detect_image_media_type(image_bytes)
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
                    "source": {"type": "base64", "media_type": image_media_type, "data": image_b64},
                },
                {"type": "text", "text": "Analyze the screenshot and return JSON."},
            ],
        }],
    )
    text = response.content[0].text.strip()
    match = re.search(r'\{[\s\S]+\}', text)
    if not match:
        logger.warning(f"Vision Agent did not return JSON. Model response: {text[:300]!r}")
        return None
    return json.loads(match.group(0))


# ─── Claude: prompt generation ────────────────────────────────────────────────

async def _call_engineer(description: str, reference_bytes: bytes | None = None) -> str:
    """Engineer Agent: generate a detailed prompt for Nano Banana Pro."""
    client = _get_anthropic_client()
    system_prompt = get_agent_prompt("engineer")

    if reference_bytes:
        image_b64 = base64.standard_b64encode(reference_bytes).decode("utf-8")
        user_content: list | str = [
            {"type": "image", "source": {"type": "base64", "media_type": _detect_image_media_type(reference_bytes), "data": image_b64}},
            {"type": "text", "text": f"Client request: {description}"},
        ]
    else:
        user_content = f"Client request: {description}"

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        temperature=0.5,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    return response.content[0].text.strip()


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


# ─── Gemini: image generation (NanaBananaPro) ─────────────────────────────────

async def _generate_image(prompt: str, reference_bytes: bytes | None = None, count: int = 3) -> list[bytes]:
    """Return image bytes from NanaBananaPro (gemini-3-pro-image-preview).

    Generate ``count`` options in parallel. Return an empty list on error or
    when no API key is configured.
    """
    client = _get_gemini_client()
    if client is None:
        return []

    async def _generate_one() -> bytes | None:
        def _sync_call() -> bytes | None:
            if reference_bytes is not None:
                contents = [
                    genai_types.Part(
                        inline_data=genai_types.Blob(mime_type=_detect_image_media_type(reference_bytes), data=reference_bytes)
                    ),
                    genai_types.Part(text=prompt),
                ]
            else:
                contents = prompt
            response = client.models.generate_content(
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

        try:
            return await asyncio.to_thread(_sync_call)
        except Exception as e:
            logger.error(f"Gemini image-generation error: {e}")
            return None

    results = await asyncio.gather(*[_generate_one() for _ in range(count)], return_exceptions=True)
    return [r for r in results if isinstance(r, bytes)]


# ─── Old reference-photo cleanup ──────────────────────────────────────────────

async def _cleanup_old_reference_photos(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete reference photos older than seven days from Supabase Storage."""
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
                logger.info(f"Deleted reference for order #{order['id']}: {filename}")
            except Exception as e:
                logger.error(f"Error deleting reference for order #{order['id']}: {e}")
    except Exception as e:
        logger.error(f"Scheduled reference cleanup error: {e}")


# ─── Startup initialization ───────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    """Set the command menu and restore state after a restart."""
    await application.bot.set_my_commands([
        BotCommand("start",  "Order an AI image"),
        BotCommand("orders", "Latest orders (admin only)"),
        BotCommand("cancel", "Cancel current order"),
    ])

    # Restore pending_deliveries from the database after a restart.
    try:
        recovered = db.get_pending_deliveries()
        if recovered:
            pending_deliveries.update(recovered)
            logger.info(f"Restored {len(recovered)} deliveries from the database: {recovered}")
    except Exception as e:
        logger.error(f"Failed to restore delivery state: {e}", exc_info=True)

    # Schedule daily cleanup for reference photos older than seven days.
    if application.job_queue:
        application.job_queue.run_repeating(
            _cleanup_old_reference_photos,
            interval=86400,   # Every 24 hours.
            first=60,         # First run 60 seconds after startup.
        )


# ─── Listener Agent: messages outside active conversations ────────────────────

async def handle_unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to client messages outside ConversationHandler via Listener Agent."""
    text = update.message.text or ""
    result = await _call_listener(text)

    if result and result.get("confidence", 0) >= 0.8:
        msg_type = result.get("message_type", "OTHER")
    else:
        msg_type = "OTHER"

    await update.message.reply_text(_listener_response(msg_type))


# ─── Unhandled-error handler ──────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log unhandled exceptions and notify all administrators."""
    import telegram.error as tg_err
    if isinstance(context.error, tg_err.TimedOut):
        logger.warning(f"Network error (ignored): {context.error}")
        return
    logger.error("Unhandled error:", exc_info=context.error)
    error_text = (
        f"⚠️ Critical bot error:\n"
        f"{type(context.error).__name__}: {context.error}"
    )
    for admin_id in settings.admin_ids_list:
        try:
            await context.bot.send_message(chat_id=admin_id, text=error_text)
        except Exception:
            pass  # Do not fail if this notification also fails.


# ─── Application construction ─────────────────────────────────────────────────

def build_app() -> Application:
    persistence = PicklePersistence(filepath="bot_persistence.pkl")
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .persistence(persistence)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(90)
        .get_updates_connect_timeout(30)
        .get_updates_read_timeout(30)
        .post_init(post_init)
        .build()
    )

    # Client conversation.
    conv = ConversationHandler(
        name="main",
        persistent=True,
        entry_points=[CommandHandler("start", start)],
        states={
            CHAT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, manager_chat),
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, manager_chat_photo),
            ],
            PAYMENT: [
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, get_payment),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_payment),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)

    # Administrator commands.
    app.add_handler(CommandHandler("orders", cmd_orders))

    # Administrator buttons.
    app.add_handler(CallbackQueryHandler(button_callback))

    # Image from administrator (delivery).
    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.Document.IMAGE) & filters.User(user_id=settings.admin_ids_list),
            handle_admin_photo,
        )
    )

    # Listener Agent: catch-all for messages outside active conversations.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unknown_message))

    app.add_error_handler(error_handler)

    return app
