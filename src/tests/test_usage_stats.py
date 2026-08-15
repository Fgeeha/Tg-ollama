"""Per-day usage aggregation."""
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from bot.database import ModelUsage


@pytest.mark.asyncio
async def test_date_column_stores_a_calendar_day(db):
    """The column round-trips a date, not a timestamp truncated by chance."""
    today = date.today()
    async with db() as session:
        session.add(ModelUsage(
            user_id=1, model_name="m", request_count=1,
            total_tokens=42, total_response_time_ms=100, date=today,
        ))
        await session.commit()

        stored = await session.scalar(select(ModelUsage.date))

    assert stored == today
    assert isinstance(stored, date)


@pytest.mark.asyncio
async def test_rows_are_filtered_by_day_boundary(db):
    """Comparing against a date picks up today and skips last week."""
    today = date.today()
    async with db() as session:
        session.add(ModelUsage(
            user_id=1, model_name="new", request_count=1,
            total_tokens=10, total_response_time_ms=1, date=today,
        ))
        session.add(ModelUsage(
            user_id=1, model_name="old", request_count=1,
            total_tokens=10, total_response_time_ms=1, date=today - timedelta(days=7),
        ))
        await session.commit()

        recent = (await session.scalars(
            select(ModelUsage.model_name)
            .where(ModelUsage.date >= today - timedelta(days=1))
        )).all()

    assert list(recent) == ["new"]
