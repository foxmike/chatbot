"""add_background_jobs

Revision ID: b8d5e3f7a9c1
Revises: a12f907bc341
Create Date: 2026-11-18 10:00:00.000000

Creates ``background_jobs``, the durable table behind
``chatbot.db.models.BackgroundJob`` (Phase 2 design, Task 2.2). The
process-local ``BackgroundQueue`` loses queued and retrying work when the
process exits; a job row is inserted in the same transaction as the completed
messages, so a rollback leaves neither completed turn nor job.

Job identity is unique per persisted logical turn and work type: a unique
constraint on ``(task_type, turn_id)`` makes re-enqueueing the same turn's
work conflict-safe. The check constraint on ``status`` bounds the state
machine to ``pending``/``running``/``succeeded``/``failed``. The
``(status, available_at)`` index serves the claimable-batch query; a
``running`` claim whose ``claimed_at`` expires is recoverable after the fixed
lease timeout, so process death needs no distributed locks.

The session foreign key is ``ON DELETE CASCADE``: session deletion deletes a
session's owned conversation rows, and a job row must not outlive its session
or make the session impossible to delete. The companion foreign key cascades
too, matching the other companion-owned tables; deleting a companion is only
possible once it has no conversations, so no live job rows are lost. The user
foreign key is plain, matching ``messages`` and ``semantic_facts``.

No backfill: volatile pre-migration queue contents cannot be recovered, and
existing conversation/memory rows are not rewritten.

Downgrade drops the table this revision creates, including any job rows it
holds.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "b8d5e3f7a9c1"
down_revision: Union[str, None] = "a12f907bc341"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("job_id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("task_type", sa.String(), nullable=False),
        sa.Column("turn_id", sa.String(64), nullable=True),
        sa.Column("session_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("companion_id", sa.String(36), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_background_jobs_status",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["companion_id"], ["companions.companion_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("task_type", "turn_id", name="uq_background_jobs_turn_identity"),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index(
        op.f("ix_background_jobs_status_available_at"),
        "background_jobs",
        ["status", "available_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_background_jobs_session_id"), "background_jobs", ["session_id"], unique=False
    )
    op.create_index(
        op.f("ix_background_jobs_user_id"), "background_jobs", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_background_jobs_companion_id"), "background_jobs", ["companion_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_background_jobs_companion_id"), table_name="background_jobs")
    op.drop_index(op.f("ix_background_jobs_user_id"), table_name="background_jobs")
    op.drop_index(op.f("ix_background_jobs_session_id"), table_name="background_jobs")
    op.drop_index(op.f("ix_background_jobs_status_available_at"), table_name="background_jobs")
    op.drop_table("background_jobs")
