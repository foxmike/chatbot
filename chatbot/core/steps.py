"""Minimal turn pipeline step interface for the chatbot engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TurnContext:
    session_id: str
    user_message: str
    history: list[dict]
    persona: dict[str, Any]
    retrieved_memories: list[dict]
    compiled_context: str | None
    provider_response: str | None
    extracted_memories: list[dict]
    final_response: str | None
    metadata: dict[str, Any] = field(default_factory=dict)
    retrieval_engine: Any | None = None
    intent_classification: Any | None = None


class TurnStep(ABC):
    @abstractmethod
    async def run(self, ctx: TurnContext) -> TurnContext:
        """Run this step against the turn context."""


@dataclass(slots=True)
class StepResult:
    step_name: str
    duration_ms: float
    success: bool
    error: str | None = None


__all__ = ["StepResult", "TurnContext", "TurnStep"]
