"""Controlled companion identity evolution across fixed, slow, and mood tiers."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import random
from typing import Any, Mapping

from chatbot.soul.character import CharacterProfile
from chatbot.soul.relationship import RelationshipConfig


DRIFT_BOUND_RATIO = 0.20
DEFAULT_DRIFT_RATE = 0.02
DEFAULT_DRIFT_INTERVAL = 20
DEFAULT_MOOD_DECAY_STEPS = 10
MOOD_SIGNAL_STEP = 0.04


@dataclass(slots=True)
class IdentityState:
    """Companion identity split into stable, slow-drift, and temporary tiers."""

    fixed: dict[str, Any]
    slow: dict[str, Any]
    mood: dict[str, Any]
    slow_baseline: dict[str, Any] = field(default_factory=dict)
    mood_reset_target: dict[str, Any] = field(default_factory=dict)
    interaction_count: int = 0
    drift_interval: int = DEFAULT_DRIFT_INTERVAL

    def __post_init__(self) -> None:
        self.fixed = deepcopy(self.fixed)
        self.slow = deepcopy(self.slow)
        self.mood = _clamp_numeric_mapping(deepcopy(self.mood))
        self.slow_baseline = (
            deepcopy(self.slow_baseline) if self.slow_baseline else deepcopy(self.slow)
        )
        self.mood_reset_target = _clamp_numeric_mapping(
            deepcopy(self.mood_reset_target)
            if self.mood_reset_target
            else deepcopy(self.mood)
        )
        self.interaction_count = max(0, int(self.interaction_count))
        self.drift_interval = max(1, int(self.drift_interval))

    @classmethod
    def defaults(cls) -> "IdentityState":
        """Return a fresh default identity state from character and relationship config."""

        character = CharacterProfile.defaults()
        relationship = RelationshipConfig.defaults()

        fixed = {
            "name": character.name,
            "backstory": character.backstory,
            "core_values": [
                "careful listening",
                "practical support",
                "clear conversation",
            ],
            "humor_style": character.humor_style,
            "relationship_type": relationship.type,
        }
        slow = {
            "communication_style_weight": _communication_style_weight(
                character.communication_style
            ),
            "favorite_topics": list(character.favorite_topics),
            "relationship_warmth": relationship.warmth,
            "relationship_directness": relationship.directness,
            "initiative_level": relationship.initiative_level,
            "openness": character.personality_sliders.get("openness", 0.5),
            "conscientiousness": character.personality_sliders.get(
                "conscientiousness",
                0.5,
            ),
            "extraversion": character.personality_sliders.get("extraversion", 0.5),
            "agreeableness": character.personality_sliders.get("agreeableness", 0.5),
            "neuroticism": character.personality_sliders.get("neuroticism", 0.5),
            "curiosity": character.personality_sliders.get("curiosity", 0.5),
            "creativity": character.personality_sliders.get("creativity", 0.5),
        }
        mood = {
            "energy": 0.5,
            "interest": 0.5,
            "warmth": relationship.warmth,
            "playfulness": 0.4 if character.humor_style != "none" else 0.1,
            "short_term_tone": relationship.tone,
            "current_interest": "",
        }

        return cls(
            fixed=fixed,
            slow=slow,
            mood=mood,
            slow_baseline=deepcopy(slow),
            mood_reset_target=deepcopy(mood),
        )

    def apply_evolution(self, context: dict[str, Any]) -> "IdentityState":
        """Apply one bounded turn update while preserving fixed identity values."""

        signal_text = _flatten_text(context)
        sentiment = _sentiment_score(context, signal_text)

        updated_mood = mood_decay(
            self.mood,
            self.mood_reset_target,
            steps=_context_int(context, "mood_decay_steps", DEFAULT_MOOD_DECAY_STEPS),
        )
        updated_mood["energy"] = _clamp01(
            _number(updated_mood.get("energy"), 0.5) + (sentiment * MOOD_SIGNAL_STEP)
        )

        if sentiment > 0:
            updated_mood["warmth"] = _clamp01(
                _number(updated_mood.get("warmth"), 0.5) + (sentiment * 0.03)
            )
        elif sentiment < 0:
            updated_mood["playfulness"] = _clamp01(
                _number(updated_mood.get("playfulness"), 0.5) + (sentiment * 0.03)
            )

        focus = _first_text(
            context.get("current_interest"),
            context.get("topic"),
            context.get("intent"),
        )
        if focus:
            updated_mood["current_interest"] = focus
            updated_mood["interest"] = _clamp01(
                _number(updated_mood.get("interest"), 0.5) + 0.03
            )

        tone = _first_text(context.get("tone"), context.get("short_term_tone"))
        if tone:
            updated_mood["short_term_tone"] = tone
        elif sentiment > 0:
            updated_mood["short_term_tone"] = "warm"
        elif sentiment < 0:
            updated_mood["short_term_tone"] = "gentle"

        self.mood = _clamp_numeric_mapping(updated_mood)
        self.interaction_count += 1

        drift_interval = _context_int(context, "drift_interval", self.drift_interval)
        if self.interaction_count % max(1, drift_interval) == 0:
            self.slow = bounded_drift(
                self.slow,
                self.slow_baseline,
                rate=_context_float(context, "drift_rate", DEFAULT_DRIFT_RATE),
            )

        return self

    def to_prompt_fragment(self) -> str:
        """Render prompt guidance for stable, slow-drift, and current mood tiers."""

        return "\n\n".join(
            (
                "Stable identity:\n" + _render_mapping(self.fixed),
                "Drift-adjusted traits:\n" + _render_mapping(self.slow),
                "Current mood for this turn:\n" + _render_mapping(self.mood),
            )
        )


def bounded_drift(
    current: dict[str, Any],
    baseline: dict[str, Any],
    rate: float = DEFAULT_DRIFT_RATE,
) -> dict[str, Any]:
    """Return slow traits with numeric fields randomly drifted within baseline bounds."""

    drift_rate = max(0.0, float(rate))
    updated: dict[str, Any] = deepcopy(current)

    for key, baseline_value in baseline.items():
        current_value = current.get(key, baseline_value)

        if _is_plain_number(baseline_value) and _is_plain_number(current_value):
            delta = random.uniform(-drift_rate, drift_rate)
            updated[key] = _clamp_to_baseline_bound(
                float(current_value) + delta,
                float(baseline_value),
            )
        elif isinstance(current_value, dict) and isinstance(baseline_value, dict):
            updated[key] = bounded_drift(current_value, baseline_value, drift_rate)
        else:
            updated[key] = deepcopy(current_value)

    for key, value in current.items():
        if key not in updated:
            updated[key] = deepcopy(value)

    return updated


def mood_decay(
    mood: dict[str, Any],
    reset_target: dict[str, Any],
    steps: int = DEFAULT_MOOD_DECAY_STEPS,
) -> dict[str, Any]:
    """Return mood regressed one linear step toward the reset target."""

    step_count = max(1, int(steps))
    decayed: dict[str, Any] = {}

    for key in mood.keys() | reset_target.keys():
        value = mood.get(key, reset_target.get(key))
        target = reset_target.get(key, value)

        if _is_plain_number(value) and _is_plain_number(target):
            decayed[key] = _clamp01(
                float(value) + ((float(target) - float(value)) / step_count)
            )
        elif step_count == 1 and key in reset_target:
            decayed[key] = deepcopy(target)
        else:
            decayed[key] = deepcopy(value)

    return decayed


def apply_evolution(state: IdentityState, context: dict[str, Any]) -> IdentityState:
    """Compatibility wrapper for callers that prefer a module-level function."""

    return state.apply_evolution(context)


def _communication_style_weight(style: str) -> float:
    weights = {
        "concise": 0.35,
        "verbose": 0.7,
        "storyteller": 0.65,
        "question_driven": 0.55,
        "aphoristic": 0.45,
    }
    return weights.get(style, 0.5)


def _clamp_to_baseline_bound(value: float, baseline: float) -> float:
    lower = baseline * (1.0 - DRIFT_BOUND_RATIO)
    upper = baseline * (1.0 + DRIFT_BOUND_RATIO)
    if lower > upper:
        lower, upper = upper, lower
    return max(lower, min(upper, value))


def _clamp_numeric_mapping(values: dict[str, Any]) -> dict[str, Any]:
    clamped: dict[str, Any] = {}
    for key, value in values.items():
        if _is_plain_number(value):
            clamped[key] = _clamp01(float(value))
        else:
            clamped[key] = deepcopy(value)
    return clamped


def _sentiment_score(context: Mapping[str, Any], signal_text: str) -> float:
    explicit = context.get("sentiment")
    if _is_plain_number(explicit):
        return max(-1.0, min(1.0, float(explicit)))

    sentiment_text = _first_text(explicit, context.get("sentiment_label")).lower()
    if sentiment_text in {"positive", "happy", "excited", "grateful"}:
        return 1.0
    if sentiment_text in {"negative", "sad", "angry", "frustrated", "stressed"}:
        return -1.0

    positive_hits = _count_hits(
        signal_text,
        (
            "thank",
            "appreciat",
            "happy",
            "excited",
            "great",
            "good",
            "positive",
            "playful",
            "laugh",
        ),
    )
    negative_hits = _count_hits(
        signal_text,
        (
            "angry",
            "frustrat",
            "sad",
            "stress",
            "worry",
            "grief",
            "bad",
            "negative",
            "failed",
            "blocked",
        ),
    )
    if positive_hits == negative_hits:
        return 0.0
    return max(-1.0, min(1.0, (positive_hits - negative_hits) / 3.0))


def _count_hits(text: str, needles: tuple[str, ...]) -> int:
    return sum(1 for needle in needles if needle in text)


def _flatten_text(value: Any) -> str:
    parts: list[str] = []
    seen: set[int] = set()

    def visit(item: Any) -> None:
        if item is None:
            return
        item_id = id(item)
        if item_id in seen:
            return
        seen.add(item_id)

        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, Mapping):
            for key, child in item.items():
                parts.append(str(key))
                visit(child)
        elif isinstance(item, (list, tuple, set, frozenset)):
            for child in item:
                visit(child)
        elif isinstance(item, (bool, int, float)):
            parts.append(str(item))

    visit(value)
    return " ".join(_clean_text(part).lower() for part in parts if _clean_text(part))


def _render_mapping(values: Mapping[str, Any]) -> str:
    if not values:
        return "- none"
    return "\n".join(
        f"- {key.replace('_', ' ')}: {_render_value(values[key])}"
        for key in sorted(values, key=str)
    )


def _render_value(value: Any) -> str:
    if isinstance(value, Mapping):
        return "; ".join(
            f"{key}: {_render_value(value[key])}" for key in sorted(value, key=str)
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(_render_value(item) for item in value) or "none"
    if _is_plain_number(value):
        return f"{float(value):.2f}"
    return _clean_text(value) or "none"


def _context_int(context: Mapping[str, Any], key: str, default: int) -> int:
    value = context.get(key, default)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _context_float(context: Mapping[str, Any], key: str, default: float) -> float:
    value = context.get(key, default)
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


def _number(value: Any, default: float) -> float:
    if _is_plain_number(value):
        return float(value)
    return default


def _first_text(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def _is_plain_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


__all__ = [
    "DEFAULT_DRIFT_INTERVAL",
    "DEFAULT_DRIFT_RATE",
    "DEFAULT_MOOD_DECAY_STEPS",
    "DRIFT_BOUND_RATIO",
    "IdentityState",
    "apply_evolution",
    "bounded_drift",
    "mood_decay",
]
