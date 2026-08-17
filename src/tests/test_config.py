"""Settings validation: webhook cross-field requirements and OLLAMA_API_STYLE."""
import pytest

from bot.config import Settings


def _settings(**overrides):
    base = {"BOT_TOKEN": "t", "ADMIN_IDS": "1"}
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_webhook_mode_requires_url():
    with pytest.raises(ValueError, match="WEBHOOK_URL"):
        _settings(BOT_MODE="webhook", WEBHOOK_SECRET="s")


def test_webhook_mode_requires_secret():
    with pytest.raises(ValueError, match="WEBHOOK_SECRET"):
        _settings(BOT_MODE="webhook", WEBHOOK_URL="https://x")


def test_webhook_mode_with_url_and_secret_is_valid():
    settings = _settings(BOT_MODE="webhook", WEBHOOK_URL="https://x", WEBHOOK_SECRET="s")
    assert settings.BOT_MODE == "webhook"


def test_polling_mode_needs_neither():
    settings = _settings()
    assert settings.BOT_MODE == "polling"


def test_invalid_ollama_api_style_rejected():
    with pytest.raises(ValueError, match="OLLAMA_API_STYLE"):
        _settings(OLLAMA_API_STYLE="anthropic")


def test_ollama_api_style_defaults_to_ollama():
    assert _settings().OLLAMA_API_STYLE == "ollama"
