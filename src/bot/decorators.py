"""Decorators for bot handlers."""
import functools
from collections.abc import Callable
from datetime import timedelta

import structlog
from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from bot.config import settings
from bot.database import RateLimit, User, get_session
from bot.utils.time import utc_now

logger = structlog.get_logger()


def admin_only(func: Callable) -> Callable:
    """Decorator to restrict access to admin only."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id

        if user_id not in settings.ADMIN_IDS:
            await update.message.reply_text(
                "❌ This command is restricted to administrators only."
            )
            logger.warning(
                "Unauthorized admin command attempt",
                user_id=user_id,
                command=update.message.text
            )
            return

        return await func(update, context, *args, **kwargs)

    return wrapper


def authorized_only(func: Callable) -> Callable:
    """Decorator to restrict access to authorized users only."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id

        # Admin always has access
        if user_id in settings.ADMIN_IDS:
            return await func(update, context, *args, **kwargs)

        # Check test mode
        if settings.TEST_MODE:
            await update.message.reply_text(
                "🔒 Bot is in test mode. Only administrators can interact."
            )
            return

        # Check user authorization
        async with get_session() as session:
            result = await session.execute(
                select(User).where(User.user_id == user_id)
            )
            user = result.scalar_one_or_none()

            if not user or not user.is_active:
                await update.message.reply_text(
                    "❌ You are not authorized to use this bot.\n\n"
                    f"Please contact the administrator with your User ID: <code>{user_id}</code>",
                    parse_mode="HTML"
                )
                logger.warning(
                    "Unauthorized access attempt",
                    user_id=user_id,
                    command=update.message.text if update.message else "callback"
                )
                return

        return await func(update, context, *args, **kwargs)

    return wrapper


def rate_limited(func: Callable) -> Callable:
    """Decorator to apply rate limiting to handlers."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id

        # Admin bypasses rate limiting
        if user_id in settings.ADMIN_IDS:
            return await func(update, context, *args, **kwargs)

        async with get_session() as session:
            # Get or create rate limit record
            result = await session.execute(
                select(RateLimit).where(RateLimit.user_id == user_id)
            )
            rate_limit = result.scalar_one_or_none()

            current_time = utc_now()

            if not rate_limit:
                # Create new rate limit record
                rate_limit = RateLimit(
                    user_id=user_id,
                    message_count=1,
                    window_start=current_time,
                    last_reset=current_time
                )
                session.add(rate_limit)
                await session.commit()
                return await func(update, context, *args, **kwargs)

            # Check if we need to reset the window
            window_duration = timedelta(seconds=settings.RATE_LIMIT_WINDOW)
            if current_time - rate_limit.window_start > window_duration:
                # Reset the window
                rate_limit.message_count = 1
                rate_limit.window_start = current_time
                rate_limit.last_reset = current_time
                await session.commit()
                return await func(update, context, *args, **kwargs)

            # Check rate limit
            if rate_limit.message_count >= settings.RATE_LIMIT_MESSAGES:
                # Calculate time until reset
                reset_time = rate_limit.window_start + window_duration
                seconds_until_reset = (reset_time - current_time).total_seconds()

                await update.message.reply_text(
                    f"⏱️ Rate limit exceeded!\n\n"
                    f"You've sent {rate_limit.message_count} messages in the last {settings.RATE_LIMIT_WINDOW} seconds.\n"
                    f"Please wait {int(seconds_until_reset)} seconds before sending more messages."
                )

                logger.warning(
                    "Rate limit exceeded",
                    user_id=user_id,
                    message_count=rate_limit.message_count,
                    seconds_until_reset=int(seconds_until_reset)
                )
                return

            # Increment message count
            rate_limit.message_count += 1
            await session.commit()

        return await func(update, context, *args, **kwargs)

    return wrapper


def log_command(func: Callable) -> Callable:
    """Decorator to log command usage."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        command = update.message.text if update.message else "callback"

        logger.info(
            "Command executed",
            user_id=user_id,
            command=command,
            function=func.__name__
        )

        try:
            result = await func(update, context, *args, **kwargs)
            logger.debug("Command completed successfully", function=func.__name__)
            return result
        except Exception as e:
            logger.error(
                "Command failed",
                user_id=user_id,
                command=command,
                function=func.__name__,
                error=str(e)
            )
            raise

    return wrapper
