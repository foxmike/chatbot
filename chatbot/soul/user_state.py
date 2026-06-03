"""Conservative user emotional-state inference from cheap local signals."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from chatbot.soul.mood import MoodState


USER_STATE_LABELS: tuple[str, ...] = (
    "stressed",
    "playful",
    "lonely",
    "excited",
    "frustrated",
    "tired",
    "focused",
    "grieving",
    "angry",
    "neutral",
)

MAX_CONFIDENCE = 0.6
DEFAULT_CONFIDENCE = 0.3
MIN_EVIDENCE_SCORE = 1.5
MIN_CONSISTENT_TURN_SCORE = 1.0
RECENT_TURN_LIMIT = 6
SLOW_SHIFT_TURN_COUNT = 3
CONFIDENCE_HISTORY_LIMIT = 5

STATE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "stressed": (
        "stress",
        "stressed",
        "anxious",
        "anxiety",
        "overwhelmed",
        "pressure",
        "panic",
        "worried",
        "worry",
        "deadline",
        "too much",
    ),
    "playful": (
        "lol",
        "haha",
        "joke",
        "joking",
        "kidding",
        "silly",
        "playful",
        "funny",
        "meme",
        "teasing",
    ),
    "lonely": (
        "lonely",
        "alone",
        "isolated",
        "no one",
        "nobody",
        "miss them",
        "miss her",
        "miss him",
        "disconnected",
        "left out",
    ),
    "excited": (
        "excited",
        "thrilled",
        "amazing",
        "awesome",
        "can't wait",
        "cant wait",
        "pumped",
        "eager",
        "great news",
        "big news",
    ),
    "frustrated": (
        "frustrated",
        "annoyed",
        "stuck",
        "blocked",
        "broken",
        "not working",
        "failing",
        "failed",
        "can't get",
        "cant get",
        "why won't",
        "why wont",
    ),
    "tired": (
        "tired",
        "exhausted",
        "drained",
        "sleepy",
        "burned out",
        "burnt out",
        "burnout",
        "worn out",
        "no energy",
        "long day",
    ),
    "focused": (
        "focus",
        "focused",
        "working on",
        "debug",
        "implement",
        "task",
        "plan",
        "code",
        "solve",
        "ship",
        "review",
    ),
    "grieving": (
        "grief",
        "grieving",
        "died",
        "death",
        "loss",
        "lost my",
        "funeral",
        "mourning",
        "passed away",
        "heartbroken",
    ),
    "angry": (
        "angry",
        "furious",
        "mad",
        "pissed",
        "rage",
        "hate this",
        "unacceptable",
        "infuriating",
        "outraged",
        "screw this",
    ),
}

POSITIVE_WORDS: tuple[str, ...] = (
    "great",
    "good",
    "happy",
    "love",
    "thanks",
    "thank you",
    "awesome",
    "amazing",
    "excited",
    "fun",
)

NEGATIVE_WORDS: tuple[str, ...] = (
    "bad",
    "hard",
    "awful",
    "sad",
    "upset",
    "worried",
    "angry",
    "stuck",
    "tired",
    "hate",
    "failed",
)


@dataclass(frozen=True, slots=True)
class UserState:
    """Low-confidence estimate of a user's likely emotional state."""

    primary_state: Optional[str]
    confidence: float
    secondary_states: list[tuple[str, float]]
    evidence_summary: str
    last_inferred: datetime
    inference_count: int
    confidence_history: tuple[float, ...] = field(default_factory=tuple, repr=False)

    def __post_init__(self) -> None:
        if self.primary_state is not None and self.primary_state not in USER_STATE_LABELS:
            raise ValueError(
                f"primary_state must be one of: {', '.join(USER_STATE_LABELS)}"
            )

        confidence = _clamp(float(self.confidence), 0.0, MAX_CONFIDENCE)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(
            self,
            "secondary_states",
            _normalize_secondary_states(self.secondary_states, confidence),
        )
        object.__setattr__(
            self,
            "evidence_summary",
            _short_summary(self.evidence_summary),
        )
        object.__setattr__(self, "last_inferred", _ensure_utc(self.last_inferred))
        object.__setattr__(self, "inference_count", max(0, int(self.inference_count)))

        history = tuple(float(item) for item in self.confidence_history)
        if not history:
            history = (confidence,)
        history = tuple(_clamp(item, 0.0, MAX_CONFIDENCE) for item in history)
        object.__setattr__(self, "confidence_history", history[-CONFIDENCE_HISTORY_LIMIT:])


class UserStateEngine:
    """Infer user state from bounded local heuristics."""

    def infer(
        self,
        messages: list[dict],
        topics: list[str],
        mood_state: MoodState,
        prior_user_state: Optional[UserState],
    ) -> UserState:
        """Return a conservative user-state estimate without model calls."""

        now = datetime.now(timezone.utc)
        user_turns = _recent_user_turns(messages)
        scores, evidence = _score_evidence(user_turns, topics, mood_state)
        candidate, candidate_score = _top_candidate(scores)

        if candidate is None or candidate_score < MIN_EVIDENCE_SCORE:
            if prior_user_state is not None:
                return _carry_forward(prior_user_state, now, "insufficient new evidence")
            return UserState(
                primary_state="neutral",
                confidence=DEFAULT_CONFIDENCE,
                secondary_states=[],
                evidence_summary="insufficient evidence; defaulted to neutral",
                last_inferred=now,
                inference_count=1,
            )

        if prior_user_state is not None and candidate != prior_user_state.primary_state:
            consistent_candidate = _consistent_recent_candidate(user_turns, topics, mood_state)
            if consistent_candidate != candidate:
                return _carry_forward(
                    prior_user_state,
                    now,
                    f"candidate {candidate} not consistent across 3 recent turns",
                )

        confidence = _confidence_from_scores(scores, candidate, prior_user_state)
        return UserState(
            primary_state=candidate,
            confidence=confidence,
            secondary_states=_secondary_states(scores, candidate, confidence),
            evidence_summary=_evidence_summary(candidate, evidence),
            last_inferred=now,
            inference_count=(prior_user_state.inference_count + 1)
            if prior_user_state is not None
            else 1,
            confidence_history=_append_confidence(prior_user_state, confidence),
        )

    def is_stable(self, state: UserState) -> bool:
        """Return True after enough inferences with little recent confidence movement."""

        if state.inference_count < 5 or len(state.confidence_history) < 3:
            return False
        recent = state.confidence_history[-3:]
        return max(recent) - min(recent) <= 0.1


def _recent_user_turns(messages: list[dict]) -> list[str]:
    turns: list[str] = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        role = _clean_text(message.get("role", "")).lower()
        if role and role not in {"user", "human"}:
            continue
        text = _message_text(message)
        if text:
            turns.append(text)
    return turns[-RECENT_TURN_LIMIT:]


def _message_text(message: Mapping[str, Any]) -> str:
    for key in ("content", "text", "message", "body"):
        text = _flatten_text(message.get(key))
        if text:
            return text
    return _flatten_text(message)


def _score_evidence(
    user_turns: list[str],
    topics: list[str],
    mood_state: MoodState,
) -> tuple[Counter[str], list[str]]:
    scores: Counter[str] = Counter()
    evidence: list[str] = []

    for turn in user_turns:
        turn_scores = _score_text(turn)
        scores.update(turn_scores)
        evidence.extend(_evidence_fragments(turn, turn_scores))
        word_count = len(turn.split())
        if word_count >= 80 and turn_scores["stressed"] > 0:
            scores["stressed"] += 0.5
            evidence.append("long stressed turn")
        if word_count <= 4 and _contains_any(turn, ("ok", "fine", "sure")):
            scores["neutral"] += 0.3

    topic_text = " ".join(_clean_text(topic).lower() for topic in topics)
    if topic_text:
        topic_scores = _score_text(topic_text)
        for state, score in topic_scores.items():
            scores[state] += score * 0.5
        if topic_scores:
            evidence.append("topics align with " + _top_candidate(topic_scores)[0])

    mood = _clean_text(getattr(mood_state, "mood", "")).lower()
    if mood in {"direct", "neutral"}:
        scores["focused"] += 0.2
    elif mood in {"gentle", "reflective"}:
        scores["stressed"] += 0.2
    elif mood in {"playful", "upbeat"}:
        scores["playful"] += 0.2

    _apply_sentiment(scores, " ".join(user_turns).lower())

    return scores, evidence


def _score_text(text: str) -> Counter[str]:
    normalized = _clean_text(text).lower()
    scores: Counter[str] = Counter()
    for state, keywords in STATE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in normalized:
                scores[state] += 1.0
    if not scores and normalized:
        scores["neutral"] += 0.2
    return scores


def _evidence_fragments(text: str, scores: Counter[str]) -> list[str]:
    fragments: list[str] = []
    if not scores:
        return fragments
    top_state, top_score = _top_candidate(scores)
    if top_state is not None and top_score >= 1.0:
        fragments.append(f"{top_state} keyword signal")
    polarity = _sentiment_polarity(text.lower())
    if polarity > 0:
        fragments.append("positive wording")
    elif polarity < 0:
        fragments.append("negative wording")
    return fragments


def _apply_sentiment(scores: Counter[str], text: str) -> None:
    polarity = _sentiment_polarity(text)
    if polarity >= 2:
        scores["excited"] += 0.4
        scores["playful"] += 0.2
    elif polarity <= -2:
        scores["stressed"] += 0.2
        scores["frustrated"] += 0.2


def _sentiment_polarity(text: str) -> int:
    positive = sum(1 for word in POSITIVE_WORDS if word in text)
    negative = sum(1 for word in NEGATIVE_WORDS if word in text)
    return positive - negative


def _consistent_recent_candidate(
    user_turns: list[str],
    topics: list[str],
    mood_state: MoodState,
) -> Optional[str]:
    del topics, mood_state

    if len(user_turns) < SLOW_SHIFT_TURN_COUNT:
        return None

    candidates: list[str] = []
    for turn in user_turns[-SLOW_SHIFT_TURN_COUNT:]:
        scores = _score_text(turn)
        _apply_sentiment(scores, turn.lower())
        candidate, score = _top_candidate(scores)
        if candidate is None or score < MIN_CONSISTENT_TURN_SCORE:
            return None
        candidates.append(candidate)

    first = candidates[0]
    return first if all(candidate == first for candidate in candidates) else None


def _top_candidate(scores: Mapping[str, float]) -> tuple[Optional[str], float]:
    ranked = sorted(
        (
            (state, float(score))
            for state, score in scores.items()
            if state in USER_STATE_LABELS and score > 0
        ),
        key=lambda item: (-item[1], USER_STATE_LABELS.index(item[0])),
    )
    if not ranked:
        return None, 0.0
    return ranked[0]


def _confidence_from_scores(
    scores: Counter[str],
    candidate: str,
    prior_user_state: Optional[UserState],
) -> float:
    ranked = sorted(scores.values(), reverse=True)
    top_score = float(scores[candidate])
    margin = top_score - float(ranked[1] if len(ranked) > 1 else 0.0)
    confidence = DEFAULT_CONFIDENCE + min(0.2, top_score * 0.04) + min(0.1, margin * 0.03)
    if prior_user_state is not None and candidate == prior_user_state.primary_state:
        confidence = max(confidence, prior_user_state.confidence)
    return _clamp(confidence, 0.0, MAX_CONFIDENCE)


def _secondary_states(
    scores: Counter[str],
    primary_state: str,
    primary_confidence: float,
) -> list[tuple[str, float]]:
    ranked = sorted(
        (
            (state, float(score))
            for state, score in scores.items()
            if state != primary_state and state in USER_STATE_LABELS and score > 0
        ),
        key=lambda item: (-item[1], USER_STATE_LABELS.index(item[0])),
    )
    secondary: list[tuple[str, float]] = []
    for state, score in ranked[:3]:
        confidence = min(primary_confidence - 0.05, DEFAULT_CONFIDENCE + score * 0.03)
        if confidence > 0:
            secondary.append((state, _clamp(confidence, 0.0, MAX_CONFIDENCE)))
    return secondary


def _normalize_secondary_states(
    secondary_states: list[tuple[str, float]],
    primary_confidence: float,
) -> list[tuple[str, float]]:
    normalized: list[tuple[str, float]] = []
    for state, confidence in secondary_states:
        if state not in USER_STATE_LABELS:
            raise ValueError(f"secondary state must be one of: {', '.join(USER_STATE_LABELS)}")
        capped = _clamp(float(confidence), 0.0, min(MAX_CONFIDENCE, primary_confidence))
        if capped >= primary_confidence and primary_confidence > 0:
            capped = max(0.0, primary_confidence - 0.01)
        normalized.append((state, capped))
    return normalized


def _carry_forward(state: UserState, now: datetime, reason: str) -> UserState:
    return UserState(
        primary_state=state.primary_state,
        confidence=state.confidence,
        secondary_states=list(state.secondary_states),
        evidence_summary=_short_summary(reason),
        last_inferred=now,
        inference_count=state.inference_count + 1,
        confidence_history=_append_confidence(state, state.confidence),
    )


def _append_confidence(
    prior_user_state: Optional[UserState],
    confidence: float,
) -> tuple[float, ...]:
    history = prior_user_state.confidence_history if prior_user_state is not None else ()
    return (*history, _clamp(confidence, 0.0, MAX_CONFIDENCE))[-CONFIDENCE_HISTORY_LIMIT:]


def _evidence_summary(candidate: str, evidence: list[str]) -> str:
    counts = Counter(evidence)
    fragments = [fragment for fragment, _ in counts.most_common(3)]
    if not fragments:
        return f"{candidate} inferred from weak local signals"
    return f"{candidate}: " + "; ".join(fragments)


def _flatten_text(value: Any) -> str:
    parts: list[str] = []

    def visit(item: Any) -> None:
        if item is None:
            return
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, Mapping):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (list, tuple, set, frozenset)):
            for nested in item:
                visit(nested)
        elif isinstance(item, (bool, int, float)):
            parts.append(str(item))

    visit(value)
    return _clean_text(" ".join(parts))


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _short_summary(value: str) -> str:
    return _clean_text(value)[:240]


def _clean_text(value: Any) -> str:
    return " ".join(str(value).strip().split())


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


__all__ = [
    "DEFAULT_CONFIDENCE",
    "MAX_CONFIDENCE",
    "USER_STATE_LABELS",
    "UserState",
    "UserStateEngine",
]
