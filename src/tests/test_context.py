"""Conversation context: ordering, cache bounds and reset semantics."""
import pytest

from bot.utils.context import ConversationContext

from .conftest import add_messages


@pytest.mark.asyncio
async def test_history_is_chronological_within_one_second(db):
    """Messages written in the same second keep their insertion order.

    created_at only has second resolution in SQLite, so ordering by it left the
    model reading the dialogue backwards.
    """
    await add_messages(
        db, 1,
        ("user", "msg0"), ("assistant", "msg1"),
        ("user", "msg2"), ("assistant", "msg3"),
    )

    messages = await ConversationContext(1, "m").get_context()

    assert [m["content"] for m in messages] == ["msg0", "msg1", "msg2", "msg3"]


@pytest.mark.asyncio
async def test_history_limit_keeps_the_newest_messages(db):
    await add_messages(db, 1, *[("user", f"msg{i}") for i in range(15)])

    messages = await ConversationContext(1, "m").get_context(message_limit=5)

    assert [m["content"] for m in messages] == ["msg10", "msg11", "msg12", "msg13", "msg14"]


@pytest.mark.asyncio
async def test_clear_survives_reload_from_database(db):
    """A cleared context must not come back from the database."""
    from sqlalchemy import delete

    from bot.database import Conversation

    await add_messages(db, 1, ("user", "вопрос"), ("assistant", "ответ"))
    assert await ConversationContext(1, "m").get_context()

    # what the /clear handler does
    await ConversationContext(1, "", 0).clear()
    async with db() as session:
        await session.execute(delete(Conversation).where(Conversation.user_id == 1))

    assert await ConversationContext(1, "m").get_context() == []


@pytest.mark.asyncio
async def test_admin_clearing_history_drops_cached_context(db):
    """Deleted history must not keep reaching the model from the cache."""
    from bot.database import Conversation

    await add_messages(db, 1, ("user", "секрет"))
    assert await ConversationContext(1, "m").get_context()

    async with db() as session:
        await session.execute(Conversation.__table__.delete())
    ConversationContext.forget(1)

    assert await ConversationContext(1, "m").get_context() == []


@pytest.mark.asyncio
async def test_cache_is_bounded_and_evicts_least_recently_used(db, monkeypatch):
    """Memory stays flat no matter how many users ever wrote to the bot."""
    from bot.utils import context as context_module

    monkeypatch.setattr(context_module, "MAX_CACHED_CONTEXTS", 3)

    for user_id in range(1, 5):
        await add_messages(db, user_id, ("user", f"привет от {user_id}"))
        await ConversationContext(user_id, "m").get_context()

    cached = list(ConversationContext._contexts)
    assert len(cached) == 3
    assert 1 not in cached
    assert cached[-1] == 4


@pytest.mark.asyncio
async def test_evicted_context_is_rebuilt_from_database(db, monkeypatch):
    from bot.utils import context as context_module

    monkeypatch.setattr(context_module, "MAX_CACHED_CONTEXTS", 1)

    await add_messages(db, 1, ("user", "мой вопрос"))
    await ConversationContext(1, "m").get_context()
    await add_messages(db, 2, ("user", "чужой"))
    await ConversationContext(2, "m").get_context()

    assert 1 not in ConversationContext._contexts
    restored = await ConversationContext(1, "m").get_context()
    assert [m["content"] for m in restored] == ["мой вопрос"]
