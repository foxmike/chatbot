"""Dynamic bot mood state separate from fixed personality."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


MoodLabel = Literal[
    "warm",
    "neutral",
    "playful",
    "tired",
    "reflective",
    "direct",
    "gentle",
    "upbeat",
]

MOOD_LABELS: tuple[str, ...] = (
    "warm",
    "neutral",
    "playful",
    "tired",
    "reflective",
    "direct",
    "gentle",
    "upbeat",
)

RECENT_EMOTIONAL_CONTEXT_LIMIT = 10
MAX_ENERGY_DELTA = 0.05
MAX_CLOSENESS_DELTA = 0.03
MIN_TURNS_BETWEEN_MOOD_SHIFTS = 5


@dataclass(frozen=True, slots=True)
class MoodState:
    """Durable affect state for the bot, not a replacement for personality."""

    mood: str
    energy: float
    closeness: float
    last_updated: datetime
    recent_emotional_context: list[str] = field(default_factory=list)
    turns_since_mood_shift: int = 0

    def __post_init__(self) -> None:
        if self.mood not in MOOD_LABELS:
            raise ValueError(f"mood must be one of: {', '.join(MOOD_LABELS)}")

        object.__setattr__(self, "energy", _clamp(float(self.energy), 0.0, 1.0))
        object.__setattr__(
            self,
            "closeness",
            _clamp(float(self.closeness), 0.0, 1.0),
        )
        object.__setattr__(
            self,
            "last_updated",
            _ensure_utc(self.last_updated),
        )
        object.__setattr__(
            self,
            "recent_emotional_context",
            [_clean_text(item) for item in self.recent_emotional_context if _clean_text(item)][
                -RECENT_EMOTIONAL_CONTEXT_LIMIT:
            ],
        )
        object.__setattr__(
            self,
            "turns_since_mood_shift",
            max(0, int(self.turns_since_mood_shift)),
        )


class MoodEngine:
    """Synchronous affect updater for use inside the turn pipeline."""

    def update(self, turn_result: Any, current_state: MoodState) -> MoodState:
        """Return a new mood state after applying one bounded turn update."""

        summary = _turn_summary(turn_result)
        recent_context = _append_context(current_state.recent_emotional_context, summary)
        signal_text = _flatten_text(turn_result, summary)

        energy_delta = _bounded_delta(
            _energy_delta(signal_text),
            MAX_ENERGY_DELTA,
        )
        closeness_delta = _bounded_delta(
            _closeness_delta(signal_text),
            MAX_CLOSENESS_DELTA,
        )

        energy = _clamp(current_state.energy + energy_delta, 0.0, 1.0)
        closeness = _clamp(current_state.closeness + closeness_delta, 0.0, 1.0)

        turns_since_shift = current_state.turns_since_mood_shift + 1
        mood = current_state.mood
        if turns_since_shift >= MIN_TURNS_BETWEEN_MOOD_SHIFTS:
            candidate = _candidate_mood(
                current_mood=current_state.mood,
                energy=energy,
                closeness=closeness,
                recent_context=recent_context,
            )
            if candidate != current_state.mood:
                mood = candidate
                turns_since_shift = 0

        return MoodState(
            mood=mood,
            energy=energy,
            closeness=closeness,
            last_updated=datetime.now(timezone.utc),
            recent_emotional_context=recent_context,
            turns_since_mood_shift=turns_since_shift,
        )

    def default_state(self) -> MoodState:
        """Return the baseline used before conversation history exists."""

        return MoodState(
            mood="warm",
            energy=0.5,
            closeness=0.0,
            last_updated=datetime.now(timezone.utc),
            recent_emotional_context=[],
        )

    def to_prompt_hints(self, state: MoodState) -> str:
        """Render compact bot-affect guidance for persona prompt assembly."""

        energy_hint = _energy_hint(state.energy)
        familiarity_hint = _familiarity_hint(state.closeness)
        return (
            f"Current bot affect: {state.mood}, {energy_hint}. "
            f"{familiarity_hint} Stay attentive and consistent with the fixed persona."
        )


def _turn_summary(turn_result: Any) -> str:
    for key in (
        "recent_emotional_context",
        "emotional_context",
        "emotional_summary",
        "affect_summary",
        "turn_summary",
        "summary",
    ):
        value = _read_value(turn_result, key)
        summary = _coerce_summary(value)
        if summary:
            return summary

    text = _flatten_text(turn_result)
    if _contains_any(text, ("thank", "appreciat", "laughed", "joke", "playful")):
        return "positive engagement"
    if _contains_any(text, ("personal", "self-disclosure", "self_disclosure", "shared")):
        return "personal sharing"
    if _contains_any(text, ("worry", "stress", "hard", "grief", "concern")):
        return "serious or tender topic"
    if _contains_any(text, ("urgent", "direct", "task", "fix", "debug")):
        return "focused task work"
    return ""


def _coerce_summary(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, (list, tuple)):
        return _clean_text(" ".join(str(item) for item in value if _clean_text(item)))
    return _clean_text(value)


def _append_context(current: list[str], summary: str) -> list[str]:
    if not summary:
        return list(current)[-RECENT_EMOTIONAL_CONTEXT_LIMIT:]
    return [*current, summary][-RECENT_EMOTIONAL_CONTEXT_LIMIT:]


def _energy_delta(signal_text: str) -> float:
    delta = -0.01

    if _contains_any(
        signal_text,
        ("thank", "appreciat", "laughed", "joke", "positive", "playful", "engaged"),
    ):
        delta += 0.03

    if _contains_any(signal_text, ("stress", "grief", "hard", "worry", "concern")):
        delta -= 0.02

    if _contains_any(signal_text, ("urgent", "frustrat", "blocked", "failed")):
        delta -= 0.01

    return delta


def _closeness_delta(signal_text: str) -> float:
    delta = 0.0

    if _contains_any(
        signal_text,
        (
            "personal",
            "self-disclosure",
            "self_disclosure",
            "shared",
            "trust",
            "vulnerab",
            "private",
            "opened up",
        ),
    ):
        delta += 0.02

    if _contains_any(signal_text, ("thank", "appreciat", "helpful", "means a lot")):
        delta += 0.01

    return delta


def _candidate_mood(
    *,
    current_mood: str,
    energy: float,
    closeness: float,
    recent_context: list[str],
) -> str:
    trajectory = " ".join(recent_context[-5:]).lower()

    if energy <= 0.25:
        return "tired"
    if _contains_any(trajectory, ("urgent", "focused task", "debug", "fix")):
        return "direct"
    if _contains_any(trajectory, ("serious", "tender", "worry", "stress", "grief")):
        return "gentle" if closeness >= 0.12 else "reflective"
    if _contains_any(trajectory, ("laughed", "joke", "playful")) and energy >= 0.45:
        return "playful"
    if _contains_any(trajectory, ("positive", "thank", "appreciat")) and energy >= 0.6:
        return "upbeat"
    if closeness >= 0.35 and energy >= 0.35:
        return "warm"
    if energy < 0.4:
        return "reflective"
    return current_mood if current_mood in MOOD_LABELS else "warm"


def _read_value(source: Any, key: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(key)
    return getattr(source, key, None)


def _flatten_text(*values: Any) -> str:
    parts: list[str] = []
    seen: set[int] = set()

    def visit(value: Any) -> None:
        if value is None:
            return
        value_id = id(value)
        if value_id in seen:
            return
        seen.add(value_id)

        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, Mapping):
            for item_key, item_value in value.items():
                parts.append(str(item_key))
                visit(item_value)
        elif isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                visit(item)
        elif isinstance(value, (bool, int, float)):
            parts.append(str(value))
        else:
            for name in (
                "summary",
                "turn_summary",
                "emotional_context",
                "signals",
                "metadata",
                "sentiment",
                "intent",
                "user_message",
            ):
                item = getattr(value, name, None)
                if item is not None:
                    parts.append(name)
                    visit(item)

    for value in values:
        visit(value)

    return " ".join(_clean_text(part).lower() for part in parts if _clean_text(part))


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _energy_hint(energy: float) -> str:
    if energy >= 0.75:
        return "energetic"
    if energy >= 0.55:
        return "steady energy"
    if energy >= 0.35:
        return "calm energy"
    return "a bit tired but attentive"


def _familiarity_hint(closeness: float) -> str:
    if closeness >= 0.65:
        return "Use a relaxed, familiar tone."
    if closeness >= 0.25:
        return "Use a lightly familiar tone."
    return "Keep the tone welcoming without assuming familiarity."


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clean_text(value: Any) -> str:
    return " ".join(str(value).strip().split())


def _bounded_delta(value: float, limit: float) -> float:
    return _clamp(value, -limit, limit)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


__all__ = [
    "MAX_CLOSENESS_DELTA",
    "MAX_ENERGY_DELTA",
    "MIN_TURNS_BETWEEN_MOOD_SHIFTS",
    "MOOD_LABELS",
    "MoodEngine",
    "MoodLabel",
    "MoodState",
    "RECENT_EMOTIONAL_CONTEXT_LIMIT",
]
