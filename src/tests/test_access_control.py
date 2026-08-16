"""Access control and rate limiting against a real database.

These paths decide who may talk to the bot, so they are exercised with actual
rows rather than mocks.
"""
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from bot.config import settings
from bot.database import RateLimit, User
from bot.decorators import admin_only, authorized_only, rate_limited
from bot.utils.time import utc_now

ADMIN = 111
STRANGER = 999


def make_event(user_id: int):
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.text = "привет"
    update.message.reply_text = AsyncMock()
    return update


def replies(update) -> list[str]:
    return [
        (call.args[0] if call.args else call.kwargs.get("text", ""))
        for call in update.message.reply_text.await_args_list
    ]


@pytest.fixture(autouse=True)
def admin_id(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_IDS", [ADMIN])
    monkeypatch.setattr(settings, "TEST_MODE", False)


async def add_user(db, user_id: int, *, is_active: bool) -> None:
    async with db() as session:
        session.add(User(
            user_id=user_id, full_name=f"User {user_id}",
            is_active=is_active, is_admin=False,
        ))


@authorized_only
async def guarded(update, context=None):
    return "прошёл"


@admin_only
async def admin_guarded(update, context=None):
    return "прошёл"


@pytest.mark.asyncio
async def test_unknown_user_is_rejected(db):
    event = make_event(STRANGER)

    assert await guarded(event, None) is None
    assert "not authorized" in replies(event)[0]


@pytest.mark.asyncio
async def test_deactivated_user_is_rejected(db):
    await add_user(db, STRANGER, is_active=False)
    event = make_event(STRANGER)

    assert await guarded(event, None) is None


@pytest.mark.asyncio
async def test_active_user_passes(db):
    await add_user(db, STRANGER, is_active=True)
    event = make_event(STRANGER)

    assert await guarded(event, None) == "прошёл"
    event.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_test_mode_blocks_users_but_not_admin(db, monkeypatch):
    await add_user(db, STRANGER, is_active=True)
    monkeypatch.setattr(settings, "TEST_MODE", True)

    assert await guarded(make_event(STRANGER), None) is None
    assert await guarded(make_event(ADMIN), None) == "прошёл"


@pytest.mark.asyncio
async def test_admin_command_is_closed_to_others(db):
    await add_user(db, STRANGER, is_active=True)

    assert await admin_guarded(make_event(STRANGER), None) is None
    assert await admin_guarded(make_event(ADMIN), None) == "прошёл"


@rate_limited
async def limited(update, context=None):
    return "прошёл"


@pytest.mark.asyncio
async def test_rate_limit_blocks_after_the_allowance(db, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES", 3)
    monkeypatch.setattr(settings, "RATE_LIMIT_WINDOW", 60)
    await add_user(db, STRANGER, is_active=True)

    results = [await limited(make_event(STRANGER), None) for _ in range(4)]

    assert results[:3] == ["прошёл"] * 3
    assert results[3] is None


@pytest.mark.asyncio
async def test_allowance_returns_after_the_window(db, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_WINDOW", 60)
    await add_user(db, STRANGER, is_active=True)

    assert await limited(make_event(STRANGER), None) == "прошёл"
    assert await limited(make_event(STRANGER), None) is None

    # Move the window into the past, as waiting would
    async with db() as session:
        row = await session.scalar(
            select(RateLimit).where(RateLimit.user_id == STRANGER)
        )
        row.window_start = utc_now() - timedelta(seconds=120)

    assert await limited(make_event(STRANGER), None) == "прошёл"


@pytest.mark.asyncio
async def test_admin_is_not_rate_limited(db, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_MESSAGES", 1)

    results = [await limited(make_event(ADMIN), None) for _ in range(5)]

    assert results == ["прошёл"] * 5
