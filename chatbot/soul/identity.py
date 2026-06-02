from __future__ import annotations

from dataclasses import replace
from typing import Optional

from chatbot.soul.personality import Personality
from chatbot.soul.state import SoulState


def compute_identity_anchor(state: SoulState, personality: Personality) -> str:
    """Return the persisted identity anchor or derive one from personality config."""

    if state.identity_anchor:
        return state.identity_anchor

    core_traits = ", ".join(personality.core_traits)
    return (
        f"Name: {personality.name}\n"
        f"Description: {personality.description}\n"
        f"Core traits: {core_traits}\n"
        f"Tone: {personality.tone}"
    )


def update_identity_anchor(
    state: SoulState,
    personality: Personality,
    new_anchor: Optional[str] = None,
) -> SoulState:
    """Return a new state with an identity anchor without overwriting an existing one."""

    if state.identity_anchor:
        return replace(state)

    identity_anchor = compute_identity_anchor(state, personality) if new_anchor is None else new_anchor
    return replace(state, identity_anchor=identity_anchor)


__all__ = [
    "compute_identity_anchor",
    "update_identity_anchor",
]
