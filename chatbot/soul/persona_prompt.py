from __future__ import annotations

from typing import Optional

from chatbot.soul.personality import Personality
from chatbot.soul.state import SoulState


def assemble_persona_prompt(
    personality: Personality,
    identity_anchor: str,
    state: Optional[SoulState] = None,
) -> str:
    """Assemble a deterministic system prompt from persona inputs."""

    traits = personality.core_traits or ["(none specified)"]
    sections = [
        "IDENTITY",
        identity_anchor,
        "",
        "PERSONALITY",
        f"Description: {personality.description}",
        "Traits:",
        *[f"- {trait}" for trait in traits],
        "",
        "TONE",
        f"Use a {personality.tone} tone.",
        "",
        "CONSTRAINTS",
        f"Verbosity: {personality.verbosity}",
        f"Max response length: {personality.max_response_length}",
        f"Emojis: {'allowed' if personality.use_emojis else 'do not use'}",
        f"Markdown: {'allowed' if personality.use_markdown else 'do not use'}",
    ]

    if personality.custom_instructions is not None:
        sections.extend(
            [
                "",
                "CUSTOM",
                personality.custom_instructions,
            ]
        )

    if state is not None:
        sections.extend(
            [
                "",
                "CONTEXT",
                f"User name: {state.user_name}",
                f"Conversation summary: {state.conversation_summary}",
            ]
        )

    return "\n".join(sections)


__all__ = ["assemble_persona_prompt"]
