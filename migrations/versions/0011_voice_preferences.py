""""voice_preferences" migration: Add voice preferences table.

Revision ID: 0011_voice_preferences
Revises: 0010_continuity_reconciliation
Create Date: 2026-06-04 10:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
import sqlalchemy.dialects.postgresql as postgresql


# revision identifiers, used by Alembic.
revision = "0011_voice_preferences"
down_revision = "0010_continuity_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("tts_provider_id", sa.String(64), nullable=True),
        sa.Column("tts_model_id", sa.String(128), nullable=True),
        sa.Column("stt_provider_id", sa.String(64), nullable=True),
        sa.Column("stt_model_id", sa.String(128), nullable=True),
        sa.Column("voice_speed", sa.Float(), nullable=True),
        sa.Column("voice_pitch", sa.Float(), nullable=True),
        sa.Column("voice_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="voice_preferences_pkey"),
        sa.UniqueConstraint("user_id", name="voice_preferences_user_id_key"),
    )

    op.create_index(
        "ix_voice_preferences_user_id",
        "voice_preferences",
        ["user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_voice_preferences_user_id", table_name="voice_preferences")
    op.drop_table("voice_preferences")