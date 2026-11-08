"""Add memory lifecycle and immutable multi-source evidence without backfilling."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "d41e8a7c502b"
down_revision = "c93a14e2786b"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "semantic_facts",
        sa.Column("lifecycle_status", sa.String(24), nullable=False, server_default="active"),
    )
    for field in ("scope_key", "assertion_key", "slot_key"):
        op.add_column("semantic_facts", sa.Column(field, sa.String(64), nullable=True))
    op.create_index("ix_semantic_facts_scope_key", "semantic_facts", ["scope_key"])
    op.create_index(
        "ix_memory_scope_assertion", "semantic_facts", ["user_id", "scope_key", "assertion_key"]
    )
    op.create_index("ix_memory_scope_slot", "semantic_facts", ["user_id", "scope_key", "slot_key"])
    op.add_column(
        "semantic_facts",
        sa.Column("reinforcement_count", sa.Integer(), nullable=False, server_default="1"),
    )
    for field in ("last_reinforced_at", "superseded_at"):
        op.add_column("semantic_facts", sa.Column(field, sa.DateTime(timezone=True), nullable=True))
    op.add_column("semantic_facts", sa.Column("superseded_by", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_memory_superseded_by",
        "semantic_facts",
        "semantic_facts",
        ["superseded_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_memory_lifecycle_status",
        "semantic_facts",
        "lifecycle_status IN ('active', 'superseded', 'historical', 'suppressed')",
    )
    op.create_table(
        "memory_evidence",
        sa.Column("evidence_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "memory_id",
            UUID(as_uuid=True),
            sa.ForeignKey("semantic_facts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "source_message_id",
            UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "session_id", UUID(as_uuid=True), sa.ForeignKey("sessions.id", ondelete="SET NULL")
        ),
        sa.Column("turn_id", sa.String(64), nullable=False),
        sa.Column("assertion_text", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("source_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("memory_id", "user_id"):
        op.create_index(f"ix_memory_evidence_{column}", "memory_evidence", [column])
    op.create_table(
        "memory_relations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id",
            UUID(as_uuid=True),
            sa.ForeignKey("semantic_facts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_id",
            UUID(as_uuid=True),
            sa.ForeignKey("semantic_facts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation", sa.String(24), nullable=False),
        sa.Column(
            "evidence_id",
            UUID(as_uuid=True),
            sa.ForeignKey("memory_evidence.evidence_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("source_id", "target_id", "relation", name="uq_memory_relation"),
        sa.CheckConstraint("source_id <> target_id", name="ck_memory_relation_distinct"),
        sa.CheckConstraint(
            "relation IN ('supersedes', 'changed_from', 'conflicts_with')",
            name="ck_memory_relation_kind",
        ),
    )
    for column in ("source_id", "target_id"):
        op.create_index(f"ix_memory_relations_{column}", "memory_relations", [column])


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS(SELECT 1 FROM memory_evidence LIMIT 1)"))
        .scalar()
    ):
        raise RuntimeError("Refusing to discard durable memory evidence during lifecycle downgrade")
    op.drop_table("memory_relations")
    op.drop_table("memory_evidence")
    op.drop_constraint("fk_memory_superseded_by", "semantic_facts", type_="foreignkey")
    op.drop_constraint("ck_memory_lifecycle_status", "semantic_facts", type_="check")
    for name in (
        "ix_memory_scope_assertion",
        "ix_memory_scope_slot",
        "ix_semantic_facts_scope_key",
    ):
        op.drop_index(name, table_name="semantic_facts")
    for field in (
        "lifecycle_status",
        "scope_key",
        "assertion_key",
        "slot_key",
        "reinforcement_count",
        "last_reinforced_at",
        "superseded_by",
        "superseded_at",
    ):
        op.drop_column("semantic_facts", field)
