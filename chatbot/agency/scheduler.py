from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any, Literal
import uuid


EventType = Literal[
    "ritual", "timeline", "checkin", "reminder", "continuation", "milestone"
]
InitiativeLevel = Literal["never", "low", "medium", "high", "max"]


@dataclass
class ScheduledEvent:
    event_id: str
    user_id: str
    event_type: EventType
    scheduled_at: datetime
    action_context: dict[str, Any]


@dataclass
class ProactiveAction:
    action_id: str
    user_id: str
    action_type: str
    message: str
    priority: int = 0


class Scheduler:
    def __init__(
        self,
        *,
        polling_interval_seconds: int = 300,
        quiet_hours_start: time = time(22, 0),
        quiet_hours_end: time = time(8, 0),
    ) -> None:
        self._events: list[ScheduledEvent] = []
        self.polling_interval_seconds = polling_interval_seconds
        self._stop_event = asyncio.Event()
        self._initiative_levels: dict[str, InitiativeLevel] = {}
        self.quiet_hours_start = quiet_hours_start
        self.quiet_hours_end = quiet_hours_end

    def set_initiative_level(self, user_id: str, level: InitiativeLevel) -> None:
        self._initiative_levels[user_id] = level

    def get_initiative_level(self, user_id: str) -> InitiativeLevel:
        return self._initiative_levels.get(user_id, "medium")

    def _is_quiet_hours(self) -> bool:
        now = datetime.now(timezone.utc).time()
        if self.quiet_hours_start <= self.quiet_hours_end:
            return self.quiet_hours_start <= now < self.quiet_hours_end
        else:
            return now >= self.quiet_hours_start or now < self.quiet_hours_end

    def should_initiate(self, user_id: str) -> bool:
        level = self.get_initiative_level(user_id)
        if level == "never":
            return False
        if self._is_quiet_hours():
            return False
        return True

    def get_next_event(self) -> ScheduledEvent | None:
        now = datetime.now(timezone.utc)
        upcoming = [e for e in self._events if e.scheduled_at > now]
        if not upcoming:
            return None
        return min(upcoming, key=lambda e: e.scheduled_at)

    def decide_action(self, user_id: str) -> ProactiveAction | None:
        if not self.should_initiate(user_id):
            return None
        candidate = None
        now = datetime.now(timezone.utc)
        for event in self._events:
            if event.user_id != user_id:
                continue
            if (event.scheduled_at - now).total_seconds() > self.polling_interval_seconds:
                continue
            if candidate is None or event.scheduled_at < candidate.scheduled_at:
                candidate = event
        if candidate is None:
            return None
        return ProactiveAction(
            action_id=str(uuid.uuid4()),
            user_id=user_id,
            action_type=candidate.event_type,
            message="Proactive {event_type}: {title}".format(
                event_type=candidate.event_type,
                title=candidate.action_context.get("title", ""),
            ),
            priority=0,
        )

    def add_event(self, event: ScheduledEvent) -> None:
        self._events.append(event)

    def remove_event(self, event_id: str) -> None:
        self._events = [e for e in self._events if e.event_id != event_id]

    async def run_loop(self) -> None:
        while not self._stop_event.is_set():
            active_user_ids = {e.user_id for e in self._events}
            for user_id in active_user_ids:
                if self.should_initiate(user_id):
                    self.decide_action(user_id)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.polling_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass


__all__ = ["ProactiveAction", "ScheduledEvent", "Scheduler"]