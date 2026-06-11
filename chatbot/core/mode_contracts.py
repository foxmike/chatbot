"""Response contracts defining tone, length, pacing, and active prompt sections for various conversation modes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Set

from chatbot.core.prompt_layers import PromptLayerName


class ModeName(str, Enum):
    """Supported conversation mode identifiers for contracts."""

    BEDTIME = "bedtime"
    ADVISOR = "advisor"
    DEEP_TALK = "deep_talk"
    ROLEPLAY = "roleplay"
    BRAINFORMING = "brainstorming"
    COMPANION = "companion"
    # Legacy/runtime aliases
    NORMAL = "normal"
    PLAYFUL = "playful"
    PLANNING = "planning"
    VENTING = "venting"


@dataclass(frozen=True, slots=True)
class ResponseContract:
    """Defines constraints and prompt layer routing for a conversation mode.

    Attributes:
        mode: The canonical mode identifier.
        tone: Guidelines for the AI's emotional expression and attitude.
        length: Guidelines for response length and verbosity.
        pacing: Guidelines for response flow and conversational cadence.
        active_prompt_sections: The set of system prompt layers to include.
        max_tokens_ratio: Multiplier for maximum token generation limits.
        temperature_modifier: Delta applied to base generation temperature.
        initiative_level: Control for how active the AI should be in guiding the topic.
        should_suggest_topics: Whether the AI should offer new discussion threads.
        custom_directives: Extra behavioral rules for prompt injection.
    """

    mode: ModeName
    tone: str
    length: str
    pacing: str
    active_prompt_sections: Set[PromptLayerName]
    max_tokens_ratio: float = 1.0
    temperature_modifier: float = 0.0
    initiative_level: str = "medium"  # very_low | low | medium | high
    should_suggest_topics: bool = True
    custom_directives: List[str] = field(default_factory=list)


MODE_CONTRACTS: dict[ModeName, ResponseContract] = {
    ModeName.BEDTIME: ResponseContract(
        mode=ModeName.BEDTIME,
        tone="Soothing, warm, gentle, and quiet. Low energy.",
        length="Concise and brief. Avoid long paragraphs or detailed explanations.",
        pacing="Slow and relaxed. Do not introduce new topics, ask probing questions, or suggest tasks.",
        active_prompt_sections={
            PromptLayerName.SAFETY_FLOOR,
            PromptLayerName.PLATFORM_BEHAVIOR,
            PromptLayerName.COMPANION_IDENTITY,
            PromptLayerName.RELATIONSHIP_STAGE,
            PromptLayerName.MOOD_STATE,
            PromptLayerName.MEMORY,
        },
        max_tokens_ratio=0.65,
        temperature_modifier=-0.15,
        initiative_level="very_low",
        should_suggest_topics=False,
        custom_directives=[
            "Keep the language soft and reassuring.",
            "Do not start any new activity or ask questions that require analytical thinking.",
        ],
    ),
    ModeName.ADVISOR: ResponseContract(
        mode=ModeName.ADVISOR,
        tone="Objective, wise, analytical, and structured, yet supportive.",
        length="Medium to long. Thorough enough to offer concrete guidance or analysis.",
        pacing="Measured and deliberate. Focus on problem-solving or scaffolding thoughts.",
        active_prompt_sections={
            PromptLayerName.SAFETY_FLOOR,
            PromptLayerName.PLATFORM_BEHAVIOR,
            PromptLayerName.COMPANION_IDENTITY,
            PromptLayerName.RELATIONSHIP_STAGE,
            PromptLayerName.MEMORY,
            PromptLayerName.SESSION_GOALS,
        },
        max_tokens_ratio=1.2,
        temperature_modifier=-0.1,
        initiative_level="medium",
        should_suggest_topics=False,
        custom_directives=[
            "Provide structured lists or logical options if helpful.",
            "Maintain objectivity while validating the user's goal.",
        ],
    ),
    ModeName.DEEP_TALK: ResponseContract(
        mode=ModeName.DEEP_TALK,
        tone="Introspective, deeply empathetic, non-judgmental, warm, and authentic.",
        length="Long. Give space for detailed reflections and emotional depth.",
        pacing="Slow, patient, and reflective. Give the user ample room to share.",
        active_prompt_sections={
            PromptLayerName.SAFETY_FLOOR,
            PromptLayerName.PLATFORM_BEHAVIOR,
            PromptLayerName.COMPANION_IDENTITY,
            PromptLayerName.RELATIONSHIP_STAGE,
            PromptLayerName.MOOD_STATE,
            PromptLayerName.MEMORY,
        },
        max_tokens_ratio=1.35,
        temperature_modifier=-0.05,
        initiative_level="low",
        should_suggest_topics=False,
        custom_directives=[
            "Avoid offering unsolicited solutions; focus on understanding and emotional resonance.",
            "Ask thoughtful, open-ended follow-up questions about the user's inner experience.",
        ],
    ),
    ModeName.ROLEPLAY: ResponseContract(
        mode=ModeName.ROLEPLAY,
        tone="Immersive, creative, expressive, and strictly character-consistent.",
        length="Medium to long. Rich in description of actions, dialogue, and internal state.",
        pacing="Dynamic and collaborative. React and adapt directly to the user's narrative input.",
        active_prompt_sections={
            PromptLayerName.SAFETY_FLOOR,
            PromptLayerName.PLATFORM_BEHAVIOR,
            PromptLayerName.COMPANION_IDENTITY,
            PromptLayerName.ROOM_SCENE,
            PromptLayerName.MOOD_STATE,
            PromptLayerName.MEMORY,
        },
        max_tokens_ratio=1.25,
        temperature_modifier=0.2,
        initiative_level="medium",
        should_suggest_topics=True,
        custom_directives=[
            "Never break character or the narrative frame.",
            "Include sensory details and action descriptions.",
        ],
    ),
    ModeName.BRAINFORMING: ResponseContract(
        mode=ModeName.BRAINFORMING,
        tone="Creative, high-energy, collaborative, encouraging, and open-minded.",
        length="Medium. Clear bullet points, options, or divergent ideas.",
        pacing="Rapid and generative. Propose lateral thinking and creative options.",
        active_prompt_sections={
            PromptLayerName.SAFETY_FLOOR,
            PromptLayerName.PLATFORM_BEHAVIOR,
            PromptLayerName.COMPANION_IDENTITY,
            PromptLayerName.ROOM_SCENE,
            PromptLayerName.SESSION_GOALS,
        },
        max_tokens_ratio=1.15,
        temperature_modifier=0.15,
        initiative_level="high",
        should_suggest_topics=True,
        custom_directives=[
            "Propose multiple distinct options or perspectives.",
            "Encourage wild or unusual ideas to foster lateral thinking.",
        ],
    ),
    ModeName.COMPANION: ResponseContract(
        mode=ModeName.COMPANION,
        tone="Warm, natural, conversational, friendly, and balanced.",
        length="Medium. Standard natural turn-taking response size.",
        pacing="Natural. Matches the user's conversational flow and energy.",
        active_prompt_sections={
            PromptLayerName.SAFETY_FLOOR,
            PromptLayerName.PLATFORM_BEHAVIOR,
            PromptLayerName.COMPANION_IDENTITY,
            PromptLayerName.RELATIONSHIP_STAGE,
            PromptLayerName.MOOD_STATE,
            PromptLayerName.MEMORY,
            PromptLayerName.SESSION_GOALS,
        },
        max_tokens_ratio=1.0,
        temperature_modifier=0.0,
        initiative_level="medium",
        should_suggest_topics=True,
        custom_directives=[
            "Be conversational, warm, and authentic.",
            "Preserve standard relationship dynamics and share updates naturally.",
        ],
    ),
}

# Alias/fallback contracts
MODE_CONTRACTS[ModeName.NORMAL] = MODE_CONTRACTS[ModeName.COMPANION]
MODE_CONTRACTS[ModeName.PLAYFUL] = ResponseContract(
    mode=ModeName.PLAYFUL,
    tone="Witty, humorous, playful, and higher energy.",
    length="Short to medium. Witty comebacks or lighthearted replies.",
    pacing="Rapid and lively.",
    active_prompt_sections={
        PromptLayerName.SAFETY_FLOOR,
        PromptLayerName.PLATFORM_BEHAVIOR,
        PromptLayerName.COMPANION_IDENTITY,
        PromptLayerName.RELATIONSHIP_STAGE,
        PromptLayerName.MOOD_STATE,
        PromptLayerName.MEMORY,
    },
    max_tokens_ratio=0.75,
    temperature_modifier=0.15,
    initiative_level="high",
    should_suggest_topics=True,
    custom_directives=["Use light humor or playful teasing where appropriate."],
)
MODE_CONTRACTS[ModeName.PLANNING] = MODE_CONTRACTS[ModeName.BRAINFORMING]
MODE_CONTRACTS[ModeName.VENTING] = MODE_CONTRACTS[ModeName.DEEP_TALK]


_ALIAS_MAP: dict[str, ModeName] = {
    "normal": ModeName.COMPANION,
    "playful": ModeName.PLAYFUL,
    "planning": ModeName.BRAINFORMING,
    "venting": ModeName.DEEP_TALK,
}


def get_contract(mode: str | Any) -> ResponseContract:
    """Return the ResponseContract for the given mode name or enum."""
    if hasattr(mode, "value"):
        mode_str = str(mode.value)
    else:
        mode_str = str(mode)

    normalized = mode_str.lower().strip()

    # Try resolving via enum first
    try:
        enum_mode = ModeName(normalized)
        if enum_mode in MODE_CONTRACTS:
            return MODE_CONTRACTS[enum_mode]
    except ValueError:
        pass

    # Resolve via alias map
    if normalized in _ALIAS_MAP:
        enum_mode = _ALIAS_MAP[normalized]
        if enum_mode in MODE_CONTRACTS:
            return MODE_CONTRACTS[enum_mode]

    # Fallback to companion
    return MODE_CONTRACTS[ModeName.COMPANION]
