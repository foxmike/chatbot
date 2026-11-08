"""Focused tests for the durable background job worker (Phase 2 design, Task 2.2b).

The worker claims persisted ``background_jobs`` rows with PostgreSQL row
locking (``FOR UPDATE SKIP LOCKED``), recovers abandoned claims by lease
timeout, and transitions rows through retry, backoff and terminal failure.
These tests initialize fresh PostgreSQL databases through the project's own
``alembic upgrade head`` (the same fixtures as ``test_timeline_migration``).
They need a PostgreSQL server with pgvector and run only when ``PHASE0_PG_URL``
names a disposable instance. No migration acceptance is repeated here — the
schema itself is covered by ``test_background_jobs_migration.py``.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from test_timeline_migration import fresh_database, needs_postgres  # noqa: F401

from chatbot.db.models import BackgroundJob, Session, User
from chatbot.perf.durable_worker import DurableJobWorker


async def make_session(db: Any) -> uuid.UUID:
    uid, sid = uuid.uuid4(), uuid.uuid4()
    async with db.factory() as session:
        session.add(
            User(
                id=uid, external_id=f"ext_{uid.hex}", username=f"u_{uid.hex[:8]}",
                email=f"{uid.hex[:8]}@example.com", display_name="U", password_hash="h",
            )
        )
        session.add(Session(id=sid, user_id=uid, title="t"))
        await session.commit()
    return sid


async def add_job(
    db: Any,
    session_id: uuid.UUID,
    turn_id: str,
    task_type: str = "memory_extract",
    **kwargs: Any,
) -> uuid.UUID:
    async with db.factory() as session:
        job = BackgroundJob(
            session_id=session_id,
            task_type=task_type,
            turn_id=turn_id,
            payload={"message_ids": [str(uuid.uuid4())]},
            **kwargs,
        )
        session.add(job)
        await session.commit()
        return job.job_id


async def get_job(db: Any, job_id: uuid.UUID) -> BackgroundJob:
    async with db.factory() as session:
        stored = await session.get(BackgroundJob, job_id)
        assert stored is not None
        return stored


async def run_until_terminal(
    worker: DurableJobWorker,
    db: Any,
    job_id: uuid.UUID,
    timeout_seconds: float = 5.0,
) -> None:
    """Run the worker loop until the job reaches a terminal DB state, then stop it.

    The worker polls in a background task; the test polls the DB for the job's
    terminal status and then sets the stop event, so the loop exits cleanly
    without depending on the worker's own timing.
    """

    worker_task = asyncio.create_task(worker.run())
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        async with db.factory() as session:
            stored = await session.get(BackgroundJob, job_id)
            if stored is not None and stored.status in ("succeeded", "failed"):
                break
        await asyncio.sleep(0.01)
    worker.stop_event.set()
    await worker_task


def _worker(db: Any, **kwargs: Any) -> DurableJobWorker:
    return DurableJobWorker(db.factory, **kwargs)


# --------------------------------------------------------------------------
# Claiming eligible jobs
# --------------------------------------------------------------------------


@needs_postgres
async def test_claiming_eligible_jobs_claims_only_due_pending_jobs(fresh_database):
    db = await fresh_database()
    sid = await make_session(db)
    now = datetime.now(timezone.utc)
    due_job = await add_job(db, sid, "t1")
    # A pending job whose available_at is in the future is not due.
    await add_job(db, sid, "t2", available_at=now + timedelta(hours=1))
    # A succeeded job is not claimable.
    await add_job(db, sid, "t3", status="succeeded", completed_at=now)

    worker = _worker(db)
    claimed = await worker.claim_due_jobs()

    assert [job.job_id for job in claimed] == [due_job]
    assert claimed[0].status == "running"
    assert claimed[0].attempt_count == 1
    assert claimed[0].claimed_at is not None
    stored = await get_job(db, due_job)
    assert stored.status == "running"
    assert stored.attempt_count == 1


@needs_postgres
async def test_a_running_claim_is_not_claimed_again_before_lease_expiry(fresh_database):
    db = await fresh_database()
    sid = await make_session(db)
    job_id = await add_job(db, sid, "t1")

    worker = _worker(db)
    claimed = await worker.claim_due_jobs()
    assert [job.job_id for job in claimed] == [job_id]

    # The live claim is running, not pending: another claim pass finds nothing.
    assert await worker.claim_due_jobs() == []


# --------------------------------------------------------------------------
# Preventing duplicate concurrent claims
# --------------------------------------------------------------------------


@needs_postgres
async def test_a_locked_row_is_skipped_by_a_concurrent_claim(fresh_database):
    db = await fresh_database()
    sid = await make_session(db)
    first_job = await add_job(db, sid, "t1")
    second_job = await add_job(db, sid, "t2")

    # A concurrent worker holds a row lock on the first job inside an open
    # transaction; a claim with SKIP LOCKED must skip that row and claim the
    # second one instead.
    async with db.engine.connect() as connection:
        outer = await connection.begin()
        locked = await connection.execute(
            sa.select(BackgroundJob)
            .where(BackgroundJob.job_id == first_job)
            .with_for_update()
        )
        assert locked.scalar_one() is not None

        worker = _worker(db)
        claimed = await worker.claim_due_jobs()
        assert [job.job_id for job in claimed] == [second_job]
        await outer.rollback()

    async with db.factory() as session:
        stored = await session.get(BackgroundJob, first_job)
        assert stored is not None
        assert stored.status == "pending"


@needs_postgres
async def test_two_workers_cannot_claim_the_same_job(fresh_database):
    db = await fresh_database()
    sid = await make_session(db)
    job_id = await add_job(db, sid, "t1")

    first = _worker(db)
    second = _worker(db)
    claimed_by_first = await first.claim_due_jobs()
    claimed_by_second = await second.claim_due_jobs()

    assert [job.job_id for job in claimed_by_first] == [job_id]
    assert claimed_by_second == []


# --------------------------------------------------------------------------
# Successful completion
# --------------------------------------------------------------------------


@needs_postgres
async def test_a_successful_job_is_marked_succeeded(fresh_database):
    db = await fresh_database()
    sid = await make_session(db)
    job_id = await add_job(db, sid, "t1")
    handled: list[uuid.UUID] = []

    async def handler(job: BackgroundJob) -> None:
        handled.append(job.job_id)

    worker = _worker(db, handlers={"memory_extract": handler}, poll_interval_seconds=0.01)
    await run_until_terminal(worker, db, job_id)

    assert handled == [job_id]
    assert worker.jobs_processed == 1
    stored = await get_job(db, job_id)
    assert stored.status == "succeeded"
    assert stored.completed_at is not None
    assert stored.claimed_at is not None
    assert stored.attempt_count == 1


# --------------------------------------------------------------------------
# Retry after failure
# --------------------------------------------------------------------------


@needs_postgres
async def test_a_failed_job_is_retried_with_bounded_backoff_and_succeeds(fresh_database):
    db = await fresh_database()
    sid = await make_session(db)
    job_id = await add_job(db, sid, "t1")
    attempts = 0

    async def flaky_handler(job: BackgroundJob) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("boom")

    worker = _worker(
        db,
        handlers={"memory_extract": flaky_handler},
        retry_backoff_base_seconds=0.01,
        retry_backoff_cap_seconds=0.02,
        poll_interval_seconds=0.01,
    )
    await run_until_terminal(worker, db, job_id)

    assert attempts == 2
    assert worker.jobs_processed == 1
    stored = await get_job(db, job_id)
    assert stored.status == "succeeded"
    assert stored.completed_at is not None
    assert stored.attempt_count == 2
    # The recorded error from the failed attempt survives into the success.
    assert stored.last_error == "boom"


@needs_postgres
async def test_a_failed_job_returns_to_pending_with_a_delayed_available_at(fresh_database):
    db = await fresh_database()
    sid = await make_session(db)
    job_id = await add_job(db, sid, "t1")

    async def failing_handler(job: BackgroundJob) -> None:
        raise RuntimeError("boom")

    worker = _worker(
        db,
        handlers={"memory_extract": failing_handler},
        retry_backoff_base_seconds=0.5,
        retry_backoff_cap_seconds=0.5,
        poll_interval_seconds=0.01,
    )
    claimed = await worker.claim_due_jobs()
    assert [job.job_id for job in claimed] == [job_id]
    await worker._run_one(claimed[0])

    stored = await get_job(db, job_id)
    assert stored.status == "pending"
    assert stored.attempt_count == 1
    assert stored.last_error == "boom"
    assert stored.completed_at is None
    assert stored.available_at is not None
    # The retry delay is bounded and in the future.
    assert stored.available_at > datetime.now(timezone.utc) - timedelta(seconds=1)
    assert stored.available_at <= datetime.now(timezone.utc) + timedelta(seconds=1)


# --------------------------------------------------------------------------
# Maximum attempts
# --------------------------------------------------------------------------


@needs_postgres
async def test_a_job_exhausting_attempts_is_marked_failed(fresh_database):
    db = await fresh_database()
    sid = await make_session(db)
    job_id = await add_job(db, sid, "t1")
    attempts = 0

    async def failing_handler(job: BackgroundJob) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("nope")

    worker = _worker(
        db,
        handlers={"memory_extract": failing_handler},
        max_attempts=3,
        retry_backoff_base_seconds=0.01,
        retry_backoff_cap_seconds=0.01,
        poll_interval_seconds=0.01,
    )
    await run_until_terminal(worker, db, job_id)

    assert attempts == 3
    assert worker.jobs_processed == 0
    stored = await get_job(db, job_id)
    assert stored.status == "failed"
    assert stored.completed_at is not None
    assert stored.attempt_count == 3
    assert stored.last_error == "nope"


@needs_postgres
async def test_a_job_with_no_registered_handler_is_not_silently_dropped(fresh_database):
    db = await fresh_database()
    sid = await make_session(db)
    job_id = await add_job(db, sid, "t1", task_type="unregistered_type")


    worker = _worker(
        db,
        handlers={},
        max_attempts=1,
        retry_backoff_base_seconds=0.01,
        retry_backoff_cap_seconds=0.01,
        poll_interval_seconds=0.01,
    )
    await run_until_terminal(worker, db, job_id)

    stored = await get_job(db, job_id)
    assert stored.status == "failed"
    assert stored.completed_at is not None
    assert stored.last_error is not None and "no handler registered" in stored.last_error


@needs_postgres
async def test_the_recorded_last_error_is_bounded(fresh_database):
    db = await fresh_database()
    sid = await make_session(db)
    job_id = await add_job(db, sid, "t1")

    async def failing_handler(job: BackgroundJob) -> None:
        raise RuntimeError("x" * 5000)

    worker = _worker(
        db,
        handlers={"memory_extract": failing_handler},
        max_attempts=1,
        retry_backoff_base_seconds=0.01,
        retry_backoff_cap_seconds=0.01,
        poll_interval_seconds=0.01,
    )
    await run_until_terminal(worker, db, job_id)

    stored = await get_job(db, job_id)
    assert stored.status == "failed"
    assert stored.last_error is not None
    assert len(stored.last_error) == 2000


# --------------------------------------------------------------------------
# Recovery of expired claims
# --------------------------------------------------------------------------


@needs_postgres
async def test_an_expired_claim_is_recovered_by_the_lease_timeout(fresh_database):
    db = await fresh_database()
    sid = await make_session(db)
    now = datetime.now(timezone.utc)
    job_id = await add_job(
        db,
        sid,
        "t1",
        status="running",
        claimed_at=now - timedelta(hours=1),
        attempt_count=1,
    )

    worker = _worker(db, lease_timeout_seconds=60.0)
    claimed = await worker.claim_due_jobs()

    assert [job.job_id for job in claimed] == [job_id]
    assert claimed[0].attempt_count == 2
    assert claimed[0].claimed_at is not None
    assert worker.jobs_recovered == 1
    stored = await get_job(db, job_id)
    assert stored.status == "running"
    assert stored.attempt_count == 2


@needs_postgres
async def test_a_fresh_running_claim_is_not_recovered_before_lease_expiry(fresh_database):
    db = await fresh_database()
    sid = await make_session(db)
    job_id = await add_job(
        db,
        sid,
        "t1",
        status="running",
        claimed_at=datetime.now(timezone.utc),
        attempt_count=1,
    )

    worker = _worker(db, lease_timeout_seconds=300.0)
    assert await worker.claim_due_jobs() == []
    stored = await get_job(db, job_id)
    assert stored.status == "running"
    assert stored.attempt_count == 1
