"""Shared fixtures for tests that touch the database."""
import pytest

from bot.database import models
from bot.database.connection import close_database, get_session, init_database


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """Fresh SQLite database per test, wired into the real session factory."""
    from bot import config
    from bot.database import connection

    monkeypatch.setattr(
        config.settings, "DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}"
    )
    monkeypatch.setattr(connection.settings, "DATABASE_URL", config.settings.DATABASE_URL)

    await init_database()
    try:
        yield get_session
    finally:
        await close_database()


@pytest.fixture(autouse=True)
def clean_context():
    """Keep the class-level context cache from leaking between tests."""
    from bot.utils.context import ConversationContext

    ConversationContext.clear_all()
    yield
    ConversationContext.clear_all()


async def add_messages(session_factory, user_id: int, *pairs: tuple[str, str]) -> None:
    """Insert conversation rows in the given order."""
    async with session_factory() as session:
        for role, content in pairs:
            session.add(
                models.Conversation(
                    user_id=user_id,
                    model_name="test-model",
                    message_role=role,
                    message_content=content,
                )
            )
        await session.commit()
