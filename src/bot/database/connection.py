"""Database connection and session management."""
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from pathlib import Path
import os

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # для SQLite это безопаснее

from bot.config import settings
from bot.database.models import Base

logger = structlog.get_logger()

engine = None
async_session_factory = None


def _normalize_db_url(url: str) -> str:
    # 1) Преобразуем схемы
    if url.startswith("sqlite:///"):
        url = url.replace("sqlite:///", "sqlite+aiosqlite:///")
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://")

    # 2) Для sqlite — сделать путь абсолютным и создать директорию
    if url.startswith("sqlite+aiosqlite:///"):
        # отрезаем схему и получаем файловый путь
        fs_path = url.replace("sqlite+aiosqlite:///", "", 1)

        # если путь относительный — делаем абсолютным к CWD
        if not fs_path.startswith("/"):
            fs_path = os.path.abspath(fs_path)

        # создаём родительскую директорию
        Path(fs_path).parent.mkdir(parents=True, exist_ok=True)

        # собираем обратно абсолютный URL (четыре слэша!)
        url = f"sqlite+aiosqlite:////{fs_path.lstrip('/')}"
        logger.info("SQLite path resolved", path=fs_path)

    return url

async def init_database() -> None:
    """Initialize database connection and create tables."""
    global engine, async_session_factory
    
    # Convert sync SQLite URL to async if needed
    db_url = _normalize_db_url(settings.DATABASE_URL)

    # Для SQLite лучше без пулов (иначе бывают тонкие ошибки с aiosqlite)
    engine_kwargs = dict(
        echo=settings.LOG_LEVEL == "DEBUG",
        pool_pre_ping=True,
    )
    if db_url.startswith("sqlite+aiosqlite:"):
        engine_kwargs["poolclass"] = NullPool
    else:
        # для Postgres оставим пулы
        engine_kwargs["pool_size"] = 5
        engine_kwargs["max_overflow"] = 10

    engine = create_async_engine(db_url, **engine_kwargs)

    async_session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database initialized", url=db_url.split("@")[0])


async def close_database() -> None:
    """Close database connection."""
    global engine
    
    if engine:
        await engine.dispose()
        logger.info("Database connection closed")


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get database session."""
    if not async_session_factory:
        raise RuntimeError("Database not initialized. Call init_database() first.")

    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting database session."""
    async with get_session() as session:
        yield session
