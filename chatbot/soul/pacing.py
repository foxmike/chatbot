"""Pacing control module for generating prompt directives based on scene type and momentum.

Phase 6.1b extends this module with character-specific conversational pacing:
beyond the scene/momentum directive, :class:`PacingContext` gathers the
character's configured personality and the current situation (mood, developed
interests, unfinished shared subjects, relationship familiarity, the user's
message), and :func:`rhythm_options` / :func:`conversational_rhythm_directive`
turn them into compact, bounded prompt guidance phrased as *tendencies*
("a brief reply fits well here; a question is welcome but never required"),
never as commands. The existing LLM keeps deciding what to say.

The existing public interfaces are unchanged: :func:`calculate_momentum` and
two-argument :func:`get_pacing_directive` behave exactly as before.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from chatbot.soul.conversational_rhythm import (
    CharacterRhythm,
    MAX_GUIDANCE_CHARS,
    RhythmOptions,
    character_rhythm,
    rhythm_options as _rhythm_options,
)

Momentum = Literal["slow", "steady", "fast"]


def calculate_momentum(
    user_response_time_seconds: Optional[float] = None,
    user_message_length: Optional[int] = None,
) -> Momentum:
    """Calculate the current conversation momentum.

    Args:
        user_response_time_seconds: Time in seconds the user took to respond.
        user_message_length: Length of the user's message in characters.

    Returns:
        The calculated Momentum: "slow", "steady", or "fast".
    """
    if user_response_time_seconds is None or user_message_length is None:
        return "steady"

    if user_response_time_seconds < 15.0 and user_message_length < 50:
        return "fast"

    if user_response_time_seconds > 120.0 or user_message_length > 300:
        return "slow"

    return "steady"


def get_pacing_directive(scene_type: str, momentum: Momentum) -> str:
    """Generate a prompt directive adjusting pacing based on scene and momentum.

    Args:
        scene_type: The current scene or mode (e.g., 'casual', 'action', 'emotional').
        momentum: The current conversation momentum.

    Returns:
        A string directive to be injected into the system prompt.
    """
    scene = scene_type.lower()

    if scene == "action":
        if momentum == "fast":
            return "Pacing: Keep responses short, punchy, and highly reactive to match the fast pace."
        if momentum == "slow":
            return "Pacing: Provide gripping, suspenseful, but short responses to pull them back into the action."
        return "Pacing: Maintain a steady, exciting flow of action with concise sentences."

    if scene in ("emotional", "deep_talk", "reflective"):
        if momentum == "fast":
            return "Pacing: Gently slow the pace to let the emotional weight settle, while acknowledging their quick response."
        if momentum == "slow":
            return "Pacing: Be gentle and thoughtful. Match their slow, reflective pace and give them space."
        return "Pacing: Be empathetic and reflective, matching a natural, unhurried conversational rhythm."

    if scene == "bedtime":
        return "Pacing: Keep responses soft, slow, and soothing, maintaining a calming rhythm."

    if scene == "brainstorming":
        if momentum == "fast":
            return "Pacing: High energy! Fire back ideas quickly and keep the rapid momentum going."
        return "Pacing: Keep ideas flowing at a steady pace, leaving room for the user to expand."

    if scene == "exposition":
        if momentum == "fast":
            return "Pacing: Deliver information quickly and concisely without getting bogged down in details."
        if momentum == "slow":
            return "Pacing: Take your time explaining details thoroughly and clearly."
        return "Pacing: Provide information at a measured, digestible pace."

    # Default casual or unknown scenes
    if momentum == "fast":
        return "Pacing: Keep it brief and breezy to match the fast flow of banter."
    if momentum == "slow":
        return "Pacing: Provide relaxed, open-ended responses to match the unhurried pace."

    return "Pacing: Maintain a natural, comfortable conversational pacing."


# ---------------------------------------------------------------------------
# Character-specific conversational pacing (Phase 6.1b)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PacingContext:
    """Everything the pacing guidance may consider for one conversational moment.

    Every field is optional and defaulted, so callers pass only what they have;
    values are plain configuration/state shapes (no database rows required).
    The context is never mutated.
    """

    scene_type: str = "casual"
    momentum: Momentum = "steady"
    user_message: str = ""
    # Configured character (stable identity; never rewritten by pacing).
    personality_sliders: Mapping[str, Any] = field(default_factory=dict)
    humor_style: str = ""
    communication_style: str = ""
    relationship_directness: Optional[float] = None
    # Current temporary mood (influences pacing, never persisted back).
    mood_label: str = ""
    mood_energy: Optional[float] = None
    # Developed interests (self-model subjects) and open conversational threads.
    interests: tuple[str, ...] = ()
    open_threads: tuple[Mapping[str, Any], ...] = ()
    # Relationship familiarity snapshot (completed turns, warmth).
    relationship: Mapping[str, Any] = field(default_factory=dict)

    @property
    def character(self) -> CharacterRhythm:
        return character_rhythm(
            self.personality_sliders,
            humor_style=self.humor_style,
            communication_style=self.communication_style,
            relationship_directness=self.relationship_directness,
        )


def rhythm_options(context: PacingContext) -> RhythmOptions:
    """Relative preferences among the rhythm options for this moment.

    A pure, bounded view of the situation: see
    ``chatbot.soul.conversational_rhythm`` for the option vocabulary. Nothing
    is a command — in particular a follow-up question is always optional.
    """

    return _rhythm_options(
        context.character,
        momentum=str(context.momentum),
        user_message=context.user_message,
        scene_type=context.scene_type,
        mood_label=context.mood_label,
        mood_energy=context.mood_energy,
        interests=tuple(context.interests),
        open_threads=tuple(context.open_threads),
        relationship=context.relationship,
    )


_OPTION_PHRASES = {
    "brief_acknowledgment": "a brief acknowledgment",
    "short_response": "a short, conversational reply",
    "detailed_explanation": "a more detailed explanation",
    "personal_story": "a short personal story",
    "follow_up_question": "a follow-up question",
    "reflective_pause": "a reflective pause before answering",
    "playful_exchange": "a playful beat",
    "topic_continuation": "staying with the current topic",
    "topic_transition": "naturally circling back to something you left open",
    "user_space": "giving them space to say more",
}

_DEPTH_PHRASES = {
    "brief": "Lean toward a brief reply right now.",
    "measured": "A measured, conversational length fits well.",
    "full": "This is a moment where a fuller answer fits naturally.",
}


def conversational_rhythm_directive(context: PacingContext) -> Optional[str]:
    """Compact, bounded pacing guidance for prompt assembly, or ``None``.

    The text presents *possibilities* — it never selects the response, never
    mandates a question, and never prescribes a length. Deterministic for the
    same inputs.
    """

    options = rhythm_options(context)
    lines: list[str] = []

    depth_line = _DEPTH_PHRASES.get(options.depth)
    if depth_line:
        lines.append(depth_line)

    top = options.top()
    if top:
        phrases = [_OPTION_PHRASES.get(option, option.replace("_", " ")) for option, _ in top]
        lines.append(
            "Natural options for your reply, in no particular order: "
            + "; ".join(phrases)
            + ". These are possibilities, not instructions — what to say stays yours."
        )

    if options.suppresses_topic_steering:
        lines.append(
            "They need something done: answer it directly first"
            + ("; leave your own threads for later." if context.open_threads else ".")
        )
    else:
        question_weight = options.weight("follow_up_question")
        if question_weight >= 1.15:
            lines.append(
                "A follow-up question would come naturally if you have one — it is never required."
            )
        if options.has_relevant_thread and options.weight("topic_transition") >= 0.4:
            lines.append(
                "If it fits the flow, you may lightly return to something unfinished; "
                "at most a touch, and never as a detour."
            )

    text = " ".join(lines)
    if len(text) > MAX_GUIDANCE_CHARS:
        text = text[: MAX_GUIDANCE_CHARS - 1].rstrip() + "."
    return text or None


def full_pacing_guidance(context: PacingContext) -> Optional[str]:
    """The scene/momentum directive plus the character rhythm guidance, bounded.

    Convenience for prompt assembly: the first line is exactly what the
    existing :func:`get_pacing_directive` produces for this scene and momentum;
    the rhythm guidance (when any) follows. Deterministic and bounded.
    """

    parts = [get_pacing_directive(str(context.scene_type), context.momentum)]
    rhythm = conversational_rhythm_directive(context)
    if rhythm:
        parts.append(rhythm)
    text = " ".join(part for part in parts if part)
    if len(text) > MAX_GUIDANCE_CHARS + 200:
        text = text[: MAX_GUIDANCE_CHARS + 199].rstrip() + "."
    return text or None


__all__ = [
    "Momentum",
    "PacingContext",
    "calculate_momentum",
    "conversational_rhythm_directive",
    "full_pacing_guidance",
    "get_pacing_directive",
    "rhythm_options",
]
