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
    short_response_permitted: bool | None = None


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


def evaluate_short_response_permission(
    ctx: TurnContext,
    *,
    min_user_message_length: int = 30,
    min_history_turns: int = 2,
) -> bool:
    """Evaluate whether the chatbot has permission to reply with a short response
    based on conversational context.

    Returns True if a short response is permitted, False if a full/elaborated
    response should be generated instead.

    Evaluation rules (earliest match wins):
    1. If no history exists or only a single turn, short is permitted (cold start).
    2. If intent classification indicates CASUAL, GREETING, or FEEDBACK, short is
       permitted.
    3. If intent classification indicates VENTING, ROLEPLAY, PLANNING, QUESTION, or
       COMMAND, short is NOT permitted (requires substantive response).
    4. If the user message is very short (< min_user_message_length), short is
       permitted (brevity mirroring).
    5. If the conversation has few turns (< min_history_turns), short is permitted.
    6. Default: short is permitted (less restrictive fallback).

    The caller should store the result in ctx.short_response_permitted.
    """
    # Rule 1: Cold start — no history or a single turn
    history = ctx.history or []
    if len(history) <= 1:
        return True

    # Rule 2: Intent-based permission (categorical, overrides message length)
    intent = _intent_value(ctx)
    _SHORT_PERMITTED_INTENTS = {"casual", "greeting", "feedback"}
    _SHORT_DENIED_INTENTS = {"venting", "roleplay", "planning", "question", "command"}

    if intent in _SHORT_PERMITTED_INTENTS:
        return True
    if intent in _SHORT_DENIED_INTENTS:
        return False

    # Rule 3: User message brevity mirroring
    user_msg = (ctx.user_message or "").strip()
    if len(user_msg) < min_user_message_length:
        return True

    # Rule 4: Shallow conversation — few turns
    if len(history) < min_history_turns:
        return True

    # Rule 5: Default — permit short response
    return True


def _intent_value(ctx: TurnContext) -> str:
    """Extract the canonical intent string from a TurnContext."""
    classification = ctx.intent_classification
    if classification is not None:
        intent_attr = getattr(classification, "intent", None)
        if intent_attr is not None:
            from enum import Enum

            if isinstance(intent_attr, Enum):
                return intent_attr.value
            return str(intent_attr)
    raw = ctx.metadata.get("intent")
    if raw is not None:
        return str(raw)
    return "casual"


__all__ = [
    "StepResult",
    "TurnContext",
    "TurnStep",
    "evaluate_short_response_permission",
]
