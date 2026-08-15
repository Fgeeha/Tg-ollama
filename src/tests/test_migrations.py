"""Schema migrations must handle both a fresh install and an existing database."""
import sqlite3
from datetime import date

import pytest
from sqlalchemy import inspect, select

from bot.database import ModelUsage
from bot.database.connection import close_database, get_session, init_database


def _make_legacy_database(path) -> None:
    """Recreate what the old create_all schema looked like, with real data."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY, username VARCHAR(255),
            full_name VARCHAR(255) NOT NULL, is_active BOOLEAN NOT NULL,
            is_admin BOOLEAN NOT NULL, selected_model VARCHAR(255),
            created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL);
        CREATE TABLE settings (
            key VARCHAR(255) PRIMARY KEY, value TEXT NOT NULL,
            updated_at DATETIME NOT NULL);
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            model_name VARCHAR(255) NOT NULL, message_role VARCHAR(50) NOT NULL,
            message_content TEXT NOT NULL, tokens_used INTEGER,
            response_time_ms INTEGER, created_at DATETIME NOT NULL);
        CREATE TABLE model_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            model_name VARCHAR(255) NOT NULL, request_count INTEGER NOT NULL,
            total_tokens INTEGER NOT NULL, total_response_time_ms INTEGER NOT NULL,
            date DATETIME NOT NULL);
        CREATE TABLE rate_limits (
            user_id INTEGER PRIMARY KEY, message_count INTEGER NOT NULL,
            window_start DATETIME NOT NULL, last_reset DATETIME NOT NULL);

        INSERT INTO users VALUES
            (42, 'vasya', 'Вася', 1, 0, 'llama2', '2026-08-01 10:00:00', '2026-08-01 10:00:00');
        INSERT INTO model_usage VALUES (1, 42, 'llama2', 5, 0, 500, '2026-08-14 10:00:00');
    """)
    conn.commit()
    conn.close()


@pytest.fixture
def use_database(monkeypatch):
    def _use(path):
        from bot import config
        from bot.database import connection
        url = f"sqlite:///{path}"
        monkeypatch.setattr(config.settings, "DATABASE_URL", url)
        monkeypatch.setattr(connection.settings, "DATABASE_URL", url)
    return _use


@pytest.mark.asyncio
async def test_fresh_database_gets_the_current_schema(tmp_path, use_database):
    use_database(tmp_path / "fresh.db")

    await init_database()
    try:
        async with get_session() as session:
            session.add(ModelUsage(
                user_id=10_000_000_000,  # wider than a 32-bit INTEGER
                model_name="m", request_count=1, total_tokens=7,
                total_response_time_ms=10, date=date.today(),
            ))
            await session.commit()

            stored = await session.scalar(select(ModelUsage.date))
        assert stored == date.today()
    finally:
        await close_database()


@pytest.mark.asyncio
async def test_existing_database_is_upgraded_without_losing_data(tmp_path, use_database):
    """The old schema is adopted and fixed, and the rows survive."""
    path = tmp_path / "legacy.db"
    _make_legacy_database(path)
    use_database(path)

    await init_database()
    try:
        async with get_session() as session:
            # The timestamp is now a real date, readable by the ORM
            stored = await session.scalar(select(ModelUsage.date))
            assert stored == date(2026, 8, 14)

            from bot.database import User
            user = await session.scalar(select(User).where(User.user_id == 42))
            assert user.full_name == "Вася"
            assert user.selected_model == "llama2"
    finally:
        await close_database()


@pytest.mark.asyncio
async def test_migrations_are_idempotent(tmp_path, use_database):
    """Starting the bot twice must not fail on an already-migrated database."""
    use_database(tmp_path / "twice.db")

    await init_database()
    await close_database()
    await init_database()
    try:
        from bot.database.connection import engine

        async with engine.connect() as conn:
            tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        assert {"users", "conversations", "model_usage", "alembic_version"} <= tables
    finally:
        await close_database()
