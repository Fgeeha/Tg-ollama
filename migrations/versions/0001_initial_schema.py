"""Baseline schema, matching what create_all produced before migrations.

Existing databases are stamped at this revision instead of running it, so the
follow-up revisions can fix them the same way they set up a fresh install.

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False, default=False),
        sa.Column("selected_model", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "settings",
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False, index=True),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("message_role", sa.String(50), nullable=False),
        sa.Column("message_content", sa.Text(), nullable=False),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False, index=True),
    )

    op.create_table(
        "model_usage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False, index=True),
        sa.Column("model_name", sa.String(255), nullable=False, index=True),
        sa.Column("request_count", sa.Integer(), nullable=False, default=0),
        sa.Column("total_tokens", sa.Integer(), nullable=False, default=0),
        sa.Column("total_response_time_ms", sa.Integer(), nullable=False, default=0),
        sa.Column("date", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False, index=True),
    )

    op.create_table(
        "rate_limits",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("message_count", sa.Integer(), nullable=False, default=0),
        sa.Column("window_start", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("last_reset", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("rate_limits")
    op.drop_table("model_usage")
    op.drop_table("conversations")
    op.drop_table("settings")
    op.drop_table("users")
