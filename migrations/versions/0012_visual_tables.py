""""visual_tables" migration: Add image_memories, appearances, and outfit_records tables.

Revision ID: 0012_visual_tables
Revises: 0011_voice_preferences
Create Date: 2026-06-04 11:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0012_visual_tables"
down_revision: str | None = "0011_voice_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create image_memories table
    op.create_table(
        "image_memories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("image_id", sa.String(36), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("turn_id", sa.String(64), nullable=True),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("image_data", sa.LargeBinary(), nullable=True),
        sa.Column("image_url", sa.String(1024), nullable=True),
        sa.Column("mime_type", sa.String(64), nullable=False),
        sa.Column("prompt_used", sa.Text(), nullable=True),
        sa.Column("seed_used", sa.Integer(), nullable=True),
        sa.Column("model_used", sa.String(128), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("generation_time_ms", sa.Float(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("detected_objects", sa.JSON(), nullable=True),
        sa.Column("detected_text", sa.Text(), nullable=True),
        sa.Column("scene_category", sa.String(128), nullable=True),
        sa.Column("understanding_confidence", sa.Float(), nullable=True),
        sa.Column("processing_time_ms", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_type IN ('generated', 'uploaded', 'understood')",
            name="ck_image_memories_source_type",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id", name="image_memories_pkey"),
        sa.UniqueConstraint("image_id", name="image_memories_image_id_key"),
    )
    op.create_index(
        "ix_image_memories_image_id",
        "image_memories",
        ["image_id"],
        unique=True,
    )

    # 2. Create appearances table
    op.create_table(
        "appearances",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("appearance_id", sa.String(36), nullable=False),
        sa.Column("companion_id", sa.String(36), nullable=True),
        sa.Column(
            "character_seed",
            sa.Integer(),
            server_default=sa.text("42"),
            nullable=False,
        ),
        sa.Column(
            "character_description",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "visual_traits",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
        sa.Column(
            "art_style",
            sa.String(128),
            server_default=sa.text("'digital illustration'"),
            nullable=False,
        ),
        sa.Column(
            "color_palette",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["companion_id"],
            ["companions.companion_id"],
        ),
        sa.PrimaryKeyConstraint("id", name="appearances_pkey"),
        sa.UniqueConstraint("appearance_id", name="appearances_appearance_id_key"),
    )
    op.create_index(
        "ix_appearances_appearance_id",
        "appearances",
        ["appearance_id"],
        unique=True,
    )

    # 3. Add appearance_id FK column to companions (use_alter pattern)
    op.add_column(
        "companions",
        sa.Column("appearance_id", sa.String(36), nullable=True),
    )
    op.create_foreign_key(
        "companions_appearance_id_fkey",
        "companions",
        "appearances",
        ["appearance_id"],
        ["appearance_id"],
    )

    # 4. Create outfit_records table
    op.create_table(
        "outfit_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("outfit_id", sa.String(36), nullable=False),
        sa.Column("appearance_id", sa.String(36), nullable=False),
        sa.Column(
            "name",
            sa.String(128),
            server_default=sa.text("'default'"),
            nullable=False,
        ),
        sa.Column(
            "description",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "visual_tags",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["appearance_id"],
            ["appearances.appearance_id"],
        ),
        sa.PrimaryKeyConstraint("id", name="outfit_records_pkey"),
        sa.UniqueConstraint("outfit_id", name="outfit_records_outfit_id_key"),
    )
    op.create_index(
        "ix_outfit_records_outfit_id",
        "outfit_records",
        ["outfit_id"],
        unique=True,
    )


def downgrade() -> None:
    # 4. Drop outfit_records
    op.drop_index("ix_outfit_records_outfit_id", table_name="outfit_records")
    op.drop_table("outfit_records")

    # 3. Drop appearance_id from companions
    op.drop_constraint("companions_appearance_id_fkey", "companions", type_="foreignkey")
    op.drop_column("companions", "appearance_id")

    # 2. Drop appearances
    op.drop_index("ix_appearances_appearance_id", table_name="appearances")
    op.drop_table("appearances")

    # 1. Drop image_memories
    op.drop_index("ix_image_memories_image_id", table_name="image_memories")
    op.drop_table("image_memories")