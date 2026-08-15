"""Chat handler for conversations with Ollama models."""
import asyncio
import base64
import time
from html import escape
from io import BytesIO
from typing import Any

import structlog
from sqlalchemy import select
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.database import Conversation, ModelUsage, User, get_session
from bot.decorators import authorized_only, rate_limited
from bot.utils.context import ConversationContext, normalize_chat_messages
from bot.utils.ollama import OllamaClient, OllamaModelNotFoundError
from bot.utils.time import utc_now

logger = structlog.get_logger()

IMAGE_PLACEHOLDER_PREFIX = "[image]"

# Telegram clears the typing indicator after about five seconds.
TYPING_REFRESH_INTERVAL = 4.0

# A system prompt competes with the conversation for the context window.
MAX_SYSTEM_PROMPT_CHARS = 2000

DEFAULT_SYSTEM_PROMPT = (
    "Отвечай на том же языке, что и последнее сообщение пользователя."
)

# Updates are handled concurrently, so one user can fire off several messages
# at once. Two generations for the same user would interleave their writes and
# scramble the stored conversation order, so only one runs at a time. The
# running task is kept so /stop can cancel it.
_generating: dict[int, asyncio.Task] = {}

# Telegram rejects text messages longer than 4096 characters.
TELEGRAM_MAX_MESSAGE_LENGTH = 4096


def split_message(text: str, limit: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> list[str]:
    """Split text into Telegram-sized chunks, preferring line then word boundaries."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        # Prefer a newline, then a space, else hard-cut mid-token.
        split_at = window.rfind("\n")
        if split_at <= 0:
            split_at = window.rfind(" ")
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


async def _deliver_response(update: Update, bot_message, text: str) -> None:
    """Send the final response, splitting it when it exceeds Telegram's limit."""
    chunks = split_message(text)

    if bot_message is not None:
        try:
            await bot_message.edit_text(chunks[0])
        except Exception as err:
            # Never swallow this silently: it is how long answers used to vanish.
            logger.warning("Failed to edit streamed message", error=str(err))
            await update.message.reply_text(chunks[0])
    else:
        await update.message.reply_text(chunks[0])

    for chunk in chunks[1:]:
        await update.message.reply_text(chunk)


async def _keep_typing(context, chat_id: int, action) -> None:
    """Re-send the chat action until cancelled."""
    try:
        while True:
            await asyncio.sleep(TYPING_REFRESH_INTERVAL)
            await context.bot.send_chat_action(chat_id=chat_id, action=action)
    except asyncio.CancelledError:
        pass
    except Exception as err:
        # Losing the indicator is cosmetic; never let it break the answer.
        logger.debug("Could not refresh typing indicator", error=str(err))


def _mark_past_images(message: dict) -> dict:
    """Make it explicit that an image from an earlier turn is not attached.

    History stores images as a text placeholder, so without this the model sees
    a caption referring to a picture it was never given.
    """
    content = message["content"]
    if message["role"] != "user" or not content.startswith(IMAGE_PLACEHOLDER_PREFIX):
        return message

    caption = content[len(IMAGE_PLACEHOLDER_PREFIX):].strip()
    return {
        "role": message["role"],
        "content": (
            f"[ранее пользователь присылал изображение; оно недоступно в этом "
            f"запросе] {caption}"
        ),
    }


async def _process_chat_interaction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    stored_user_message: str,
    payload_content: Any,
    typing_action: ChatAction = ChatAction.TYPING,
    requires_image_support: bool = False,
    payload_images: list[str] | None = None,
) -> None:
    """Shared logic for sending a chat request to Ollama."""
    user_id = update.effective_user.id
    user_text = stored_user_message.strip()

    if not user_text:
        await update.message.reply_text("❌ Cannot send an empty message.")
        return

    if user_id in _generating:
        await update.message.reply_text(
            "⏳ Я ещё отвечаю на предыдущее сообщение. Дождитесь ответа."
        )
        return

    # Get user's selected model
    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            await update.message.reply_text(
                "❌ You are not authorized. Please contact the administrator."
            )
            return

        model_name = user.selected_model or context.application.bot_data["settings"].DEFAULT_MODEL
        system_prompt = user.system_prompt or DEFAULT_SYSTEM_PROMPT

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=typing_action
    )
    ollama_client: OllamaClient = context.application.bot_data["ollama_client"]

    if requires_image_support:
        supports_vision = await ollama_client.supports_images(model_name)
        if not supports_vision:
            await update.message.reply_text(
                "⚠️ Текущая модель не умеет работать с изображениями.\n"
                "Используйте /models и выберите модель с поддержкой vision (например, llava)."
            )
            return

    _generating[user_id] = asyncio.current_task()
    # The typing indicator expires after a few seconds; refresh it for as long
    # as the model keeps working.
    typing = asyncio.create_task(
        _keep_typing(context, update.effective_chat.id, typing_action)
    )
    try:
        # Get conversation context
        conv_context = ConversationContext(
            user_id,
            model_name,
            context.application.bot_data["settings"].MAX_CONTEXT_TOKENS,
        )
        messages = await conv_context.get_context()
        messages = normalize_chat_messages(messages)
        messages = [_mark_past_images(m) for m in messages]
        messages.insert(0, {"role": "system", "content": system_prompt})

        user_message = {"role": "user", "content": payload_content}
        if payload_images:
            user_message["images"] = payload_images
        messages.append(user_message)
        await conv_context.add_message("user", stored_user_message)


        # Save user message to database
        async with get_session() as session:
            user_msg = Conversation(
                user_id=user_id,
                model_name=model_name,
                message_role="user",
                message_content=stored_user_message,
            )
            session.add(user_msg)

        # Generate response
        start_time = time.time()

        # Stream response for better UX
        response_text = ""
        message_sent = False
        bot_message = None
        last_edit_len = 0

        async for chunk in ollama_client.chat_stream(model_name, messages):
            if chunk.get("message", {}).get("content"):
                response_text += chunk["message"]["content"]

                # Send or update message every few chunks for smoother streaming
                if len(response_text) > 50 and not message_sent:
                    bot_message = await update.message.reply_text(response_text + "...")
                    message_sent = True
                    last_edit_len = len(response_text)
                elif (
                    bot_message
                    and len(response_text) - last_edit_len >= 100
                    # Past the limit the edit can only fail; the final send splits it.
                    and len(response_text) + 3 <= TELEGRAM_MAX_MESSAGE_LENGTH
                ):
                    last_edit_len = len(response_text)
                    try:
                        await bot_message.edit_text(response_text + "...")
                    except Exception:
                        pass

            if chunk.get("done"):
                response_time_ms = chunk.get("response_time_ms", int((time.time() - start_time) * 1000))
                # Ollama reports token counts in the final chunk.
                prompt_tokens = chunk.get("prompt_eval_count") or 0
                completion_tokens = chunk.get("eval_count") or 0
                total_tokens = prompt_tokens + completion_tokens

                if not response_text.strip():
                    await update.message.reply_text(
                        "❌ Модель вернула пустой ответ. Попробуйте ещё раз."
                    )
                    return

                await _deliver_response(update, bot_message, response_text)

                # Save assistant response and usage stats
                async with get_session() as session:
                    # Save conversation
                    assistant_msg = Conversation(
                        user_id=user_id,
                        model_name=model_name,
                        message_role="assistant",
                        message_content=response_text,
                        response_time_ms=response_time_ms,
                        tokens_used=total_tokens or None,
                    )
                    session.add(assistant_msg)

                    # Update usage stats
                    today = utc_now().date()

                    result = await session.execute(
                        select(ModelUsage).where(
                            (ModelUsage.user_id == user_id) &
                            (ModelUsage.model_name == model_name) &
                            (ModelUsage.date == today)
                        )
                    )
                    usage = result.scalar_one_or_none()

                    if usage:
                        usage.request_count += 1
                        usage.total_response_time_ms += response_time_ms
                        usage.total_tokens += total_tokens
                    else:
                        usage = ModelUsage(
                            user_id=user_id,
                            model_name=model_name,
                            request_count=1,
                            total_response_time_ms=response_time_ms,
                            total_tokens=total_tokens,
                            date=today,
                        )
                        session.add(usage)


                # Update context
                await conv_context.add_message("assistant", response_text)

                logger.info(
                    "Chat response generated",
                    user_id=user_id,
                    model=model_name,
                    response_time_ms=response_time_ms,
                    response_length=len(response_text),
                )

    except OllamaModelNotFoundError:
        await update.message.reply_text(
            f"❌ Model '{model_name}' not found.\n"
            "Please use /models to select an available model."
        )
    except asyncio.CancelledError:
        # /stop cancelled us. Awaiting anything here would just be cancelled
        # again, so the confirmation is sent by the /stop handler itself.
        logger.info("Generation cancelled by user", user_id=user_id)
        raise
    except TimeoutError:
        await update.message.reply_text(
            "⏱️ Request timed out. Please try again with a shorter message or different model."
        )
    except Exception:
        logger.exception("Chat error", user_id=user_id, model=model_name)
        await update.message.reply_text(
            "❌ An error occurred while generating the response.\n"
            "Please try again later or contact the administrator."
        )
    finally:
        typing.cancel()
        _generating.pop(user_id, None)

@authorized_only
@rate_limited
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle regular chat messages."""
    message_text = update.message.text or ""
    await _process_chat_interaction(
        update,
        context,
        stored_user_message=message_text,
        payload_content=message_text,
    )


@authorized_only
@rate_limited
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photo messages by forwarding them to a vision-capable model."""
    if not update.message:
        return
    if not update.message.photo:
        await update.message.reply_text("❌ Не удалось найти изображение в сообщении.")
        return

    photo = update.message.photo[-1]

    try:
        telegram_file = await context.bot.get_file(photo.file_id)
        buffer = BytesIO()
        await telegram_file.download_to_memory(out=buffer)
        image_bytes = buffer.getvalue()
    except Exception as err:
        logger.error("Failed to download photo", error=str(err))
        await update.message.reply_text("❌ Не удалось загрузить изображение. Попробуйте ещё раз.")
        return

    caption = update.message.caption.strip() if update.message.caption else "Опиши это изображение."

    await _process_chat_interaction(
        update,
        context,
        stored_user_message=f"{IMAGE_PLACEHOLDER_PREFIX} {caption}",
        payload_content=caption,
        payload_images=[base64.b64encode(image_bytes).decode("utf-8")],
        typing_action=ChatAction.UPLOAD_PHOTO,
        requires_image_support=True,
    )


@authorized_only
async def stop_generation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel the caller's running generation, if any."""
    user_id = update.effective_user.id
    task = _generating.get(user_id)

    if task is None or task.done():
        await update.message.reply_text("Сейчас нечего останавливать.")
        return

    task.cancel()
    await update.message.reply_text("🛑 Генерация остановлена.")


@authorized_only
async def system_prompt_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show, set or reset the caller's system prompt."""
    user_id = update.effective_user.id
    argument = " ".join(context.args).strip() if context.args else ""

    async with get_session() as session:
        user = await session.scalar(select(User).where(User.user_id == user_id))
        if not user:
            await update.message.reply_text("❌ Пользователь не найден.")
            return

        if not argument:
            current = user.system_prompt
            if current:
                await update.message.reply_text(
                    "🧭 <b>Ваш системный промпт:</b>\n\n"
                    f"<code>{escape(current)}</code>\n\n"
                    "Изменить: /system текст\n"
                    "Вернуть стандартный: /system reset",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(
                    "🧭 Используется стандартный системный промпт:\n\n"
                    f"<code>{escape(DEFAULT_SYSTEM_PROMPT)}</code>\n\n"
                    "Задать свой: /system текст",
                    parse_mode="HTML",
                )
            return

        if argument.lower() in ("reset", "сброс", "default"):
            user.system_prompt = None
            await update.message.reply_text("🧭 Восстановлен стандартный системный промпт.")
            logger.info("System prompt reset", user_id=user_id)
            return

        if len(argument) > MAX_SYSTEM_PROMPT_CHARS:
            await update.message.reply_text(
                f"❌ Слишком длинный промпт: {len(argument)} символов, "
                f"максимум {MAX_SYSTEM_PROMPT_CHARS}."
            )
            return

        user.system_prompt = argument

    await update.message.reply_text(
        f"🧭 Системный промпт обновлён:\n\n<code>{escape(argument)}</code>",
        parse_mode="HTML",
    )
    logger.info("System prompt updated", user_id=user_id, length=len(argument))


@authorized_only
async def clear_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear conversation context for the user."""
    user_id = update.effective_user.id

    # Clear from context manager
    conv_context = ConversationContext(user_id, "", 0)
    await conv_context.clear()

    # Delete the user's conversation rows. Clearing only the in-memory cache is
    # not enough: get_context() reloads the same history straight back from the
    # database, so the next message would still carry the old conversation.
    async with get_session() as session:
        from sqlalchemy import delete
        await session.execute(
            delete(Conversation).where(Conversation.user_id == user_id)
        )

    await update.message.reply_text(
        "🧹 Conversation context cleared!\n"
        "You can start a fresh conversation now."
    )

    logger.info("User cleared context", user_id=user_id)


@authorized_only
@rate_limited
async def regenerate_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Regenerate the last assistant response."""
    user_id = update.effective_user.id

    # Get last user message
    async with get_session() as session:
        result = await session.execute(
            select(Conversation)
            .where(
                (Conversation.user_id == user_id) &
                (Conversation.message_role == "user")
            )
            .order_by(Conversation.id.desc())
            .limit(1)
        )
        last_user_msg = result.scalar_one_or_none()

        if not last_user_msg:
            await update.message.reply_text(
                "❌ No previous message found to regenerate."
            )
            return

        # Проверка на пустое сообщение
        if not last_user_msg.message_content or not last_user_msg.message_content.strip():
            await update.message.reply_text(
                "❌ Last message was empty, cannot regenerate."
            )
            return

        if last_user_msg.message_content.lower().startswith(IMAGE_PLACEHOLDER_PREFIX):
            await update.message.reply_text(
                "♻️ Нельзя повторно сгенерировать ответ для сообщения с изображением."
            )
            return

        # Delete last assistant response if exists
        result = await session.execute(
            select(Conversation)
            .where(
                (Conversation.user_id == user_id) &
                (Conversation.message_role == "assistant") &
                (Conversation.id > last_user_msg.id)
            )
            .order_by(Conversation.id.desc())
            .limit(1)
        )
        last_assistant_msg = result.scalar_one_or_none()

        if last_assistant_msg:
            await session.delete(last_assistant_msg)

    await update.message.reply_text("🔄 Regenerating response...")

    # telegram.Message is immutable in PTB v20+, so replay the stored text directly
    await _process_chat_interaction(
        update,
        context,
        stored_user_message=last_user_msg.message_content,
        payload_content=last_user_msg.message_content,
    )


@authorized_only
async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent conversation history."""
    user_id = update.effective_user.id

    async with get_session() as session:
        result = await session.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.id.desc())
            .limit(10)
        )
        messages = result.scalars().all()

    if not messages:
        await update.message.reply_text("No conversation history found.")
        return

    # Reverse to show chronological order
    messages = list(reversed(messages))

    history = "📜 <b>Recent Conversation History:</b>\n\n"
    for msg in messages:
        role_emoji = "👤" if msg.message_role == "user" else "🤖"
        # Truncate long messages
        content = msg.message_content[:200] + "..." if len(msg.message_content) > 200 else msg.message_content
        time_str = msg.created_at.strftime("%H:%M")

        # Message text is arbitrary user/model output: a stray "<" would make
        # Telegram reject the whole reply as broken HTML.
        history += f"{role_emoji} <b>{escape(msg.message_role.title())} ({time_str}):</b>\n{escape(content)}\n\n"

    await update.message.reply_text(history, parse_mode="HTML")
