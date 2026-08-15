"""Tests for the Telegram Ollama Bot."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bot.config import Settings
from bot.database.models import Conversation, User
from bot.utils.ollama import OllamaClient, OllamaModelNotFoundError


@pytest.fixture
def mock_settings():
    """Create mock settings for testing."""
    return Settings(
        BOT_TOKEN="test_token",
        ADMIN_ID=123456789,
        OLLAMA_HOST="http://localhost:11434",
        DATABASE_URL="sqlite:///test.db",
        TEST_MODE=False,
    )


@pytest.fixture
async def ollama_client():
    """Create Ollama client for testing."""
    client = OllamaClient("http://localhost:11434")
    yield client
    await client.close()


class TestOllamaClient:
    """Test Ollama client functionality."""

    @pytest.mark.asyncio
    async def test_list_models(self, ollama_client):
        """Test listing models."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "models": [
                {"name": "llama2", "size": 3826793472},
                {"name": "codellama", "size": 4212735980}
            ]
        }

        with patch.object(ollama_client.client, 'get', AsyncMock(return_value=mock_response)):
            models = await ollama_client.list_models()

            assert len(models) == 2
            assert models[0]["name"] == "llama2"
            assert models[1]["name"] == "codellama"

    @pytest.mark.asyncio
    async def test_model_exists(self, ollama_client):
        """Test checking if model exists."""
        ollama_client._available_models = [
            {"name": "llama2:latest"},
            {"name": "codellama:7b"}
        ]
        ollama_client._last_model_check = datetime.now().timestamp()

        assert await ollama_client.model_exists("llama2:latest") is True
        assert await ollama_client.model_exists("llama2") is True
        assert await ollama_client.model_exists("nonexistent") is False

    @pytest.mark.asyncio
    async def test_generate_error_on_missing_model(self, ollama_client):
        """Test that generate raises error for missing model."""
        ollama_client._available_models = []

        with pytest.raises(OllamaModelNotFoundError):
            await ollama_client.generate("nonexistent", "test prompt")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "model,info,expected",
        [
            # Modern Ollama: "capabilities" is authoritative, ignore everything else.
            ("llava:13b", {"capabilities": ["completion", "vision"]}, True),
            ("qwen2.5:14b", {"capabilities": ["completion", "tools"]}, False),
            # A system prompt mentioning images must not fool the capability check.
            ("llama2:7b", {"capabilities": ["completion"], "system": "describe the image"}, False),
            # Legacy Ollama without "capabilities": fall back to families/name.
            ("llava:13b", {"details": {"families": ["llama", "clip"]}}, True),
            ("llama3.2-vision", {"details": {"families": ["mllama"]}}, True),
            ("llama2:7b", {"details": {"families": ["llama"]}}, False),
        ],
    )
    async def test_supports_images(self, ollama_client, model, info, expected):
        """Vision detection prefers /api/show capabilities, falls back to name sniffing."""
        with patch.object(ollama_client, 'show_model_info', AsyncMock(return_value=info)):
            assert await ollama_client.supports_images(model) is expected

    @pytest.mark.asyncio
    async def test_supports_images_falls_back_to_false(self, ollama_client):
        """An unreachable /api/show must not block text chat."""
        from bot.utils.ollama import OllamaError

        with patch.object(ollama_client, 'show_model_info', AsyncMock(side_effect=OllamaError("boom"))):
            assert await ollama_client.supports_images("llama2") is False


class TestDatabaseModels:
    """Test database models."""

    def test_user_model(self):
        """Test User model creation."""
        user = User(
            user_id=123456,
            username="testuser",
            full_name="Test User",
            is_active=True,
            is_admin=False
        )

        assert user.user_id == 123456
        assert user.username == "testuser"
        assert user.full_name == "Test User"
        assert user.is_active is True
        assert user.is_admin is False

    def test_conversation_model(self):
        """Test Conversation model creation."""
        conv = Conversation(
            user_id=123456,
            model_name="llama2",
            message_role="user",
            message_content="Hello, bot!"
        )

        assert conv.user_id == 123456
        assert conv.model_name == "llama2"
        assert conv.message_role == "user"
        assert conv.message_content == "Hello, bot!"


class TestDecorators:
    """Test decorators."""

    @pytest.mark.asyncio
    async def test_admin_only_decorator(self):
        """Test admin_only decorator."""
        from bot.decorators import admin_only

        @admin_only
        async def admin_command(update, context):
            return "admin_success"

        # Mock update and context
        mock_update = MagicMock()
        mock_update.effective_user.id = 999  # Non-admin
        mock_update.message.reply_text = AsyncMock()
        mock_context = MagicMock()

        with patch('bot.decorators.settings.ADMIN_ID', 123456789):
            result = await admin_command(mock_update, mock_context)

            assert result is None
            mock_update.message.reply_text.assert_called_once()

            # Test with admin user
            mock_update.effective_user.id = 123456789
            mock_update.message.reply_text.reset_mock()

            result = await admin_command(mock_update, mock_context)
            assert result == "admin_success"
            mock_update.message.reply_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_authorized_only_decorator(self):
        """Test authorized_only decorator with test mode."""
        from bot.decorators import authorized_only

        @authorized_only
        async def user_command(update, context):
            return "user_success"

        mock_update = MagicMock()
        mock_update.effective_user.id = 999
        mock_update.message.reply_text = AsyncMock()
        mock_context = MagicMock()

        # Test with TEST_MODE enabled
        with patch('bot.decorators.settings.TEST_MODE', True), \
             patch('bot.decorators.settings.ADMIN_ID', 123456789):

            result = await user_command(mock_update, mock_context)
            assert result is None
            mock_update.message.reply_text.assert_called_with(
                "🔒 Bot is in test mode. Only administrators can interact."
            )


class TestConversationContext:
    """Test conversation context management."""

    @pytest.mark.asyncio
    async def test_add_and_get_context(self):
        """Test adding and retrieving context."""
        from bot.utils.context import ConversationContext

        context = ConversationContext(user_id=123, model_name="llama2")

        # Add messages
        await context.add_message("user", "Hello")
        await context.add_message("assistant", "Hi there!")

        # Get context
        messages = await context.get_context()

        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "Hi there!"

    @pytest.mark.asyncio
    async def test_context_trimming(self):
        """Test that context is trimmed when too long."""
        from bot.utils.context import ConversationContext

        context = ConversationContext(user_id=123, model_name="llama2", max_length=100)

        # Add messages that exceed max length
        await context.add_message("user", "A" * 50)
        await context.add_message("assistant", "B" * 50)
        await context.add_message("user", "C" * 50)

        messages = await context.get_context()

        # First message should be trimmed
        assert len(messages) <= 2
        total_length = sum(len(m["content"]) for m in messages)
        assert total_length <= 100


class TestHistoryOrdering:
    """C1/C2: history must come back chronologically, and /clear must clear it."""

    @pytest.fixture
    async def db(self, tmp_path, monkeypatch):
        """Initialise a throwaway SQLite database for the conversation tables."""
        import bot.database.connection as conn
        from bot.config import settings

        monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
        await conn.init_database()
        yield
        await conn.close_database()

    @pytest.mark.asyncio
    async def test_same_second_messages_keep_order(self, db):
        """created_at only has second resolution, so ordering must use id."""
        from bot.database import Conversation, get_session
        from bot.utils.context import ConversationContext

        user_id = 4242
        async with get_session() as session:
            for i in range(4):
                session.add(Conversation(
                    user_id=user_id, model_name="m",
                    message_role="user", message_content=f"msg{i}",
                ))
            await session.commit()

        ConversationContext._contexts.clear()
        messages = await ConversationContext(user_id, "m").get_context()

        assert [m["content"] for m in messages] == ["msg0", "msg1", "msg2", "msg3"]

    @pytest.mark.asyncio
    async def test_clear_context_empties_history(self, db):
        """Clearing only the in-memory cache let history reload from the database."""
        from bot.database import Conversation, get_session
        from bot.handlers.chat import clear_context
        from bot.utils.context import ConversationContext

        user_id = 4343
        async with get_session() as session:
            for i in range(3):
                session.add(Conversation(
                    user_id=user_id, model_name="m",
                    message_role="user", message_content=f"m{i}",
                ))
            await session.commit()

        ConversationContext._contexts.clear()
        assert await ConversationContext(user_id, "m").get_context() != []

        update = MagicMock()
        update.effective_user.id = user_id
        update.message.reply_text = AsyncMock()
        await clear_context.__wrapped__(update, MagicMock())

        ConversationContext._contexts.clear()
        assert await ConversationContext(user_id, "m").get_context() == []


class TestMessageSplitting:
    """C3: responses over Telegram's limit must be delivered, not silently dropped."""

    def test_short_message_not_split(self):
        from bot.handlers.chat import split_message

        assert split_message("hello") == ["hello"]

    def test_message_at_limit_not_split(self):
        from bot.handlers.chat import TELEGRAM_MAX_MESSAGE_LENGTH, split_message

        text = "a" * TELEGRAM_MAX_MESSAGE_LENGTH
        assert split_message(text) == [text]

    def test_long_message_split_losslessly(self):
        from bot.handlers.chat import TELEGRAM_MAX_MESSAGE_LENGTH, split_message

        text = "a" * (TELEGRAM_MAX_MESSAGE_LENGTH + 500)
        chunks = split_message(text)

        assert len(chunks) > 1
        assert all(len(c) <= TELEGRAM_MAX_MESSAGE_LENGTH for c in chunks)
        assert "".join(chunks) == text

    def test_long_message_prefers_line_boundaries(self):
        from bot.handlers.chat import TELEGRAM_MAX_MESSAGE_LENGTH, split_message

        chunks = split_message("line\n" * 1200)

        assert all(len(c) <= TELEGRAM_MAX_MESSAGE_LENGTH for c in chunks)
        assert not chunks[0].endswith("li")  # split on a newline, not mid-word


@pytest.mark.asyncio
async def test_health_check_server():
    """Test health check server."""
    from aiohttp import ClientSession
    from bot.utils.health import start_health_server, stop_health_server

    mock_ollama = AsyncMock()
    mock_ollama.health_check.return_value = True

    # Start server
    runner = await start_health_server(mock_ollama, port=8888)

    try:
        # Test health endpoint
        async with ClientSession() as session:
            async with session.get("http://localhost:8888/health") as response:
                assert response.status == 200
                data = await response.json()
                assert data["status"] == "healthy"
                assert data["bot"] == "online"
    finally:
        await stop_health_server(runner)
