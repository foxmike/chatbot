from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from dataclasses_json import dataclass_json


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass_json
@dataclass(slots=True, kw_only=True)
class SoulState:
    """Durable chatbot soul state.

    Operator deployments should keep this state single-sourced in Postgres.
    The in-memory repository below is intended only for development and tests.
    """

    session_id: str
    user_id: str
    personality_id: str
    turn_count: int = 0
    conversation_summary: Optional[str] = None
    identity_anchor: str
    user_name: Optional[str] = None
    user_pronouns: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)


class StateRepository(ABC):
    @abstractmethod
    async def get_state(self, session_id: str) -> Optional[SoulState]:
        """Return state for a session if one exists."""

    @abstractmethod
    async def save_state(self, state: SoulState) -> None:
        """Persist state by session id."""

    @abstractmethod
    async def delete_state(self, session_id: str) -> None:
        """Delete state for a session if one exists."""


class InMemoryStateRepository(StateRepository):
    """Development and test repository backed by a process-local dict."""

    def __init__(self) -> None:
        self._states: dict[str, SoulState] = {}

    async def get_state(self, session_id: str) -> Optional[SoulState]:
        return self._states.get(session_id)

    async def save_state(self, state: SoulState) -> None:
        self._states[state.session_id] = state

    async def delete_state(self, session_id: str) -> None:
        self._states.pop(session_id, None)


__all__ = [
    "InMemoryStateRepository",
    "SoulState",
    "StateRepository",
]
