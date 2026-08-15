"""Utilities package."""
from bot.utils.context import ConversationContext
from bot.utils.health import start_health_server, stop_health_server
from bot.utils.logging import setup_logging
from bot.utils.ollama import (
    OllamaClient,
    OllamaConnectionError,
    OllamaError,
    OllamaModelNotFoundError,
)

__all__ = [
    "ConversationContext",
    "start_health_server",
    "stop_health_server",
    "setup_logging",
    "OllamaClient",
    "OllamaError",
    "OllamaConnectionError",
    "OllamaModelNotFoundError",
]
