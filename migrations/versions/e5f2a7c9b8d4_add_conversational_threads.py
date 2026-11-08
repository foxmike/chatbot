"""Add persistent conversational threads (Phase 4.3a).

Additive and without backfill: the new table starts empty, so prompts and
behaviour are unchanged until a completed turn's durable extraction records
unfinished conversational business. Threads are scoped to one
(user, companion, room) triple; private threads (``room_id`` NULL) follow the
companion-user pair across sessions. Resolved and superseded rows are retained
as history and are never deleted automatically.
"""

from alembic import op
import sqlalchemy as sa

revision = "e5f2a7c9b8d4"
down_revision = "f3a9d6b1c4e7"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "conversational_threads",
        sa.Column("thread_id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "companion_id",
            sa.String(36),
            sa.ForeignKey("companions.companion_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "room_id",
            sa.String(64),
            sa.ForeignKey("rooms.room_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("ownership", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("subject_key", sa.String(64), nullable=False),
        sa.Column("summary", sa.String(240), nullable=False),
        sa.Column("topic", sa.String(80), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'open'")),
        sa.Column(
            "importance", sa.String(16), nullable=False, server_default=sa.text("'ordinary'")
        ),
        sa.Column("source_evidence", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("relationship_context", sa.JSON(), nullable=True),
        sa.Column("last_discussed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.String(36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.sql.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.sql.func.now(),
        ),
        sa.CheckConstraint(
            "ownership IN ('companion', 'user', 'shared')",
            name="ck_conversational_threads_ownership",
        ),
        sa.CheckConstraint(
            "kind IN ('interest', 'request', 'subject', 'goal')",
            name="ck_conversational_threads_kind",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved', 'superseded')",
            name="ck_conversational_threads_status",
        ),
        sa.CheckConstraint(
            "importance IN ('ordinary', 'significant')",
            name="ck_conversational_threads_importance",
        ),
    )
    op.create_index("ix_conversational_threads_user_id", "conversational_threads", ["user_id"])
    op.create_index(
        "ix_conversational_threads_companion_id", "conversational_threads", ["companion_id"]
    )
    op.create_index("ix_conversational_threads_room_id", "conversational_threads", ["room_id"])
    op.create_index(
        "ix_conversational_threads_open",
        "conversational_threads",
        ["companion_id", "status", "last_discussed_at"],
    )


def downgrade():
    present = (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS(SELECT 1 FROM conversational_threads)"))
        .scalar()
    )
    if present:
        raise RuntimeError(
            "Refusing to discard recorded conversational threads on downgrade"
        )
    op.drop_index("ix_conversational_threads_open", table_name="conversational_threads")
    op.drop_index("ix_conversational_threads_room_id", table_name="conversational_threads")
    op.drop_index("ix_conversational_threads_companion_id", table_name="conversational_threads")
    op.drop_index("ix_conversational_threads_user_id", table_name="conversational_threads")
    op.drop_table("conversational_threads")
