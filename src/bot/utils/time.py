"""Time helpers."""
from datetime import UTC, datetime


def utc_now() -> datetime:
    """Current UTC time as a naive datetime, matching what the DB stores."""
    return datetime.now(UTC).replace(tzinfo=None)
