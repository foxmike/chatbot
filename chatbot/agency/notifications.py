from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

NotificationType = Literal[
    "reminder",
    "emotional_checkin",
    "continuation_prompt",
    "milestone",
    "event_callback",
]


@dataclass
class Notification:
    notification_id: str
    user_id: str
    notif_type: NotificationType
    title: str
    body: str
    created_at: str
    read: bool = False
    dismissed: bool = False
    metadata: dict = field(default_factory=dict)


class NotificationQueue:
    def __init__(self) -> None:
        self._queues: dict[str, list[Notification]] = {}

    def enqueue(
        self,
        user_id: str,
        notification: Notification,
        initiative_level: str,
    ) -> bool:
        if initiative_level == "never":
            return False
        if (
            initiative_level == "reminders_only"
            and notification.notif_type not in ("reminder", "milestone")
        ):
            return False
        self._queues.setdefault(user_id, []).append(notification)
        return True

    def dequeue(self, user_id: str) -> Notification | None:
        user_queue = self._queues.get(user_id, [])
        candidates = [n for n in user_queue if not n.read and not n.dismissed]
        if not candidates:
            return None
        return min(candidates, key=lambda n: n.created_at)

    def poll(
        self, user_id: str, since: str | None = None
    ) -> list[Notification]:
        user_queue = self._queues.get(user_id, [])
        if since is None:
            return list(user_queue)
        return [n for n in user_queue if n.created_at > since]

    def mark_read(self, notification_id: str) -> bool:
        for user_queue in self._queues.values():
            for notification in user_queue:
                if notification.notification_id == notification_id:
                    notification.read = True
                    return True
        return False

    def dismiss(self, notification_id: str) -> bool:
        for user_queue in self._queues.values():
            for notification in user_queue:
                if notification.notification_id == notification_id:
                    notification.dismissed = True
                    return True
        return False

    def pending_count(self, user_id: str) -> int:
        user_queue = self._queues.get(user_id, [])
        return sum(1 for n in user_queue if not n.read and not n.dismissed)


__all__ = ["Notification", "NotificationQueue", "NotificationType"]
