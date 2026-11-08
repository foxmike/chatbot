"""The Alembic chain creates ``relationship_timeline_entries``.

``RelationshipTimelineEntryModel`` and its writers existed, but no revision
created the table, so a database built with ``alembic upgrade head`` could not
store timeline entries. These tests initialize **fresh PostgreSQL databases**
through the project's own ``alembic upgrade head`` (a subprocess, exactly as it
is run) and never call ``metadata.create_all()`` for the schema under test.

They need a PostgreSQL server with pgvector and run only when ``PHASE0_PG_URL``
names a **disposable** instance (database ``phase0_accept`` on a non-default
port, e.g. ``pgvector/pgvector:pg16`` with RAM-backed storage). Each test
creates its own throwaway database next to it and drops it afterwards; nothing
else on the server is touched. The revision-chain checks need no database.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from chatbot.continuity.timeline import RelationshipTimeline, RelationshipTimelineEntryModel
from chatbot.db import models

ROOT = Path(__file__).resolve().parents[1]
REVISION = "f687a630a8d1"
PREVIOUS_HEAD = "e7a4c2d9f1b3"
TABLE = "relationship_timeline_entries"

PG_URL = os.environ.get("PHASE0_PG_URL")
needs_postgres = pytest.mark.skipif(
    not PG_URL, reason="set PHASE0_PG_URL to a disposable PostgreSQL 16 + pgvector instance"
)


# --------------------------------------------------------------------------
# Revision chain (no database)
# --------------------------------------------------------------------------


def _scripts() -> ScriptDirectory:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return ScriptDirectory.from_config(config)


def test_the_timeline_revision_extends_the_chain_and_is_the_only_head() -> None:
    scripts = _scripts()
    # The chain head is the conversational-threads revision (Phase 4.3a); the
    # self-model revision remains the one below it.
    assert scripts.get_heads() == ["e5f2a7c9b8d4"]
    assert scripts.get_revision("e5f2a7c9b8d4").down_revision == "f3a9d6b1c4e7"
    assert scripts.get_revision("f3a9d6b1c4e7").down_revision == "d41e8a7c502b"
    assert scripts.get_revision("d41e8a7c502b").down_revision == "c93a14e2786b"
    assert scripts.get_revision("a12f907bc341").down_revision == REVISION
    assert scripts.get_revision(REVISION).down_revision == PREVIOUS_HEAD


# --------------------------------------------------------------------------
# Fresh PostgreSQL databases
# --------------------------------------------------------------------------


def _require_disposable(url: str) -> None:
    target = make_url(url)
    if not (
        target.drivername.startswith("postgresql")
        and target.host in {"127.0.0.1", "localhost"}
        and target.port not in (None, 5432)
        and target.database == "phase0_accept"
    ):
        raise RuntimeError(
            "PHASE0_PG_URL must name database 'phase0_accept' on 127.0.0.1/localhost with a "
            "non-default port; refusing to create or drop databases anywhere else"
        )


def alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the project's own Alembic against *url* (env.py reads DATABASE_URL)."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=180,
    )


class Fresh:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = create_async_engine(url)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def run_sync(self, function: Callable[..., Any]) -> Any:
        async with self.engine.connect() as connection:
            result = await connection.run_sync(function)
            await connection.commit()  # DDL is transactional on PostgreSQL
            return result

    async def scalar(self, sql: str, **params: Any) -> Any:
        async with self.factory() as db:
            return (await db.execute(text(sql), params)).scalar()


@pytest.fixture
async def fresh_database() -> AsyncIterator[Callable[[], Any]]:
    """Factory of empty databases (one per call), all dropped at the end."""
    assert PG_URL
    _require_disposable(PG_URL)
    admin = create_async_engine(PG_URL, isolation_level="AUTOCOMMIT")
    created: list[tuple[str, Fresh]] = []

    async def create(upgrade_to: str | None = "head") -> Fresh:
        name = f"phase0_mig_{uuid.uuid4().hex[:12]}"
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
        database = Fresh(make_url(PG_URL).set(database=name).render_as_string(hide_password=False))
        created.append((name, database))
        if upgrade_to:
            done = alembic(database.url, "upgrade", upgrade_to)
            assert done.returncode == 0, done.stderr[-2000:]
        return database

    yield create  # type: ignore[misc]

    for name, database in created:
        await database.engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    await admin.dispose()


async def make_session(db: Fresh) -> tuple[uuid.UUID, uuid.UUID]:
    uid, sid = uuid.uuid4(), uuid.uuid4()
    async with db.factory() as session:
        session.add(
            models.User(
                id=uid, external_id=f"ext_{uid.hex}", username=f"u_{uid.hex[:8]}",
                email=f"{uid.hex[:8]}@example.com", display_name="U", password_hash="h",
            )
        )
        session.add(models.Session(id=sid, user_id=uid, title="t"))
        await session.commit()
    return uid, sid


@needs_postgres
async def test_a_fresh_alembic_database_has_the_timeline_table_matching_the_model(
    fresh_database: Callable[..., Any],
) -> None:
    db: Fresh = await fresh_database(upgrade_to=REVISION)
    assert await db.scalar("select version_num from alembic_version") == REVISION

    def inspect(connection: Any) -> dict[str, Any]:
        inspector = sa.inspect(connection)
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        differences = [
            d for d in compare_metadata(context, models.Base.metadata) if TABLE in repr(d)
        ]
        return {
            "exists": inspector.has_table(TABLE),
            "columns": {c["name"]: c for c in inspector.get_columns(TABLE)},
            "pk": inspector.get_pk_constraint(TABLE)["constrained_columns"],
            "fks": inspector.get_foreign_keys(TABLE),
            "indexes": {tuple(i["column_names"]): i for i in inspector.get_indexes(TABLE)},
            "checks": {c["name"] for c in inspector.get_check_constraints(TABLE)},
            "differences": differences,
        }

    found = await db.run_sync(inspect)
    model_table = RelationshipTimelineEntryModel.__table__

    assert found["exists"]
    # Alembic's own comparison of the live schema with the model finds nothing to change.
    assert found["differences"] == []
    assert set(found["columns"]) == {column.name for column in model_table.columns}
    for column in model_table.columns:
        assert found["columns"][column.name]["nullable"] == column.nullable, column.name
    assert found["pk"] == ["id"]
    (foreign_key,) = found["fks"]
    assert foreign_key["referred_table"] == "sessions"
    assert foreign_key["constrained_columns"] == ["session_id"]
    assert foreign_key["options"].get("ondelete") == "CASCADE"
    assert {("session_id",), ("entry_type",), ("turn_id",), ("status",)} <= set(found["indexes"])
    assert found["checks"] == {
        "ck_relationship_timeline_entries_entry_type",
        "ck_relationship_timeline_entries_status",
    }


@needs_postgres
async def test_timeline_entries_persist_and_read_back_without_create_all(
    fresh_database: Callable[..., Any],
) -> None:
    db: Fresh = await fresh_database(upgrade_to=REVISION)
    _, sid = await make_session(db)
    timeline = RelationshipTimeline(session_factory=db.factory)

    entry = await timeline.add_timeline_entry(
        str(sid), "milestone", "New job", "Got a job", "I got a new job today!", str(uuid.uuid4()),
        tags=["work"], metadata={"source": "test"},
    )
    stored = await timeline.get_timeline(str(sid))

    assert [e.entry_id for e in stored] == [entry.entry_id]
    assert stored[0].status == "active"  # database default
    assert stored[0].tags == ["work"] and stored[0].metadata == {"source": "test"}
    assert await db.scalar(f"select count(*) from {TABLE}") == 1


@needs_postgres
async def test_a_turn_session_entry_commits_with_the_turn_and_rolls_back_with_it(
    fresh_database: Callable[..., Any],
) -> None:
    db: Fresh = await fresh_database(upgrade_to=REVISION)
    _, sid = await make_session(db)
    timeline = RelationshipTimeline(session_factory=db.factory)

    async with db.factory() as turn:  # the path TimelineUpdateStep uses
        await timeline.add_timeline_entry(
            str(sid), "promise", "p", "p", "p", str(uuid.uuid4()), session=turn
        )
        await turn.rollback()
    assert await db.scalar(f"select count(*) from {TABLE}") == 0

    async with db.factory() as turn:
        await timeline.add_timeline_entry(
            str(sid), "promise", "p", "p", "p", str(uuid.uuid4()), session=turn
        )
        await turn.commit()
    assert await db.scalar(f"select count(*) from {TABLE}") == 1


@needs_postgres
async def test_the_database_enforces_the_timeline_constraints(
    fresh_database: Callable[..., Any],
) -> None:
    db: Fresh = await fresh_database(upgrade_to=REVISION)
    _, sid = await make_session(db)
    insert = (
        f"insert into {TABLE} (session_id, entry_type, title, description, content, turn_id, tags, metadata) "
        "values (:sid, :kind, 't', 'd', 'c', :turn, '[]', '{}')"
    )

    async with db.factory() as session:
        await session.execute(text(insert), {"sid": sid, "kind": "milestone", "turn": uuid.uuid4()})
        await session.commit()
    for params in (
        {"sid": sid, "kind": "not-a-type", "turn": uuid.uuid4()},  # check constraint
        {"sid": uuid.uuid4(), "kind": "milestone", "turn": uuid.uuid4()},  # foreign key
    ):
        async with db.factory() as session:
            with pytest.raises(sa.exc.IntegrityError):
                await session.execute(text(insert), params)
                await session.commit()


@needs_postgres
async def test_deleting_a_session_deletes_its_timeline_entries(
    fresh_database: Callable[..., Any],
) -> None:
    """Without ON DELETE CASCADE a session holding a milestone could not be deleted."""
    db: Fresh = await fresh_database(upgrade_to=REVISION)
    _, sid = await make_session(db)
    other_uid, other_sid = await make_session(db)
    timeline = RelationshipTimeline(session_factory=db.factory)
    for session_id in (sid, other_sid):
        await timeline.add_timeline_entry(
            str(session_id), "milestone", "m", "m", "m", str(uuid.uuid4())
        )

    async with db.factory() as session:  # the way DELETE /sessions/{id} deletes
        row = (await session.execute(sa.select(models.Session).where(models.Session.id == sid))).scalar_one()
        await session.delete(row)
        await session.commit()

    remaining = await db.scalar(f"select count(*) from {TABLE}")
    assert remaining == 1
    assert await db.scalar(f"select session_id from {TABLE}") == other_sid


@needs_postgres
async def test_downgrade_removes_only_the_timeline_table_and_upgrade_restores_it(
    fresh_database: Callable[..., Any],
) -> None:
    db: Fresh = await fresh_database(upgrade_to=REVISION)  # a throwaway database, never an existing one
    users_before = await db.scalar("select count(*) from information_schema.tables where table_schema='public'")

    done = alembic(db.url, "downgrade", PREVIOUS_HEAD)
    assert done.returncode == 0, done.stderr[-2000:]
    assert await db.scalar("select to_regclass('public.relationship_timeline_entries')") is None
    assert await db.scalar("select count(*) from information_schema.tables where table_schema='public'") == users_before - 1
    assert await db.scalar("select version_num from alembic_version") == PREVIOUS_HEAD

    done = alembic(db.url, "upgrade", REVISION)
    assert done.returncode == 0, done.stderr[-2000:]
    assert await db.scalar("select to_regclass('public.relationship_timeline_entries')") is not None


# --------------------------------------------------------------------------
# A deployment that already has the table
# --------------------------------------------------------------------------


@needs_postgres
async def test_an_existing_compatible_table_is_adopted_with_its_rows_untouched(
    fresh_database: Callable[..., Any],
) -> None:
    """Simulates a table created from the model's metadata before this revision existed."""
    db: Fresh = await fresh_database(upgrade_to=PREVIOUS_HEAD)
    _, sid = await make_session(db)
    await db.run_sync(lambda c: RelationshipTimelineEntryModel.__table__.create(c))
    async with db.factory() as session:  # a table that lacks the indexes the model declares
        for column in ("session_id", "entry_type", "turn_id", "status"):
            await session.execute(text(f"drop index ix_{TABLE}_{column}"))
        await session.commit()
    assert await db.scalar("select count(*) from pg_indexes where tablename = :t and indexname like 'ix_%'", t=TABLE) == 0
    turn = uuid.uuid4()
    async with db.factory() as session:
        await session.execute(
            text(
                f"insert into {TABLE} (session_id, entry_type, title, description, content, turn_id, tags, metadata) "
                "values (:sid, 'milestone', 'kept', 'd', 'c', :turn, '[]', '{}')"
            ),
            {"sid": sid, "turn": turn},
        )
        await session.commit()

    done = alembic(db.url, "upgrade", REVISION)

    assert done.returncode == 0, done.stderr[-2000:]
    assert await db.scalar("select version_num from alembic_version") == REVISION
    assert await db.scalar(f"select title from {TABLE}") == "kept"
    assert await db.scalar(f"select count(*) from {TABLE}") == 1
    # the indexes the pre-existing table lacked are added, once each
    assert await db.scalar(
        "select count(*) from pg_indexes where tablename = :t and indexname like 'ix_%'", t=TABLE
    ) == 4


@needs_postgres
async def test_an_existing_incompatible_table_stops_the_upgrade_and_is_left_untouched(
    fresh_database: Callable[..., Any],
) -> None:
    db: Fresh = await fresh_database(upgrade_to=PREVIOUS_HEAD)
    _, sid = await make_session(db)
    async with db.factory() as session:
        await session.execute(
            text(
                f"create table {TABLE} (id uuid primary key, session_id uuid not null "
                "references sessions(id), entry_type text)"
            )
        )
        await session.execute(
            text(f"insert into {TABLE} (id, session_id, entry_type) values (:i, :s, 'milestone')"),
            {"i": uuid.uuid4(), "s": sid},
        )
        await session.commit()

    done = alembic(db.url, "upgrade", REVISION)

    assert done.returncode != 0
    assert "does not match the application model" in done.stderr
    assert "missing column 'title'" in done.stderr
    assert await db.scalar("select version_num from alembic_version") == PREVIOUS_HEAD
    assert await db.scalar(f"select count(*) from {TABLE}") == 1  # rows untouched
