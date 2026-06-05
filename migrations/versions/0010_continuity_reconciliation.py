"""Record continuity schema reconciliation.

Revision ID: 0010_continuity_reconciliation
Revises: 0009_observability
Create Date: 2026-06-04

The continuity schema was already reconciled to the canonical migration-backed
tables: timeline_entries, narrative_threads, and rituals. This migration is an
intentional no-op so it does not introduce duplicate legacy continuity tables.
"""

from __future__ import annotations

from collections.abc import Sequence


revision: str = "0010_continuity_reconciliation"
down_revision: str | None = "0009_observability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
