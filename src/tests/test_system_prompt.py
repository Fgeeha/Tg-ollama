"""Per-user system prompt: show, set, reset."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from bot.config import settings
from bot.database import User
from bot.handlers import chat

USER = 321


@pytest.fixture(autouse=True)
def as_admin(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_IDS", [USER])


def make_event():
    update = MagicMock()
    update.effective_user.id = USER
    update.message.reply_text = AsyncMock()
    return update


def replies_of(update):
    return [
        (c.args[0] if c.args else c.kwargs.get("text", ""))
        for c in update.message.reply_text.await_args_list
    ]


def make_context(*args):
    context = MagicMock()
    context.args = list(args)
    return context


async def seed_user(db) -> None:
    async with db() as session:
        session.add(User(user_id=USER, full_name="Тест", is_active=True, is_admin=True))


async def stored_prompt(db) -> str | None:
    async with db() as session:
        return await session.scalar(
            select(User.system_prompt).where(User.user_id == USER)
        )


@pytest.mark.asyncio
async def test_shows_the_default_when_unset(db):
    await seed_user(db)
    event = make_event()

    await chat.system_prompt_command(event, make_context())

    assert "стандартный" in replies_of(event)[0]


@pytest.mark.asyncio
async def test_setting_a_prompt_persists_it(db):
    await seed_user(db)
    event = make_event()

    await chat.system_prompt_command(
        event, make_context("Ты", "пират.", "Отвечай", "как", "пират.")
    )

    assert await stored_prompt(db) == "Ты пират. Отвечай как пират."
    assert "обновлён" in replies_of(event)[0]


@pytest.mark.asyncio
async def test_reset_restores_the_default(db):
    await seed_user(db)
    await chat.system_prompt_command(make_event(), make_context("Ты", "пират."))

    await chat.system_prompt_command(make_event(), make_context("reset"))

    assert await stored_prompt(db) is None


@pytest.mark.asyncio
async def test_overlong_prompt_is_refused(db):
    await seed_user(db)
    event = make_event()

    await chat.system_prompt_command(
        event, make_context("x" * (chat.MAX_SYSTEM_PROMPT_CHARS + 1))
    )

    assert await stored_prompt(db) is None
    assert "Слишком длинный" in replies_of(event)[0]


@pytest.mark.asyncio
async def test_shows_the_custom_prompt_once_set(db):
    await seed_user(db)
    await chat.system_prompt_command(make_event(), make_context("Только", "факты."))

    event = make_event()
    await chat.system_prompt_command(event, make_context())

    assert "Только факты." in replies_of(event)[0]
