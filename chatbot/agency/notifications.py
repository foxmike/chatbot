from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.db.models import Notification as NotificationModel

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
    def __init__(self, db_session: AsyncSession | None = None) -> None:
        self._db_session = db_session
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

    async def enqueue_async(
        self,
        user_id: str,
        notification: Notification,
        initiative_level: str,
    ) -> bool:
        if self._db_session is None:
            return self.enqueue(user_id, notification, initiative_level)
        if not _initiative_allows(notification, initiative_level):
            return False

        self._db_session.add(_model_from_notification(user_id, notification))
        await self._db_session.commit()
        return True

    async def dequeue_async(self, user_id: str) -> Notification | None:
        if self._db_session is None:
            return self.dequeue(user_id)

        result = await self._db_session.execute(
            sa.select(NotificationModel)
            .where(
                NotificationModel.user_id == user_id,
                NotificationModel.read == False,
                NotificationModel.dismissed == False,
            )
            .order_by(NotificationModel.created_at.asc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        return _notification_from_model(model) if model is not None else None

    async def poll_async(
        self, user_id: str, since: str | None = None
    ) -> list[Notification]:
        if self._db_session is None:
            return self.poll(user_id, since=since)

        stmt = sa.select(NotificationModel).where(NotificationModel.user_id == user_id)
        if since is not None:
            stmt = stmt.where(NotificationModel.created_at > _parse_datetime(since))
        result = await self._db_session.execute(
            stmt.order_by(NotificationModel.created_at.asc())
        )
        return [_notification_from_model(model) for model in result.scalars()]

    async def mark_read_async(
        self, notification_id: str, user_id: str | None = None
    ) -> bool:
        if self._db_session is None:
            return self.mark_read(notification_id)
        model = await self._get_model(notification_id, user_id=user_id)
        if model is None:
            return False
        model.read = True
        await self._db_session.commit()
        return True

    async def dismiss_async(
        self, notification_id: str, user_id: str | None = None
    ) -> bool:
        if self._db_session is None:
            return self.dismiss(notification_id)
        model = await self._get_model(notification_id, user_id=user_id)
        if model is None:
            return False
        model.dismissed = True
        await self._db_session.commit()
        return True

    async def pending_count_async(self, user_id: str) -> int:
        if self._db_session is None:
            return self.pending_count(user_id)

        result = await self._db_session.execute(
            sa.select(sa.func.count(NotificationModel.id)).where(
                NotificationModel.user_id == user_id,
                NotificationModel.read == False,
                NotificationModel.dismissed == False,
            )
        )
        return int(result.scalar_one() or 0)

    async def _get_model(
        self, notification_id: str, user_id: str | None = None
    ) -> NotificationModel | None:
        if self._db_session is None:
            return None
        stmt = sa.select(NotificationModel).where(NotificationModel.id == notification_id)
        if user_id is not None:
            stmt = stmt.where(NotificationModel.user_id == user_id)
        result = await self._db_session.execute(stmt)
        return result.scalar_one_or_none()


def _initiative_allows(notification: Notification, initiative_level: str) -> bool:
    if initiative_level == "never":
        return False
    if (
        initiative_level == "reminders_only"
        and notification.notif_type not in ("reminder", "milestone")
    ):
        return False
    return True


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _serialize_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _metadata(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _model_from_notification(
    user_id: str, notification: Notification
) -> NotificationModel:
    return NotificationModel(
        id=notification.notification_id or str(uuid.uuid4()),
        user_id=user_id,
        notif_type=notification.notif_type,
        title=notification.title,
        body=notification.body,
        read=notification.read,
        dismissed=notification.dismissed,
        extra_metadata=notification.metadata,
        created_at=_parse_datetime(notification.created_at),
    )


def _notification_from_model(model: NotificationModel) -> Notification:
    return Notification(
        notification_id=model.id,
        user_id=model.user_id,
        notif_type=model.notif_type,
        title=model.title,
        body=model.body,
        created_at=_serialize_datetime(model.created_at),
        read=model.read,
        dismissed=model.dismissed,
        metadata=_metadata(model.extra_metadata),
    )


__all__ = ["Notification", "NotificationQueue", "NotificationType"]
