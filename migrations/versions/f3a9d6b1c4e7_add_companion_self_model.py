"""Add the companion self-model document to the durable identity row.

Additive and without backfill: every existing row starts with the empty
document ``{}``, which the application reads as "no development", so prompts and
behaviour are unchanged until evidence accumulates. Phase 1 deferred this
column until its structure and update semantics were settled
(``docs/COMPANION_PHASE3_SELF_MODEL.md``).
"""

from alembic import op
import sqlalchemy as sa

revision = "f3a9d6b1c4e7"
down_revision = "d41e8a7c502b"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "companion_identities",
        sa.Column("self_model", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade():
    developed = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM companion_identities "
                "WHERE CAST(self_model AS TEXT) NOT IN ('{}', 'null', ''))"
            )
        )
        .scalar()
    )
    if developed:
        raise RuntimeError("Refusing to discard a developed companion self-model on downgrade")
    op.drop_column("companion_identities", "self_model")
