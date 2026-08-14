"""Chat handler for conversations with Ollama models."""
import asyncio
import base64
import time
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

logger = structlog.get_logger()

IMAGE_PLACEHOLDER_PREFIX = "[image]"


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

    try:
        # Get conversation context
        conv_context = ConversationContext(
            user_id,
            model_name,
            context.application.bot_data["settings"].MAX_CONTEXT_LENGTH,
        )
        messages = await conv_context.get_context()
        messages = normalize_chat_messages(messages)
        messages.insert(0, {
            "role": "system",
            "content": (
                "Отвечай на том же языке, что и последнее сообщение пользователя."
            ),
        })

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
            await session.commit()

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
                elif bot_message and len(response_text) - last_edit_len >= 100:
                    last_edit_len = len(response_text)
                    try:
                        await bot_message.edit_text(response_text + "...")
                    except Exception:
                        pass

            if chunk.get("done"):
                response_time_ms = chunk.get("response_time_ms", int((time.time() - start_time) * 1000))

                if not response_text.strip():
                    await update.message.reply_text(
                        "❌ Модель вернула пустой ответ. Попробуйте ещё раз."
                    )
                    return

                # Final update
                if bot_message:
                    try:
                        await bot_message.edit_text(response_text)
                    except Exception:
                        pass
                elif not message_sent:
                    await update.message.reply_text(response_text)

                # Save assistant response and usage stats
                async with get_session() as session:
                    # Save conversation
                    assistant_msg = Conversation(
                        user_id=user_id,
                        model_name=model_name,
                        message_role="assistant",
                        message_content=response_text,
                        response_time_ms=response_time_ms,
                    )
                    session.add(assistant_msg)

                    # Update usage stats
                    from datetime import datetime
                    today = datetime.utcnow().date()

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
                    else:
                        usage = ModelUsage(
                            user_id=user_id,
                            model_name=model_name,
                            request_count=1,
                            total_response_time_ms=response_time_ms,
                            date=today,
                        )
                        session.add(usage)

                    await session.commit()

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
    except asyncio.TimeoutError:
        await update.message.reply_text(
            "⏱️ Request timed out. Please try again with a shorter message or different model."
        )
    except Exception as e:
        logger.error("Chat error", user_id=user_id, model=model_name, error=str(e))
        await update.message.reply_text(
            "❌ An error occurred while generating the response.\n"
            "Please try again later or contact the administrator."
        )

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
async def clear_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear conversation context for the user."""
    user_id = update.effective_user.id

    # Clear from context manager
    conv_context = ConversationContext(user_id, "", 0)
    await conv_context.clear()

    # Clear from database (keep last 10 for history)
    async with get_session() as session:
        # Get all conversations for user
        result = await session.execute(
            select(Conversation.id)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.created_at.desc())
            .offset(10)  # Keep last 10 messages
        )
        old_ids = [row[0] for row in result]

        if old_ids:
            # Delete old conversations
            from sqlalchemy import delete
            await session.execute(
                delete(Conversation).where(Conversation.id.in_(old_ids))
            )
            await session.commit()

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
            .order_by(Conversation.created_at.desc())
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
                (Conversation.created_at > last_user_msg.created_at)
            )
            .order_by(Conversation.created_at.desc())
            .limit(1)
        )
        last_assistant_msg = result.scalar_one_or_none()

        if last_assistant_msg:
            await session.delete(last_assistant_msg)
            await session.commit()

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
            .order_by(Conversation.created_at.desc())
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

        history += f"{role_emoji} <b>{msg.message_role.title()} ({time_str}):</b>\n{content}\n\n"

    await update.message.reply_text(history, parse_mode="HTML")
