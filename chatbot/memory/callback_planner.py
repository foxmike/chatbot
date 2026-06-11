from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid
from typing import Any, Protocol


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class PlannedCallback:
    id: str
    session_id: str
    topic: str
    target_date: datetime | None = None
    priority: int = 0
    status: str = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)


class CallbackPlanner(Protocol):
    """Protocol for a long-term callback planning mechanism."""

    async def schedule(
        self,
        session_id: str,
        topic: str,
        *,
        target_date: datetime | None = None,
        priority: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> PlannedCallback:
        """Schedule a new topic to be raised in a future conversation."""
        ...

    async def retrieve_due_callbacks(
        self,
        session_id: str,
        *,
        current_time: datetime | None = None,
        limit: int = 5,
    ) -> list[PlannedCallback]:
        """Retrieve pending callbacks that are due to be raised."""
        ...

    async def mark_completed(
        self,
        callback_id: str,
    ) -> None:
        """Mark a callback as having been successfully raised/completed."""
        ...

    async def cancel(
        self,
        callback_id: str,
    ) -> None:
        """Cancel a planned callback before it is raised."""
        ...


class InMemoryCallbackPlanner:
    """An in-memory implementation of the CallbackPlanner protocol."""

    def __init__(self) -> None:
        self._callbacks: dict[str, PlannedCallback] = {}

    async def schedule(
        self,
        session_id: str,
        topic: str,
        *,
        target_date: datetime | None = None,
        priority: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> PlannedCallback:
        callback = PlannedCallback(
            id=str(uuid.uuid4()),
            session_id=session_id,
            topic=topic,
            target_date=_normalize_datetime(target_date),
            priority=priority,
            status="pending",
            metadata=dict(metadata or {}),
        )
        self._callbacks[callback.id] = callback
        return callback

    async def retrieve_due_callbacks(
        self,
        session_id: str,
        *,
        current_time: datetime | None = None,
        limit: int = 5,
    ) -> list[PlannedCallback]:
        now = _normalize_datetime(current_time) or datetime.now(timezone.utc)
            
        due = []
        for cb in self._callbacks.values():
            if cb.session_id != session_id:
                continue
            if cb.status != "pending":
                continue
            
            cb_target = _normalize_datetime(cb.target_date)
            if cb_target is not None and cb_target > now:
                continue
            
            due.append(cb)
            
        def _sort_key(cb: PlannedCallback) -> tuple[int, float]:
            dt = _normalize_datetime(cb.target_date)
            ts = dt.timestamp() if dt is not None else 0.0
            return (-cb.priority, ts)

        due.sort(key=_sort_key)
        return due[:limit]

    async def mark_completed(self, callback_id: str) -> None:
        if callback_id in self._callbacks:
            cb = self._callbacks[callback_id]
            self._callbacks[callback_id] = PlannedCallback(
                id=cb.id,
                session_id=cb.session_id,
                topic=cb.topic,
                target_date=cb.target_date,
                priority=cb.priority,
                status="completed",
                metadata=cb.metadata,
            )

    async def cancel(self, callback_id: str) -> None:
        if callback_id in self._callbacks:
            cb = self._callbacks[callback_id]
            self._callbacks[callback_id] = PlannedCallback(
                id=cb.id,
                session_id=cb.session_id,
                topic=cb.topic,
                target_date=cb.target_date,
                priority=cb.priority,
                status="cancelled",
                metadata=cb.metadata,
            )


__all__ = [
    "CallbackPlanner",
    "InMemoryCallbackPlanner",
    "PlannedCallback",
]
