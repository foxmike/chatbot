"""The Alembic chain creates ``background_jobs`` (Phase 2 design, Task 2.2).

The durable job model (``BackgroundJob``) and its revision exist, but nothing
else in this task wires them: no worker, no processing, no conversation
integration. These tests initialize **fresh PostgreSQL databases** through the
project's own ``alembic upgrade head`` (a subprocess, exactly as it is run) and
never call ``metadata.create_all()`` for the schema under test.

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

from chatbot.db import models

ROOT = Path(__file__).resolve().parents[1]
REVISION = "b8d5e3f7a9c1"
PREVIOUS_HEAD = "a12f907bc341"
TABLE = "background_jobs"

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


def test_the_background_jobs_revision_extends_the_chain_and_is_the_only_head() -> None:
    scripts = _scripts()
    assert scripts.get_heads() == ["f3a9d6b1c4e7"]  # the self-model revision (Phase 3.2)
    assert scripts.get_revision("f3a9d6b1c4e7").down_revision == "d41e8a7c502b"
    assert scripts.get_revision("d41e8a7c502b").down_revision == "c93a14e2786b"
    assert scripts.get_revision("c93a14e2786b").down_revision == REVISION
    assert scripts.get_revision(REVISION).down_revision == PREVIOUS_HEAD
    assert scripts.get_revision(PREVIOUS_HEAD).down_revision == "f687a630a8d1"


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


def make_job(
    session_id: uuid.UUID,
    turn_id: str | None = None,
    task_type: str = "memory_extract",
    **kwargs: Any,
) -> models.BackgroundJob:
    return models.BackgroundJob(
        session_id=session_id,
        task_type=task_type,
        turn_id=turn_id or f"turn_{uuid.uuid4().hex[:12]}",
        payload={"message_ids": [str(uuid.uuid4())]},
        **kwargs,
    )


@needs_postgres
async def test_a_fresh_alembic_database_has_the_background_jobs_table_matching_the_model(
    fresh_database: Callable[..., Any],
) -> None:
    db: Fresh = await fresh_database(upgrade_to=REVISION)
    assert await db.scalar("select version_num from alembic_version") == REVISION

    def _include_object(obj: Any, name: str, type_: str, reflected: bool, compare_to: Any) -> bool:
        # Compare only the table under test: the pre-existing metadata-wide
        # comparison trips on the JSON server default of ``companion_identities``
        # (``json = unknown`` has no operator), which is unrelated to this task.
        if type_ == "table":
            return name == TABLE
        return True

    def inspect(connection: Any) -> dict[str, Any]:
        inspector = sa.inspect(connection)
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                "compare_server_default": True,
                "include_object": _include_object,
            },
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
            "unique": {tuple(u["column_names"]) for u in inspector.get_unique_constraints(TABLE)},
            "checks": {c["name"] for c in inspector.get_check_constraints(TABLE)},
            "differences": differences,
        }

    found = await db.run_sync(inspect)
    model_table = models.BackgroundJob.__table__

    assert found["exists"] is True
    assert found["differences"] == []
    assert set(found["columns"]) == {column.name for column in model_table.columns}
    for column in model_table.columns:
        assert found["columns"][column.name]["nullable"] == column.nullable, column.name
    assert found["pk"] == ["job_id"]

    by_referred_table = {fk["referred_table"]: fk for fk in found["fks"]}
    assert set(by_referred_table) == {"sessions", "users", "companions"}
    assert by_referred_table["sessions"]["constrained_columns"] == ["session_id"]
    assert by_referred_table["sessions"]["options"].get("ondelete") == "CASCADE"
    assert by_referred_table["users"]["constrained_columns"] == ["user_id"]
    assert by_referred_table["companions"]["constrained_columns"] == ["companion_id"]
    assert by_referred_table["companions"]["options"].get("ondelete") == "CASCADE"

    assert ("status", "available_at") in found["indexes"]
    assert found["unique"] == {("task_type", "turn_id")}
    assert found["checks"] == {"ck_background_jobs_status"}


@needs_postgres
async def test_a_job_insertion_defaults_and_lifecycle_fields(
    fresh_database: Callable[..., Any],
) -> None:
    db: Fresh = await fresh_database()
    _, sid = await make_session(db)

    async with db.factory() as session:
        job = make_job(sid)
        session.add(job)
        await session.commit()
        job_id = job.job_id

    async with db.factory() as session:
        stored = await session.get(models.BackgroundJob, job_id)
        assert stored is not None
        assert stored.status == "pending"
        assert stored.attempt_count == 0
        assert stored.available_at is not None
        assert stored.created_at is not None
        assert stored.updated_at is not None
        assert stored.claimed_at is None
        assert stored.completed_at is None
        assert stored.last_error is None

        # Claiming: a worker's claim updates the lease fields.
        stored.status = "running"
        stored.claimed_at = stored.created_at
        await session.commit()

    async with db.factory() as session:
        stored = await session.get(models.BackgroundJob, job_id)
        assert stored is not None
        assert stored.status == "running"
        assert stored.claimed_at is not None
        # Success marks completion only after output persistence.
        stored.status = "succeeded"
        stored.completed_at = stored.updated_at
        await session.commit()

    async with db.factory() as session:
        stored = await session.get(models.BackgroundJob, job_id)
        assert stored is not None
        assert stored.status == "succeeded"
        assert stored.completed_at is not None


@needs_postgres
async def test_job_identity_is_unique_per_persisted_turn_and_work_type(
    fresh_database: Callable[..., Any],
) -> None:
    db: Fresh = await fresh_database()
    _, sid = await make_session(db)

    async with db.factory() as session:
        job = make_job(sid, turn_id="turn_1")
        session.add(job)
        await session.commit()
        first_job_id = job.job_id

    async with db.factory() as session:
        # Same persisted turn and work type: conflict-safe identity, not a
        # second job for the same turn's work.
        session.add(make_job(sid, turn_id="turn_1"))
        with pytest.raises(sa.exc.IntegrityError):
            await session.commit()
        await session.rollback()

        # The same turn with a different work type is a distinct job.
        session.add(make_job(sid, turn_id="turn_1", task_type="consolidate"))
        await session.commit()

    async with db.factory() as session:
        # A different persisted turn with the same work type is a distinct job.
        session.add(make_job(sid, turn_id="turn_2"))
        await session.commit()

    async with db.factory() as session:
        count = await session.scalar(
            sa.select(sa.func.count()).select_from(models.BackgroundJob)
        )
        assert count == 3
        turns = await session.scalars(
            sa.select(models.BackgroundJob.turn_id).order_by(models.BackgroundJob.turn_id)
        )
        assert list(turns) == ["turn_1", "turn_1", "turn_2"]
        first = await session.get(models.BackgroundJob, first_job_id)
        assert first is not None and first.task_type == "memory_extract"


@needs_postgres
async def test_job_status_is_bounded_by_the_database(
    fresh_database: Callable[..., Any],
) -> None:
    db: Fresh = await fresh_database()
    _, sid = await make_session(db)

    async with db.factory() as session:
        session.add(make_job(sid, status="queued"))
        with pytest.raises(sa.exc.IntegrityError):
            await session.commit()
        await session.rollback()


@needs_postgres
async def test_session_deletion_removes_job_rows_and_companion_is_nullable(
    fresh_database: Callable[..., Any],
) -> None:
    db: Fresh = await fresh_database()
    uid, sid = await make_session(db)

    async with db.factory() as session:
        session.add(make_job(sid, turn_id="turn_1"))
        session.add(make_job(sid, turn_id="turn_2", task_type="consolidate"))
        await session.commit()

    async with db.factory() as session:
        # A session deletion must delete its jobs, not make the session
        # impossible to delete.
        session.add(await session.get(models.Session, sid))
        await session.delete(await session.get(models.Session, sid))
        await session.commit()

    async with db.factory() as session:
        count = await session.scalar(
            sa.select(sa.func.count()).select_from(models.BackgroundJob)
        )
        assert count == 0
        assert await session.get(models.User, uid) is not None
