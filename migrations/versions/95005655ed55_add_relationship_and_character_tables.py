"""Add relationship and character tables.

Revision ID: 95005655ed55
Revises: 0002
Create Date: 2026-06-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "95005655ed55"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "relationship_configs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("tone", sa.String(), nullable=False),
        sa.Column("directness", sa.Float(), nullable=False),
        sa.Column("warmth", sa.Float(), nullable=False),
        sa.Column("initiative_level", sa.Float(), nullable=False),
        sa.Column("content_level", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_relationship_configs_session_id",
        "relationship_configs",
        ["session_id"],
        unique=False,
    )

    op.create_table(
        "character_profiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("backstory", sa.Text(), nullable=False),
        sa.Column("personality_sliders", sa.JSON(), nullable=False),
        sa.Column("humor_style", sa.String(), nullable=False),
        sa.Column("communication_style", sa.String(), nullable=False),
        sa.Column("favorite_topics", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_character_profiles_session_id",
        "character_profiles",
        ["session_id"],
        unique=False,
    )

    op.create_table(
        "identity_states",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fixed", sa.JSON(), nullable=False),
        sa.Column("slow", sa.JSON(), nullable=False),
        sa.Column("mood", sa.JSON(), nullable=False),
        sa.Column(
            "last_updated",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_identity_states_session_id",
        "identity_states",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_identity_states_session_id", table_name="identity_states")
    op.drop_table("identity_states")
    op.drop_index("ix_character_profiles_session_id", table_name="character_profiles")
    op.drop_table("character_profiles")
    op.drop_index("ix_relationship_configs_session_id", table_name="relationship_configs")
    op.drop_table("relationship_configs")
