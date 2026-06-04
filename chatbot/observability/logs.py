"""Structured event logs for memory-write, contradiction, and failed-retrieval events."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4


LogEventType = Literal[
    "memory_write",
    "contradiction",
    "failed_retrieval",
    "extraction_error",
    "consolidation_complete",
]


@dataclass(kw_only=True)
class LogEvent:
    event_id: str = field(default_factory=lambda: str(uuid4()))
    event_type: LogEventType
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: str | None = None
    turn_id: str | None = None
    user_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    severity: str = "info"


class StructuredLogger:
    def __init__(self, max_events: int = 10000) -> None:
        if max_events <= 0:
            raise ValueError("max_events must be positive")

        self.max_events = max_events
        self._events: deque[LogEvent] = deque()
        self._events_by_id: dict[str, LogEvent] = {}

    async def log_memory_write(
        self,
        session_id: str | None,
        turn_id: str | None,
        user_id: str | None,
        memory_id: str,
        content_length: int,
        extraction_time_ms: float,
    ) -> LogEvent:
        return self._store(
            LogEvent(
                event_type="memory_write",
                session_id=session_id,
                turn_id=turn_id,
                user_id=user_id,
                details={
                    "memory_id": memory_id,
                    "content_length": int(content_length),
                    "extraction_time_ms": float(extraction_time_ms),
                },
            )
        )

    async def log_contradiction(
        self,
        session_id: str | None,
        turn_id: str | None,
        user_id: str | None,
        original_memory_id: str,
        new_memory_id: str,
        conflict_fields: list[str],
    ) -> LogEvent:
        return self._store(
            LogEvent(
                event_type="contradiction",
                session_id=session_id,
                turn_id=turn_id,
                user_id=user_id,
                details={
                    "original_memory_id": original_memory_id,
                    "new_memory_id": new_memory_id,
                    "conflict_fields": list(conflict_fields),
                },
                severity="warning",
            )
        )

    async def log_failed_retrieval(
        self,
        session_id: str | None,
        turn_id: str | None,
        user_id: str | None,
        query: str,
        reason: str,
        retry_count: int,
    ) -> LogEvent:
        return self._store(
            LogEvent(
                event_type="failed_retrieval",
                session_id=session_id,
                turn_id=turn_id,
                user_id=user_id,
                details={
                    "query": query,
                    "reason": reason,
                    "retry_count": int(retry_count),
                },
                severity="warning",
            )
        )

    async def query_events(
        self,
        event_type: LogEventType | None = None,
        session_id: str | None = None,
        since: str | datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[LogEvent]:
        since_at = _parse_timestamp(since)
        events: list[LogEvent] = []

        for event in self._events:
            if event_type is not None and event.event_type != event_type:
                continue
            if session_id is not None and event.session_id != session_id:
                continue
            if since_at is not None and _parse_timestamp(event.timestamp) < since_at:
                continue
            events.append(event)

        start = max(0, int(offset))
        stop = start + max(0, int(limit))
        return events[start:stop]

    async def get_event(self, event_id: str) -> LogEvent | None:
        return self._events_by_id.get(event_id)

    async def clear(self) -> None:
        self._events.clear()
        self._events_by_id.clear()

    def _store(self, event: LogEvent) -> LogEvent:
        if len(self._events) >= self.max_events:
            expired_event = self._events.popleft()
            self._events_by_id.pop(expired_event.event_id, None)

        self._events.append(event)
        self._events_by_id[event.event_id] = event
        return event


def _parse_timestamp(timestamp: str | datetime | None) -> datetime | None:
    if timestamp is None:
        return None
    if isinstance(timestamp, datetime):
        parsed = timestamp
    else:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


structured_logger = StructuredLogger()


__all__ = [
    "LogEvent",
    "LogEventType",
    "StructuredLogger",
    "structured_logger",
]
