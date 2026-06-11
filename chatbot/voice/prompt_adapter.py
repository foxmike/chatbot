"""Voice-vs-text prompt variant logic.

Adjusts system prompts to enforce formatting suitable for text-to-speech (TTS) output.
"""

from __future__ import annotations


_VOICE_FORMATTING_RULES = (
    "VOICE OUTPUT MODE ENABLED:\n"
    "Your responses will be read aloud by a text-to-speech engine. "
    "You MUST follow these formatting rules:\n"
    "- DO NOT use any markdown syntax (no asterisks, no bold, no italics, no bullet points, no code blocks).\n"
    "- Format numbers as words (e.g., 'three' instead of '3', 'twenty percent' instead of '20%').\n"
    "- Keep responses conversational, concise, and natural to speak.\n"
    "- Avoid long lists or complex visual layouts.\n"
    "- Spell out acronyms if they should be read as individual letters."
)


def adapt_prompt_for_voice(base_prompt: str) -> str:
    """Append voice-specific formatting rules to a system prompt.

    Args:
        base_prompt: The original text-oriented system prompt.

    Returns:
        A modified prompt that includes instructions for TTS-friendly output.
    """
    if not base_prompt:
        return _VOICE_FORMATTING_RULES

    return f"{base_prompt}\n\n{_VOICE_FORMATTING_RULES}"
