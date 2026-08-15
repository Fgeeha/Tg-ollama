"""Schema migrations, applied automatically at startup.

The schema used to be created by ``Base.metadata.create_all``, which silently
does nothing when a table already exists — a changed column type never reached
a database that was already in use. Migrations run instead, and a database
created by the old code is stamped at the baseline revision so the later
revisions can bring it up to date.
"""
import asyncio
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

logger = structlog.get_logger()

BASELINE_REVISION = "0001"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


def _alembic_config() -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    # The bot configures logging itself; see migrations/env.py.
    config.attributes["configure_logger"] = False
    return config


def _inspect_state(sync_connection) -> tuple[bool, str | None]:
    """Return whether app tables exist and the current alembic revision."""
    tables = set(inspect(sync_connection).get_table_names())
    revision = MigrationContext.configure(sync_connection).get_current_revision()
    return "users" in tables, revision


async def run_migrations(engine) -> None:
    """Bring the database schema up to date."""
    async with engine.connect() as connection:
        has_tables, revision = await connection.run_sync(_inspect_state)

    config = _alembic_config()

    if has_tables and revision is None:
        # Created by the old create_all path: adopt it at the baseline so the
        # following revisions apply, rather than trying to create tables again.
        logger.info("Adopting existing database into migrations")
        await asyncio.to_thread(command.stamp, config, BASELINE_REVISION)

    await asyncio.to_thread(command.upgrade, config, "head")
    logger.info("Database schema is up to date")
