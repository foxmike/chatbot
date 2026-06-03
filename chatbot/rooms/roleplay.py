"""Roleplay engine with scene-aware persona and memory isolation. Scene content is isolated from durable memory unless explicitly marked for persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from chatbot.rooms.room import DEFAULT_ROOM_POLICIES, RoomMemoryPolicy, RoomType
from chatbot.rooms.scene import SceneState


_PERSIST_MARKERS: tuple[str, ...] = (
    "[remember this]",
    "[important]",
    "[persist]",
    "[save this]",
    "[keep this]",
)

_RELATIONSHIP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bmy\s+(name|partner|friend|family|sister|brother|mother|father|"
        r"wife|husband|girlfriend|boyfriend|child|son|daughter)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bI\s+am\s+(married|related|engaged|dating)\b", re.IGNORECASE),
    re.compile(r"\bmarried\s+to\b", re.IGNORECASE),
    re.compile(r"\bmy\s+name\s+is\b", re.IGNORECASE),
)


def _has_persist_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _PERSIST_MARKERS)


def _has_proper_nouns(text: str) -> bool:
    """Return True if text contains mid-sentence capitalized words (likely proper nouns)."""
    for sentence in re.split(r"[.!?]+", text):
        words = sentence.strip().split()
        for word in words[1:]:
            cleaned = re.sub(r"[^a-zA-Z]", "", word)
            if cleaned and cleaned[0].isupper() and len(cleaned) > 2:
                return True
    return False


def _has_relationship_declaration(text: str) -> bool:
    return any(pat.search(text) for pat in _RELATIONSHIP_PATTERNS)


@dataclass
class RoleplayPersona:
    name: str
    description: str
    traits: list[str]
    voice_style: str
    backstory: str
    scene_goals: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)


class RoleplayEngine:
    def __init__(
        self,
        scene: SceneState,
        persona: RoleplayPersona,
        memory_policy: RoomMemoryPolicy | None = None,
    ) -> None:
        self._scene = scene
        self._persona = persona
        self._memory_policy: RoomMemoryPolicy = (
            memory_policy
            if memory_policy is not None
            else DEFAULT_ROOM_POLICIES[RoomType.ROLEPLAY_SCENE]
        )

    def build_scene_prompt(self) -> str:
        scene = self._scene
        persona = self._persona

        parts: list[str] = [
            f"You are {persona.name}. {persona.description}",
            f"Voice style: {persona.voice_style}.",
        ]

        if persona.backstory:
            parts.append(f"Backstory: {persona.backstory}")

        scene_parts: list[str] = []
        if scene.location:
            scene_parts.append(f"location: {scene.location}")
        if scene.activity:
            scene_parts.append(f"activity: {scene.activity}")
        scene_parts.append(f"tone: {scene.tone}")
        scene_parts.append(f"intensity: {scene.intensity}/10")
        parts.append("Scene context — " + ", ".join(scene_parts) + ".")

        if scene.scene_context:
            parts.append(f"Additional context: {scene.scene_context}")

        if persona.constraints:
            constraints_text = "; ".join(persona.constraints)
            parts.append(f"Constraints: {constraints_text}.")

        parts.append(
            "Stay in character at all times. Do not break the fourth wall unless a constraint explicitly permits it."
        )

        return "\n\n".join(parts)

    def classify_memory_importance(
        self, content: str
    ) -> Literal["scene_only", "durable", "both"]:
        if _has_persist_marker(content):
            return "durable"
        if _has_relationship_declaration(content):
            return "both"
        if _has_proper_nouns(content):
            return "both"
        return "scene_only"

    def generate_scene_context(self) -> str:
        scene = self._scene
        persona = self._persona

        parts: list[str] = []

        summary_fragments: list[str] = []
        if scene.location:
            summary_fragments.append(scene.location)
        if scene.activity:
            summary_fragments.append(scene.activity)
        summary_fragments.append(f"{scene.tone} tone")
        summary_fragments.append(f"intensity {scene.intensity}/10")
        parts.append("Scene: " + ", ".join(summary_fragments))

        if persona.traits:
            parts.append("Traits: " + ", ".join(persona.traits))

        if persona.scene_goals:
            parts.append("Goals: " + "; ".join(persona.scene_goals))

        return " | ".join(parts)

    def is_durable_candidate(self, turn_text: str) -> bool:
        if _has_persist_marker(turn_text):
            return True
        return self.classify_memory_importance(turn_text) == "durable"

    def get_scene_memory_scope(self) -> str:
        return self._memory_policy.scope


__all__ = [
    "RoleplayEngine",
    "RoleplayPersona",
]
