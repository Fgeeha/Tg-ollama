"""Database connection and session management."""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from bot.config import settings
from bot.database.models import Base

logger = structlog.get_logger()

# Global engine and session factory
engine = None
async_session_factory = None


async def init_database() -> None:
    """Initialize database connection and create tables."""
    global engine, async_session_factory
    
    # Convert sync SQLite URL to async if needed
    db_url = settings.DATABASE_URL
    if db_url.startswith("sqlite:///"):
        db_url = db_url.replace("sqlite:///", "sqlite+aiosqlite:///")
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://")
    
    # Create engine
    engine = create_async_engine(
        db_url,
        echo=settings.LOG_LEVEL == "DEBUG",
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )
    
    # Create session factory
    async_session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    # Create tables
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
