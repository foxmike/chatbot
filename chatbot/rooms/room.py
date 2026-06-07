"""Room model with typed rooms and per-room memory policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

import sqlalchemy as sa

from chatbot.db import async_session_factory
from chatbot.db.models import Room as RoomRecord


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
# Conversion helpers
# ---------------------------------------------------------------------------

def _created_at_to_domain(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _created_at_to_model(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _model_to_domain(model: RoomRecord) -> Room:
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
        created_at=_created_at_to_domain(model.created_at),
        metadata=model.metadata_json or {},
    )


def _domain_to_model(room: Room, model: RoomRecord | None = None) -> RoomRecord:
    if model is None:
        model = RoomRecord()
    model.room_id = room.room_id
    model.user_id = room.user_id
    model.companion_id = room.companion_id
    model.room_type = room.room_type.value
    model.display_name = room.display_name
    model.scene_id = room.scene_id
    model.created_at = _created_at_to_model(room.created_at)
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
            sa.select(RoomRecord).where(RoomRecord.room_id == room_id)
        )
        model = result.scalar_one_or_none()
    if model is None:
        return None
    return _model_to_domain(model)


async def get_user_rooms(user_id: str) -> list[Room]:
    """List all rooms belonging to a user."""
    async with async_session_factory() as session:
        result = await session.execute(
            sa.select(RoomRecord)
            .where(RoomRecord.user_id == user_id)
            .order_by(RoomRecord.created_at.desc())
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
            sa.select(RoomRecord).where(RoomRecord.room_id == room_id)
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
    "RoomType",
    "create_room",
    "get_room",
    "get_user_rooms",
    "update_room_memory_policy",
]
