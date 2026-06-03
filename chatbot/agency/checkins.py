from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy.orm import Session as DBSession

from chatbot.agency.scheduler import InitiativeLevel


CheckInType = Literal[
    "reminder",
    "emotional_checkin",
    "continuation_prompt",
    "milestone_callback",
    "event_callback",
]

_REMINDER_TEMPLATE = "Just checking in — did you want to pick up where we left off?"

_EMOTIONAL_CHECKIN_TEMPLATE = (
    "How are you feeling right now? I'm here if you want to talk."
)
_EMOTIONAL_CHECKIN_CONTEXT_TEMPLATE = (
    "How are you feeling? Our last conversation touched on: {context}. "
    "I'm here if you want to talk."
)

_CONTINUATION_TEMPLATE = "Ready to continue our conversation from earlier?"
_CONTINUATION_CONTEXT_TEMPLATE = "Ready to continue? We were discussing: {context}"

_MILESTONE_TEMPLATE = (
    "You've hit a milestone — congratulations! "
    "Want to reflect on it together?"
)
_MILESTONE_CONTEXT_TEMPLATE = (
    "You've hit a milestone — congratulations! "
    "Let's reflect on: {context}"
)
_MILESTONE_BASIC_TEMPLATE = "You've reached a milestone."
_MILESTONE_BASIC_CONTEXT_TEMPLATE = "You've reached a milestone: {context}"

_EVENT_TEMPLATE = "Something happened — I thought you might want to know."
_EVENT_CONTEXT_TEMPLATE = "Something happened: {context}"


@dataclass
class CheckIn:
    checkin_id: str
    user_id: str
    checkin_type: CheckInType
    message: str
    context_summary: str
    generated_at: str
    priority: int = 0


class CheckInGenerator:
    def __init__(
        self,
        initiative: Any,
        *,
        db_session: DBSession | None = None,
    ) -> None:
        self._initiative = initiative
        self._db_session = db_session

    def _get_level(self, user_id: str) -> str:
        return self._initiative.get_initiative_level(user_id)

    def _is_never(self, user_id: str) -> bool:
        return self._get_level(user_id) == "never"

    def _is_reminders_only(self, user_id: str) -> bool:
        return self._get_level(user_id) == "reminders_only"

    def _build_checkin(
        self,
        user_id: str,
        checkin_type: CheckInType,
        message: str,
        context: str = "",
    ) -> CheckIn:
        return CheckIn(
            checkin_id=str(uuid.uuid4()),
            user_id=user_id,
            checkin_type=checkin_type,
            message=message,
            context_summary=context,
            generated_at=datetime.now(timezone.utc).isoformat(),
            priority=0,
        )

    def get_recent_context(self, user_id: str) -> list[str]:
        if self._db_session is None:
            return []
        try:
            from chatbot.db.models import Message, Session

            import sqlalchemy as sa

            recent = (
                self._db_session.query(Message.content)
                .join(Session, Message.session_id == Session.id)
                .filter(
                    Session.user_id == user_id,
                    Message.role.in_(["user", "assistant"]),
                )
                .order_by(Message.created_at.desc())
                .limit(10)
                .all()
            )

            return [content for (content,) in recent if content]
        except Exception:
            return []

    def generate_reminder(self, user_id: str) -> CheckIn | None:
        if self._is_never(user_id) or self._is_reminders_only(user_id):
            return None
        return self._build_checkin(user_id, "reminder", _REMINDER_TEMPLATE)

    def generate_emotional_checkin(self, user_id: str) -> CheckIn | None:
        if self._is_never(user_id) or self._is_reminders_only(user_id):
            return None
        recent = self.get_recent_context(user_id)
        if recent:
            preview = recent[0][:100]
            message = _EMOTIONAL_CHECKIN_CONTEXT_TEMPLATE.format(context=preview)
            context_summary = "; ".join(recent[:3])
        else:
            message = _EMOTIONAL_CHECKIN_TEMPLATE
            context_summary = ""
        return self._build_checkin(
            user_id, "emotional_checkin", message, context=context_summary,
        )

    def generate_continuation_prompt(self, user_id: str) -> CheckIn | None:
        if self._is_never(user_id):
            return None
        recent = self.get_recent_context(user_id)
        if recent:
            preview = recent[0][:100]
            message = _CONTINUATION_CONTEXT_TEMPLATE.format(context=preview)
            context_summary = "; ".join(recent[:3])
        else:
            message = _CONTINUATION_TEMPLATE
            context_summary = ""
        return self._build_checkin(
            user_id, "continuation_prompt", message, context=context_summary,
        )

    def generate_milestone_callback(
        self, user_id: str, context: str = ""
    ) -> CheckIn | None:
        if self._is_never(user_id):
            return None
        if self._is_reminders_only(user_id):
            if context:
                message = _MILESTONE_BASIC_CONTEXT_TEMPLATE.format(context=context)
            else:
                message = _MILESTONE_BASIC_TEMPLATE
            return self._build_checkin(
                user_id, "milestone_callback", message, context=context,
            )
        if context:
            message = _MILESTONE_CONTEXT_TEMPLATE.format(context=context)
        else:
            message = _MILESTONE_TEMPLATE
        return self._build_checkin(
            user_id, "milestone_callback", message, context=context,
        )

    def generate_event_callback(
        self, user_id: str, context: str = ""
    ) -> CheckIn | None:
        if self._is_never(user_id):
            return None
        if context:
            message = _EVENT_CONTEXT_TEMPLATE.format(context=context)
        else:
            message = _EVENT_TEMPLATE
        return self._build_checkin(
            user_id, "event_callback", message, context=context,
        )

    def generate(
        self,
        checkin_type: CheckInType,
        user_id: str,
        context: str | None = None,
    ) -> CheckIn | None:
        ctx = context or ""
        dispatch: dict[CheckInType, Any] = {
            "reminder": lambda: self.generate_reminder(user_id),
            "emotional_checkin": lambda: self.generate_emotional_checkin(user_id),
            "continuation_prompt": lambda: self.generate_continuation_prompt(user_id),
            "milestone_callback": lambda: self.generate_milestone_callback(user_id, ctx),
            "event_callback": lambda: self.generate_event_callback(user_id, ctx),
        }
        handler = dispatch.get(checkin_type)
        if handler is None:
            return None
        return handler()


__all__ = ["CheckIn", "CheckInGenerator", "CheckInType"]
