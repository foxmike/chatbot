"""Dynamic bot mood state separate from fixed personality.

Phase 5.1b adds mood *continuity*: temporary emotional state persists across
turns and sessions instead of resetting, evolves consistently with the
character's configured temperament, and relaxes back toward that temperament's
baseline over elapsed time. Mood never rewrites personality — the temperament
here is read-only input derived from configuration, and no durable trait, self-
model or relationship value is modified by anything in this module.

Everything is deterministic: given the same inputs (including an explicit
``now``), the same state comes out. Elapsed time is computed from persisted
timestamps, normalized to UTC, never negative, and decay never moves a value
outside ``[0, 1]``.
"""

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

# A temporary reaction retains this fraction of its deviation from the
# temperament baseline after one half-life of elapsed time. Ten minutes is a
# short fraction of the default half-life, so a brief pause does not reset the
# companion; months pass through many half-lives and the mood is back at its
# baseline. Only this transient state decays — durable emotional memories
# (episodes, self-model, relationship facts) are never touched.
DEFAULT_REACTION_HALF_LIVE_SECONDS = 45.0 * 60.0
# Recent emotional context describes very recent conversation; after this much
# silence it no longer describes the current moment and is dropped.
RECENT_CONTEXT_RETENTION_SECONDS = 2.0 * 60.0 * 60.0


@dataclass(frozen=True, slots=True)
class Temperament:
    """Stable, configured emotional disposition of one companion.

    Derived only from the character configuration (personality sliders and
    humour style); it is a read-only input to mood updates and is never written
    back, so mood cannot rewrite personality. There is no stage and no assumed
    ideal: the baseline is the companion's own, and an introverted, low-warmth
    companion gets a calm, reserved baseline, not a warm one.
    """

    # Where this companion's transient energy settles over time.
    baseline_energy: float = 0.5
    # The mood label this companion returns to when nothing recent is in play.
    baseline_label: str = "warm"
    # How strongly positive / negative events move this companion.
    reaction_gain_positive: float = 1.0
    reaction_gain_negative: float = 1.0
    # How quickly closeness builds from personal sharing.
    closeness_gain: float = 1.0
    # Turns of consistent signal needed before the mood label may shift.
    shift_patience: int = MIN_TURNS_BETWEEN_MOOD_SHIFTS
    # Half-life of a temporary reaction, in seconds of elapsed time.
    reaction_half_life_seconds: float = DEFAULT_REACTION_HALF_LIVE_SECONDS

    def __post_init__(self) -> None:
        object.__setattr__(self, "baseline_energy", _clamp(float(self.baseline_energy), 0.05, 0.95))
        if self.baseline_label not in MOOD_LABELS:
            raise ValueError(f"baseline_label must be one of: {', '.join(MOOD_LABELS)}")
        object.__setattr__(
            self, "reaction_gain_positive", _clamp(float(self.reaction_gain_positive), 0.4, 1.5)
        )
        object.__setattr__(
            self, "reaction_gain_negative", _clamp(float(self.reaction_gain_negative), 0.4, 1.5)
        )
        object.__setattr__(self, "closeness_gain", _clamp(float(self.closeness_gain), 0.4, 1.5))
        object.__setattr__(self, "shift_patience", max(2, min(8, int(self.shift_patience))))
        object.__setattr__(
            self,
            "reaction_half_life_seconds",
            _clamp(float(self.reaction_half_life_seconds), 5.0 * 60.0, 6.0 * 60.0 * 60.0),
        )


def temperament_from_character(source: Any) -> Temperament:
    """Derive a temperament from configured character values.

    *source* is a ``CharacterProfile``, a Companion row-like object or a plain
    mapping: whatever carries ``personality_sliders`` and ``humor_style``.
    Missing values fall back to neutral sliders; nothing here is stored.
    """
    if isinstance(source, Mapping):
        sliders = dict(source.get("personality_sliders") or {})
        humor_style = str(source.get("humor_style") or "").strip().lower()
    else:
        sliders = dict(getattr(source, "personality_sliders", None) or {})
        humor_style = str(getattr(source, "humor_style", None) or "").strip().lower()

    def slider(name: str) -> float:
        try:
            return _clamp(float(sliders.get(name, 0.5)), 0.0, 1.0)
        except (TypeError, ValueError):
            return 0.5

    extraversion = slider("extraversion")
    neuroticism = slider("neuroticism")
    agreeableness = slider("agreeableness")
    conscientiousness = slider("conscientiousness")
    openness = slider("openness")

    baseline_label = _baseline_label(humor_style, extraversion, agreeableness)
    half_life_minutes = (
        45.0 * (1.2 - neuroticism) * (0.75 + 0.5 * conscientiousness)
    )
    return Temperament(
        baseline_energy=_clamp(0.30 + 0.40 * extraversion, 0.05, 0.95),
        baseline_label=baseline_label,
        reaction_gain_positive=0.6 + 0.8 * extraversion,
        reaction_gain_negative=0.6 + 0.8 * neuroticism,
        closeness_gain=0.7 + 0.6 * ((agreeableness + openness) / 2.0),
        shift_patience=MIN_TURNS_BETWEEN_MOOD_SHIFTS
        - (1 if neuroticism >= 0.7 else 0)
        + (2 if conscientiousness >= 0.7 else 0),
        reaction_half_life_seconds=half_life_minutes * 60.0,
    )


def _baseline_label(humor_style: str, extraversion: float, agreeableness: float) -> str:
    """The companion's own resting mood, from its configured character."""
    if humor_style == "none":
        return "reflective"
    if humor_style == "silly":
        return "playful" if extraversion >= 0.4 else "warm"
    if humor_style in {"dry", "dark"}:
        return "direct" if agreeableness < 0.5 else "reflective"
    if humor_style == "witty":
        return "playful" if extraversion >= 0.5 else "warm"
    return "warm" if agreeableness >= 0.5 else "reflective"


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
    """Synchronous affect updater for use inside the turn pipeline.

    One call applies one bounded turn update on top of the *carried-over*
    state: energy and closeness accumulate from recent turns instead of being
    reset after every reply, and any elapsed time since the state was last
    written first relaxes the transient energy back toward the temperament
    baseline, so a state carried across a pause continues smoothly rather than
    jumping. With no temperament the previous behaviour is preserved exactly.
    """

    def update(
        self,
        turn_result: Any,
        current_state: MoodState,
        *,
        temperament: Temperament | None = None,
        now: datetime | None = None,
    ) -> MoodState:
        """Return a new mood state after applying one bounded turn update."""

        now = _ensure_utc(now) if now is not None else datetime.now(timezone.utc)
        state = self.decay(current_state, now=now, temperament=temperament)

        summary = _turn_summary(turn_result)
        recent_context = _append_context(state.recent_emotional_context, summary)
        signal_text = _flatten_text(turn_result, summary)

        energy_delta = _temperament_energy_delta(signal_text, temperament)
        closeness_delta = _temperament_closeness_delta(signal_text, temperament)

        energy = _clamp(state.energy + energy_delta, 0.0, 1.0)
        closeness = _clamp(state.closeness + closeness_delta, 0.0, 1.0)

        turns_since_shift = state.turns_since_mood_shift + 1
        patience = (
            temperament.shift_patience if temperament is not None
            else MIN_TURNS_BETWEEN_MOOD_SHIFTS
        )
        mood = state.mood
        if turns_since_shift >= patience:
            candidate = _candidate_mood(
                current_mood=state.mood,
                energy=energy,
                closeness=closeness,
                recent_context=recent_context,
                temperament=temperament,
            )
            if candidate != state.mood:
                mood = candidate
                turns_since_shift = 0

        return MoodState(
            mood=mood,
            energy=energy,
            closeness=closeness,
            last_updated=now,
            recent_emotional_context=recent_context,
            turns_since_mood_shift=turns_since_shift,
        )

    def decay(
        self,
        state: MoodState,
        *,
        now: datetime | None = None,
        temperament: Temperament | None = None,
    ) -> MoodState:
        """Relax the transient energy toward the temperament baseline over elapsed time.

        The mood label, closeness and turn counters are preserved (time alone
        does not invent a new mood); recent emotional context is kept only
        while it still describes the current moment. ``last_updated`` moves to
        *now*, so the next update measures elapsed time from here and state
        carried across a session boundary does not decay twice.
        """

        now = _ensure_utc(now) if now is not None else datetime.now(timezone.utc)
        elapsed = max(0.0, (now - _ensure_utc(state.last_updated)).total_seconds())
        if temperament is None and elapsed <= 0.0:
            return state  # nothing to do; identical input state
        baseline = (
            temperament.baseline_energy
            if temperament is not None
            else 0.5
        )
        half_life = (
            temperament.reaction_half_life_seconds
            if temperament is not None
            else DEFAULT_REACTION_HALF_LIVE_SECONDS
        )
        energy = _decay_toward(state.energy, baseline, elapsed, half_life)
        recent_context = list(state.recent_emotional_context)
        if elapsed > RECENT_CONTEXT_RETENTION_SECONDS:
            recent_context = []
        mood = state.mood
        if temperament is not None and mood != temperament.baseline_label:
            retained = _retained_fraction(elapsed, half_life)
            if retained < 0.1 and energy <= baseline + 0.05:
                mood = temperament.baseline_label  # fully settled: back to rest
        return MoodState(
            mood=mood,
            energy=energy,
            closeness=state.closeness,
            last_updated=now,
            recent_emotional_context=recent_context,
            turns_since_mood_shift=state.turns_since_mood_shift,
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

    def baseline_state(self, temperament: Temperament | None = None) -> MoodState:
        """Return the resting state for a temperament, or the generic default."""

        if temperament is None:
            return self.default_state()
        return MoodState(
            mood=temperament.baseline_label,
            energy=temperament.baseline_energy,
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


def _temperament_energy_delta(signal_text: str, temperament: Temperament | None) -> float:
    """Bounded energy delta, scaled by this companion's reaction intensity.

    Positive and negative events are scaled separately: an excitable
    (extraverted) companion reacts more to good news, a sensitive (neurotic)
    one more to bad news. The temperament can amplify a signal but never push
    a single turn beyond the existing bounded delta, so ordinary chatter stays
    small.
    """

    raw = _energy_delta(signal_text)
    if temperament is None:
        return _bounded_delta(raw, MAX_ENERGY_DELTA)
    gain = (
        temperament.reaction_gain_positive
        if raw > 0
        else temperament.reaction_gain_negative
        if raw < 0
        else 1.0
    )
    return _bounded_delta(raw * gain, MAX_ENERGY_DELTA)


def _temperament_closeness_delta(signal_text: str, temperament: Temperament | None) -> float:
    raw = _closeness_delta(signal_text)
    if temperament is None:
        return _bounded_delta(raw, MAX_CLOSENESS_DELTA)
    gain = temperament.closeness_gain if raw > 0 else 1.0
    return _bounded_delta(raw * gain, MAX_CLOSENESS_DELTA)


def _candidate_mood(
    *,
    current_mood: str,
    energy: float,
    closeness: float,
    recent_context: list[str],
    temperament: Temperament | None = None,
) -> str:
    trajectory = " ".join(recent_context[-5:]).lower()

    tired_threshold = (
        max(0.15, temperament.baseline_energy - 0.2)
        if temperament is not None
        else 0.25
    )
    if energy <= tired_threshold:
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
    baseline = temperament.baseline_label if temperament is not None else "warm"
    return current_mood if current_mood in MOOD_LABELS else baseline


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


def _decay_toward(
    value: float, baseline: float, elapsed_seconds: float, half_life_seconds: float
) -> float:
    """Exponential relaxation of *value* toward *baseline* over elapsed time.

    Zero elapsed time changes nothing (no discontinuity between consecutive
    turns); a future timestamp is clamped to zero elapsed. The result is
    clamped to the valid range, so decay can never produce an invalid value.
    """

    elapsed = max(0.0, float(elapsed_seconds))
    half_life = max(float(half_life_seconds), 1.0)
    retained = 0.5 ** (elapsed / half_life)
    return _clamp(baseline + (float(value) - baseline) * retained, 0.0, 1.0)


def _retained_fraction(elapsed_seconds: float, half_life_seconds: float) -> float:
    elapsed = max(0.0, float(elapsed_seconds))
    half_life = max(float(half_life_seconds), 1.0)
    return 0.5 ** (elapsed / half_life)


def _clean_text(value: Any) -> str:
    return " ".join(str(value).strip().split())


def _bounded_delta(value: float, limit: float) -> float:
    return _clamp(value, -limit, limit)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


__all__ = [
    "DEFAULT_REACTION_HALF_LIVE_SECONDS",
    "MAX_CLOSENESS_DELTA",
    "MAX_ENERGY_DELTA",
    "MIN_TURNS_BETWEEN_MOOD_SHIFTS",
    "MOOD_LABELS",
    "MoodEngine",
    "MoodLabel",
    "MoodState",
    "RECENT_CONTEXT_RETENTION_SECONDS",
    "RECENT_EMOTIONAL_CONTEXT_LIMIT",
    "Temperament",
    "temperament_from_character",
]
