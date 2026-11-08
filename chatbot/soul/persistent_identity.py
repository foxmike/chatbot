"""Pure composition of a companion's effective identity; no random session drift is promoted.

Layers (Phase 1 precedence, ``docs/COMPANION_PHASE1_DESIGN.md`` §11):

1. configuration (``companions``)           -- the authority for the initial identity
2. durable adjustments (``companion_identities``) -- applied on top of configuration
3. durable relationship (``companion_relationships``) -- the only relationship authority
4. the session working copy (``identity_states``) -- seeded from 1+2, never written back
5. temporary emotion (session mood/user state)
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from chatbot.soul.character import DEFAULT_PERSONALITY_SLIDERS, CharacterProfile
from chatbot.soul.evolution import IdentityState

if TYPE_CHECKING:
    from chatbot.db.models import Companion, CompanionIdentityModel

_COMM_STYLE_WEIGHTS: dict[str, float] = {
    "concise": 0.35,
    "verbose": 0.7,
    "storyteller": 0.65,
    "question_driven": 0.55,
    "aphoristic": 0.45,
}

# Session-identity keys copied from configuration. The configured identity
# reaches the prompt directly, so these copies are left out of the prompt for a
# companion session: they repeat it, and go stale when the companion is edited.
CONFIGURED_COPY_KEYS: tuple[str, ...] = ("name", "backstory", "humor_style")

# Relationship defaults that older seeds copied into the session identity. The
# durable relationship is now the only relationship authority.
RELATIONSHIP_FIXED_KEYS: tuple[str, ...] = ("relationship_type",)
RELATIONSHIP_SLOW_KEYS: tuple[str, ...] = (
    "relationship_warmth",
    "relationship_directness",
    "initiative_level",
)

# The generic values every companion used to be seeded with (removed by
# decision D8). Session rows created before the change still store them.
LEGACY_DEFAULT_CORE_VALUES: tuple[str, ...] = (
    "careful listening",
    "practical support",
    "clear conversation",
)


def effective_traits(
    companion: Companion, identity: CompanionIdentityModel | None = None
) -> dict[str, float]:
    traits = {**DEFAULT_PERSONALITY_SLIDERS, **(companion.personality_sliders or {})}
    adjustments = identity.trait_adjustments if identity is not None else {}
    return {
        key: max(0.0, min(1.0, float(value) + float(adjustments.get(key, 0))))
        for key, value in traits.items()
    }


def session_identity_seed(
    profile: CharacterProfile, traits: Mapping[str, float] | None = None
) -> IdentityState:
    """The working identity a new session of this companion starts from.

    *traits* are the companion's effective traits (configuration plus durable
    adjustments); without them the configured sliders are used. Relationship
    values are not part of the seed: they come from the durable relationship.
    """
    base = IdentityState.defaults()
    fixed = {
        key: value for key, value in base.fixed.items() if key not in RELATIONSHIP_FIXED_KEYS
    }
    fixed.update(
        name=profile.name, backstory=profile.backstory, humor_style=profile.humor_style
    )
    slow = {key: value for key, value in base.slow.items() if key not in RELATIONSHIP_SLOW_KEYS}
    slow["communication_style_weight"] = _COMM_STYLE_WEIGHTS.get(profile.communication_style, 0.5)
    slow["favorite_topics"] = list(profile.favorite_topics)
    if traits:
        slow.update({key: float(value) for key, value in traits.items()})
    else:
        slow.update({key: float(value) for key, value in profile.personality_sliders.items()})
    return IdentityState(fixed=fixed, slow=slow, mood=dict(base.mood))


def initial_session_identity(
    companion: Companion, identity: CompanionIdentityModel | None = None
) -> IdentityState:
    return session_identity_seed(
        companion.to_character_profile(), effective_traits(companion, identity)
    )


def prompt_identity_view(
    state: IdentityState, *, companion_bound: bool, relationship_bound: bool
) -> IdentityState:
    """The session working identity as it should appear in the prompt.

    A read-only view; the stored session identity is not changed. Removes
    the legacy generic core values (D8) everywhere and, for a companion
    session, the configuration copies (the configured identity is already in
    the prompt). When a durable relationship exists, it also removes the
    relationship defaults copied into older seeds.
    """
    fixed = dict(state.fixed)
    slow = dict(state.slow)
    if tuple(fixed.get("core_values") or ()) == LEGACY_DEFAULT_CORE_VALUES:
        fixed.pop("core_values")
    if companion_bound:
        for key in CONFIGURED_COPY_KEYS:
            fixed.pop(key, None)
    if relationship_bound:
        for key in RELATIONSHIP_FIXED_KEYS:
            fixed.pop(key, None)
        for key in RELATIONSHIP_SLOW_KEYS:
            slow.pop(key, None)
    return IdentityState(
        fixed=fixed,
        slow=slow,
        mood=dict(state.mood),
        slow_baseline=dict(slow),
        mood_reset_target=dict(state.mood),
    )


def relationship_history_line(history: Mapping[str, Any] | None) -> str | None:
    """A factual line about past interactions, or ``None`` before the first completed turn.

    It states counts and dates only; it does not imply closeness.
    """
    if not history:
        return None
    turns = int(history.get("completed_turns") or 0)
    if turns <= 0:
        return None
    first = _date(history.get("first_interaction_at"))
    last = _date(history.get("last_interaction_at"))
    noun = "exchange" if turns == 1 else "exchanges"
    line = f"{turns} completed {noun} to date"
    if first:
        line += f" since {first}"
    if last and last != first:
        line += f" (most recent {last})"
    return line


def _date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, str) and value:
        return value[:10]
    return None
