"""Temperature and sampling profile configurations.

Provides sampling settings per model family and conversation mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chatbot.core.prompt_formatter import ModelFamily, detect_model_family
from chatbot.soul.modes import ConversationMode, RESPONSE_PARAMS


@dataclass(frozen=True)
class SamplingSettings:
    """Sampling parameters for model generation."""

    temperature: float
    top_p: float | None = None
    top_k: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to a dictionary, omitting None values."""
        return {k: v for k, v in self.__dict__.items() if v is not None}


_BASE_PROFILES: dict[ModelFamily, SamplingSettings] = {
    ModelFamily.OPENAI: SamplingSettings(
        temperature=0.7,
        top_p=0.95,
        presence_penalty=0.0,
        frequency_penalty=0.0,
    ),
    ModelFamily.ANTHROPIC: SamplingSettings(
        temperature=0.7,
        top_p=0.9,
    ),
    ModelFamily.GEMINI: SamplingSettings(
        temperature=0.7,
        top_p=0.95,
        top_k=40,
    ),
    ModelFamily.DEFAULT: SamplingSettings(
        temperature=0.7,
        top_p=0.95,
    ),
}


def get_sampling_settings(
    model: str | None = None,
    mode: ConversationMode | str = ConversationMode.NORMAL,
    *,
    provider: str | None = None,
    family: ModelFamily | None = None,
) -> SamplingSettings:
    """Get sampling settings for a specific model and conversation mode.

    Combines the base profile for the model family with the temperature
    modifier defined by the conversation mode.
    """
    if family is None:
        family = detect_model_family(model, provider=provider)

    if isinstance(mode, str):
        try:
            mode = ConversationMode(mode)
        except ValueError:
            mode = ConversationMode.NORMAL

    base_settings = _BASE_PROFILES.get(family, _BASE_PROFILES[ModelFamily.DEFAULT])

    # Retrieve the temperature modifier for the given mode
    mode_params = RESPONSE_PARAMS.get(mode, {})
    temp_modifier = float(mode_params.get("temperature_modifier", 0.0))

    # Apply modifier, ensuring temperature stays within bounds (e.g. 0.0 to 2.0)
    new_temp = round(max(0.0, min(2.0, base_settings.temperature + temp_modifier)), 2)

    return SamplingSettings(
        temperature=new_temp,
        top_p=base_settings.top_p,
        top_k=base_settings.top_k,
        presence_penalty=base_settings.presence_penalty,
        frequency_penalty=base_settings.frequency_penalty,
    )


__all__ = [
    "SamplingSettings",
    "get_sampling_settings",
]
