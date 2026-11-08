"""Pure conversational-rhythm engine for character-specific pacing (Phase 6.1b).

This module decides nothing and selects nothing: it turns configured
personality and the current conversational situation into *relative
preferences* among rhythm options — a brief acknowledgment, a short
conversational response, a detailed explanation, a personal story, a follow-up
question, a reflective pause, a playful exchange, continuing the topic, a
natural topic transition, or giving the user conversational space. The existing
LLM still decides what to say; these weights only shape the guidance that goes
into the prompt, phrased as tendencies rather than commands.

Everything here is a pure function of its inputs:

* no randomness and no time dependence — the same inputs give the same output;
* inputs are never mutated;
* all outputs are bounded;
* personality comes only from the character's existing configuration
  (personality sliders, humour style, communication style) — there is no new
  personality-scoring system and nothing is written back to any durable state,
  so temporary mood can shape pacing without ever rewriting identity.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any

# Rhythm options (relative preference weights in [0, 2]; 1.0 is neutral).
BRIEF_ACKNOWLEDGMENT = "brief_acknowledgment"
SHORT_RESPONSE = "short_response"
DETAILED_EXPLANATION = "detailed_explanation"
PERSONAL_STORY = "personal_story"
FOLLOW_UP_QUESTION = "follow_up_question"
REFLECTIVE_PAUSE = "reflective_pause"
PLAYFUL_EXCHANGE = "playful_exchange"
TOPIC_CONTINUATION = "topic_continuation"
TOPIC_TRANSITION = "topic_transition"
USER_SPACE = "user_space"

RHYTHM_OPTIONS = (
    BRIEF_ACKNOWLEDGMENT,
    SHORT_RESPONSE,
    DETAILED_EXPLANATION,
    PERSONAL_STORY,
    FOLLOW_UP_QUESTION,
    REFLECTIVE_PAUSE,
    PLAYFUL_EXCHANGE,
    TOPIC_CONTINUATION,
    TOPIC_TRANSITION,
    USER_SPACE,
)

# How many options may appear in the compiled guidance (a bounded shortlist).
MAX_OPTIONS_IN_GUIDANCE = 4
MAX_GUIDANCE_CHARS = 620
# An open thread may only lightly invite a transition; steering is never forced.
MAX_THREAD_TRANSITION_WEIGHT = 0.55

# How strongly the current momentum shapes the situation (relative multipliers).
_MOMENTUM_FAST = {"brief_acknowledgment": 1.6, "short_response": 1.4, "playful_exchange": 1.2,
                  "detailed_explanation": 0.5, "reflective_pause": 0.4, "personal_story": 0.5,
                  "topic_transition": 0.4, "user_space": 0.7}
_MOMENTUM_SLOW = {"brief_acknowledgment": 0.7, "detailed_explanation": 1.4,
                  "reflective_pause": 1.6, "personal_story": 1.4, "user_space": 1.5,
                  "topic_transition": 0.7, "short_response": 0.8}
_MOMENTUM_STEADY: dict[str, float] = {}

_GREETING = re.compile(
    r"^(hey|hi|hello|yo|sup|hiya|good (morning|evening|afternoon)|howdy|what'?s up)\b[!.?, ]*$",
    re.IGNORECASE,
)
_URGENT = re.compile(
    r"\b(urgent|asap|emergency|right now|hurry|quick(ly)?|deadline|critical|"
    r"breaking|immediately)\b",
    re.IGNORECASE,
)
_DIRECT_REQUEST = re.compile(
    r"\b(can you|could you|please|fix|debug|help me|how do i|show me|write|build|"
    r"calculate|find|explain)\b",
    re.IGNORECASE,
)
_DISTRESS = re.compile(
    r"\b(sad|devastated|grief|worried|anxious|scared|terrible|awful|died|lost|"
    r"stressed|overwhelmed|exhausted|burn(ed|t)? out|lonely|hurting)\b",
    re.IGNORECASE,
)
_QUESTION = re.compile(r"\?|\b(what|why|how|when|where|who|which|do you|did you|have you)\b", re.IGNORECASE)

# Communication style → verbosity tendency (same spirit as the identity seed's
# communication_style_weight; no new scoring system, just the configured style).
_STYLE_VERBOSITY = {
    "verbose": 0.7,
    "storyteller": 0.65,
    "question_driven": 0.55,
    "aphoristic": 0.45,
    "concise": 0.35,
}
_HUMOR_PLAYFULNESS = {"silly": 0.8, "witty": 0.65, "dry": 0.5, "dark": 0.45, "none": 0.15}


@dataclass(frozen=True, slots=True)
class CharacterRhythm:
    """Stable conversational tendencies derived from configured character values.

    Read-only input shaping; never persisted and never fed back into identity.
    """

    energy: float = 0.5            # extraversion: rapid, animated exchanges
    curiosity: float = 0.5         # curiosity/openness: asking more questions
    verbosity: float = 0.5         # communication style + extraversion
    directness: float = 0.5        # concise style / configured directness
    reflection: float = 0.5        # openness + introversion: pausing, elaborating
    playfulness: float = 0.5       # humour style

    def __post_init__(self) -> None:
        object.__setattr__(self, "energy", _clamp01(self.energy))
        object.__setattr__(self, "curiosity", _clamp01(self.curiosity))
        object.__setattr__(self, "verbosity", _clamp01(self.verbosity))
        object.__setattr__(self, "directness", _clamp01(self.directness))
        object.__setattr__(self, "reflection", _clamp01(self.reflection))
        object.__setattr__(self, "playfulness", _clamp01(self.playfulness))


def character_rhythm(
    personality_sliders: Mapping[str, Any] | None = None,
    *,
    humor_style: Any = None,
    communication_style: Any = None,
    relationship_directness: Any = None,
) -> CharacterRhythm:
    """Derive stable rhythm tendencies from existing configured values."""

    sliders = {
        _clean(key): _clamp01(float(value))
        for key, value in dict(personality_sliders or {}).items()
        if _is_number(value)
    }

    def slider(name: str) -> float:
        return sliders.get(name, 0.5)

    style = _clean(communication_style).lower()
    humor = _clean(humor_style).lower()
    verbosity = _STYLE_VERBOSITY.get(style, 0.5)
    verbosity = _clamp01(0.65 * verbosity + 0.35 * slider("extraversion"))
    directness = 0.75 if style == "concise" else 0.45 if style == "verbose" else 0.5
    if _is_number(relationship_directness):
        directness = _clamp01(0.5 * directness + 0.5 * float(relationship_directness))
    return CharacterRhythm(
        energy=slider("extraversion"),
        curiosity=_clamp01(0.6 * slider("curiosity") + 0.4 * slider("openness")),
        verbosity=verbosity,
        directness=directness,
        reflection=_clamp01(0.6 * slider("openness") + 0.4 * (1.0 - slider("extraversion"))),
        playfulness=_HUMOR_PLAYFULNESS.get(humor, 0.5),
    )


@dataclass(frozen=True, slots=True)
class RhythmOptions:
    """Relative preferences among rhythm options for one conversational moment.

    ``weights`` covers all ten options; the guidance shortlists the top few.
    Nothing here is a command: a low weight only means "less natural right
    now", and no option is ever mandatory (in particular, a follow-up question
    is always just one possibility). ``depth`` is a tendency
    (``brief``/``measured``/``full``), never a length selector.
    """

    weights: tuple[tuple[str, float], ...]
    depth: str                    # brief | measured | full
    suppresses_topic_steering: bool
    allows_reflective_pause: bool
    # True only when an unfinished subject genuinely fits this moment.
    has_relevant_thread: bool = False

    def top(self, limit: int = MAX_OPTIONS_IN_GUIDANCE) -> tuple[tuple[str, float], ...]:
        ordered = sorted(self.weights, key=lambda item: (-item[1], item[0]))
        return tuple(ordered[: max(1, limit)])

    def weight(self, option: str) -> float:
        return dict(self.weights).get(option, 1.0)


def rhythm_options(
    character: CharacterRhythm,
    *,
    momentum: str = "steady",
    user_message: str = "",
    scene_type: str = "casual",
    mood_label: str = "",
    mood_energy: float | None = None,
    interests: tuple[str, ...] | list[str] = (),
    open_threads: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] = (),
    relationship: Mapping[str, Any] | None = None,
) -> RhythmOptions:
    """Weight the rhythm options for this moment from character and situation.

    The character sets the base tendencies; the situation (momentum, message
    shape, scene, mood, interests, open threads, familiarity) adapts them.
    All adaptation is bounded, so no single signal can dominate the character.
    """

    momentum = _clean(momentum).lower() or "steady"
    scene = _clean(scene_type).lower() or "casual"
    message = _clean(user_message)
    message_words = len(message.split())

    weights: dict[str, float] = {option: 1.0 for option in RHYTHM_OPTIONS}

    # --- stable character tendencies ---------------------------------------
    weights[BRIEF_ACKNOWLEDGMENT] += 0.5 * (1.0 - character.verbosity)
    weights[SHORT_RESPONSE] += 0.3 * (1.0 - character.verbosity) + 0.2 * character.directness
    weights[DETAILED_EXPLANATION] += 0.5 * (character.verbosity - 0.5) + 0.2 * character.reflection
    weights[PERSONAL_STORY] += 0.4 * (character.verbosity - 0.5) + 0.2 * character.energy
    weights[FOLLOW_UP_QUESTION] += 0.6 * (character.curiosity - 0.5)
    weights[REFLECTIVE_PAUSE] += 0.5 * (character.reflection - 0.5)
    weights[PLAYFUL_EXCHANGE] += 0.6 * (character.playfulness - 0.5) + 0.2 * (character.energy - 0.5)
    weights[USER_SPACE] += 0.2 * (1.0 - character.energy)
    if character.directness >= 0.6:  # impatient characters favour directness
        weights[BRIEF_ACKNOWLEDGMENT] += 0.2
        weights[DETAILED_EXPLANATION] -= 0.2

    # --- conversation momentum ---------------------------------------------
    momentum_modifiers = {
        "fast": _MOMENTUM_FAST,
        "slow": _MOMENTUM_SLOW,
    }.get(momentum, _MOMENTUM_STEADY)
    for option, factor in momentum_modifiers.items():
        weights[option] *= factor

    # --- the user's message -------------------------------------------------
    urgent = bool(_URGENT.search(message))
    distress = bool(_DISTRESS.search(message))
    asks_question = bool(_QUESTION.search(message))
    short_message = 0 < message_words <= 6

    if urgent or (short_message and bool(_DIRECT_REQUEST.search(message))):
        # An urgent or direct request is answered, not redirected: unrelated
        # topics and tangential questions are actively suppressed.
        weights[FOLLOW_UP_QUESTION] *= 0.3
        weights[TOPIC_TRANSITION] = 0.1
        weights[TOPIC_CONTINUATION] *= 0.6
        weights[BRIEF_ACKNOWLEDGMENT] *= 1.3
        weights[USER_SPACE] *= 0.5
    if distress or scene in {"emotional", "deep_talk", "emotional_support", "reflective"}:
        weights[USER_SPACE] *= 1.5
        weights[REFLECTIVE_PAUSE] *= 1.4
        weights[PLAYFUL_EXCHANGE] *= 0.5
    if short_message and _GREETING.match(message):
        weights[BRIEF_ACKNOWLEDGMENT] *= 1.8
        weights[SHORT_RESPONSE] *= 1.4
        weights[DETAILED_EXPLANATION] *= 0.4
        weights[PERSONAL_STORY] *= 0.4
        weights[REFLECTIVE_PAUSE] = 0.3
    if asks_question:
        weights[TOPIC_CONTINUATION] *= 1.2  # answering their question continues the topic
    if message_words > 120:
        weights[DETAILED_EXPLANATION] *= 1.2
        weights[REFLECTIVE_PAUSE] *= 1.2

    # --- mood (temporary; never persisted back) -----------------------------
    energy = character.energy if mood_energy is None else _clamp01(float(mood_energy))
    label = _clean(mood_label).lower()
    if energy >= 0.7:
        weights[PLAYFUL_EXCHANGE] *= 1.2
        weights[SHORT_RESPONSE] *= 1.1
    elif energy <= 0.3:
        weights[BRIEF_ACKNOWLEDGMENT] *= 1.2
        weights[PLAYFUL_EXCHANGE] *= 0.6
        weights[DETAILED_EXPLANATION] *= 0.85
    if label in {"reflective", "tired", "gentle"}:
        weights[REFLECTIVE_PAUSE] *= 1.2
        weights[USER_SPACE] *= 1.15
    if label in {"playful", "upbeat"}:
        weights[PLAYFUL_EXCHANGE] *= 1.25
    if label in {"direct"}:
        weights[BRIEF_ACKNOWLEDGMENT] *= 1.15

    # --- developed interests and unfinished subjects -------------------------
    interest_text = " ".join(_clean(interest).lower() for interest in interests)
    if interest_text and _shares_topic(message, interest_text):
        # A topic the companion genuinely cares about earns a fuller answer —
        # bounded so enthusiasm does not take over every reply.
        weights[DETAILED_EXPLANATION] *= 1.3
        weights[TOPIC_CONTINUATION] *= 1.15
    relevant_thread, thread_importance = _relevant_thread(message, open_threads, interest_text)
    if relevant_thread and not urgent:
        weights[TOPIC_TRANSITION] = min(
            MAX_THREAD_TRANSITION_WEIGHT,
            0.3 + 0.25 * thread_importance,
        )
    else:
        # No fitting open business: a topic transition is clearly not invited.
        weights[TOPIC_TRANSITION] *= 0.35

    # --- relationship familiarity -------------------------------------------
    turns = _relationship_turns(relationship)
    if turns >= 20:
        weights[PERSONAL_STORY] *= 1.2  # shared history to draw on
        weights[USER_SPACE] *= 1.1
    elif turns <= 2:
        weights[PERSONAL_STORY] *= 0.7

    clamped = ((option, round(_clamp_range(weight, 0.05, 2.0), 3)) for option, weight in weights.items())
    weights_tuple = tuple(sorted(clamped, key=lambda item: (-item[1], item[0])))

    depth = _depth_tendency(weights, momentum, scene, character)
    allows_pause = weights[REFLECTIVE_PAUSE] >= 0.6
    return RhythmOptions(
        weights=weights_tuple,
        depth=depth,
        suppresses_topic_steering=urgent,
        allows_reflective_pause=allows_pause,
        has_relevant_thread=bool(relevant_thread) and not urgent,
    )


def _depth_tendency(
    weights: Mapping[str, float], momentum: str, scene: str, character: CharacterRhythm
) -> str:
    """``brief`` / ``measured`` / ``full`` as a tendency, never a length order."""

    score = 0.5 + 0.35 * (character.verbosity - 0.5)
    score *= weights[DETAILED_EXPLANATION] / max(weights[BRIEF_ACKNOWLEDGMENT], 0.05)
    if momentum == "fast":
        score *= 0.7
    elif momentum == "slow":
        score *= 1.25
    if scene in {"emotional", "deep_talk", "reflective", "exposition"}:
        score *= 1.2
    if scene == "bedtime":
        score *= 0.6
    if score >= 0.85:
        return "full"
    if score >= 0.45:
        return "measured"
    return "brief"


def _relevant_thread(
    message: str,
    open_threads: Any,
    interest_text: str,
) -> tuple[bool, float]:
    """Whether an unfinished shared subject fits this moment, and how strongly.

    Only significant, genuinely related threads count, and the weight is
    capped well below a command, so the companion is never repeatedly steered
    toward its own interests.
    """

    message_lower = message.lower()
    for thread in open_threads or ():
        if not isinstance(thread, Mapping):
            continue
        summary = _clean(thread.get("summary") or thread.get("subject") or "").lower()
        if not summary:
            continue
        related = (
            _shares_topic(message, summary)
            or (bool(interest_text) and _shares_topic(summary, interest_text))
        )
        if not related:
            continue
        importance = _clean(thread.get("importance") or "ordinary").lower()
        return True, 1.0 if importance == "significant" else 0.5
    return False, 0.0


def _shares_topic(text: str, topics: str) -> bool:
    """Whether *text* plausibly touches one of the topic phrases (stem overlap)."""

    words = _words(text)
    if not words:
        return False
    topic_stems = {_stem(word) for word in _words(topics)}
    topic_stems -= _STOP
    if not topic_stems:
        return False
    text_stems = {_stem(word) for word in words} - _STOP
    return bool(text_stems & topic_stems)


_STOP = frozenset("a an the of to in on at for with and or is are my about it its i you your we".split())
_WORD = re.compile(r"[a-z0-9]+")
_PLURAL = re.compile(r"(?:ies|es|s)$")


def _stem(word: str) -> str:
    word = word.lower()
    if len(word) > 4 and _PLURAL.search(word):
        return _PLURAL.sub("", word)
    return word


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _relationship_turns(relationship: Mapping[str, Any] | None) -> int:
    if not isinstance(relationship, Mapping):
        return 0
    try:
        return max(0, int(relationship.get("completed_turns") or 0))
    except (TypeError, ValueError):
        return 0


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


def _clamp_range(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


__all__ = [
    "MAX_GUIDANCE_CHARS",
    "MAX_OPTIONS_IN_GUIDANCE",
    "MAX_THREAD_TRANSITION_WEIGHT",
    "CharacterRhythm",
    "RHYTHM_OPTIONS",
    "RhythmOptions",
    "character_rhythm",
    "rhythm_options",
]