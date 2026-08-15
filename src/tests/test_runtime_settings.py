"""Admin settings must survive a restart."""
import pytest

from bot.config import settings
from bot.utils.runtime_settings import (
    TEST_MODE_KEY,
    get_flag,
    load_into_settings,
    set_flag,
)


@pytest.mark.asyncio
async def test_unset_flag_reads_as_none(db):
    assert await get_flag(TEST_MODE_KEY) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, False])
async def test_flag_roundtrip(db, value):
    await set_flag(TEST_MODE_KEY, value)
    assert await get_flag(TEST_MODE_KEY) is value


@pytest.mark.asyncio
async def test_stored_test_mode_overrides_env_on_startup(db, monkeypatch):
    """Admin turned test mode on; a restart must not silently turn it off."""
    monkeypatch.setattr(settings, "TEST_MODE", False)  # .env says off
    await set_flag(TEST_MODE_KEY, True)                # admin said on

    await load_into_settings()

    assert settings.TEST_MODE is True


@pytest.mark.asyncio
async def test_env_value_kept_when_admin_never_changed_it(db, monkeypatch):
    monkeypatch.setattr(settings, "TEST_MODE", True)

    await load_into_settings()

    assert settings.TEST_MODE is True
