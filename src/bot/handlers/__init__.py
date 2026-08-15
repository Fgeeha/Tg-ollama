"""Handler setup for the bot."""
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot.handlers import admin, chat, errors, models
from bot.handlers.common import help_command, start, status


def setup_handlers(application: Application) -> None:
    """Setup all bot handlers."""

    # Common commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))

    # Admin commands
    application.add_handler(CommandHandler("add_user", admin.add_user))
    application.add_handler(CommandHandler("remove_user", admin.remove_user))
    application.add_handler(CommandHandler("list_users", admin.list_users))
    application.add_handler(CommandHandler("test_mode", admin.toggle_test_mode))
    application.add_handler(CommandHandler("stats", admin.show_stats))
    application.add_handler(CommandHandler("clear_history", admin.clear_history))
    application.add_handler(CommandHandler("broadcast", admin.broadcast))

    # Model management commands
    application.add_handler(CommandHandler("models", models.list_models))
    application.add_handler(CommandHandler("switch_model", models.switch_model))
    application.add_handler(CommandHandler("model_info", models.model_info))
    application.add_handler(CommandHandler("current_model", models.current_model))

    # Chat commands
    application.add_handler(CommandHandler("clear", chat.clear_context))
    application.add_handler(CommandHandler("regenerate", chat.regenerate_response))
    application.add_handler(CommandHandler("history", chat.show_history))
    application.add_handler(CommandHandler("stop", chat.stop_generation))
    application.add_handler(CommandHandler("system", chat.system_prompt_command))

    # Callback queries
    application.add_handler(
        CallbackQueryHandler(models.handle_model_selection, pattern="^select_model:")
    )

    # Errors from any handler above
    application.add_error_handler(errors.on_error)

    # Message handler for chat (must be last)
    application.add_handler(
        MessageHandler(filters.PHOTO, chat.handle_photo)
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, chat.handle_message)
    )
