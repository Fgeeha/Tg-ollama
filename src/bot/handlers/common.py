"""Common command handlers."""
from html import escape

import structlog
from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from bot.config import settings
from bot.database import User, get_session
from bot.decorators import authorized_only

logger = structlog.get_logger()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    user = update.effective_user
    user_id = user.id

    # Check if user is admin
    is_admin = user_id in settings.ADMIN_IDS

    # Add or update user in database
    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        db_user = result.scalar_one_or_none()

        if not db_user:
            # Create new user
            db_user = User(
                user_id=user_id,
                username=user.username,
                full_name=user.full_name or f"User {user_id}",
                is_active=is_admin,  # Admin is automatically active
                is_admin=is_admin
            )
            session.add(db_user)
            await session.commit()

            if is_admin:
                logger.info("Admin user registered", user_id=user_id)
            else:
                logger.info("New user registered", user_id=user_id)
        else:
            # Update existing user info
            db_user.username = user.username
            db_user.full_name = user.full_name or db_user.full_name
            if is_admin:
                db_user.is_admin = True
                db_user.is_active = True

    # Send appropriate welcome message
    if is_admin:
        message = (
            "👑 <b>Welcome, Admin!</b>\n\n"
            "You have full access to all bot features.\n\n"
            "Admin Commands:\n"
            "• /add_user &lt;user_id&gt; - Authorize a user\n"
            "• /remove_user &lt;user_id&gt; - Revoke user access\n"
            "• /list_users - List all users\n"
            "• /test_mode - Toggle test mode\n"
            "• /stats - View usage statistics\n"
            "• /broadcast &lt;message&gt; - Send to all users\n\n"
            "Use /help to see all available commands."
        )
    elif db_user.is_active:
        message = (
            f"👋 <b>Welcome back, {escape(user.first_name)}!</b>\n\n"
            "I'm your AI assistant powered by Ollama.\n\n"
            "Quick Start:\n"
            "• Send me any message to chat\n"
            "• Use /models to select an AI model\n"
            "• Use /clear to reset conversation\n"
            "• Use /help for all commands\n\n"
            "Let's start chatting! 💬"
        )
    else:
        message = (
            f"👋 Hello, {escape(user.first_name)}!\n\n"
            "This bot requires authorization to use.\n"
            f"Please contact the administrator with your User ID: <code>{user_id}</code>\n\n"
            "Once authorized, you'll be able to chat with AI models."
        )

    await update.message.reply_text(message, parse_mode="HTML")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    user_id = update.effective_user.id
    is_admin = user_id in settings.ADMIN_IDS

    # Check if user is authorized
    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = result.scalar_one_or_none()
        is_authorized = user and user.is_active if user else False

    message = "📚 <b>Bot Commands</b>\n\n"

    if is_authorized or is_admin:
        message += (
            "<b>General Commands:</b>\n"
            "• /start - Start the bot\n"
            "• /help - Show this help message\n"
            "• /status - Check bot status\n\n"

            "<b>Model Commands:</b>\n"
            "• /models - List and select AI models\n"
            "• /switch_model &lt;name&gt; - Switch model\n"
            "• /current_model - Show current model\n"
            "• /model_info [name] - Model details\n\n"

            "<b>Chat Commands:</b>\n"
            "• Just send a message to chat!\n"
            "• /clear - Clear conversation context\n"
            "• /regenerate - Regenerate last response\n"
            "• /history - Show recent messages\n"
            "• /stop - Stop the current generation\n"
            "• /system [text|reset] - Your own system prompt\n\n"
        )

    if is_admin:
        message += (
            "<b>Admin Commands:</b>\n"
            "• /add_user &lt;user_id&gt; - Authorize user\n"
            "• /remove_user &lt;user_id&gt; - Revoke access\n"
            "• /list_users - Show all users\n"
            "• /test_mode [on/off] - Toggle test mode\n"
            "• /stats - Usage statistics\n"
            "• /clear_history [user_id/all] - Clear history\n"
            "• /broadcast &lt;msg&gt; - Message all users\n\n"
        )

    if not is_authorized and not is_admin:
        message += (
            "ℹ️ <i>You need authorization to use this bot.\n"
            f"Your User ID: <code>{user_id}</code></i>"
        )

    await update.message.reply_text(message, parse_mode="HTML")


@authorized_only
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check bot and Ollama status."""
    from bot.utils.ollama import OllamaClient

    ollama_client: OllamaClient = context.application.bot_data["ollama_client"]
    settings = context.application.bot_data["settings"]

    message = "🤖 <b>Bot Status</b>\n\n"

    # Bot status
    message += "✅ Bot: Online\n"

    # Ollama status
    if await ollama_client.health_check():
        models = await ollama_client.get_model_names()
        message += f"✅ Ollama: Online ({len(models)} models)\n"
    else:
        message += "❌ Ollama: Offline\n"

    # Test mode status
    test_mode = "ON 🔒" if settings.TEST_MODE else "OFF 🔓"
    message += f"📋 Test Mode: {test_mode}\n"

    # User's current model
    user_id = update.effective_user.id
    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = result.scalar_one_or_none()
        current_model = user.selected_model if user and user.selected_model else settings.DEFAULT_MODEL

    message += f"🎯 Your Model: {current_model}\n"

    # Rate limit info
    message += f"\n📊 Rate Limit: {settings.RATE_LIMIT_MESSAGES} msgs/{settings.RATE_LIMIT_WINDOW}s"

    await update.message.reply_text(message, parse_mode="HTML")
