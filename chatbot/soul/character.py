"""Character profile model for companion persona prompt shaping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


HumorStyle = Literal["dry", "witty", "silly", "dark", "none"]
CommunicationStyle = Literal[
    "verbose",
    "concise",
    "storyteller",
    "question_driven",
    "aphoristic",
]

HUMOR_STYLES: tuple[str, ...] = ("dry", "witty", "silly", "dark", "none")
COMMUNICATION_STYLES: tuple[str, ...] = (
    "verbose",
    "concise",
    "storyteller",
    "question_driven",
    "aphoristic",
)
DEFAULT_PERSONALITY_SLIDERS: dict[str, float] = {
    "openness": 0.7,
    "conscientiousness": 0.6,
    "extraversion": 0.5,
    "agreeableness": 0.8,
    "neuroticism": 0.3,
    "curiosity": 0.7,
    "creativity": 0.6,
}


@dataclass(frozen=True, slots=True)
class CharacterProfile:
    """Stable companion identity details used by the soul prompt core."""

    name: str
    backstory: str
    personality_sliders: dict[str, float]
    humor_style: HumorStyle
    communication_style: CommunicationStyle
    favorite_topics: list[str]

    def __post_init__(self) -> None:
        if self.humor_style not in HUMOR_STYLES:
            raise ValueError(f"humor_style must be one of: {', '.join(HUMOR_STYLES)}")
        if self.communication_style not in COMMUNICATION_STYLES:
            raise ValueError(
                "communication_style must be one of: "
                f"{', '.join(COMMUNICATION_STYLES)}"
            )

        object.__setattr__(self, "name", _clean_text(self.name))
        object.__setattr__(self, "backstory", _clean_multiline(self.backstory))
        object.__setattr__(
            self,
            "personality_sliders",
            {
                _clean_text(key): _clamp(float(value), 0.0, 1.0)
                for key, value in self.personality_sliders.items()
                if _clean_text(key)
            },
        )
        object.__setattr__(
            self,
            "favorite_topics",
            [_clean_text(topic) for topic in self.favorite_topics if _clean_text(topic)],
        )

    @classmethod
    def defaults(cls) -> "CharacterProfile":
        """Return the baseline companion character profile."""

        return cls(
            name="Companion",
            backstory=(
                "A steady companion shaped around careful listening, practical "
                "support, and clear conversation. They do not claim a human past, "
                "but they carry a consistent identity built on helping people turn "
                "scattered thoughts into useful next steps."
            ),
            personality_sliders=dict(DEFAULT_PERSONALITY_SLIDERS),
            humor_style="witty",
            communication_style="concise",
            favorite_topics=[],
        )

    def to_prompt_fragment(self) -> str:
        """Render compact character guidance for persona prompt injection."""

        topics = (
            ", ".join(self.favorite_topics)
            if self.favorite_topics
            else "none specified"
        )
        return "\n".join(
            (
                "Character:",
                f"- Name: {self.name or 'Companion'}",
                f"- Backstory: {_condense_backstory(self.backstory)}",
                f"- Personality: {_personality_summary(self.personality_sliders)}",
                f"- Humor style: {self.humor_style}",
                f"- Communication style: {self.communication_style}",
                f"- Favorite topics: {topics}",
            )
        )


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _clean_text(value: object) -> str:
    return " ".join(str(value).strip().split())


def _clean_multiline(value: object) -> str:
    paragraphs = [
        " ".join(paragraph.strip().split())
        for paragraph in str(value).strip().splitlines()
        if paragraph.strip()
    ]
    return "\n\n".join(paragraphs)


def _condense_backstory(value: str, limit: int = 360) -> str:
    text = _clean_text(value)
    if not text:
        return "A companion with a steady, helpful presence and a developing shared history."
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _personality_summary(sliders: dict[str, float]) -> str:
    if not sliders:
        return "balanced"

    parts = [
        f"{name.replace('_', ' ')} {_format_ratio(value)} ({_slider_band(value)})"
        for name, value in sorted(sliders.items())
    ]
    return "; ".join(parts)


def _slider_band(value: float) -> str:
    if value >= 0.67:
        return "high"
    if value <= 0.33:
        return "low"
    return "moderate"


def _format_ratio(value: float) -> str:
    return f"{value:.2f}"


__all__ = [
    "COMMUNICATION_STYLES",
    "DEFAULT_PERSONALITY_SLIDERS",
    "HUMOR_STYLES",
    "CharacterProfile",
    "CommunicationStyle",
    "HumorStyle",
]
