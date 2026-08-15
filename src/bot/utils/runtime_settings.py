"""Settings an admin can change at runtime, persisted in the database.

Values set through admin commands must outlive a restart, so they are stored in
the ``settings`` table and loaded back at startup. Environment variables provide
the initial value the first time the bot runs.
"""
import structlog
from sqlalchemy import select

from bot.config import settings
from bot.database import Setting, get_session

logger = structlog.get_logger()

TEST_MODE_KEY = "test_mode"


async def get_flag(key: str) -> bool | None:
    """Read a stored boolean flag, or None when it was never set."""
    async with get_session() as session:
        raw = await session.scalar(select(Setting.value).where(Setting.key == key))

    if raw is None:
        return None
    return raw.strip().lower() in ("true", "1", "yes", "on")


async def set_flag(key: str, value: bool) -> None:
    """Store a boolean flag."""
    async with get_session() as session:
        setting = await session.scalar(select(Setting).where(Setting.key == key))
        if setting:
            setting.value = str(value)
        else:
            session.add(Setting(key=key, value=str(value)))


async def load_into_settings() -> None:
    """Apply stored admin settings on top of the environment configuration."""
    stored_test_mode = await get_flag(TEST_MODE_KEY)
    if stored_test_mode is not None and stored_test_mode != settings.TEST_MODE:
        logger.info(
            "Restored test mode from database",
            env_value=settings.TEST_MODE,
            stored_value=stored_test_mode,
        )
        settings.TEST_MODE = stored_test_mode
