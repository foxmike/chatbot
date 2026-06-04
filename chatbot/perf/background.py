"""Lightweight async background task queue for off-path work — memory extraction, consolidation, and post-turn processing."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

logger = logging.getLogger(__name__)

TaskType = Literal["memory_extract", "consolidate", "sentiment", "index", "custom"]


@dataclass(slots=True)
class BackgroundTask:
    task_id: str
    task_type: TaskType
    payload: dict[str, Any]
    created_at: str
    priority: int = 0
    max_retries: int = 3


class BackgroundQueue:
    """In-process async queue for deferred post-turn work."""

    def __init__(self, dequeue_timeout_seconds: float = 0.05) -> None:
        if dequeue_timeout_seconds <= 0:
            raise ValueError("dequeue_timeout_seconds must be greater than zero")

        self.high_priority: asyncio.Queue[BackgroundTask] = asyncio.Queue()
        self.normal: asyncio.Queue[BackgroundTask] = asyncio.Queue()
        self._dequeue_timeout_seconds = dequeue_timeout_seconds

    async def enqueue(self, task: BackgroundTask) -> None:
        """Add a task to the high-priority or normal queue."""

        target = self.high_priority if task.priority > 0 else self.normal
        await target.put(task)

    async def dequeue(self) -> BackgroundTask | None:
        """Return the next queued task, preferring high-priority work."""

        task = self._get_nowait(self.high_priority)
        if task is not None:
            return task

        task = self._get_nowait(self.normal)
        if task is not None:
            return task

        task = await self._get_with_timeout(self.high_priority)
        if task is not None:
            return task

        return await self._get_with_timeout(self.normal)

    async def enqueue_memory_extraction(
        self,
        user_id: str,
        session_id: str,
        turn_context: dict,
    ) -> None:
        await self.enqueue(
            BackgroundTask(
                task_id=_task_id(),
                task_type="memory_extract",
                payload={
                    "user_id": user_id,
                    "session_id": session_id,
                    "turn_context": turn_context,
                },
                created_at=_iso_timestamp(),
            )
        )

    async def enqueue_consolidation(self, user_id: str) -> None:
        await self.enqueue(
            BackgroundTask(
                task_id=_task_id(),
                task_type="consolidate",
                payload={"user_id": user_id},
                created_at=_iso_timestamp(),
            )
        )

    async def enqueue_sentiment(self, user_id: str, session_id: str, text: str) -> None:
        await self.enqueue(
            BackgroundTask(
                task_id=_task_id(),
                task_type="sentiment",
                payload={
                    "user_id": user_id,
                    "session_id": session_id,
                    "text": text,
                },
                created_at=_iso_timestamp(),
            )
        )

    async def enqueue_index(self, user_id: str, doc_id: str) -> None:
        await self.enqueue(
            BackgroundTask(
                task_id=_task_id(),
                task_type="index",
                payload={
                    "user_id": user_id,
                    "doc_id": doc_id,
                },
                created_at=_iso_timestamp(),
            )
        )

    @property
    def pending_count(self) -> int:
        return self.high_priority.qsize() + self.normal.qsize()

    def _get_nowait(self, queue: asyncio.Queue[BackgroundTask]) -> BackgroundTask | None:
        try:
            return queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def _get_with_timeout(
        self,
        queue: asyncio.Queue[BackgroundTask],
    ) -> BackgroundTask | None:
        try:
            return await asyncio.wait_for(
                queue.get(),
                timeout=self._dequeue_timeout_seconds,
            )
        except TimeoutError:
            return None


async def run_background_worker(
    queue: BackgroundQueue,
    stop_event: asyncio.Event,
    handler: Callable[[BackgroundTask], Awaitable[Any]],
) -> None:
    """Consume background tasks until shutdown is requested and the queue drains."""

    while not stop_event.is_set() or queue.pending_count > 0:
        task = await queue.dequeue()
        if task is None:
            await asyncio.sleep(0.05)
            continue

        try:
            await handler(task)
        except Exception:
            task.max_retries -= 1
            if task.max_retries > 0:
                logger.warning(
                    "Background task %s failed; retrying with %s retries remaining",
                    task.task_id,
                    task.max_retries,
                    exc_info=True,
                )
                await queue.enqueue(task)
                continue

            logger.error(
                "Background task %s failed and exhausted retries; dropping",
                task.task_id,
                exc_info=True,
            )


def _task_id() -> str:
    return str(uuid.uuid4())


def _iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["BackgroundQueue", "BackgroundTask", "run_background_worker"]
