"""Prompt effectiveness telemetry.

Logs the combinations of prompt template, model, mode, and formatter that produced each response.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class PromptTelemetryEvent:
    event_id: str = field(default_factory=lambda: str(uuid4()))
    trace_id: str | None = None
    prompt_template: str
    model_id: str
    mode: str
    formatter: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PromptTelemetryCollector:
    """Collects telemetry on prompt configurations used to generate responses."""

    def __init__(self, max_events: int = 10000) -> None:
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        self.max_events = max_events
        self._events: deque[PromptTelemetryEvent] = deque()

    async def record_generation(
        self,
        *,
        trace_id: str | None = None,
        prompt_template: str,
        model_id: str,
        mode: str,
        formatter: str,
    ) -> PromptTelemetryEvent:
        """Record the prompt configuration that was used for a generated response."""
        event = PromptTelemetryEvent(
            trace_id=trace_id,
            prompt_template=prompt_template,
            model_id=model_id,
            mode=mode,
            formatter=formatter,
        )

        if len(self._events) >= self.max_events:
            self._events.popleft()

        self._events.append(event)

        logger.debug(
            "Recorded prompt telemetry: template=%s model=%s mode=%s formatter=%s trace_id=%s",
            prompt_template,
            model_id,
            mode,
            formatter,
            trace_id,
        )
        return event

    async def get_events(
        self, limit: int = 100, offset: int = 0
    ) -> list[PromptTelemetryEvent]:
        """Retrieve recent telemetry events."""
        start = max(0, int(offset))
        stop = start + max(0, int(limit))
        # Note: deque doesn't support slicing directly, so we convert to a list
        return list(self._events)[start:stop]

    async def clear(self) -> None:
        """Clear all stored telemetry events."""
        self._events.clear()


prompt_telemetry_collector = PromptTelemetryCollector()


__all__ = [
    "PromptTelemetryEvent",
    "PromptTelemetryCollector",
    "prompt_telemetry_collector",
]
