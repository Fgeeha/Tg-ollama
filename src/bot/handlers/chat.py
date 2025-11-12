"""Chat handler for conversations with Ollama models."""
import asyncio
import time
from typing import List, Dict, Any

import structlog
from sqlalchemy import select
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.database import get_session, User, Conversation, ModelUsage
from bot.decorators import authorized_only, rate_limited
from bot.utils.ollama import OllamaClient, OllamaModelNotFoundError
from bot.utils.context import ConversationContext, normalize_chat_messages

logger = structlog.get_logger()


@authorized_only
@rate_limited
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle regular chat messages."""
    user_id = update.effective_user.id
    message_text = update.message.text
    
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
    
    # Send typing indicator
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    
    # Get Ollama client
    ollama_client: OllamaClient = context.application.bot_data["ollama_client"]
    
    try:
        # Get conversation context
        conv_context = ConversationContext(user_id, model_name, context.application.bot_data["settings"].MAX_CONTEXT_LENGTH)
        messages = await conv_context.get_context()
        messages = normalize_chat_messages(messages)
        messages.insert(0, {
            "role": "system",
            "content": (
                "Отвечай на том же языке, что и последнее сообщение пользователя."
            )
        })

        user_message = {"role": "user", "content": message_text}
        messages.append(user_message)
        await conv_context.add_message(**user_message)

        
        # Save user message to database
        async with get_session() as session:
            user_msg = Conversation(
                user_id=user_id,
                model_name=model_name,
                message_role="user",
                message_content=message_text
            )
            session.add(user_msg)
            await session.commit()
        
        # Generate response
        start_time = time.time()
        
        # Stream response for better UX
        response_text = ""
        message_sent = False
        bot_message = None
        
        async for chunk in ollama_client.chat_stream(model_name, messages):
            if chunk.get("message", {}).get("content"):
                response_text += chunk["message"]["content"]
                
                # Send or update message every few chunks for smoother streaming
                if len(response_text) > 50 and not message_sent:
                    bot_message = await update.message.reply_text(response_text + "...")
                    message_sent = True
                elif message_sent and bot_message and len(response_text) % 100 == 0:
                    try:
                        await bot_message.edit_text(response_text + "...")
                    except Exception:
                        pass  # Ignore telegram rate limit errors
            
            if chunk.get("done"):
                response_time_ms = chunk.get("response_time_ms", int((time.time() - start_time) * 1000))
                
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
                        response_time_ms=response_time_ms
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
                            date=today
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
                    response_length=len(response_text)
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

    # Create a fake update with the last user message
    update.message.text = last_user_msg.message_content

    await update.message.reply_text("🔄 Regenerating response...")

    # Regenerate response
    await handle_message(update, context)


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
