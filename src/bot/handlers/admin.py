"""Admin command handlers."""
import asyncio
from datetime import timedelta
from html import escape

import structlog
from sqlalchemy import func, select
from telegram import Update
from telegram.ext import ContextTypes

from bot.database import Conversation, ModelUsage, User, get_session
from bot.decorators import admin_only
from bot.utils.context import ConversationContext
from bot.utils.ollama import OllamaClient
from bot.utils.runtime_settings import TEST_MODE_KEY, set_flag
from bot.utils.time import utc_now

logger = structlog.get_logger()

# Broadcasting to every user at full speed hits Telegram's rate limits and hogs
# the event loop, so each send is spaced out and retried once.
BROADCAST_DELAY = 0.05
BROADCAST_RETRIES = 2
BROADCAST_RETRY_DELAY = 1.0


async def _send_broadcast(context, user_id: int, text: str) -> bool:
    """Send one broadcast message, retrying once on a transient failure."""
    for attempt in range(1, BROADCAST_RETRIES + 1):
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="HTML",
            )
            return True
        except Exception as err:
            if attempt < BROADCAST_RETRIES:
                await asyncio.sleep(BROADCAST_RETRY_DELAY)
                continue
            logger.warning(
                "Failed to send broadcast",
                user_id=user_id,
                attempts=attempt,
                error=str(err),
            )
    return False


@admin_only
async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add a new authorized user."""
    if not context.args:
        await update.message.reply_text(
            "Usage: /add_user <user_id>\n"
            "Example: /add_user 123456789"
        )
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID. Please provide a numeric ID.")
        return

    async with get_session() as session:
        # Check if user already exists
        result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        existing_user = result.scalar_one_or_none()

        if existing_user:
            if existing_user.is_active:
                await update.message.reply_text(f"ℹ️ User {user_id} is already authorized.")
            else:
                existing_user.is_active = True
                await session.commit()
                await update.message.reply_text(f"✅ User {user_id} has been reactivated.")
        else:
            # Add new user
            new_user = User(
                user_id=user_id,
                full_name=f"User {user_id}",
                is_active=True,
                is_admin=False
            )
            session.add(new_user)
            await session.commit()
            await update.message.reply_text(f"✅ User {user_id} has been authorized.")

            logger.info("User added by admin", user_id=user_id, admin_id=update.effective_user.id)


@admin_only
async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove/deactivate a user."""
    if not context.args:
        await update.message.reply_text(
            "Usage: /remove_user <user_id>\n"
            "Example: /remove_user 123456789"
        )
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID. Please provide a numeric ID.")
        return

    # Prevent admin from removing themselves
    if user_id == update.effective_user.id:
        await update.message.reply_text("❌ You cannot remove yourself!")
        return

    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            await update.message.reply_text(f"❌ User {user_id} not found.")
        elif not user.is_active:
            await update.message.reply_text(f"ℹ️ User {user_id} is already deactivated.")
        else:
            user.is_active = False
            await session.commit()
            await update.message.reply_text(f"✅ User {user_id} has been deactivated.")

            logger.info("User removed by admin", user_id=user_id, admin_id=update.effective_user.id)


@admin_only
async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all authorized users."""
    async with get_session() as session:
        result = await session.execute(
            select(User).order_by(User.created_at.desc())
        )
        users = result.scalars().all()

        if not users:
            await update.message.reply_text("No users found.")
            return

        message = "👥 <b>Authorized Users:</b>\n\n"

        for user in users:
            status = "✅" if user.is_active else "❌"
            admin_badge = "👑" if user.is_admin else ""
            username = f"@{escape(user.username)}" if user.username else "No username"

            message += (
                f"{status} <b>{user.user_id}</b> {admin_badge}\n"
                f"   Name: {escape(user.full_name)}\n"
                f"   Username: {username}\n"
                f"   Model: {escape(user.selected_model or 'default')}\n"
                f"   Added: {user.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
            )

        await update.message.reply_text(message, parse_mode="HTML")


@admin_only
async def toggle_test_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle test mode on/off."""
    if not context.args:
        # Show current status
        settings = context.application.bot_data["settings"]
        status = "ON 🔒" if settings.TEST_MODE else "OFF 🔓"
        await update.message.reply_text(
            f"Test mode is currently: <b>{status}</b>\n\n"
            "Use /test_mode on or /test_mode off to change.",
            parse_mode="HTML"
        )
        return

    command = context.args[0].lower()
    if command not in ["on", "off"]:
        await update.message.reply_text("❌ Use /test_mode on or /test_mode off")
        return

    new_state = command == "on"
    settings = context.application.bot_data["settings"]

    # Persist first, then apply: a restart must keep the admin's choice.
    await set_flag(TEST_MODE_KEY, new_state)
    settings.TEST_MODE = new_state

    status = "ON 🔒 (Admin only)" if new_state else "OFF 🔓 (All users)"
    await update.message.reply_text(
        f"✅ Test mode is now: <b>{status}</b>",
        parse_mode="HTML"
    )

    logger.info("Test mode toggled", new_state=new_state, admin_id=update.effective_user.id)


@admin_only
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show usage statistics."""
    async with get_session() as session:
        # Get user count
        user_count = await session.execute(
            select(func.count(User.user_id)).where(User.is_active.is_(True))
        )
        active_users = user_count.scalar()

        # Get conversation stats for last 24 hours
        yesterday = utc_now() - timedelta(days=1)
        conv_stats = await session.execute(
            select(
                func.count(Conversation.id).label("total"),
                func.count(func.distinct(Conversation.user_id)).label("unique_users")
            ).where(Conversation.created_at >= yesterday)
        )
        stats = conv_stats.one()

        # Get model usage stats
        model_stats = await session.execute(
            select(
                ModelUsage.model_name,
                func.sum(ModelUsage.request_count).label("requests"),
                func.avg(ModelUsage.total_response_time_ms / ModelUsage.request_count).label("avg_time")
            )
            .where(ModelUsage.date >= yesterday)
            .group_by(ModelUsage.model_name)
            .order_by(func.sum(ModelUsage.request_count).desc())
        )
        model_data = model_stats.all()

        message = (
            "📊 <b>Bot Statistics (Last 24h)</b>\n\n"
            f"👥 Active Users: {active_users}\n"
            f"💬 Total Messages: {stats.total}\n"
            f"🔄 Unique Users: {stats.unique_users}\n\n"
        )

        if model_data:
            message += "<b>Model Usage:</b>\n"
            for model in model_data:
                avg_time = model.avg_time or 0
                message += f"• {model.model_name}: {model.requests} requests (avg {avg_time:.0f}ms)\n"

        # Get Ollama status
        ollama_client: OllamaClient = context.application.bot_data["ollama_client"]
        if await ollama_client.health_check():
            models = await ollama_client.get_model_names()
            message += f"\n✅ Ollama: Online ({len(models)} models available)"
        else:
            message += "\n❌ Ollama: Offline"

        await update.message.reply_text(message, parse_mode="HTML")


@admin_only
async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear conversation history for a user or all users."""
    if not context.args:
        await update.message.reply_text(
            "Usage:\n"
            "/clear_history <user_id> - Clear history for specific user\n"
            "/clear_history all - Clear all history\n"
        )
        return

    target = context.args[0]

    async with get_session() as session:
        if target.lower() == "all":
            # Clear all history
            await session.execute(Conversation.__table__.delete())
            await session.commit()
            # Deleted rows are still cached in memory and would keep reaching
            # the model until a restart.
            ConversationContext.clear_all()
            await update.message.reply_text("✅ All conversation history has been cleared.")
            logger.info("All conversation history cleared", admin_id=update.effective_user.id)
        else:
            try:
                user_id = int(target)
                result = await session.execute(
                    Conversation.__table__.delete().where(Conversation.user_id == user_id)
                )
                await session.commit()
                ConversationContext.forget(user_id)


                if result.rowcount > 0:
                    await update.message.reply_text(
                        f"✅ Cleared {result.rowcount} messages for user {user_id}."
                    )
                    logger.info(
                        "User history cleared",
                        user_id=user_id,
                        admin_id=update.effective_user.id,
                        messages_deleted=result.rowcount
                    )
                else:
                    await update.message.reply_text(f"ℹ️ No history found for user {user_id}.")

            except ValueError:
                await update.message.reply_text("❌ Invalid user ID. Use a number or 'all'.")


@admin_only
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Broadcast a message to all active users."""
    if not context.args:
        await update.message.reply_text(
            "Usage: /broadcast <message>\n"
            "Example: /broadcast The bot will be down for maintenance at 10 PM UTC"
        )
        return

    message = " ".join(context.args)

    async with get_session() as session:
        result = await session.execute(
            select(User.user_id).where(User.is_active.is_(True))
        )
        user_ids = [row[0] for row in result]

    if not user_ids:
        await update.message.reply_text("No active users to broadcast to.")
        return

    success = 0
    failed = 0

    broadcast_msg = f"📢 <b>Admin Broadcast:</b>\n\n{escape(message)}"

    await update.message.reply_text(f"📤 Отправляю {len(user_ids)} пользователям...")

    for user_id in user_ids:
        if await _send_broadcast(context, user_id, broadcast_msg):
            success += 1
        else:
            failed += 1

        # Pace the run: sending as fast as the loop allows runs into Telegram's
        # rate limits and starves other handlers.
        await asyncio.sleep(BROADCAST_DELAY)

    await update.message.reply_text(
        f"✅ Broadcast complete!\n"
        f"Success: {success}\n"
        f"Failed: {failed}"
    )

    logger.info(
        "Broadcast sent",
        admin_id=update.effective_user.id,
        success=success,
        failed=failed
    )
