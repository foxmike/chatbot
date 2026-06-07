"""Prompt templates package. Room-type-keyed system prompt generators."""

from chatbot.prompts.templates import (
    ROOM_TYPE_PROMPT_TEMPLATES,
    get_prompt_template,
    render_room_prompt,
)

__all__ = [
    "ROOM_TYPE_PROMPT_TEMPLATES",
    "get_prompt_template",
    "render_room_prompt",
]
