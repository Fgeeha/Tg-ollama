"""Database connection and session management."""
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from bot.config import settings
from bot.database.migrate import run_migrations


def to_async_url(db_url: str) -> str:
    """Convert a plain database URL to its async driver equivalent."""
    if db_url.startswith("sqlite:///"):
        return db_url.replace("sqlite:///", "sqlite+aiosqlite:///")
    if db_url.startswith("postgresql://"):
        return db_url.replace("postgresql://", "postgresql+asyncpg://")
    return db_url

logger = structlog.get_logger()

engine = None
async_session_factory = None



async def init_database() -> None:
    """Initialize database connection and create tables."""
    global engine, async_session_factory

    db_url = to_async_url(settings.DATABASE_URL)


    engine = create_async_engine(
        db_url,
        echo=settings.LOG_LEVEL == "DEBUG",
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )

    async_session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    await run_migrations(engine)

    logger.info("Database initialized", url=db_url.split("@")[0])


async def close_database() -> None:
    """Close database connection."""
    global engine

    if engine:
        await engine.dispose()
        logger.info("Database connection closed")


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session that commits on a clean exit.

    Leaving the block commits, and any exception rolls back. Callers therefore
    do not need a trailing ``session.commit()``; an explicit commit is only
    meaningful mid-block, to flush before doing more work in the same session.
    """
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
