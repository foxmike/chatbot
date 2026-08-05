"""add_gender_to_companions

Revision ID: b7f2e2a6d8c1
Revises: a01b4ebf6ff0
Create Date: 2026-08-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7f2e2a6d8c1"
down_revision: Union[str, None] = "a01b4ebf6ff0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("companions", sa.Column("gender", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("companions", "gender")
