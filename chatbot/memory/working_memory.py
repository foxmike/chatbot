from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
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


__all__ = [
    "WorkingMemoryBuffer",
    "WorkingMemoryProvider",
    "WorkingMemoryTurn",
    "WorkingMemoryWindow",
]
