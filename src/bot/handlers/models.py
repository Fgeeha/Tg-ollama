"""User command handlers for model management."""
import structlog
from sqlalchemy import select
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot.database import get_session, User
from bot.decorators import authorized_only
from bot.utils.ollama import OllamaClient, OllamaModelNotFoundError

logger = structlog.get_logger()


@authorized_only
async def list_models(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List available Ollama models."""
    ollama_client: OllamaClient = context.application.bot_data["ollama_client"]
    
    try:
        models = await ollama_client.list_models()
        
        if not models:
            await update.message.reply_text(
                "❌ No models available. Please ensure Ollama has models installed."
            )
            return
        
        # Get current user's selected model
        async with get_session() as session:
            result = await session.execute(
                select(User).where(User.user_id == update.effective_user.id)
            )
            user = result.scalar_one_or_none()
            current_model = user.selected_model if user else None
        
        # Create inline keyboard for model selection
        keyboard = []
        for model in models:
            model_name = model["name"]
            # Format size
            size_gb = model.get("size", 0) / (1024**3)
            size_str = f"{size_gb:.1f}GB"
            
            # Add checkmark for current model
            is_current = model_name == current_model
            display_name = f"{'✅ ' if is_current else ''}{model_name} ({size_str})"
            
            keyboard.append([
                InlineKeyboardButton(
                    display_name,
                    callback_data=f"select_model:{model_name}"
                )
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = (
            "🤖 <b>Available Models:</b>\n\n"
            "Select a model to use for conversations:"
        )
        
        if current_model:
            message += f"\n\n<i>Current model: {current_model}</i>"
        
        await update.message.reply_text(
            message,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error("Failed to list models", error=str(e))
        await update.message.reply_text(
            "❌ Failed to retrieve model list. Please try again later."
        )


@authorized_only
async def switch_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Switch to a different model."""
    if not context.args:
        await update.message.reply_text(
            "Usage: /switch_model <model_name>\n"
            "Example: /switch_model llama2\n\n"
            "Use /models to see available models."
        )
        return
    
    model_name = context.args[0]
    ollama_client: OllamaClient = context.application.bot_data["ollama_client"]
    
    # Validate model exists
    try:
        if not await ollama_client.model_exists(model_name):
            available = await ollama_client.get_model_names()
            await update.message.reply_text(
                f"❌ Model '{model_name}' not found.\n\n"
                f"Available models: {', '.join(available)}"
            )
            return
    except Exception as e:
        logger.error("Failed to validate model", error=str(e))
        await update.message.reply_text(
            "❌ Failed to validate model. Please try again later."
        )
        return
    
    # Update user's selected model
    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.user_id == update.effective_user.id)
        )
        user = result.scalar_one_or_none()
        
        if user:
            user.selected_model = model_name
            await session.commit()
            
            await update.message.reply_text(
                f"✅ Switched to model: <b>{model_name}</b>",
                parse_mode="HTML"
            )
            
            logger.info(
                "User switched model",
                user_id=update.effective_user.id,
                model=model_name
            )
        else:
            await update.message.reply_text(
                "❌ User not found. Please contact the administrator."
            )


async def handle_model_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle model selection from inline keyboard."""
    query = update.callback_query
    await query.answer()
    
    # Parse the callback data
    _, model_name = query.data.split(":", 1)
    
    # Get current user
    user_id = query.from_user.id
    
    # Update user's selected model
    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = result.scalar_one_or_none()
        
        if not user:
            await query.message.reply_text(
                "❌ You are not authorized. Please contact the administrator."
            )
            return
        
        if not user.is_active:
            await query.message.reply_text(
                "❌ Your access has been revoked. Please contact the administrator."
            )
            return
        
        # Update model
        old_model = user.selected_model
        user.selected_model = model_name
        await session.commit()
    
    # Update the message
    await query.edit_message_text(
        f"✅ Model switched successfully!\n\n"
        f"Previous: {old_model or 'default'}\n"
        f"Current: <b>{model_name}</b>\n\n"
        f"You can now start chatting with the new model.",
        parse_mode="HTML"
    )
    
    logger.info(
        "User selected model via keyboard",
        user_id=user_id,
        old_model=old_model,
        new_model=model_name
    )


@authorized_only
async def model_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show detailed information about a model."""
    if not context.args:
        # Show info about current model
        async with get_session() as session:
            result = await session.execute(
                select(User).where(User.user_id == update.effective_user.id)
            )
            user = result.scalar_one_or_none()
            model_name = user.selected_model if user else None
        
        if not model_name:
            await update.message.reply_text(
                "You haven't selected a model yet.\n"
                "Use /models to select one, or use:\n"
                "/model_info <model_name>"
            )
            return
    else:
        model_name = context.args[0]
    
    ollama_client: OllamaClient = context.application.bot_data["ollama_client"]
    
    try:
        # Get model information
        info = await ollama_client.show_model_info(model_name)
        
        # Format the information
        message = f"🤖 <b>Model: {model_name}</b>\n\n"
        
        # Add model details if available
        if "details" in info:
            details = info["details"]
            if "parameter_size" in details:
                message += f"📊 Parameters: {details['parameter_size']}\n"
            if "quantization_level" in details:
                message += f"🔧 Quantization: {details['quantization_level']}\n"
            if "family" in details:
                message += f"👪 Family: {details['family']}\n"
        
        # Add license info if available
        if "license" in info:
            message += f"\n📜 License: {info['license']}\n"
        
        # Add template if available (truncated)
        if "template" in info:
            template = info["template"][:200] + "..." if len(info["template"]) > 200 else info["template"]
            message += f"\n📝 Template preview:\n<code>{template}</code>\n"
        
        await update.message.reply_text(message, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Failed to get model info", model=model_name, error=str(e))
        await update.message.reply_text(
            f"❌ Failed to get information for model '{model_name}'.\n"
            "Make sure the model exists and try again."
        )


@authorized_only
async def current_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the currently selected model."""
    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.user_id == update.effective_user.id)
        )
        user = result.scalar_one_or_none()
        
        if user and user.selected_model:
            await update.message.reply_text(
                f"🤖 Current model: <b>{user.selected_model}</b>\n\n"
                "Use /models to switch to a different model.",
                parse_mode="HTML"
            )
        else:
            settings = context.application.bot_data["settings"]
            await update.message.reply_text(
                f"🤖 Using default model: <b>{settings.DEFAULT_MODEL}</b>\n\n"
                "Use /models to select a different model.",
                parse_mode="HTML"
            )
