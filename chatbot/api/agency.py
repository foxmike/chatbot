from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from chatbot.agency.checkins import CheckInGenerator
from chatbot.agency.checkins import CheckInType
from chatbot.agency.initiative import InitiativeLevel
from chatbot.agency.initiative import InitiativeSettings
from chatbot.agency.initiative import ProjectInitiative
from chatbot.agency.notifications import NotificationQueue
from chatbot.agency.scheduler import Scheduler
from chatbot.auth.dependencies import get_current_user, CurrentUser

agency_router = APIRouter(prefix="/agency", tags=["agency"])

scheduler = Scheduler()
notification_queue = NotificationQueue()
project_initiative = ProjectInitiative()
checkin_generator = CheckInGenerator(initiative=project_initiative)

_INITIATIVE_LEVEL_VALUES = {e.value for e in InitiativeLevel}

_SCHEDULER_LEVEL_MAP = {
    "never_proactive": "never",
    "reminders_only": "low",
    "gentle_checkins": "medium",
    "frequent": "high",
    "high_energy": "max",
}


class InitiativeResponse(BaseModel):
    user_id: str
    level: str
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None


class InitiativeUpdateRequest(BaseModel):
    level: str
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None


class NotificationResponse(BaseModel):
    notification_id: str
    user_id: str
    notif_type: str
    title: str
    body: str
    created_at: str
    read: bool
    dismissed: bool
    metadata: dict[str, Any]


class NotificationListResponse(BaseModel):
    notifications: list[NotificationResponse]
    pending_count: int


class CheckInTriggerRequest(BaseModel):
    checkin_type: str
    context: dict[str, Any] | None = None


class CheckInResponse(BaseModel):
    checkin_id: str
    message: str
    checkin_type: str
    priority: int


class ScheduleResponse(BaseModel):
    events: list[dict[str, Any]]
    next_event: dict[str, Any] | None = None


def _scheduled_event_to_dict(event: Any) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "user_id": event.user_id,
        "event_type": event.event_type,
        "scheduled_at": event.scheduled_at.isoformat(),
        "action_context": event.action_context,
    }


def _validate_initiative_level(level: str) -> None:
    if level not in _INITIATIVE_LEVEL_VALUES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid initiative level: {level!r}. "
            f"Valid levels: {sorted(_INITIATIVE_LEVEL_VALUES)}",
        )


def _sync_scheduler_level(user_id: str, level: str) -> None:
    mapped = _SCHEDULER_LEVEL_MAP.get(level)
    if mapped is not None:
        scheduler.set_initiative_level(user_id, mapped)


@agency_router.get("/initiative", response_model=InitiativeResponse)
async def get_initiative(current_user: CurrentUser = Depends(get_current_user)) -> InitiativeResponse:
    settings: InitiativeSettings = await project_initiative.get_initiative(current_user.user_id)
    return InitiativeResponse(
        user_id=settings.user_id,
        level=settings.level.value,
        quiet_hours_start=settings.quiet_hours_start,
        quiet_hours_end=settings.quiet_hours_end,
    )


@agency_router.put("/initiative", response_model=InitiativeResponse)
async def update_initiative(
    req: InitiativeUpdateRequest, current_user: CurrentUser = Depends(get_current_user)
) -> InitiativeResponse:
    _validate_initiative_level(req.level)
    level_enum = InitiativeLevel(req.level)
    settings = InitiativeSettings(
        user_id=current_user.user_id,
        level=level_enum,
        quiet_hours_start=req.quiet_hours_start,
        quiet_hours_end=req.quiet_hours_end,
    )
    await project_initiative.set_initiative(current_user.user_id, settings)
    _sync_scheduler_level(current_user.user_id, req.level)
    return InitiativeResponse(
        user_id=current_user.user_id,
        level=req.level,
        quiet_hours_start=req.quiet_hours_start,
        quiet_hours_end=req.quiet_hours_end,
    )


@agency_router.get(
    "/notifications", response_model=NotificationListResponse
)
async def get_notifications(
    since: str | None = None, current_user: CurrentUser = Depends(get_current_user)
) -> NotificationListResponse:
    notifications = notification_queue.poll(current_user.user_id, since=since)
    pending = notification_queue.pending_count(current_user.user_id)
    return NotificationListResponse(
        notifications=[
            NotificationResponse(
                notification_id=n.notification_id,
                user_id=n.user_id,
                notif_type=n.notif_type,
                title=n.title,
                body=n.body,
                created_at=n.created_at,
                read=n.read,
                dismissed=n.dismissed,
                metadata=n.metadata,
            )
            for n in notifications
        ],
        pending_count=pending,
    )


@agency_router.put("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str) -> dict[str, str]:
    if not notification_queue.mark_read(notification_id):
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "read"}


@agency_router.put("/notifications/{notification_id}/dismiss")
async def dismiss_notification(notification_id: str) -> dict[str, str]:
    if not notification_queue.dismiss(notification_id):
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "dismissed"}


@agency_router.post("/checkins/trigger", response_model=CheckInResponse)
async def trigger_checkin(req: CheckInTriggerRequest, current_user: CurrentUser = Depends(get_current_user)) -> CheckInResponse:
    if req.checkin_type not in CheckInType.__args__:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid checkin_type: {req.checkin_type!r}",
        )
    context_str: str | None = None
    if req.context is not None:
        context_str = str(req.context)
    checkin = checkin_generator.generate(
        req.checkin_type, current_user.user_str, context=context_str,
    )
    if checkin is None:
        raise HTTPException(
            status_code=404,
            detail="Check-in not available for this user/type combination",
        )
    return CheckInResponse(
        checkin_id=checkin.checkin_id,
        message=checkin.message,
        checkin_type=checkin.checkin_type,
        priority=checkin.priority,
    )


@agency_router.get("/schedule", response_model=ScheduleResponse)
async def get_schedule(current_user: CurrentUser = Depends(get_current_user)) -> ScheduleResponse:
    user_events = [
        _scheduled_event_to_dict(e)
        for e in scheduler._events
        if e.user_id == current_user.user_id
    ]
    next_event = scheduler.get_next_event()
    next_event_dict = (
        _scheduled_event_to_dict(next_event) if next_event else None
    )
    return ScheduleResponse(events=user_events, next_event=next_event_dict)