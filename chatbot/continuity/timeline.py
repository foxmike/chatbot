"""Relationship timeline tracking milestones, emotional events, promises, and running projects for conversational continuity."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import inspect
import uuid
from typing import Any, Literal, TypeAlias

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from chatbot.db.models import Base
from chatbot.db.session import async_session_factory


TimelineEntryType: TypeAlias = Literal[
    "milestone",
    "emotional_event",
    "promise",
    "running_project",
]
TimelineEntryStatus: TypeAlias = Literal[
    "active",
    "resolved",
    "broken",
    "completed",
    "abandoned",
]

TIMELINE_ENTRY_TYPES: tuple[str, ...] = (
    "milestone",
    "emotional_event",
    "promise",
    "running_project",
)
TIMELINE_ENTRY_STATUSES: tuple[str, ...] = (
    "active",
    "resolved",
    "broken",
    "completed",
    "abandoned",
)


@dataclass(slots=True)
class TimelineEntry:
    entry_id: str
    session_id: str
    entry_type: TimelineEntryType
    title: str
    description: str
    content: str
    turn_id: str
    created_at: str
    updated_at: str
    status: TimelineEntryStatus = "active"
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


TimelineEvent = TimelineEntry


@dataclass(slots=True)
class TimelineFilter:
    session_id: str | None = None
    entry_types: list[str] | None = None
    statuses: list[str] | None = None
    tags: list[str] | None = None
    date_from: str | None = None
    date_to: str | None = None


class RelationshipTimelineEntryModel(Base):
    __tablename__ = "relationship_timeline_entries"
    __table_args__ = (
        sa.CheckConstraint(
            "entry_type IN ('milestone', 'emotional_event', 'promise', 'running_project')",
            name="ck_relationship_timeline_entries_entry_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'resolved', 'broken', 'completed', 'abandoned')",
            name="ck_relationship_timeline_entries_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("sessions.id"),
        index=True,
        nullable=False,
    )
    entry_type: Mapped[str] = mapped_column(sa.String, index=True, nullable=False)
    title: Mapped[str] = mapped_column(sa.String, nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    turn_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        index=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        sa.String,
        index=True,
        nullable=False,
        default="active",
        server_default=sa.text("'active'"),
    )
    tags: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        sa.JSON,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
        onupdate=sa.sql.func.now(),
    )


class RelationshipTimeline:
    def __init__(
        self,
        session_factory: Callable[[], Any] = async_session_factory,
    ) -> None:
        self._session_factory = session_factory

    async def add_timeline_entry(
        self,
        session_id: str,
        entry_type: str,
        title: str,
        description: str,
        content: str,
        turn_id: str,
        tags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> TimelineEntry:
        _validate_entry_type(entry_type)
        entry_id = uuid.uuid4()
        now = _now()
        model = RelationshipTimelineEntryModel(
            id=entry_id,
            session_id=_to_uuid(session_id, "session_id"),
            entry_type=entry_type,
            title=title,
            description=description,
            content=content,
            turn_id=_to_uuid(turn_id, "turn_id"),
            created_at=now,
            updated_at=now,
            tags=list(tags or []),
            metadata_json=dict(metadata or {}),
        )

        async with self._session() as db:
            db.add(model)
            await _flush_or_commit(db)
        return _model_to_entry(model)

    async def update_timeline_entry(self, entry_id: str, **kwargs: Any) -> TimelineEntry:
        allowed_fields = {
            "session_id",
            "entry_type",
            "title",
            "description",
            "content",
            "turn_id",
            "status",
            "tags",
            "metadata",
        }
        unknown_fields = sorted(set(kwargs) - allowed_fields)
        if unknown_fields:
            message = f"Unsupported timeline entry fields: {', '.join(unknown_fields)}"
            raise ValueError(message)

        async with self._session() as db:
            model = await self._get_model(db, entry_id)
            if model is None:
                raise ValueError(f"Timeline entry {entry_id} not found")

            for key, value in kwargs.items():
                if key == "session_id":
                    model.session_id = _to_uuid(value, "session_id")
                elif key == "entry_type":
                    _validate_entry_type(value)
                    model.entry_type = value
                elif key == "turn_id":
                    model.turn_id = _to_uuid(value, "turn_id")
                elif key == "status":
                    _validate_status(value)
                    model.status = value
                elif key == "tags":
                    model.tags = list(value or [])
                elif key == "metadata":
                    model.metadata_json = dict(value or {})
                else:
                    setattr(model, key, value)

            model.updated_at = _now()
            await _flush_or_commit(db)
            return _model_to_entry(model)

    async def get_timeline(
        self,
        session_id: str,
        filter: TimelineFilter | None = None,
    ) -> list[TimelineEntry]:
        timeline_filter = filter or TimelineFilter()
        effective_session_id = timeline_filter.session_id or session_id
        statement = sa.select(RelationshipTimelineEntryModel).where(
            RelationshipTimelineEntryModel.session_id == _to_uuid(
                effective_session_id,
                "session_id",
            )
        )
        statement = _apply_filter(statement, timeline_filter)
        statement = statement.order_by(RelationshipTimelineEntryModel.created_at.desc())

        async with self._session() as db:
            result = await db.execute(statement)
            entries = [_model_to_entry(model) for model in result.scalars().all()]

        if timeline_filter.tags:
            filter_tags = set(timeline_filter.tags)
            entries = [
                entry for entry in entries if filter_tags.issubset(set(entry.tags))
            ]
        return entries

    async def get_active_projects(self, session_id: str) -> list[TimelineEntry]:
        return await self.get_timeline(
            session_id,
            TimelineFilter(entry_types=["running_project"], statuses=["active"]),
        )

    async def get_pending_promises(self, session_id: str) -> list[TimelineEntry]:
        return await self.get_timeline(
            session_id,
            TimelineFilter(entry_types=["promise"], statuses=["active"]),
        )

    async def resolve_entry(
        self,
        entry_id: str,
        resolution: str | None = None,
    ) -> TimelineEntry:
        async with self._session() as db:
            model = await self._get_model(db, entry_id)
            if model is None:
                raise ValueError(f"Timeline entry {entry_id} not found")

            if model.entry_type in {"milestone", "running_project"}:
                model.status = "completed"
            else:
                model.status = "resolved"
            metadata = dict(model.metadata_json or {})
            if resolution is not None:
                metadata["resolution"] = resolution
            model.metadata_json = metadata
            model.updated_at = _now()
            await _flush_or_commit(db)
            return _model_to_entry(model)

    async def get_recent_milestones(
        self,
        session_id: str,
        limit: int = 10,
    ) -> list[TimelineEntry]:
        statement = (
            sa.select(RelationshipTimelineEntryModel)
            .where(
                RelationshipTimelineEntryModel.session_id == _to_uuid(
                    session_id,
                    "session_id",
                ),
                RelationshipTimelineEntryModel.entry_type == "milestone",
            )
            .order_by(RelationshipTimelineEntryModel.created_at.desc())
            .limit(limit)
        )

        async with self._session() as db:
            result = await db.execute(statement)
            return [_model_to_entry(model) for model in result.scalars().all()]

    async def _get_model(
        self,
        db: AsyncSession,
        entry_id: str,
    ) -> RelationshipTimelineEntryModel | None:
        result = await db.execute(
            sa.select(RelationshipTimelineEntryModel).where(
                RelationshipTimelineEntryModel.id == _to_uuid(entry_id, "entry_id")
            )
        )
        return result.scalar_one_or_none()

    @asynccontextmanager
    async def _session(self) -> Any:
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


_default_timeline = RelationshipTimeline()


async def add_timeline_entry(
    session_id: str,
    entry_type: str,
    title: str,
    description: str,
    content: str,
    turn_id: str,
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> TimelineEntry:
    return await _default_timeline.add_timeline_entry(
        session_id=session_id,
        entry_type=entry_type,
        title=title,
        description=description,
        content=content,
        turn_id=turn_id,
        tags=tags,
        metadata=metadata,
    )


async def update_timeline_entry(entry_id: str, **kwargs: Any) -> TimelineEntry:
    return await _default_timeline.update_timeline_entry(entry_id, **kwargs)


async def get_timeline(
    session_id: str,
    filter: TimelineFilter | None = None,
) -> list[TimelineEntry]:
    return await _default_timeline.get_timeline(session_id, filter)


async def get_active_projects(session_id: str) -> list[TimelineEntry]:
    return await _default_timeline.get_active_projects(session_id)


async def get_pending_promises(session_id: str) -> list[TimelineEntry]:
    return await _default_timeline.get_pending_promises(session_id)


async def resolve_entry(entry_id: str, resolution: str | None = None) -> TimelineEntry:
    return await _default_timeline.resolve_entry(entry_id, resolution)


async def get_recent_milestones(session_id: str, limit: int = 10) -> list[TimelineEntry]:
    return await _default_timeline.get_recent_milestones(session_id, limit)


def _apply_filter(
    statement: sa.Select[Any],
    timeline_filter: TimelineFilter,
) -> sa.Select[Any]:
    if timeline_filter.entry_types:
        for entry_type in timeline_filter.entry_types:
            _validate_entry_type(entry_type)
        statement = statement.where(
            RelationshipTimelineEntryModel.entry_type.in_(timeline_filter.entry_types)
        )
    if timeline_filter.statuses:
        for status in timeline_filter.statuses:
            _validate_status(status)
        statement = statement.where(
            RelationshipTimelineEntryModel.status.in_(timeline_filter.statuses)
        )
    if timeline_filter.date_from:
        statement = statement.where(
            RelationshipTimelineEntryModel.created_at >= _parse_datetime(timeline_filter.date_from)
        )
    if timeline_filter.date_to:
        statement = statement.where(
            RelationshipTimelineEntryModel.created_at <= _parse_datetime(timeline_filter.date_to)
        )
    return statement


def _model_to_entry(model: RelationshipTimelineEntryModel) -> TimelineEntry:
    return TimelineEntry(
        entry_id=str(model.id),
        session_id=str(model.session_id),
        entry_type=_entry_type(model.entry_type),
        title=model.title,
        description=model.description,
        content=model.content,
        turn_id=str(model.turn_id),
        created_at=_dt_iso(model.created_at),
        updated_at=_dt_iso(model.updated_at),
        status=_status(model.status),
        tags=list(model.tags or []),
        metadata=dict(model.metadata_json or {}),
    )


def _entry_type(value: str) -> TimelineEntryType:
    _validate_entry_type(value)
    return value  # type: ignore[return-value]


def _status(value: str) -> TimelineEntryStatus:
    _validate_status(value)
    return value  # type: ignore[return-value]


def _validate_entry_type(value: str) -> None:
    if value not in TIMELINE_ENTRY_TYPES:
        raise ValueError(f"Unsupported timeline entry type: {value}")


def _validate_status(value: str) -> None:
    if value not in TIMELINE_ENTRY_STATUSES:
        raise ValueError(f"Unsupported timeline entry status: {value}")


def _to_uuid(value: str | uuid.UUID, field_name: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a UUID string") from exc


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse_datetime(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def _flush_or_commit(db: Any) -> None:
    in_transaction = getattr(db, "in_transaction", None)
    if callable(in_transaction) and in_transaction():
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
    "TIMELINE_ENTRY_STATUSES",
    "TIMELINE_ENTRY_TYPES",
    "RelationshipTimeline",
    "TimelineEntry",
    "TimelineEntryStatus",
    "TimelineEntryType",
    "TimelineEvent",
    "TimelineFilter",
    "add_timeline_entry",
    "get_active_projects",
    "get_pending_promises",
    "get_recent_milestones",
    "get_timeline",
    "resolve_entry",
    "update_timeline_entry",
]
