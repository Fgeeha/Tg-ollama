"""/stop cancels a running generation."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.handlers import chat

USER = 555


def make_event(user_id=USER):
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    return update


@pytest.fixture(autouse=True)
def clean_registry():
    chat._generating.clear()
    yield
    chat._generating.clear()


@pytest.fixture(autouse=True)
def as_admin(monkeypatch):
    """Skip the authorization lookup; access control is tested separately."""
    from bot.config import settings

    monkeypatch.setattr(settings, "ADMIN_IDS", [USER])


def replies(update):
    return [
        (c.args[0] if c.args else c.kwargs.get("text", ""))
        for c in update.message.reply_text.await_args_list
    ]


@pytest.mark.asyncio
async def test_nothing_to_stop_is_reported():
    event = make_event()

    await chat.stop_generation(event, None)

    assert "нечего останавливать" in replies(event)[0]


@pytest.mark.asyncio
async def test_running_generation_is_cancelled():
    event = make_event()
    started = asyncio.Event()

    async def long_generation():
        started.set()
        await asyncio.sleep(30)

    task = asyncio.create_task(long_generation())
    await started.wait()
    chat._generating[USER] = task

    await chat.stop_generation(event, None)
    await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled()
    assert "остановлена" in replies(event)[0]


@pytest.mark.asyncio
async def test_one_user_cannot_stop_another(db):
    from bot.database import User

    async with db() as session:
        session.add(User(user_id=999, full_name="Другой", is_active=True, is_admin=False))

    other = make_event(user_id=999)
    async def long_generation():
        await asyncio.sleep(30)

    task = asyncio.create_task(long_generation())
    chat._generating[USER] = task

    await chat.stop_generation(other, None)

    assert not task.cancelled()
    assert "нечего останавливать" in replies(other)[0]

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
