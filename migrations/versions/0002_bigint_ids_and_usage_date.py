"""Widen user ids and store daily usage as a calendar day.

Messenger user ids do not fit a 32-bit INTEGER on PostgreSQL, and model_usage
stored a per-day counter in a DateTime column, which made the /stats comparison
depend on SQLite's string ordering.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    is_sqlite = op.get_bind().dialect.name == "sqlite"

    if is_sqlite:
        # SQLite types are advisory, but recreating the table casts every value
        # through CAST(date AS DATE) — and DATE has NUMERIC affinity there, so
        # '2026-08-14' would collapse to the integer 2026. Park the day in a
        # text column first and write it back afterwards, never casting it.
        op.add_column("model_usage", sa.Column("date_iso", sa.String(10)))
        op.execute(
            "UPDATE model_usage SET date_iso = substr(CAST(date AS VARCHAR), 1, 10)"
        )

    with op.batch_alter_table("model_usage") as batch:
        batch.alter_column("user_id", type_=sa.BigInteger(), existing_nullable=False)
        batch.alter_column(
            "date",
            type_=sa.Date(),
            existing_nullable=False,
            server_default=sa.func.current_date(),
            postgresql_using="date::date",
        )

    if is_sqlite:
        op.execute("UPDATE model_usage SET date = date_iso WHERE date_iso IS NOT NULL")
        with op.batch_alter_table("model_usage") as batch:
            batch.drop_column("date_iso")

    with op.batch_alter_table("users") as batch:
        batch.alter_column("user_id", type_=sa.BigInteger(), existing_nullable=False)

    with op.batch_alter_table("conversations") as batch:
        batch.alter_column("user_id", type_=sa.BigInteger(), existing_nullable=False)

    with op.batch_alter_table("rate_limits") as batch:
        batch.alter_column("user_id", type_=sa.BigInteger(), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("model_usage") as batch:
        batch.alter_column(
            "date",
            type_=sa.DateTime(timezone=True),
            existing_nullable=False,
            server_default=sa.func.now(),
        )
        batch.alter_column("user_id", type_=sa.Integer(), existing_nullable=False)

    for table in ("users", "conversations", "rate_limits"):
        with op.batch_alter_table(table) as batch:
            batch.alter_column("user_id", type_=sa.Integer(), existing_nullable=False)
