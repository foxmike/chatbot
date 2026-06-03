"""Room model with typed rooms and per-room memory policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase

from chatbot.db import async_session_factory


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RoomType(str, Enum):
    ONE_ON_ONE_CHAT = "one_on_one_chat"
    ROLEPLAY_SCENE = "roleplay_scene"
    PROJECT = "project"
    EMOTIONAL_SUPPORT = "emotional_support"
    MEMORY_REVIEW = "memory_review"


# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RoomMemoryPolicy:
    """Per-room memory behaviour controls."""

    persist_to_durable: bool = True
    scope: Literal["room", "companion", "global"] = "room"
    max_context_turns: int = 50
    include_embeddings: bool = True


@dataclass(slots=True)
class Room:
    room_id: str
    user_id: str
    companion_id: str | None
    room_type: RoomType
    display_name: str
    memory_policy: RoomMemoryPolicy
    scene_id: str | None
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Default policies per room type
# ---------------------------------------------------------------------------

DEFAULT_ROOM_POLICIES: dict[RoomType, RoomMemoryPolicy] = {
    RoomType.ONE_ON_ONE_CHAT: RoomMemoryPolicy(
        persist_to_durable=True,
        scope="room",
        max_context_turns=50,
        include_embeddings=True,
    ),
    RoomType.ROLEPLAY_SCENE: RoomMemoryPolicy(
        persist_to_durable=False,
        scope="room",
        max_context_turns=50,
        include_embeddings=True,
    ),
    RoomType.PROJECT: RoomMemoryPolicy(
        persist_to_durable=True,
        scope="room",
        max_context_turns=50,
        include_embeddings=True,
    ),
    RoomType.EMOTIONAL_SUPPORT: RoomMemoryPolicy(
        persist_to_durable=True,
        scope="room",
        max_context_turns=50,
        include_embeddings=True,
    ),
    RoomType.MEMORY_REVIEW: RoomMemoryPolicy(
        persist_to_durable=True,
        scope="room",
        max_context_turns=200,
        include_embeddings=True,
    ),
}


# ---------------------------------------------------------------------------
# SQLAlchemy ORM model
# ---------------------------------------------------------------------------

class _RoomDeclarativeBase(DeclarativeBase):
    pass


class RoomModel(_RoomDeclarativeBase):
    __tablename__ = "rooms"

    room_id: sa.orm.Mapped[str] = sa.orm.mapped_column(
        sa.String(36),
        primary_key=True,
    )
    user_id: sa.orm.Mapped[str] = sa.orm.mapped_column(
        sa.String(36),
        index=True,
        nullable=False,
    )
    companion_id: sa.orm.Mapped[str | None] = sa.orm.mapped_column(
        sa.String(36),
        nullable=True,
    )
    room_type: sa.orm.Mapped[str] = sa.orm.mapped_column(
        sa.String(32),
        nullable=False,
    )
    display_name: sa.orm.Mapped[str] = sa.orm.mapped_column(
        sa.String(255),
        nullable=False,
    )
    scene_id: sa.orm.Mapped[str | None] = sa.orm.mapped_column(
        sa.String(36),
        nullable=True,
    )
    created_at: sa.orm.Mapped[str] = sa.orm.mapped_column(
        sa.String(64),
        nullable=False,
    )
    metadata_json: sa.orm.Mapped[dict[str, object] | None] = sa.orm.mapped_column(
        sa.JSON,
        nullable=True,
    )
    # Memory policy columns (denormalised for per-room queries/overrides)
    persist_to_durable: sa.orm.Mapped[bool] = sa.orm.mapped_column(
        sa.Boolean,
        nullable=False,
        default=True,
    )
    memory_scope: sa.orm.Mapped[str] = sa.orm.mapped_column(
        sa.String(32),
        nullable=False,
        default="room",
    )
    max_context_turns: sa.orm.Mapped[int] = sa.orm.mapped_column(
        sa.Integer,
        nullable=False,
        default=50,
    )
    include_embeddings: sa.orm.Mapped[bool] = sa.orm.mapped_column(
        sa.Boolean,
        nullable=False,
        default=True,
    )


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

def _model_to_domain(model: RoomModel) -> Room:
    return Room(
        room_id=model.room_id,
        user_id=model.user_id,
        companion_id=model.companion_id,
        room_type=RoomType(model.room_type),
        display_name=model.display_name,
        memory_policy=RoomMemoryPolicy(
            persist_to_durable=model.persist_to_durable,
            scope=model.memory_scope,  # type: ignore[arg-type]
            max_context_turns=model.max_context_turns,
            include_embeddings=model.include_embeddings,
        ),
        scene_id=model.scene_id,
        created_at=model.created_at,
        metadata=model.metadata_json or {},
    )


def _domain_to_model(room: Room, model: RoomModel | None = None) -> RoomModel:
    if model is None:
        model = RoomModel()
    model.room_id = room.room_id
    model.user_id = room.user_id
    model.companion_id = room.companion_id
    model.room_type = room.room_type.value
    model.display_name = room.display_name
    model.scene_id = room.scene_id
    model.created_at = room.created_at
    model.metadata_json = room.metadata or {}
    model.persist_to_durable = room.memory_policy.persist_to_durable
    model.memory_scope = room.memory_policy.scope
    model.max_context_turns = room.memory_policy.max_context_turns
    model.include_embeddings = room.memory_policy.include_embeddings
    return model


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------

async def create_room(
    user_id: str,
    room_type: RoomType,
    display_name: str | None = None,
    companion_id: str | None = None,
) -> Room:
    """Create a new room with a fresh uuid4 id and the default memory policy."""
    import uuid

    policy = DEFAULT_ROOM_POLICIES[room_type]
    room = Room(
        room_id=str(uuid.uuid4()),
        user_id=user_id,
        companion_id=companion_id,
        room_type=room_type,
        display_name=display_name or f"{room_type.value}_{room.room_id[:8]}",
        memory_policy=policy,
        scene_id=None,
        created_at=_utcnow_iso(),
        metadata={},
    )

    model = _domain_to_model(room)
    async with async_session_factory() as session:
        session.add(model)
        await session.commit()
    return room


async def get_room(room_id: str) -> Room | None:
    """Read a room by id from the database."""
    async with async_session_factory() as session:
        result = await session.execute(
            sa.select(RoomModel).where(RoomModel.room_id == room_id)
        )
        model = result.scalar_one_or_none()
    if model is None:
        return None
    return _model_to_domain(model)


async def get_user_rooms(user_id: str) -> list[Room]:
    """List all rooms belonging to a user."""
    async with async_session_factory() as session:
        result = await session.execute(
            sa.select(RoomModel)
            .where(RoomModel.user_id == user_id)
            .order_by(RoomModel.created_at.desc())
        )
        models = result.scalars().all()
    return [_model_to_domain(m) for m in models]


async def update_room_memory_policy(
    room_id: str,
    policy: RoomMemoryPolicy,
) -> None:
    """Override the memory policy for an existing room."""
    async with async_session_factory() as session:
        result = await session.execute(
            sa.select(RoomModel).where(RoomModel.room_id == room_id)
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"Room not found: {room_id!r}")
        model.persist_to_durable = policy.persist_to_durable
        model.memory_scope = policy.scope
        model.max_context_turns = policy.max_context_turns
        model.include_embeddings = policy.include_embeddings
        await session.commit()


__all__ = [
    "DEFAULT_ROOM_POLICIES",
    "Room",
    "RoomMemoryPolicy",
    "RoomModel",
    "RoomType",
    "create_room",
    "get_room",
    "get_user_rooms",
    "update_room_memory_policy",
]