"""Add users, user_sessions, and companions tables; add FK columns to sessions and messages.

Revision ID: 0008_auth_companions
Revises: 0007_rooms_scenes
Create Date: 2026-06-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0008_auth_companions"
down_revision: str | None = "0007_rooms_scenes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create users table
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(128), nullable=False),
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
        sa.UniqueConstraint("external_id"),
        sa.UniqueConstraint("username"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_external_id", "users", ["external_id"], unique=True)
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # 2. Create user_sessions table
    op.create_table(
        "user_sessions",
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "revoked",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("session_id"),
    )

    # 3. Create companions table
    op.create_table(
        "companions",
        sa.Column("companion_id", sa.String(36), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("persona", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("avatar_url", sa.String(512), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("companion_id"),
    )

    # 4. Add user_id and companion_id to sessions table
    op.add_column(
        "sessions",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_sessions_user_id",
        "sessions",
        "users",
        ["user_id"],
        ["id"],
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"], unique=False)

    op.add_column(
        "sessions",
        sa.Column("companion_id", sa.String(36), nullable=True),
    )
    op.create_foreign_key(
        "fk_sessions_companion_id",
        "sessions",
        "companions",
        ["companion_id"],
        ["companion_id"],
    )
    op.create_index(
        "ix_sessions_companion_id",
        "sessions",
        ["companion_id"],
        unique=False,
    )

    # 5. Add user_id and companion_id to messages table
    op.add_column(
        "messages",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_messages_user_id",
        "messages",
        "users",
        ["user_id"],
        ["id"],
    )
    op.create_index("ix_messages_user_id", "messages", ["user_id"], unique=False)

    op.add_column(
        "messages",
        sa.Column("companion_id", sa.String(36), nullable=True),
    )
    op.create_foreign_key(
        "fk_messages_companion_id",
        "messages",
        "companions",
        ["companion_id"],
        ["companion_id"],
    )
    op.create_index(
        "ix_messages_companion_id",
        "messages",
        ["companion_id"],
        unique=False,
    )


def downgrade() -> None:
    # 5. Drop columns from messages
    op.drop_index("ix_messages_companion_id", table_name="messages")
    op.drop_constraint("fk_messages_companion_id", "messages", type_="foreignkey")
    op.drop_column("messages", "companion_id")

    op.drop_index("ix_messages_user_id", table_name="messages")
    op.drop_constraint("fk_messages_user_id", "messages", type_="foreignkey")
    op.drop_column("messages", "user_id")

    # 4. Drop columns from sessions
    op.drop_index("ix_sessions_companion_id", table_name="sessions")
    op.drop_constraint("fk_sessions_companion_id", "sessions", type_="foreignkey")
    op.drop_column("sessions", "companion_id")

    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_constraint("fk_sessions_user_id", "sessions", type_="foreignkey")
    op.drop_column("sessions", "user_id")

    # 3. Drop companions table
    op.drop_table("companions")

    # 2. Drop user_sessions table
    op.drop_table("user_sessions")

    # 1. Drop users table
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_external_id", table_name="users")
    op.drop_table("users")