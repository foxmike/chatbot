"""add_avatar_header_to_companions

Revision ID: c8d34b8a1f2e
Revises: b7f2e2a6d8c1
Create Date: 2026-08-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c8d34b8a1f2e"
down_revision: Union[str, None] = "b7f2e2a6d8c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "companions",
        sa.Column("avatar_header", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("companions", "avatar_header")
