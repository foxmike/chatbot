"""Conversation mode selection for persona prompt assembly."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any, Optional

from chatbot.soul.mood import MoodState
from chatbot.soul.user_state import UserState


class ConversationMode(str, Enum):
    """High-level conversational frame for one response."""

    NORMAL = "normal"
    DEEP_TALK = "deep_talk"
    PLAYFUL = "playful"
    ROLEPLAY = "roleplay"
    PLANNING = "planning"
    VENTING = "venting"
    BEDTIME = "bedtime"


PROMPT_HINTS: dict[ConversationMode, str] = {
    ConversationMode.NORMAL: (
        "Conversation mode: normal. Keep the tone balanced, clear, and naturally paced."
    ),
    ConversationMode.DEEP_TALK: (
        "Conversation mode: deep talk. Be introspective, allow longer responses, "
        "ask thoughtful follow-up questions, and keep initiative low."
    ),
    ConversationMode.PLAYFUL: (
        "Conversation mode: playful. Use light humor and wordplay, keep replies witty "
        "and short, and bring higher energy."
    ),
    ConversationMode.ROLEPLAY: (
        "Conversation mode: roleplay. Use narrative framing, stay consistently "
        "in character, and support creative collaboration."
    ),
    ConversationMode.PLANNING: (
        "Conversation mode: planning. Be structured, list-oriented, goal-directed, "
        "and ask clarifying questions when needed."
    ),
    ConversationMode.VENTING: (
        "Conversation mode: venting. Validate first, use reflective listening, and "
        "avoid solutions unless the user asks for them."
    ),
    ConversationMode.BEDTIME: (
        "Conversation mode: bedtime. Keep energy low, soothing, and brief; do not "
        "introduce new topics."
    ),
}

RESPONSE_PARAMS: dict[ConversationMode, dict[str, float | bool | str]] = {
    ConversationMode.NORMAL: {
        "max_tokens_ratio": 1.0,
        "temperature_modifier": 0.0,
        "initiative_level": "medium",
        "should_suggest_topics": True,
    },
    ConversationMode.DEEP_TALK: {
        "max_tokens_ratio": 1.35,
        "temperature_modifier": -0.05,
        "initiative_level": "low",
        "should_suggest_topics": False,
    },
    ConversationMode.PLAYFUL: {
        "max_tokens_ratio": 0.75,
        "temperature_modifier": 0.15,
        "initiative_level": "high",
        "should_suggest_topics": True,
    },
    ConversationMode.ROLEPLAY: {
        "max_tokens_ratio": 1.15,
        "temperature_modifier": 0.2,
        "initiative_level": "medium",
        "should_suggest_topics": True,
    },
    ConversationMode.PLANNING: {
        "max_tokens_ratio": 1.1,
        "temperature_modifier": -0.1,
        "initiative_level": "medium",
        "should_suggest_topics": False,
    },
    ConversationMode.VENTING: {
        "max_tokens_ratio": 0.95,
        "temperature_modifier": -0.1,
        "initiative_level": "low",
        "should_suggest_topics": False,
    },
    ConversationMode.BEDTIME: {
        "max_tokens_ratio": 0.65,
        "temperature_modifier": -0.15,
        "initiative_level": "very_low",
        "should_suggest_topics": False,
    },
}

ROLEPLAY_MARKERS: tuple[str, ...] = (
    "act as",
    "as if you are",
    "be my character",
    "in character",
    "let's roleplay",
    "lets roleplay",
    "pretend you are",
    "role play",
    "role-play",
    "roleplay",
    "you are playing",
)

PLANNING_MARKERS: tuple[str, ...] = (
    "action plan",
    "break this down",
    "can you plan",
    "checklist",
    "figure out",
    "goal is",
    "goals are",
    "help me plan",
    "i need a plan",
    "make a plan",
    "next steps",
    "organize this",
    "plan for",
    "planning",
    "prioritize",
    "roadmap",
    "schedule",
    "step by step",
    "strategy",
    "to-do",
    "todo",
)

DEEP_TALK_MARKERS: tuple[str, ...] = (
    "afraid",
    "ashamed",
    "childhood",
    "confused about my life",
    "deep down",
    "depressed",
    "feel empty",
    "grief",
    "guilty",
    "heartbroken",
    "i feel",
    "i have been feeling",
    "i keep thinking",
    "i never told",
    "i'm scared",
    "im scared",
    "lonely",
    "my family",
    "my relationship",
    "personal",
    "private",
    "trauma",
    "vulnerable",
)


class ModeSelector:
    """Select deterministic conversation modes from local state and recent turns."""

    def select(
        self,
        messages: list[dict],
        user_state: Optional[UserState],
        mood_state: MoodState,
    ) -> ConversationMode:
        """Return the best conversation mode for the current turn."""

        primary_state = _primary_state(user_state)
        if primary_state in {"frustrated", "angry"}:
            return ConversationMode.VENTING

        if primary_state == "tired" or float(getattr(mood_state, "energy", 1.0)) < 0.3:
            return ConversationMode.BEDTIME

        if primary_state == "playful":
            return ConversationMode.PLAYFUL

        user_turns = _recent_user_turns(messages)
        recent_text = f" {' '.join(user_turns[-3:]).lower()} "

        if _contains_any(recent_text, ROLEPLAY_MARKERS):
            return ConversationMode.ROLEPLAY

        if _contains_any(recent_text, PLANNING_MARKERS):
            return ConversationMode.PLANNING

        if _is_rapid_deep_personal_sharing(user_turns):
            return ConversationMode.DEEP_TALK

        return ConversationMode.NORMAL

    def to_prompt_hints(self, mode: ConversationMode) -> str:
        """Return persona-prompt guidance for a mode."""

        return PROMPT_HINTS[_coerce_mode(mode)]

    def get_response_params(self, mode: ConversationMode) -> dict[str, float | bool | str]:
        """Return generation hints for a mode."""

        return dict(RESPONSE_PARAMS[_coerce_mode(mode)])


def _coerce_mode(mode: ConversationMode) -> ConversationMode:
    if isinstance(mode, ConversationMode):
        return mode
    return ConversationMode(mode)


def _primary_state(user_state: Optional[UserState]) -> str:
    if user_state is None:
        return ""
    primary = getattr(user_state, "primary_state", None)
    if primary is None:
        primary = getattr(user_state, "primary", "")
    return _clean_text(primary).lower()


def _recent_user_turns(messages: list[dict]) -> list[str]:
    turns: list[str] = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        role = _clean_text(message.get("role", "")).lower()
        if role and role not in {"user", "human"}:
            continue
        text = _message_text(message)
        if text:
            turns.append(text)
    return turns[-6:]


def _message_text(message: Mapping[str, Any]) -> str:
    for key in ("content", "text", "message", "body"):
        text = _flatten_text(message.get(key))
        if text:
            return text
    return _flatten_text(message)


def _is_rapid_deep_personal_sharing(user_turns: list[str]) -> bool:
    recent_turns = user_turns[-3:]
    if not recent_turns:
        return False

    personal_turn_count = sum(1 for turn in recent_turns if _is_personal_turn(turn))
    if len(recent_turns) >= 2 and personal_turn_count >= 2:
        return True

    latest_turn = recent_turns[-1]
    return len(latest_turn.split()) >= 35 and _is_personal_turn(latest_turn)


def _is_personal_turn(text: str) -> bool:
    normalized = f" {_clean_text(text).lower()} "
    has_first_person = _contains_any(
        normalized,
        (" i ", " i'm", " im ", " i've", " ive ", " my ", " me "),
    )
    return has_first_person and _contains_any(normalized, DEEP_TALK_MARKERS)


def _flatten_text(value: Any) -> str:
    parts: list[str] = []

    def visit(item: Any) -> None:
        if item is None:
            return
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, Mapping):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (list, tuple, set, frozenset)):
            for nested in item:
                visit(nested)
        elif isinstance(item, (bool, int, float)):
            parts.append(str(item))

    visit(value)
    return _clean_text(" ".join(parts))


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _clean_text(value: Any) -> str:
    return " ".join(str(value).strip().split())


__all__ = [
    "ConversationMode",
    "ModeSelector",
]
