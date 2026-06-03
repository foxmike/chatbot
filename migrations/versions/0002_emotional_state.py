"""Add emotional state persistence.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("mood_state", sa.JSON(), nullable=True))
    op.add_column("sessions", sa.Column("user_state", sa.JSON(), nullable=True))
    op.add_column("sessions", sa.Column("conversation_mode", sa.String(), nullable=True))

    op.create_table(
        "bot_mood_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mood", sa.String(), nullable=False),
        sa.Column("energy", sa.Float(), nullable=False),
        sa.Column("closeness", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bot_mood_logs_session_id",
        "bot_mood_logs",
        ["session_id"],
        unique=False,
    )

    op.create_table(
        "user_state_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("primary_state", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_user_state_logs_session_id",
        "user_state_logs",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_state_logs_session_id", table_name="user_state_logs")
    op.drop_table("user_state_logs")
    op.drop_index("ix_bot_mood_logs_session_id", table_name="bot_mood_logs")
    op.drop_table("bot_mood_logs")

    op.drop_column("sessions", "conversation_mode")
    op.drop_column("sessions", "user_state")
    op.drop_column("sessions", "mood_state")
