"""Rooms and roleplay package. Typed rooms with per-room memory policy, scene state engine, and roleplay memory isolation."""

from chatbot.rooms.room import (
    DEFAULT_ROOM_POLICIES,
    Room,
    RoomMemoryPolicy,
    RoomType,
    create_room,
    get_room,
    get_user_rooms,
    update_room_memory_policy,
)


# SceneState and RoleplayEngine will be implemented in future tasks.
try:
    from chatbot.rooms.scene import SceneState
except ImportError:
    SceneState = None  # type: ignore[assignment,misc]

try:
    from chatbot.rooms.engine import RoleplayEngine
except ImportError:
    RoleplayEngine = None  # type: ignore[assignment,misc]


__all__ = [
    "DEFAULT_ROOM_POLICIES",
    "Room",
    "RoomMemoryPolicy",
    "RoomType",
    "RoleplayEngine",
    "SceneState",
    "create_room",
    "get_room",
    "get_user_rooms",
    "update_room_memory_policy",
]