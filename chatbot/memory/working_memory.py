"""Working-memory history loader for the chatbot engine."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import inspect
import uuid
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class WorkingMemoryTurn:
    role: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": self.role,
            "content": self.content,
        }
        if self.metadata:
            message["metadata"] = dict(self.metadata)
        return message


@dataclass(frozen=True, slots=True)
class WorkingMemoryWindow:
    turns: tuple[WorkingMemoryTurn, ...]
    max_turns: int

    def as_messages(self) -> list[dict[str, Any]]:
        return [turn.as_message() for turn in self.turns]


class WorkingMemoryProvider(Protocol):
    def window(self, size: int | None = None) -> WorkingMemoryWindow:
        """Return a bounded window of recent conversation turns."""


class WorkingMemoryBuffer:
    def __init__(
        self,
        max_turns: int,
        turns: Iterable[WorkingMemoryTurn] | None = None,
    ) -> None:
        if max_turns <= 0:
            raise ValueError("max_turns must be greater than zero")

        self.max_turns = max_turns
        self._turns: deque[WorkingMemoryTurn] = deque(maxlen=max_turns)
        if turns is not None:
            for turn in turns:
                self.append(turn)

    def append(self, turn: WorkingMemoryTurn) -> None:
        self._turns.append(turn)

    def truncate(self, size: int | None = None) -> None:
        if size is None:
            size = self.max_turns
        if size < 0:
            raise ValueError("size must be non-negative")
        if size >= len(self._turns):
            return

        retained = list(self._turns)[-size:] if size else []
        self._turns = deque(retained, maxlen=self.max_turns)

    def window(self, size: int | None = None) -> WorkingMemoryWindow:
        if size is None:
            size = self.max_turns
        if size < 0:
            raise ValueError("size must be non-negative")

        turns: Sequence[WorkingMemoryTurn]
        if size == 0:
            turns = ()
        else:
            turns = tuple(self._turns)[-size:]

        return WorkingMemoryWindow(
            turns=tuple(turns),
            max_turns=self.max_turns,
        )

    def clear(self) -> None:
        self._turns.clear()

    def __len__(self) -> int:
        return len(self._turns)

    def __iter__(self) -> Iterable[WorkingMemoryTurn]:
        return iter(self._turns)


class WorkingMemoryLoader:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def load(
        self,
        session_id: str,
        max_turns: int = 20,
        max_tokens_estimate: int = 4000,
    ) -> list[dict]:
        if not session_id or self.store is None:
            return []
        if max_turns <= 0 or max_tokens_estimate <= 0:
            return []

        try:
            raw_turns = await self._load_raw_turns(session_id, max_turns)
        except Exception:
            return []

        history: list[dict] = []
        token_total = 0
        for raw_turn in raw_turns:
            if len(history) >= max_turns:
                break

            message = _message_from_turn(raw_turn)
            if message is None:
                continue

            token_cost = _estimate_tokens(message["content"])
            if token_total + token_cost > max_tokens_estimate:
                break

            history.append(message)
            token_total += token_cost

        history.reverse()
        return history

    async def _load_raw_turns(self, session_id: str, max_turns: int) -> list[Any]:
        session = _db_session(self.store)
        if session is not None:
            rows = await _fetch_recent_db_messages(session, session_id, max_turns)
            if rows:
                return rows

        adapter_rows = await _fetch_recent_adapter_turns(
            self.store,
            session_id,
            max_turns,
        )
        return adapter_rows


async def load_working_memory(
    session_id: str,
    store: Any,
    max_turns: int = 20,
) -> list[dict]:
    loader = WorkingMemoryLoader(store)
    return await loader.load(session_id, max_turns=max_turns)


async def _fetch_recent_db_messages(
    session: Any,
    session_id: str,
    max_turns: int,
) -> list[Any]:
    try:
        import sqlalchemy as sa

        from chatbot.db import models

        stmt = (
            sa.select(models.Message)
            .where(models.Message.session_id == _coerce_session_id(session_id))
            .where(models.Message.role.in_(("user", "assistant")))
            .order_by(models.Message.created_at.desc(), models.Message.id.desc())
            .limit(max_turns)
        )
        result = await _maybe_await(session.execute(stmt))
        scalars = result.scalars()
        return list(scalars.all())
    except Exception:
        return []


async def _fetch_recent_adapter_turns(
    store: Any,
    session_id: str,
    max_turns: int,
) -> list[Any]:
    if isinstance(store, Mapping):
        turns = store.get(session_id, [])
        return _newest_first_list(turns, max_turns)

    for method_name in (
        "fetch_recent_turns",
        "get_recent_turns",
        "load_recent_turns",
        "recent_turns",
        "fetch_history",
        "get_history",
        "load_history",
        "query_by_session",
    ):
        method = getattr(store, method_name, None)
        if not callable(method):
            continue

        turns = await _call_adapter_method(method, session_id, max_turns)
        if turns is not None:
            return _newest_first_list(turns, max_turns)

    return []


async def _call_adapter_method(
    method: Any,
    session_id: str,
    max_turns: int,
) -> Any | None:
    call_patterns = (
        ((), {"session_id": session_id, "max_turns": max_turns}),
        ((), {"session_id": session_id, "limit": max_turns}),
        ((), {"session_id": session_id, "top_k": max_turns}),
        ((session_id,), {"max_turns": max_turns}),
        ((session_id,), {"limit": max_turns}),
        ((session_id,), {"top_k": max_turns}),
        ((session_id, max_turns), {}),
        ((session_id,), {}),
    )
    for args, kwargs in call_patterns:
        try:
            return await _maybe_await(method(*args, **kwargs))
        except TypeError:
            continue
        except Exception:
            return None
    return None


def _newest_first_list(turns: Any, max_turns: int) -> list[Any]:
    if turns is None:
        return []
    if isinstance(turns, str):
        return []

    try:
        items = list(turns)
    except TypeError:
        return []

    return items[:max_turns]


def _message_from_turn(turn: Any) -> dict[str, str] | None:
    nested_turn = getattr(turn, "turn", None)
    if nested_turn is not None:
        return _message_from_turn(nested_turn)

    role: Any
    content: Any
    if isinstance(turn, Mapping):
        role = turn.get("role")
        content = turn.get("content")
    else:
        role = getattr(turn, "role", None)
        content = getattr(turn, "content", None)

    if role not in {"user", "assistant"} or content is None:
        return None

    return {"role": str(role), "content": str(content)}


def _estimate_tokens(content: str) -> int:
    if not content:
        return 0
    return (len(content) + 3) // 4


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _db_session(store: Any) -> Any | None:
    if hasattr(store, "execute"):
        return store

    session = getattr(store, "session", None)
    if session is not None and hasattr(session, "execute"):
        return session

    return None


def _coerce_session_id(session_id: str) -> uuid.UUID | str:
    try:
        return uuid.UUID(session_id)
    except (TypeError, ValueError, AttributeError):
        return session_id


__all__ = [
    "WorkingMemoryBuffer",
    "WorkingMemoryLoader",
    "WorkingMemoryProvider",
    "WorkingMemoryTurn",
    "WorkingMemoryWindow",
    "load_working_memory",
]
