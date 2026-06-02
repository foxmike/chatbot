from __future__ import annotations

from dataclasses import dataclass as _dataclass
from dataclasses_json import dataclass_json as _dataclass_json
from typing import Literal as _Literal
from typing import Optional as _Optional


@_dataclass_json
@_dataclass(frozen=True, slots=True)
class Personality:
    """Bot persona configuration."""

    name: str
    description: str
    core_traits: list[str]
    tone: _Literal["casual", "formal", "neutral"]
    verbosity: _Literal["concise", "balanced", "detailed"]
    use_emojis: bool = False
    use_markdown: bool = True
    max_response_length: int = 2000
    custom_instructions: _Optional[str] = None


DEFAULT_PERSONALITY = Personality(
    name="Companion",
    description="A helpful, thoughtful chatbot companion.",
    core_traits=["empathetic", "analytical", "clear"],
    tone="neutral",
    verbosity="balanced",
)
