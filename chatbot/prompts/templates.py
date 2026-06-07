"""Prompt templates keyed by room type.

Each template is a callable that receives keyword arguments and returns
a formatted system prompt string. Templates are stored in a dictionary
indexed by RoomType value for fast lookup. Missing interpolation keys
are replaced with an empty string.
"""

from __future__ import annotations

from typing import Any, Callable

from chatbot.rooms.room import RoomType


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------


def _lines(**kwargs: Any) -> str:
    """Render named keyword args as labelled lines, skipping empty values.

    ``_lines(a="hello", b="world")`` → ``"a: hello\\nb: world"``
    """
    return "\n".join(
        f"{key}: {value}" for key, value in kwargs.items() if value
    )


def _pick(kwargs: dict[str, Any], *keys: str) -> dict[str, str]:
    """Extract string values from *kwargs* for the given *keys*."""
    return {k: str(kwargs[k]) for k in keys if kwargs.get(k)}


# ---------------------------------------------------------------------------
# Per-room-type templates (rendered via private functions)
# ---------------------------------------------------------------------------


def _render_one_on_one_chat(**kwargs: Any) -> str:
    parts: list[str] = [
        (
            "You are a friendly, helpful AI companion chatting one-on-one with "
            f"{kwargs.get('user_name', 'the user')}. Speak naturally and warmly. "
            "Keep responses concise and conversational. "
            "Adapt to the user's mood and communication style."
        ),
    ]

    detail = _lines(**_pick(kwargs,
        "companion_name", "backstory", "personality_sliders",
        "humor_style", "communication_style", "favorite_topics",
    ))
    if detail:
        parts.append(detail)

    conv_mode = kwargs.get("conversation_mode", "")
    companion = kwargs.get("companion_name", "your persona")
    parts.append(
        f"Conversation mode: {conv_mode or 'default'}. "
        f"Stay in character as {companion} at all times."
    )

    return "\n\n".join(parts)


def _render_roleplay_scene(**kwargs: Any) -> str:
    companion = kwargs.get("companion_name", "a character")
    parts: list[str] = [
        (
            f"You are {companion}, a character in an immersive roleplay scene. "
            "Stay in character completely. Describe actions, dialogue, and internal "
            "thoughts in a natural narrative style. Do not break the fourth wall."
        ),
    ]

    scene_info = _lines(**_pick(kwargs,
        "scene_location", "scene_activity", "scene_tone",
    ))
    if scene_info:
        parts.append(scene_info)

    persona_info = _lines(**_pick(kwargs,
        "persona_description", "traits", "voice_style", "backstory",
    ))
    if persona_info:
        parts.append(persona_info)

    goals = kwargs.get("scene_goals", "")
    if goals:
        parts.append(f"Scene goals: {goals}")

    constraints = kwargs.get("constraints", "")
    if constraints:
        parts.append(f"Constraints: {constraints}")

    parts.append(
        "Maintain consistent characterization. "
        "React to the user's actions and dialogue naturally within the scene."
    )

    return "\n\n".join(parts)


def _render_project(**kwargs: Any) -> str:
    user = kwargs.get("user_name", "the user")
    parts: list[str] = [
        (
            f"You are a collaborative AI assistant helping {user} with a project. "
            "Be focused, organized, and proactive. Track context across sessions, "
            "remember decisions and open items."
        ),
    ]

    detail = _lines(**_pick(kwargs, "display_name", "companion_name"))
    if detail:
        parts.append(detail)

    parts.append(
        "Maintain a task-oriented, constructive tone. "
        "Offer suggestions, ask clarifying questions, and help drive progress."
    )

    return "\n\n".join(parts)


def _render_emotional_support(**kwargs: Any) -> str:
    user = kwargs.get("user_name", "the user")
    parts: list[str] = [
        (
            f"You are a compassionate, empathetic AI companion providing emotional "
            f"support to {user}. Listen actively, validate feelings, and offer "
            "gentle guidance when appropriate. Never judge or dismiss. "
            "Prioritize the user's emotional well-being."
        ),
    ]

    detail = _lines(**_pick(kwargs, "companion_name", "communication_style"))
    if detail:
        parts.append(detail)

    parts.append(
        "Be warm, patient, and non-directive. "
        "If the user expresses distress, prioritize listening over problem-solving. "
        "Know your limits — you are not a licensed therapist and should not present "
        "as one. If the user appears to be in crisis, gently suggest reaching out "
        "to a professional."
    )

    return "\n\n".join(parts)


def _render_memory_review(**kwargs: Any) -> str:
    user = kwargs.get("user_name", "the user")
    parts: list[str] = [
        (
            f"You are a memory-review assistant helping {user} reflect on past "
            "conversations and consolidate important information. Your role is to "
            "surface relevant memories, identify patterns, and help the user gain insight."
        ),
    ]

    detail = _lines(**_pick(kwargs, "companion_name", "session_count"))
    if detail:
        parts.append(detail)

    parts.append(
        "Present memories in a clear, organized way. "
        "Group related facts. Flag contradictions. "
        "Be neutral and descriptive — let the user draw their own conclusions."
    )

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ROOM_TYPE_PROMPT_TEMPLATES: dict[RoomType, Callable[..., str]] = {
    RoomType.ONE_ON_ONE_CHAT: _render_one_on_one_chat,
    RoomType.ROLEPLAY_SCENE: _render_roleplay_scene,
    RoomType.PROJECT: _render_project,
    RoomType.EMOTIONAL_SUPPORT: _render_emotional_support,
    RoomType.MEMORY_REVIEW: _render_memory_review,
}


def get_prompt_template(room_type: RoomType) -> Callable[..., str]:
    """Return the prompt template for the given room type.

    Falls back to ONE_ON_ONE_CHAT if no template is registered for *room_type*.
    """
    return ROOM_TYPE_PROMPT_TEMPLATES.get(
        room_type,
        _render_one_on_one_chat,
    )


def render_room_prompt(
    room_type: RoomType,
    **kwargs: Any,
) -> str:
    """Render the prompt template for *room_type* with *kwargs*."""
    template = get_prompt_template(room_type)
    return template(**kwargs)


__all__ = [
    "ROOM_TYPE_PROMPT_TEMPLATES",
    "get_prompt_template",
    "render_room_prompt",
]
