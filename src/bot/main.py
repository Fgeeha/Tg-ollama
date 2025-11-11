"""Main entry point for the Telegram Ollama Bot."""
import asyncio
import signal
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from telegram.ext import Application

from bot.config import settings
from bot.database.connection import init_database, close_database
from bot.handlers import setup_handlers
from bot.utils.health import start_health_server, stop_health_server
from bot.utils.logging import setup_logging
from bot.utils.ollama import OllamaClient

logger = structlog.get_logger()


class BotApplication:
    """Main bot application class."""

    def __init__(self):
        self.application: Application | None = None
        self.ollama_client: OllamaClient | None = None
        self.health_server = None
        self.shutdown_event = asyncio.Event()

    async def initialize(self) -> None:
        """Initialize all bot components."""
        logger.info("Initializing bot application...")
        
        # Initialize database
        await init_database()
        logger.info("Database initialized")
        
        # Initialize Ollama client
        self.ollama_client = OllamaClient(
            base_url=settings.OLLAMA_HOST,
            timeout=settings.OLLAMA_TIMEOUT
        )
        await self.ollama_client.verify_connection()
        logger.info("Ollama client initialized", host=settings.OLLAMA_HOST)
        
        # Initialize Telegram application
        self.application = (
            Application.builder()
            .token(settings.BOT_TOKEN)
            .build()
        )
        
        # Pass ollama_client to handlers via bot_data
        self.application.bot_data["ollama_client"] = self.ollama_client
        self.application.bot_data["settings"] = settings
        
        # Setup handlers
        setup_handlers(self.application)
        logger.info("Handlers configured")
        
        # Start health check server if enabled
        if settings.HEALTH_CHECK_ENABLED:
            self.health_server = await start_health_server(
                self.ollama_client,
                settings.HEALTH_CHECK_PORT
            )
            logger.info("Health check server started", port=settings.HEALTH_CHECK_PORT)

    async def start(self) -> None:
        """Start the bot."""
        logger.info("Starting bot...")
        
        # Initialize and start polling
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling(
            allowed_updates=["message", "callback_query", "inline_query"],
            drop_pending_updates=True
        )
        
        logger.info("Bot started successfully", test_mode=settings.TEST_MODE)
        if settings.TEST_MODE:
            logger.warning("Bot is running in TEST MODE - only admin can interact")

    async def stop(self) -> None:
        """Stop the bot gracefully."""
        logger.info("Stopping bot...")
        
        if self.application:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
        
        if self.health_server:
            await stop_health_server(self.health_server)
        
        if self.ollama_client:
            await self.ollama_client.close()
        
        await close_database()
        
        logger.info("Bot stopped")

    async def run(self) -> None:
        """Run the bot until interrupted."""
        await self.initialize()
        await self.start()
        
        # Wait for shutdown signal
        await self.shutdown_event.wait()
        await self.stop()

    def handle_signal(self, sig: int, frame) -> None:
        """Handle shutdown signals."""
        logger.info(f"Received signal {sig}, shutting down...")
        self.shutdown_event.set()


@asynccontextmanager
async def lifespan() -> AsyncGenerator[BotApplication, None]:
    """Application lifespan manager."""
    app = BotApplication()
    
    # Register signal handlers
    signal.signal(signal.SIGINT, app.handle_signal)
    signal.signal(signal.SIGTERM, app.handle_signal)
    
    try:
        yield app
    finally:
        await app.stop()


async def main() -> None:
    """Main function."""
    setup_logging(settings.LOG_LEVEL)
    
    logger.info(
        "Starting Telegram Ollama Bot",
        version="1.0.0",
        admin_id=settings.ADMIN_ID,
        test_mode=settings.TEST_MODE,
    )
    
    try:
        app = BotApplication()
        
        # Register signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, app.handle_signal)
        
        await app.run()
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.exception("Fatal error in main", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
