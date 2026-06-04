"""Add observability tables.

Revision ID: 0009_observability
Revises: 0008_auth_companions
Create Date: 2026-06-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0009_observability"
down_revision: str | None = "0008_auth_companions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "observability_traces",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trace_id", sa.String(36), nullable=False),
        sa.Column("turn_id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=True),
        sa.Column("model_provider", sa.String(64), nullable=True),
        sa.Column("model_id", sa.String(128), nullable=True),
        sa.Column("task_class", sa.String(32), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd_micros", sa.Integer(), nullable=False),
        sa.Column("total_latency_ms", sa.Float(), nullable=False),
        sa.Column("retrieve_latency_ms", sa.Float(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("routing_decision", sa.JSON(), nullable=False),
        sa.Column("retrieval_snippets", sa.JSON(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_observability_traces_trace_id",
        "observability_traces",
        ["trace_id"],
        unique=True,
    )
    op.create_index(
        "ix_observability_traces_turn_id",
        "observability_traces",
        ["turn_id"],
        unique=False,
    )
    op.create_index(
        "ix_observability_traces_session_id",
        "observability_traces",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "ix_observability_traces_user_id",
        "observability_traces",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "observability_usage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("model_provider", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("turn_count", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd_micros", sa.Integer(), nullable=False),
        sa.Column("avg_latency_ms", sa.Float(), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "model_provider", "model_id"),
    )
    op.create_index(
        "ix_observability_usage_session_id",
        "observability_usage",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "ix_observability_usage_model_provider",
        "observability_usage",
        ["model_provider"],
        unique=False,
    )
    op.create_index(
        "ix_observability_usage_model_id",
        "observability_usage",
        ["model_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("observability_usage")
    op.drop_table("observability_traces")
