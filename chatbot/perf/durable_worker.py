"""Durable background job worker — claims persisted jobs with PostgreSQL row locking.

The in-process ``BackgroundQueue`` (``chatbot/perf/background.py``) loses queued
and retrying work when the process exits; this worker processes the durable
``background_jobs`` table instead (Phase 2 design, Task 2.2). The volatile
queue, its handlers and its lifecycle stay untouched.

Claiming: a bounded batch of due jobs is claimed transactionally with
``FOR UPDATE SKIP LOCKED`` — other workers cannot claim the same live row, and
the claim (status ``running`` + ``claimed_at`` + attempt increment) is committed
before any handler is invoked. An expired ``running`` claim becomes eligible
again after the fixed lease timeout, so process death is recoverable without
distributed locks. Handlers run outside the claim transaction; processing is
therefore at-least-once, not exactly-once, and handlers must be idempotent.

Failure: on handler error the row returns to pending with a bounded exponential
backoff while attempts remain; after the finite attempt limit it persists
``failed`` with ``completed_at`` and a bounded ``last_error``. Terminal work is
never silently discarded.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa

from chatbot.db.models import BackgroundJob

logger = logging.getLogger(__name__)

# A handler receives the claimed job row; it loads persisted turn data itself
# (Task 2.2c) and must be idempotent — processing is at-least-once.
DurableJobHandler = Callable[[BackgroundJob], Awaitable[None]]


class DurableJobWorker:
    """Claims durable ``background_jobs`` rows and dispatches them to handlers.

    Designed to be run as an asyncio task, typically alongside a web server
    (Task 2.2c wires it into the app lifespan). Supports graceful shutdown via
    :class:`asyncio.Event`; on shutdown, rows left in ``running`` are recovered
    by the lease timeout after a restart.
    """

    def __init__(
        self,
        session_factory: Any,
        *,
        handlers: Mapping[str, DurableJobHandler] | None = None,
        default_handler: DurableJobHandler | None = None,
        batch_size: int = 10,
        lease_timeout_seconds: float = 300.0,
        max_attempts: int = 5,
        retry_backoff_base_seconds: float = 2.0,
        retry_backoff_cap_seconds: float = 60.0,
        poll_interval_seconds: float = 1.0,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")
        if lease_timeout_seconds <= 0:
            raise ValueError("lease_timeout_seconds must be greater than zero")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be greater than zero")
        if retry_backoff_base_seconds <= 0:
            raise ValueError("retry_backoff_base_seconds must be greater than zero")
        if retry_backoff_cap_seconds <= 0:
            raise ValueError("retry_backoff_cap_seconds must be greater than zero")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be greater than zero")

        self.session_factory = session_factory
        self.handlers: dict[str, DurableJobHandler] = dict(handlers or {})
        self.default_handler = default_handler
        self.batch_size = batch_size
        self.lease_timeout_seconds = lease_timeout_seconds
        self.max_attempts = max_attempts
        self.retry_backoff_base_seconds = retry_backoff_base_seconds
        self.retry_backoff_cap_seconds = retry_backoff_cap_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.stop_event = stop_event or asyncio.Event()

        self._jobs_processed: int = 0
        self._jobs_failed: int = 0
        self._jobs_recovered: int = 0
        self._started_at: str | None = None
        self._stopped_at: str | None = None
        self._run_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Handler dispatch interface (Task 2.2c registers memory handlers here)
    # ------------------------------------------------------------------

    def register_handler(self, task_type: str, handler: DurableJobHandler) -> None:
        """Register a handler for a task type. Last registration wins."""

        self.handlers[task_type] = handler

    # ------------------------------------------------------------------
    # Claiming and lease recovery
    # ------------------------------------------------------------------

    async def claim_due_jobs(self) -> list[BackgroundJob]:
        """Claim a bounded batch of due jobs transactionally.

        Due jobs are ``pending`` rows whose ``available_at`` has passed and
        ``running`` rows whose ``claimed_at`` is older than the lease timeout
        (recovery of abandoned claims). Rows are locked with
        ``FOR UPDATE SKIP LOCKED`` so concurrent workers cannot claim the same
        live row; the claim (status ``running`` + ``claimed_at`` + attempt
        increment) is committed before any handler is invoked.
        """

        now = datetime.now(timezone.utc)
        lease_cutoff = now - timedelta(seconds=self.lease_timeout_seconds)
        async with self.session_factory() as session:
            result = await session.execute(
                sa.select(BackgroundJob)
                .where(
                    sa.or_(
                        sa.and_(
                            BackgroundJob.status == "pending",
                            BackgroundJob.available_at <= now,
                        ),
                        sa.and_(
                            BackgroundJob.status == "running",
                            BackgroundJob.claimed_at <= lease_cutoff,
                        ),
                    )
                )
                .order_by(BackgroundJob.available_at)
                .limit(self.batch_size)
                .with_for_update(skip_locked=True)
            )
            jobs = list(result.scalars().all())
            recovered = sum(1 for job in jobs if job.status == "running")
            for job in jobs:
                job.status = "running"
                job.claimed_at = now
                job.attempt_count += 1
            await session.commit()
        self._jobs_recovered += recovered
        return jobs

    # ------------------------------------------------------------------
    # Terminal status operations
    # ------------------------------------------------------------------

    async def _mark_succeeded(self, job_id: Any) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            job = await session.get(BackgroundJob, job_id)
            if job is None:
                return
            job.status = "succeeded"
            job.completed_at = now
            await session.commit()

    async def _mark_failure(self, job: BackgroundJob, error: str) -> None:
        """Return a failed job to pending with bounded backoff, or persist it as failed.

        While attempts remain, the row is scheduled for retry with a bounded
        exponential delay; after the finite attempt limit the row persists
        ``failed`` with ``completed_at`` and the bounded ``last_error``.
        """

        now = datetime.now(timezone.utc)
        bounded = error[: BackgroundJob.__table__.c.last_error.type.length]
        async with self.session_factory() as session:
            row = await session.get(BackgroundJob, job.job_id)
            if row is None:
                return
            if row.attempt_count < self.max_attempts:
                row.status = "pending"
                row.available_at = now + timedelta(seconds=self._retry_delay(row.attempt_count))
                row.last_error = bounded
            else:
                row.status = "failed"
                row.completed_at = now
                row.last_error = bounded
            await session.commit()

    def _retry_delay(self, attempt_count: int) -> float:
        """Bounded exponential backoff for the given attempt count."""

        return min(
            self.retry_backoff_base_seconds * 2 ** (attempt_count - 1),
            self.retry_backoff_cap_seconds,
        )

    # ------------------------------------------------------------------
    # Dispatch and run loop
    # ------------------------------------------------------------------

    async def _run_one(self, job: BackgroundJob) -> None:
        handler = self.handlers.get(job.task_type) or self.default_handler
        if handler is None:
            logger.warning(
                "No handler registered for task_type=%s job_id=%s; returning to pending",
                job.task_type,
                job.job_id,
            )
            await self._mark_failure(job, f"no handler registered for task_type={job.task_type!r}")
            self._jobs_failed += 1
            return

        try:
            await handler(job)
        except Exception as exc:
            logger.warning(
                "Durable job %s (type=%s) attempt %d failed; returning to pending",
                job.job_id,
                job.task_type,
                job.attempt_count,
                exc_info=True,
            )
            await self._mark_failure(job, str(exc))
            self._jobs_failed += 1
        else:
            await self._mark_succeeded(job.job_id)
            self._jobs_processed += 1

    async def run(self) -> None:
        """Poll and process durable jobs until shutdown is requested.

        Unlike the in-process worker, this loop does not drain on shutdown: a
        job left in ``running`` is recovered by the lease timeout after a
        restart, so queued work is never lost.
        """

        self._started_at = _iso_timestamp()
        logger.info("DurableJobWorker started at %s", self._started_at)

        try:
            while not self.stop_event.is_set():
                claimed = await self.claim_due_jobs()
                if not claimed:
                    await asyncio.sleep(self.poll_interval_seconds)
                    continue
                for job in claimed:
                    await self._run_one(job)
        finally:
            self._stopped_at = _iso_timestamp()
            logger.info(
                "DurableJobWorker stopped at %s — processed=%d failed=%d recovered=%d",
                self._stopped_at,
                self._jobs_processed,
                self._jobs_failed,
                self._jobs_recovered,
            )

    def start(self) -> asyncio.Task[None]:
        """Start the worker loop as an asyncio task and return the task handle."""

        if self._run_task is not None and not self._run_task.done():
            return self._run_task

        self._run_task = asyncio.create_task(self.run())
        return self._run_task

    async def shutdown(self, *, timeout_seconds: float | None = None) -> None:
        """Signal the worker to stop and wait for its run loop to exit."""

        self.stop_event.set()
        if self._run_task is None:
            return

        try:
            if timeout_seconds is None:
                await self._run_task
            else:
                await asyncio.wait_for(
                    asyncio.shield(self._run_task),
                    timeout=timeout_seconds,
                )
        except TimeoutError:
            logger.warning(
                "DurableJobWorker shutdown timed out after %.2f seconds; cancelling",
                timeout_seconds,
            )
            self._run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._run_task

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    @property
    def jobs_processed(self) -> int:
        """Number of jobs marked succeeded."""

        return self._jobs_processed

    @property
    def jobs_failed(self) -> int:
        """Number of handler invocations that ended in a failure transition."""

        return self._jobs_failed

    @property
    def jobs_recovered(self) -> int:
        """Number of expired claims recovered by lease timeout."""

        return self._jobs_recovered


async def start_durable_job_worker(
    session_factory: Any,
    *,
    handlers: Mapping[str, DurableJobHandler] | None = None,
    default_handler: DurableJobHandler | None = None,
    **kwargs: Any,
) -> DurableJobWorker:
    """Convenience: create a :class:`DurableJobWorker` and begin its run loop as a background task."""

    worker = DurableJobWorker(
        session_factory,
        handlers=handlers,
        default_handler=default_handler,
        **kwargs,
    )
    worker.start()
    return worker


def _iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["DurableJobHandler", "DurableJobWorker", "start_durable_job_worker"]
