"""drop_voice_preferences

Revision ID: d9e5a1c3b7f4
Revises: c8d34b8a1f2e
Create Date: 2026-10-27 00:00:00.000000

The per-user TTS/STT provider preferences served the retired Kokoro/Deepgram/ElevenLabs
adapters. Speech is now fixed to Faster-Whisper + Qwen3-TTS + AVTR-1. The initial migration
still creates the table, so history replays unchanged; downgrade recreates it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9e5a1c3b7f4"
down_revision: Union[str, None] = "c8d34b8a1f2e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("voice_preferences")


def downgrade() -> None:
    op.create_table(
        "voice_preferences",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("tts_provider_id", sa.String(length=64), nullable=True),
        sa.Column("tts_model_id", sa.String(length=128), nullable=True),
        sa.Column("stt_provider_id", sa.String(length=64), nullable=True),
        sa.Column("stt_model_id", sa.String(length=128), nullable=True),
        sa.Column("voice_speed", sa.Float(), nullable=True),
        sa.Column("voice_pitch", sa.Float(), nullable=True),
        sa.Column("voice_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
