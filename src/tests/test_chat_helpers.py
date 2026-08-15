"""Small chat-handler helpers: image history and the typing indicator."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.handlers.chat import IMAGE_PLACEHOLDER_PREFIX, _keep_typing, _mark_past_images


def test_plain_message_is_untouched():
    message = {"role": "user", "content": "как дела?"}
    assert _mark_past_images(message) == message


def test_assistant_message_is_untouched():
    message = {"role": "assistant", "content": f"{IMAGE_PLACEHOLDER_PREFIX} что-то"}
    assert _mark_past_images(message) == message


def test_past_image_is_marked_as_unavailable():
    """The model must not think it still has the picture."""
    message = {
        "role": "user",
        "content": f"{IMAGE_PLACEHOLDER_PREFIX} что на фото?",
    }

    marked = _mark_past_images(message)

    assert "недоступно" in marked["content"]
    assert "что на фото?" in marked["content"]
    assert not marked["content"].startswith(IMAGE_PLACEHOLDER_PREFIX)


@pytest.mark.asyncio
async def test_typing_is_refreshed_until_cancelled(monkeypatch):
    from telegram.constants import ChatAction

    from bot.handlers import chat

    monkeypatch.setattr(chat, "TYPING_REFRESH_INTERVAL", 0.01)
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()

    task = asyncio.create_task(_keep_typing(context, 1, ChatAction.TYPING))
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert context.bot.send_chat_action.await_count >= 2
