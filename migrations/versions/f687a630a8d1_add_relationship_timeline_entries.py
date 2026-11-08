"""add_relationship_timeline_entries

Revision ID: f687a630a8d1
Revises: e7a4c2d9f1b3
Create Date: 2026-11-07 21:35:41.171704

Creates ``relationship_timeline_entries``, the table behind
``chatbot.continuity.timeline.RelationshipTimelineEntryModel``. The model and
its writers (``TimelineUpdateStep`` / ``RelationshipTimeline``) existed, but no
earlier revision created the table, so on a database built from migrations every
timeline write failed (and was logged and skipped). This is distinct from the
older ``timeline_entries`` table of the initial revision, which has a different
shape and is not touched here.

A deployment may already have the table because it was created by hand or from
the model's metadata. In that case nothing is dropped, recreated or rewritten:
the existing table is checked against the model and either adopted (any missing
index is added) or, if it is incompatible, the upgrade stops with an error that
lists the differences.

The session foreign key is ``ON DELETE CASCADE``: session deletion deletes a
session's conversation rows, and a plain foreign key would make any session that
has a timeline entry impossible to delete. An adopted pre-existing table keeps
the foreign key it already has.

Downgrade drops the table this revision creates, including any timeline rows it
holds.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f687a630a8d1"
down_revision: Union[str, None] = "e7a4c2d9f1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "relationship_timeline_entries"
_INDEXED_COLUMNS = ("session_id", "entry_type", "turn_id", "status")

# column -> (acceptable reflected type names, nullable)
_EXPECTED_COLUMNS: dict[str, tuple[set[str], bool]] = {
    "id": ({"UUID"}, False),
    "session_id": ({"UUID"}, False),
    "entry_type": ({"VARCHAR"}, False),
    "title": ({"VARCHAR"}, False),
    "description": ({"TEXT"}, False),
    "content": ({"TEXT"}, False),
    "turn_id": ({"UUID"}, False),
    "status": ({"VARCHAR"}, False),
    "tags": ({"JSON", "JSONB"}, False),
    "metadata": ({"JSON", "JSONB"}, False),
    "created_at": ({"TIMESTAMP"}, False),
    "updated_at": ({"TIMESTAMP"}, False),
}


def _index_name(column: str) -> str:
    return f"ix_{_TABLE}_{column}"


def _create_indexes(columns: Sequence[str]) -> None:
    for column in columns:
        op.create_index(_index_name(column), _TABLE, [column])


def _incompatibilities(inspector: sa.Inspector) -> list[str]:
    problems: list[str] = []
    actual = {column["name"]: column for column in inspector.get_columns(_TABLE)}

    for name, (type_names, nullable) in _EXPECTED_COLUMNS.items():
        column = actual.get(name)
        if column is None:
            problems.append(f"missing column {name!r}")
            continue
        type_name = type(column["type"]).__name__.upper()
        if type_name not in type_names:
            problems.append(f"column {name!r} is {type_name}, expected {'/'.join(sorted(type_names))}")
        elif type_name == "TIMESTAMP" and not getattr(column["type"], "timezone", False):
            problems.append(f"column {name!r} is TIMESTAMP WITHOUT TIME ZONE, expected WITH")
        if column["nullable"] != nullable:
            problems.append(f"column {name!r} nullable={column['nullable']}, expected {nullable}")

    for name, column in actual.items():
        if name not in _EXPECTED_COLUMNS and not column["nullable"] and column.get("default") is None:
            problems.append(f"unexpected NOT NULL column {name!r} without a default")

    if inspector.get_pk_constraint(_TABLE).get("constrained_columns") != ["id"]:
        problems.append("primary key is not (id)")
    if not any(
        fk["referred_table"] == "sessions" and fk["constrained_columns"] == ["session_id"]
        for fk in inspector.get_foreign_keys(_TABLE)
    ):
        problems.append("no foreign key from session_id to sessions")
    return problems


def upgrade() -> None:
    bind = op.get_bind()
    if not op.get_context().as_sql:
        inspector = sa.inspect(bind)
        if inspector.has_table(_TABLE):
            problems = _incompatibilities(inspector)
            if problems:
                raise RuntimeError(
                    f"Table {_TABLE} already exists but does not match the application model "
                    f"({'; '.join(problems)}). It was left untouched; reconcile it by hand "
                    "before running this migration."
                )
            existing = {tuple(index["column_names"]) for index in inspector.get_indexes(_TABLE)}
            _create_indexes([column for column in _INDEXED_COLUMNS if (column,) not in existing])
            return

    op.create_table(
        _TABLE,
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("entry_type", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("turn_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "entry_type IN ('milestone', 'emotional_event', 'promise', 'running_project')",
            name="ck_relationship_timeline_entries_entry_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'resolved', 'broken', 'completed', 'abandoned')",
            name="ck_relationship_timeline_entries_status",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_indexes(_INDEXED_COLUMNS)


def downgrade() -> None:
    for column in reversed(_INDEXED_COLUMNS):
        op.drop_index(_index_name(column), table_name=_TABLE)
    op.drop_table(_TABLE)
