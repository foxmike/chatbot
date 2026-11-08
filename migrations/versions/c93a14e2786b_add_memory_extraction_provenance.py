"""Preserve bounded extraction evidence and source metadata on memory rows.

Revision ID: c93a14e2786b
Revises: b8d5e3f7a9c1
"""

from alembic import op
import sqlalchemy as sa

revision = "c93a14e2786b"
down_revision = "b8d5e3f7a9c1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("semantic_facts", sa.Column("extraction_metadata", sa.JSON(), nullable=True))
    op.add_column("episodes", sa.Column("extraction_metadata", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("episodes", "extraction_metadata")
    op.drop_column("semantic_facts", "extraction_metadata")
