"""Catch-all error handler.

Without it an exception raised outside a handler's own ``try`` only reaches the
log, and the user is left waiting for a reply that will never arrive.
"""
import structlog
from telegram import Update
from telegram.ext import ContextTypes

logger = structlog.get_logger()


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the failure with a traceback and tell the user something broke."""
    logger.error(
        "Unhandled error in handler",
        update=str(update)[:200],
        exc_info=context.error,
    )

    if not isinstance(update, Update) or update.effective_message is None:
        return

    try:
        await update.effective_message.reply_text(
            "❌ Что-то пошло не так при обработке сообщения.\n"
            "Попробуйте ещё раз, а если повторится — сообщите администратору."
        )
    except Exception:
        # The failure may itself be "cannot send a message" — never let the
        # error handler raise a second exception on top of the first.
        logger.warning("Could not notify the user about an error")
