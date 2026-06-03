"""Add provenance and privacy columns to semantic_facts.

Revision ID: 0004
Revises: 95005655ed55
Create Date: 2026-06-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0004"
down_revision: str | None = "95005655ed55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "semantic_facts",
        sa.Column(
            "source_type",
            sa.String(),
            nullable=False,
            server_default=sa.text("'inferred'"),
        ),
    )
    op.add_column(
        "semantic_facts",
        sa.Column(
            "source_turn_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "semantic_facts",
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default=sa.text("1.0"),
        ),
    )
    op.add_column(
        "semantic_facts",
        sa.Column(
            "privacy_level",
            sa.String(),
            nullable=False,
            server_default=sa.text("'temporary'"),
        ),
    )
    op.add_column(
        "semantic_facts",
        sa.Column(
            "pinned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.create_check_constraint(
        "ck_semantic_facts_source_type",
        "semantic_facts",
        "source_type IN ('explicit', 'inferred')",
    )
    op.create_check_constraint(
        "ck_semantic_facts_privacy_level",
        "semantic_facts",
        "privacy_level IN ('private', 'temporary', 'permanent')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_semantic_facts_source_type", "semantic_facts")
    op.drop_constraint("ck_semantic_facts_privacy_level", "semantic_facts")
    op.drop_column("semantic_facts", "pinned")
    op.drop_column("semantic_facts", "privacy_level")
    op.drop_column("semantic_facts", "confidence")
    op.drop_column("semantic_facts", "source_turn_id")
    op.drop_column("semantic_facts", "source_type")