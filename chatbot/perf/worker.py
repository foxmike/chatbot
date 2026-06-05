"""Background worker — consumes BackgroundQueue tasks, dispatches to registered handlers, supports graceful shutdown and retries."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from chatbot.perf.background import BackgroundQueue, BackgroundTask

logger = logging.getLogger(__name__)

TaskHandler = Callable[[BackgroundTask], Awaitable[Any]]


class BackgroundWorker:
    """Long-running worker loop that dequeues BackgroundTasks and dispatches them.

    Designed to be run as an asyncio task, typically alongside a web server.
    Supports graceful shutdown via :class:`asyncio.Event` and retries for failed
    tasks up to their configured ``max_retries``.
    """

    def __init__(
        self,
        queue: BackgroundQueue,
        *,
        stop_event: asyncio.Event | None = None,
        handlers: Mapping[str, TaskHandler] | None = None,
        default_handler: TaskHandler | None = None,
        idle_sleep_seconds: float = 0.05,
    ) -> None:
        if idle_sleep_seconds <= 0:
            raise ValueError("idle_sleep_seconds must be greater than zero")

        self.queue = queue
        self.stop_event = stop_event or asyncio.Event()
        self.handlers: dict[str, TaskHandler] = dict(handlers or {})
        self.default_handler = default_handler
        self.idle_sleep_seconds = idle_sleep_seconds

        self._tasks_processed: int = 0
        self._tasks_failed: int = 0
        self._tasks_dropped: int = 0
        self._started_at: str | None = None
        self._stopped_at: str | None = None

    async def run(self) -> None:
        """Begin the worker loop. Blocks until shutdown is requested and the queue drains."""

        self._started_at = _iso_timestamp()
        logger.info("BackgroundWorker started at %s", self._started_at)

        try:
            while not self.stop_event.is_set() or self.queue.pending_count > 0:
                task = await self.queue.dequeue()
                if task is None:
                    await asyncio.sleep(self.idle_sleep_seconds)
                    continue

                await self._dispatch(task)
        finally:
            self._stopped_at = _iso_timestamp()
            logger.info(
                "BackgroundWorker stopped at %s — processed=%d failed=%d dropped=%d",
                self._stopped_at,
                self._tasks_processed,
                self._tasks_failed,
                self._tasks_dropped,
            )

    async def shutdown(self) -> None:
        """Signal the worker to stop after draining the queue."""

        self.stop_event.set()

    async def _dispatch(self, task: BackgroundTask) -> None:
        handler = self.handlers.get(task.task_type) or self.default_handler
        if handler is None:
            logger.warning(
                "No handler registered for task_type=%s task_id=%s; dropping task",
                task.task_type,
                task.task_id,
            )
            self._tasks_dropped += 1
            return

        try:
            await handler(task)
        except Exception:
            task.max_retries -= 1
            if task.max_retries > 0:
                logger.warning(
                    "Background task %s (type=%s) failed; %s retries remaining",
                    task.task_id,
                    task.task_type,
                    task.max_retries,
                    exc_info=True,
                )
                await self.queue.enqueue(task)
            else:
                logger.error(
                    "Background task %s (type=%s) exhausted retries; dropping",
                    task.task_id,
                    task.task_type,
                    exc_info=True,
                )
                self._tasks_failed += 1
        else:
            self._tasks_processed += 1

    @property
    def tasks_processed(self) -> int:
        """Number of tasks successfully processed."""

        return self._tasks_processed

    @property
    def tasks_failed(self) -> int:
        """Number of tasks that exhausted retries and were dropped."""

        return self._tasks_failed

    @property
    def tasks_dropped(self) -> int:
        """Number of tasks dropped due to missing handlers."""

        return self._tasks_dropped

    @property
    def started_at(self) -> str | None:
        """ISO timestamp of when :meth:`run` was entered."""

        return self._started_at

    @property
    def stopped_at(self) -> str | None:
        """ISO timestamp of when :meth:`run` exited."""

        return self._stopped_at


async def start_worker(
    queue: BackgroundQueue,
    *,
    stop_event: asyncio.Event | None = None,
    handlers: Mapping[str, TaskHandler] | None = None,
    default_handler: TaskHandler | None = None,
    idle_sleep_seconds: float = 0.05,
) -> BackgroundWorker:
    """Convenience: create a :class:`BackgroundWorker` and begin its run loop as a background task."""

    worker = BackgroundWorker(
        queue,
        stop_event=stop_event,
        handlers=handlers,
        default_handler=default_handler,
        idle_sleep_seconds=idle_sleep_seconds,
    )
    asyncio.create_task(worker.run())
    return worker


def _iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["BackgroundWorker", "TaskHandler", "start_worker"]
