"""add_voice_library

Revision ID: e7a4c2d9f1b3
Revises: d9e5a1c3b7f4
Create Date: 2026-11-06 00:00:00.000000

Voice Library: saved voices (metadata; the reference recordings are files in the voice
library directory), each companion's default voice, and a per-session voice override.
Both references are RESTRICT: a voice in use cannot be deleted out from under them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7a4c2d9f1b3"
down_revision: Union[str, None] = "d9e5a1c3b7f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "voices",
        sa.Column("voice_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("reference_path", sa.String(length=255), nullable=False),
        sa.Column("reference_sha256", sa.String(length=64), nullable=False),
        sa.Column("reference_duration", sa.Float(), nullable=False),
        sa.Column("reference_sample_rate", sa.Integer(), nullable=False),
        sa.Column("reference_transcript", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=32), server_default="English", nullable=False),
        sa.Column("model_family", sa.String(length=64), nullable=False),
        sa.Column("creation_model", sa.String(length=128), nullable=False),
        sa.Column("synthesis_model", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ready", nullable=False),
        sa.Column("is_builtin", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("extra_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("method IN ('designed', 'cloned')", name="voices_method_check"),
        sa.CheckConstraint("(user_id IS NULL) = is_builtin", name="voices_owner_check"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("voice_id"),
    )
    op.create_index("ix_voices_user_id", "voices", ["user_id"])

    op.add_column("companions", sa.Column("voice_id", sa.String(length=36), nullable=True))
    op.create_index("ix_companions_voice_id", "companions", ["voice_id"])
    op.create_foreign_key("companions_voice_id_fkey", "companions", "voices", ["voice_id"], ["voice_id"],
                          ondelete="RESTRICT")

    op.add_column("sessions", sa.Column("voice_id", sa.String(length=36), nullable=True))
    op.create_index("ix_sessions_voice_id", "sessions", ["voice_id"])
    op.create_foreign_key("sessions_voice_id_fkey", "sessions", "voices", ["voice_id"], ["voice_id"],
                          ondelete="RESTRICT")


def downgrade() -> None:
    op.drop_constraint("sessions_voice_id_fkey", "sessions", type_="foreignkey")
    op.drop_index("ix_sessions_voice_id", table_name="sessions")
    op.drop_column("sessions", "voice_id")
    op.drop_constraint("companions_voice_id_fkey", "companions", type_="foreignkey")
    op.drop_index("ix_companions_voice_id", table_name="companions")
    op.drop_column("companions", "voice_id")
    op.drop_index("ix_voices_user_id", table_name="voices")
    op.drop_table("voices")
