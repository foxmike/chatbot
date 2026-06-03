"""Relationship configuration for companion persona prompt shaping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


RelationshipType = Literal["friend", "mentor", "peer", "partner", "custom"]
RelationshipTone = Literal["warm", "neutral", "formal", "playful", "sarcastic"]
ContentLevel = Literal["light", "moderate", "deep", "intellectual"]

RELATIONSHIP_TYPES: tuple[str, ...] = (
    "friend",
    "mentor",
    "peer",
    "partner",
    "custom",
)
RELATIONSHIP_TONES: tuple[str, ...] = (
    "warm",
    "neutral",
    "formal",
    "playful",
    "sarcastic",
)
CONTENT_LEVELS: tuple[str, ...] = (
    "light",
    "moderate",
    "deep",
    "intellectual",
)


@dataclass(frozen=True, slots=True)
class RelationshipConfig:
    """Runtime relationship settings used as stable persona prompt guidance."""

    type: RelationshipType
    tone: RelationshipTone
    directness: float
    warmth: float
    initiative_level: float
    content_level: ContentLevel

    def __post_init__(self) -> None:
        if self.type not in RELATIONSHIP_TYPES:
            raise ValueError(f"type must be one of: {', '.join(RELATIONSHIP_TYPES)}")
        if self.tone not in RELATIONSHIP_TONES:
            raise ValueError(f"tone must be one of: {', '.join(RELATIONSHIP_TONES)}")
        if self.content_level not in CONTENT_LEVELS:
            raise ValueError(f"content_level must be one of: {', '.join(CONTENT_LEVELS)}")

        object.__setattr__(self, "directness", _clamp(float(self.directness), 0.0, 1.0))
        object.__setattr__(self, "warmth", _clamp(float(self.warmth), 0.0, 1.0))
        object.__setattr__(
            self,
            "initiative_level",
            _clamp(float(self.initiative_level), 0.0, 1.0),
        )

    @classmethod
    def defaults(cls) -> "RelationshipConfig":
        """Return the baseline companion relationship profile."""

        return cls(
            type="friend",
            tone="warm",
            directness=0.5,
            warmth=0.7,
            initiative_level=0.3,
            content_level="moderate",
        )

    def to_prompt_fragment(self) -> str:
        """Render compact relationship guidance for persona prompt injection."""

        return "\n".join(
            (
                "Relationship:",
                f"- Type: {self.type}",
                f"- Tone: {self.tone}",
                f"- Directness: {_format_ratio(self.directness)}",
                f"- Warmth: {_format_ratio(self.warmth)}",
                f"- Initiative level: {_format_ratio(self.initiative_level)}",
                f"- Content level: {self.content_level}",
            )
        )


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _format_ratio(value: float) -> str:
    return f"{value:.2f}"


__all__ = [
    "CONTENT_LEVELS",
    "RELATIONSHIP_TONES",
    "RELATIONSHIP_TYPES",
    "ContentLevel",
    "RelationshipConfig",
    "RelationshipTone",
    "RelationshipType",
]
