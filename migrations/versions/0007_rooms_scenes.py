"""Add rooms and scenes tables.

Revision ID: 0007_rooms_scenes
Revises: 0006_agency
Create Date: 2026-06-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0007_rooms_scenes"
down_revision: str | None = "0006_agency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create rooms table without scene_id FK (circular dependency on scenes)
    op.create_table(
        "rooms",
        sa.Column("room_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("companion_id", sa.String(64), nullable=True),
        sa.Column("room_type", sa.String(20), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column(
            "memory_persist_to_durable",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "memory_scope",
            sa.String(20),
            server_default=sa.text("'room'"),
            nullable=False,
        ),
        sa.Column(
            "memory_max_context_turns",
            sa.Integer(),
            server_default=sa.text("50"),
            nullable=False,
        ),
        sa.Column(
            "memory_include_embeddings",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("room_id"),
    )
    op.create_index("ix_rooms_user_id", "rooms", ["user_id"], unique=False)

    # 2. Create scenes table with FK to rooms.room_id
    op.create_table(
        "scenes",
        sa.Column("scene_id", sa.String(64), nullable=False),
        sa.Column("room_id", sa.String(64), nullable=False),
        sa.Column(
            "location",
            sa.String(255),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "activity",
            sa.String(255),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "tone",
            sa.String(20),
            server_default=sa.text("'neutral'"),
            nullable=False,
        ),
        sa.Column(
            "intensity",
            sa.Integer(),
            server_default=sa.text("5"),
            nullable=False,
        ),
        sa.Column(
            "is_paused",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("participants", sa.JSON(), nullable=True),
        sa.Column("scene_context", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.room_id"]),
        sa.PrimaryKeyConstraint("scene_id"),
    )
    op.create_index("ix_scenes_room_id", "scenes", ["room_id"], unique=False)

    # 3. Add scene_id FK on rooms referencing scenes.scene_id
    op.add_column(
        "rooms",
        sa.Column("scene_id", sa.String(64), sa.ForeignKey("scenes.scene_id"), nullable=True),
    )


def downgrade() -> None:
    # Reverse order to avoid FK dependency violations
    op.drop_column("rooms", "scene_id")
    op.drop_index("ix_scenes_room_id", table_name="scenes")
    op.drop_table("scenes")
    op.drop_index("ix_rooms_user_id", table_name="rooms")
    op.drop_table("rooms")