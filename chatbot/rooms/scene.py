"""Scene state engine for roleplay and contextual rooms. Tracks location, activity, tone, intensity, and supports pause/resume across sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from chatbot.db import async_session_factory


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


ToneType = Literal["light", "serious", "tense", "playful", "romantic", "neutral"]

ALLOWED_UPDATE_FIELDS: frozenset[str] = frozenset({
    "location",
    "activity",
    "tone",
    "intensity",
    "is_paused",
    "scene_context",
    "participants",
})


# ---------------------------------------------------------------------------
# Domain dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SceneState:
    """Immutable-ish snapshot of a roleplay or contextual scene."""

    scene_id: str
    room_id: str
    location: str = ""
    activity: str = ""
    tone: ToneType = "neutral"
    intensity: int = 5
    is_paused: bool = False
    started_at: datetime = field(default_factory=_utcnow)
    paused_at: datetime | None = None
    resumed_at: datetime | None = None
    ended_at: datetime | None = None
    participants: list[str] = field(default_factory=list)
    scene_context: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.intensity = max(1, min(10, self.intensity))


# ---------------------------------------------------------------------------
# SQLAlchemy ORM model
# ---------------------------------------------------------------------------


class _SceneDeclarativeBase(DeclarativeBase):
    pass


class SceneModel(_SceneDeclarativeBase):
    __tablename__ = "scenes"

    scene_id: Mapped[str] = mapped_column(
        sa.String(36),
        primary_key=True,
    )
    room_id: Mapped[str] = mapped_column(
        sa.String(36),
        index=True,
        nullable=False,
    )
    location: Mapped[str] = mapped_column(
        sa.String(255),
        nullable=False,
        default="",
    )
    activity: Mapped[str] = mapped_column(
        sa.String(255),
        nullable=False,
        default="",
    )
    tone: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        default="neutral",
    )
    intensity: Mapped[int] = mapped_column(
        sa.Integer,
        nullable=False,
        default=5,
    )
    is_paused: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        default=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.sql.func.now(),
    )
    paused_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    resumed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    participants: Mapped[list[str]] = mapped_column(
        sa.JSON,
        nullable=False,
        default=list,
    )
    scene_context: Mapped[str | None] = mapped_column(
        sa.Text,
        nullable=True,
    )
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(
        sa.JSON,
        nullable=True,
    )


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def _model_to_domain(model: SceneModel) -> SceneState:
    return SceneState(
        scene_id=model.scene_id,
        room_id=model.room_id,
        location=model.location,
        activity=model.activity,
        tone=model.tone,  # type: ignore[arg-type]
        intensity=model.intensity,
        is_paused=model.is_paused,
        started_at=model.started_at,
        paused_at=model.paused_at,
        resumed_at=model.resumed_at,
        ended_at=model.ended_at,
        participants=list(model.participants),
        scene_context=model.scene_context or "",
        metadata=model.metadata_json or {},
    )


def _domain_to_model(scene: SceneState, model: SceneModel | None = None) -> SceneModel:
    if model is None:
        model = SceneModel()
    model.scene_id = scene.scene_id
    model.room_id = scene.room_id
    model.location = scene.location
    model.activity = scene.activity
    model.tone = scene.tone
    model.intensity = scene.intensity
    model.is_paused = scene.is_paused
    model.started_at = scene.started_at
    model.paused_at = scene.paused_at
    model.resumed_at = scene.resumed_at
    model.ended_at = scene.ended_at
    model.participants = list(scene.participants)
    model.scene_context = scene.scene_context
    model.metadata_json = scene.metadata or {}
    return model


async def _fetch_model(scene_id: str) -> SceneModel:
    """Return the SceneModel for *scene_id* or raise ValueError."""
    async with async_session_factory() as session:
        result = await session.execute(
            sa.select(SceneModel).where(SceneModel.scene_id == scene_id),
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"Scene not found: {scene_id!r}")
        return model


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


async def create_scene(
    room_id: str,
    location: str = "",
    activity: str = "",
) -> SceneState:
    """Create a new scene with a fresh uuid4 id and persist to the database."""
    scene = SceneState(
        scene_id=str(uuid.uuid4()),
        room_id=room_id,
        location=location,
        activity=activity,
    )
    model = _domain_to_model(scene)
    async with async_session_factory() as session:
        session.add(model)
        await session.commit()
    return scene


async def get_scene(scene_id: str) -> SceneState | None:
    """Read a scene by id from the database.  Returns None if not found."""
    async with async_session_factory() as session:
        result = await session.execute(
            sa.select(SceneModel).where(SceneModel.scene_id == scene_id),
        )
        model = result.scalar_one_or_none()
    if model is None:
        return None
    return _model_to_domain(model)


async def update_scene(scene_id: str, **kwargs: Any) -> SceneState:
    """Apply partial updates to an existing scene.

    Accepted keys: location, activity, tone, intensity, is_paused,
    scene_context, participants.

    Raises ValueError if *scene_id* is not found or an unknown key is
    supplied.
    """
    unknown = set(kwargs) - ALLOWED_UPDATE_FIELDS
    if unknown:
        raise ValueError(f"Unknown update fields: {sorted(unknown)}")

    async with async_session_factory() as session:
        result = await session.execute(
            sa.select(SceneModel).where(SceneModel.scene_id == scene_id),
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"Scene not found: {scene_id!r}")

        for key, value in kwargs.items():
            if key == "participants":
                model.participants = list(value)
            else:
                setattr(model, key, value)

        await session.commit()

    # Re-fetch to get a fresh domain object with clamped intensity, etc.
    return await get_scene(scene_id)  # type: ignore[return-value]


async def pause_scene(scene_id: str) -> SceneState:
    """Pause a scene: set is_paused=True and record *paused_at*."""
    async with async_session_factory() as session:
        result = await session.execute(
            sa.select(SceneModel).where(SceneModel.scene_id == scene_id),
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"Scene not found: {scene_id!r}")
        model.is_paused = True
        model.paused_at = _utcnow()
        await session.commit()
    return _model_to_domain(model)


async def resume_scene(scene_id: str) -> SceneState:
    """Resume a paused scene.  Raises ValueError if the scene has already
    been ended."""
    async with async_session_factory() as session:
        result = await session.execute(
            sa.select(SceneModel).where(SceneModel.scene_id == scene_id),
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"Scene not found: {scene_id!r}")
        if model.ended_at is not None:
            raise ValueError(
                f"Cannot resume scene {scene_id!r}: it was ended at {model.ended_at}",
            )
        model.is_paused = False
        model.resumed_at = _utcnow()
        await session.commit()
    return _model_to_domain(model)


async def end_scene(scene_id: str) -> SceneState:
    """End a scene permanently.  Records *ended_at* and unpauses.

    Once ended, ``resume_scene`` will raise ValueError.
    """
    async with async_session_factory() as session:
        result = await session.execute(
            sa.select(SceneModel).where(SceneModel.scene_id == scene_id),
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"Scene not found: {scene_id!r}")
        model.is_paused = False
        model.ended_at = _utcnow()
        await session.commit()
    return _model_to_domain(model)


async def get_active_scene(room_id: str) -> SceneState | None:
    """Return the most recent unpaused, un-ended scene for *room_id*.

    Returns None when no active scene exists for the room.
    """
    async with async_session_factory() as session:
        result = await session.execute(
            sa.select(SceneModel)
            .where(
                SceneModel.room_id == room_id,
                SceneModel.is_paused == False,  # noqa: E712
                SceneModel.ended_at == None,  # noqa: E711
            )
            .order_by(SceneModel.started_at.desc())
            .limit(1),
        )
        model = result.scalar_one_or_none()
    if model is None:
        return None
    return _model_to_domain(model)


async def get_scene_summary(scene_id: str) -> str:
    """Return a one-paragraph text summary suitable for context injection.

    Includes: location, activity, tone, intensity, participants, and
    scene duration.
    """
    scene = await get_scene(scene_id)
    if scene is None:
        raise ValueError(f"Scene not found: {scene_id!r}")

    # Determine duration
    if scene.ended_at:
        duration_label = f"lasted until {scene.ended_at}"
    elif scene.is_paused:
        duration_label = f"started at {scene.started_at} (currently paused)"
    else:
        duration_label = f"ongoing since {scene.started_at}"

    participants_str = ", ".join(scene.participants) if scene.participants else "no participants recorded"

    summary = (
        f"Scene in {scene.location or 'an unspecified location'} "
        f"involving {scene.activity or 'no recorded activity'}. "
        f"The tone is {scene.tone} with an intensity of {scene.intensity}/10. "
        f"Participants: {participants_str}. "
        f"The scene {duration_label}."
    )

    if scene.scene_context:
        summary += f" Context notes: {scene.scene_context}"

    return summary


__all__ = [
    "ALLOWED_UPDATE_FIELDS",
    "SceneModel",
    "SceneState",
    "ToneType",
    "create_scene",
    "end_scene",
    "get_active_scene",
    "get_scene",
    "get_scene_summary",
    "pause_scene",
    "resume_scene",
    "update_scene",
]
