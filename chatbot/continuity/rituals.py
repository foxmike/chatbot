"""Rituals and shared routines — recurring activities, inside jokes, check-ins, and routine templates for the companion."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import inspect
import uuid
from typing import Any, Literal, TypeAlias

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.db.models import CompanionRitualModel
from chatbot.db.session import async_session_factory


RitualType: TypeAlias = Literal[
    "check_in",
    "recap",
    "planning",
    "recurring",
    "joke",
    "custom",
]

RITUAL_TYPES: tuple[str, ...] = (
    "check_in",
    "recap",
    "planning",
    "recurring",
    "joke",
    "custom",
)

MORNING_CHECK_IN: dict[str, str] = {
    "name": "Morning check-in",
    "ritual_type": "check_in",
    "description": "A brief start-of-day check-in about mood, priorities, and support needed.",
    "schedule_description": "daily at conversation start",
    "trigger_prompt": "Check in on how the user is doing and what would make today easier.",
}

NIGHTLY_RECAP: dict[str, str] = {
    "name": "Nightly recap",
    "ritual_type": "recap",
    "description": "A gentle end-of-day recap of what happened, what mattered, and what to carry forward.",
    "schedule_description": "daily at conversation end",
    "trigger_prompt": "Invite a short recap of the day and note anything worth remembering.",
}

WEEKLY_PLANNING: dict[str, str] = {
    "name": "Weekly planning",
    "ritual_type": "planning",
    "description": "A recurring planning routine for the week ahead, priorities, and open loops.",
    "schedule_description": "weekly",
    "trigger_prompt": "Help the user review the week ahead and choose a few concrete priorities.",
}


@dataclass(slots=True)
class Ritual:
    ritual_id: str
    session_id: str
    name: str
    ritual_type: RitualType
    description: str
    schedule_description: str
    last_triggered_at: str | None
    next_trigger_at: str | None
    is_active: bool = True
    trigger_prompt: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


class Rituals:
    def __init__(
        self,
        session_factory: Callable[[], Any] = async_session_factory,
    ) -> None:
        self._session_factory = session_factory

    async def create_ritual(
        self,
        session_id: str,
        name: str,
        ritual_type: str,
        description: str,
        schedule_description: str,
        trigger_prompt: str | None = None,
        metadata: dict | None = None,
        *,
        session: AsyncSession | None = None,
    ) -> Ritual:
        _validate_ritual_type(ritual_type)
        ritual_id = str(uuid.uuid4())
        now = _now()
        model = CompanionRitualModel(
            id=ritual_id,
            session_id=str(_to_uuid(session_id, "session_id")),
            name=name,
            ritual_type=ritual_type,
            description=description,
            schedule_description=schedule_description,
            last_triggered_at=None,
            next_trigger_at=None,
            is_active=True,
            trigger_prompt=trigger_prompt,
            created_at=now,
            metadata_=dict(metadata or {}),
        )

        async with self._session(session) as db:
            db.add(model)
            await _flush_or_commit(db, owned=session is None)
        return _model_to_ritual(model)

    async def get_rituals(
        self,
        session_id: str,
        ritual_type: str | None = None,
        active_only: bool = True,
    ) -> list[Ritual]:
        if ritual_type is not None:
            _validate_ritual_type(ritual_type)

        statement = sa.select(CompanionRitualModel).where(
            CompanionRitualModel.session_id == str(_to_uuid(session_id, "session_id"))
        )
        if ritual_type is not None:
            statement = statement.where(CompanionRitualModel.ritual_type == ritual_type)
        if active_only:
            statement = statement.where(CompanionRitualModel.is_active.is_(True))
        statement = statement.order_by(
            CompanionRitualModel.created_at.asc(),
            CompanionRitualModel.id.asc(),
        )

        async with self._session() as db:
            result = await db.execute(statement)
            return [_model_to_ritual(model) for model in result.scalars().all()]

    async def get_due_rituals(self, session_id: str) -> list[Ritual]:
        now = _now()
        statement = (
            sa.select(CompanionRitualModel)
            .where(
                CompanionRitualModel.session_id == str(_to_uuid(session_id, "session_id")),
                CompanionRitualModel.is_active.is_(True),
            )
            .order_by(
                CompanionRitualModel.next_trigger_at.asc().nullsfirst(),
                CompanionRitualModel.created_at.asc(),
                CompanionRitualModel.id.asc(),
            )
        )

        async with self._session() as db:
            result = await db.execute(statement)
            models = list(result.scalars().all())

        return [_model_to_ritual(model) for model in models if _ritual_is_due(model, now)]

    async def trigger_ritual(self, ritual_id: str, *, session: AsyncSession | None = None) -> str:
        now = _now()
        async with self._session(session) as db:
            model = await self._get_model(db, ritual_id)
            if model is None:
                raise ValueError(f"Ritual {ritual_id} not found")

            model.last_triggered_at = now
            model.next_trigger_at = _next_trigger_at(model.schedule_description, now)
            await _flush_or_commit(db, owned=session is None)
            return model.trigger_prompt or ""

    async def add_inside_joke(
        self,
        session_id: str,
        name: str,
        joke_text: str,
        context: str,
        *,
        session: AsyncSession | None = None,
    ) -> Ritual:
        return await self.create_ritual(
            session_id=session_id,
            name=name,
            ritual_type="joke",
            description=context,
            schedule_description="when context is relevant",
            trigger_prompt=joke_text,
            metadata={"context": context, "joke_text": joke_text},
            session=session,
        )

    async def deactivate_ritual(self, ritual_id: str, *, session: AsyncSession | None = None) -> None:
        async with self._session(session) as db:
            model = await self._get_model(db, ritual_id)
            if model is None:
                raise ValueError(f"Ritual {ritual_id} not found")

            model.is_active = False
            await _flush_or_commit(db, owned=session is None)

    async def _get_model(
        self,
        db: AsyncSession,
        ritual_id: str,
    ) -> CompanionRitualModel | None:
        result = await db.execute(
            sa.select(CompanionRitualModel).where(
                CompanionRitualModel.id == str(_to_uuid(ritual_id, "ritual_id"))
            )
        )
        return result.scalar_one_or_none()

    @asynccontextmanager
    async def _session(self, session: AsyncSession | None = None) -> Any:
        if session is not None:
            # The caller retains transaction and session lifetime ownership.
            yield session
            return
        session = self._session_factory()
        if hasattr(session, "__aenter__"):
            async with session as active_session:
                yield active_session
            return
        if hasattr(session, "__enter__"):
            with session as active_session:
                yield active_session
            return
        yield session


_default_rituals = Rituals()


async def create_ritual(
    session_id: str,
    name: str,
    ritual_type: str,
    description: str,
    schedule_description: str,
    trigger_prompt: str | None = None,
    metadata: dict | None = None,
    *,
    session: AsyncSession | None = None,
) -> Ritual:
    return await _default_rituals.create_ritual(
        session_id=session_id,
        name=name,
        ritual_type=ritual_type,
        description=description,
        schedule_description=schedule_description,
        trigger_prompt=trigger_prompt,
        metadata=metadata,
        session=session,
    )


async def get_rituals(
    session_id: str,
    ritual_type: str | None = None,
    active_only: bool = True,
) -> list[Ritual]:
    return await _default_rituals.get_rituals(session_id, ritual_type, active_only)


async def get_due_rituals(session_id: str) -> list[Ritual]:
    return await _default_rituals.get_due_rituals(session_id)


async def trigger_ritual(ritual_id: str, *, session: AsyncSession | None = None) -> str:
    return await _default_rituals.trigger_ritual(ritual_id, session=session)


async def add_inside_joke(
    session_id: str,
    name: str,
    joke_text: str,
    context: str,
    *,
    session: AsyncSession | None = None,
) -> Ritual:
    return await _default_rituals.add_inside_joke(session_id, name, joke_text, context, session=session)


async def deactivate_ritual(ritual_id: str, *, session: AsyncSession | None = None) -> None:
    await _default_rituals.deactivate_ritual(ritual_id, session=session)


def _ritual_is_due(model: CompanionRitualModel, now: datetime) -> bool:
    if model.next_trigger_at is not None:
        return _ensure_timezone(model.next_trigger_at) <= now

    reference_time = model.last_triggered_at or model.created_at
    elapsed = now - _ensure_timezone(reference_time)
    return elapsed >= _schedule_window(model.schedule_description)


def _next_trigger_at(schedule_description: str, triggered_at: datetime) -> datetime:
    normalized = schedule_description.lower()
    if "weekly" in normalized or "every week" in normalized:
        weekday = _weekday_from_schedule(normalized)
        if weekday is None:
            return triggered_at + timedelta(days=7)
        days_until = (weekday - triggered_at.weekday()) % 7
        if days_until == 0:
            days_until = 7
        return triggered_at + timedelta(days=days_until)
    return triggered_at + _schedule_window(schedule_description)


def _schedule_window(schedule_description: str) -> timedelta:
    normalized = schedule_description.lower()
    if "hourly" in normalized or "every hour" in normalized:
        return timedelta(hours=1)
    if "weekly" in normalized or "every week" in normalized:
        return timedelta(days=7)
    if "monthly" in normalized or "every month" in normalized:
        return timedelta(days=30)
    if (
        "daily" in normalized
        or "every day" in normalized
        or "morning" in normalized
        or "nightly" in normalized
        or "evening" in normalized
    ):
        return timedelta(days=1)
    return timedelta(days=1)


def _weekday_from_schedule(normalized_schedule_description: str) -> int | None:
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    for name, index in weekdays.items():
        if name in normalized_schedule_description:
            return index
    return None


def _model_to_ritual(model: CompanionRitualModel) -> Ritual:
    return Ritual(
        ritual_id=str(model.id),
        session_id=str(model.session_id),
        name=model.name,
        ritual_type=_ritual_type(model.ritual_type),
        description=model.description,
        schedule_description=model.schedule_description,
        last_triggered_at=_dt_iso(model.last_triggered_at),
        next_trigger_at=_dt_iso(model.next_trigger_at),
        is_active=model.is_active,
        trigger_prompt=model.trigger_prompt,
        created_at=_dt_iso(model.created_at),
        metadata=dict(model.metadata_ or {}),
    )


def _ritual_type(value: str) -> RitualType:
    _validate_ritual_type(value)
    return value  # type: ignore[return-value]


def _validate_ritual_type(value: str) -> None:
    if value not in RITUAL_TYPES:
        raise ValueError(f"Unsupported ritual type: {value}")


def _to_uuid(value: str | uuid.UUID, field_name: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a UUID string") from exc


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_timezone(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _dt_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return _ensure_timezone(dt).isoformat()


async def _flush_or_commit(db: Any, *, owned: bool) -> None:
    """Commit factory-owned writes, including autobegun transactions.

    Only an explicitly supplied session belongs to a caller; its writes are
    flushed without committing. Owned context managers roll back on failure.
    """
    if not owned:
        flush = getattr(db, "flush", None)
        if callable(flush):
            await _maybe_await(flush())
        return

    commit = getattr(db, "commit", None)
    if callable(commit):
        await _maybe_await(commit())
        return

    flush = getattr(db, "flush", None)
    if callable(flush):
        await _maybe_await(flush())


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = [
    "MORNING_CHECK_IN",
    "NIGHTLY_RECAP",
    "RITUAL_TYPES",
    "Ritual",
    "RitualType",
    "Rituals",
    "WEEKLY_PLANNING",
    "add_inside_joke",
    "create_ritual",
    "deactivate_ritual",
    "get_due_rituals",
    "get_rituals",
    "trigger_ritual",
]
