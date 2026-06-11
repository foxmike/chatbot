from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Literal, TypeAlias


MemoryTimescale: TypeAlias = Literal[
    "recent_conversational_state",
    "stable_identity_fact",
    "long_term_narrative_arc",
    "active_goal",
]

RECENT_CONVERSATIONAL_STATE: MemoryTimescale = "recent_conversational_state"
STABLE_IDENTITY_FACT: MemoryTimescale = "stable_identity_fact"
LONG_TERM_NARRATIVE_ARC: MemoryTimescale = "long_term_narrative_arc"
ACTIVE_GOAL: MemoryTimescale = "active_goal"

MEMORY_TIMESCALES: tuple[MemoryTimescale, ...] = (
    RECENT_CONVERSATIONAL_STATE,
    STABLE_IDENTITY_FACT,
    LONG_TERM_NARRATIVE_ARC,
    ACTIVE_GOAL,
)

_DEFAULT_RECENT_WINDOW = timedelta(hours=24)
_EXPLICIT_TIMESCALE_KEYS = (
    "timescale",
    "memory_timescale",
    "memory_tier",
    "tier",
    "category",
    "memory_type",
    "type",
    "domain",
)
_ACTIVE_STATUSES = {"active", "open", "pending", "in_progress", "current"}

_EXPLICIT_VALUE_MAP: dict[str, MemoryTimescale] = {
    "active_goal": ACTIVE_GOAL,
    "active_goals": ACTIVE_GOAL,
    "goal": ACTIVE_GOAL,
    "goals": ACTIVE_GOAL,
    "objective": ACTIVE_GOAL,
    "objectives": ACTIVE_GOAL,
    "promise": ACTIVE_GOAL,
    "promises": ACTIVE_GOAL,
    "running_project": ACTIVE_GOAL,
    "todo": ACTIVE_GOAL,
    "task": ACTIVE_GOAL,
    "recent": RECENT_CONVERSATIONAL_STATE,
    "recent_conversational_state": RECENT_CONVERSATIONAL_STATE,
    "conversation_state": RECENT_CONVERSATIONAL_STATE,
    "working_memory": RECENT_CONVERSATIONAL_STATE,
    "session_state": RECENT_CONVERSATIONAL_STATE,
    "stable_identity_fact": STABLE_IDENTITY_FACT,
    "stable_identity_facts": STABLE_IDENTITY_FACT,
    "identity": STABLE_IDENTITY_FACT,
    "identity_fact": STABLE_IDENTITY_FACT,
    "identity_facts": STABLE_IDENTITY_FACT,
    "profile": STABLE_IDENTITY_FACT,
    "persona": STABLE_IDENTITY_FACT,
    "preference": STABLE_IDENTITY_FACT,
    "semantic_fact": STABLE_IDENTITY_FACT,
    "semantic_facts": STABLE_IDENTITY_FACT,
    "long_term_narrative_arc": LONG_TERM_NARRATIVE_ARC,
    "long_term_narrative_arcs": LONG_TERM_NARRATIVE_ARC,
    "narrative": LONG_TERM_NARRATIVE_ARC,
    "narrative_arc": LONG_TERM_NARRATIVE_ARC,
    "narrative_thread": LONG_TERM_NARRATIVE_ARC,
    "timeline": LONG_TERM_NARRATIVE_ARC,
    "milestone": LONG_TERM_NARRATIVE_ARC,
    "emotional_event": LONG_TERM_NARRATIVE_ARC,
}

_PATTERNS: dict[MemoryTimescale, tuple[re.Pattern[str], ...]] = {
    ACTIVE_GOAL: (
        re.compile(r"\b(?:goal|objective|todo|to-do|next step|deadline|plan)\b", re.IGNORECASE),
        re.compile(r"\b(?:i|we|you)\s+(?:need|want|plan|intend|hope|aim|must|should)\s+to\b", re.IGNORECASE),
        re.compile(r"\b(?:working on|trying to|follow up|in progress|not done yet)\b", re.IGNORECASE),
    ),
    STABLE_IDENTITY_FACT: (
        re.compile(r"\b(?:my|your)\s+(?:name|birthday|timezone|job|role|company|team|email|phone)\b", re.IGNORECASE),
        re.compile(r"\b(?:i|you)\s+(?:am|work as|work at|live in|live near|grew up in|study|use|prefer|like|dislike|own|have)\b", re.IGNORECASE),
        re.compile(r"\b(?:remember that|noted that|keep in mind)\b", re.IGNORECASE),
    ),
    LONG_TERM_NARRATIVE_ARC: (
        re.compile(r"\b(?:narrative|thread|milestone|timeline|arc|recurring|over time)\b", re.IGNORECASE),
        re.compile(r"\b(?:we talked|we discussed|last time|previously|since then|again)\b", re.IGNORECASE),
        re.compile(r"\b(?:relationship|history|pattern|project|promise|event)\b", re.IGNORECASE),
    ),
    RECENT_CONVERSATIONAL_STATE: (
        re.compile(r"\b(?:just|currently|right now|today|this turn|this chat|earlier)\b", re.IGNORECASE),
    ),
}


@dataclass(frozen=True, slots=True)
class TimescaleClassification:
    """A retrieved memory assigned to one of the supported timescales."""

    memory: Any
    timescale: MemoryTimescale
    confidence: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TimescaleBuckets:
    """Retrieved memories grouped by their conversational timescale."""

    recent_conversational_state: tuple[TimescaleClassification, ...] = ()
    stable_identity_facts: tuple[TimescaleClassification, ...] = ()
    long_term_narrative_arcs: tuple[TimescaleClassification, ...] = ()
    active_goals: tuple[TimescaleClassification, ...] = ()

    def for_timescale(self, timescale: MemoryTimescale) -> tuple[TimescaleClassification, ...]:
        if timescale == RECENT_CONVERSATIONAL_STATE:
            return self.recent_conversational_state
        if timescale == STABLE_IDENTITY_FACT:
            return self.stable_identity_facts
        if timescale == LONG_TERM_NARRATIVE_ARC:
            return self.long_term_narrative_arcs
        if timescale == ACTIVE_GOAL:
            return self.active_goals
        raise ValueError(f"Unsupported memory timescale: {timescale}")

    def as_dict(self) -> dict[str, list[Any]]:
        return {
            RECENT_CONVERSATIONAL_STATE: [item.memory for item in self.recent_conversational_state],
            STABLE_IDENTITY_FACT: [item.memory for item in self.stable_identity_facts],
            LONG_TERM_NARRATIVE_ARC: [item.memory for item in self.long_term_narrative_arcs],
            ACTIVE_GOAL: [item.memory for item in self.active_goals],
        }


@dataclass(frozen=True, slots=True)
class _TimescaleScore:
    value: float = 0.0
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def add(self, amount: float, reason: str) -> _TimescaleScore:
        return _TimescaleScore(
            value=self.value + amount,
            reasons=(*self.reasons, reason),
        )


class MemoryTimescaleClassifier:
    """Classify retrieved memories into context-building timescales.

    The classifier is deliberately structural: it accepts retrieval result
    objects, episodic turns, search results, mappings, or plain strings.
    """

    def __init__(
        self,
        *,
        recent_window: timedelta = _DEFAULT_RECENT_WINDOW,
    ) -> None:
        if recent_window.total_seconds() < 0:
            raise ValueError("recent_window must be non-negative")
        self.recent_window = recent_window

    def classify(
        self,
        memory: Any,
        *,
        reference_time: datetime | None = None,
    ) -> TimescaleClassification:
        content = _extract_content(memory)
        metadata = _extract_metadata(memory)
        created_at = _extract_created_at(memory, metadata)
        reference = _normalize_datetime(reference_time) or created_at

        scores = _empty_scores()
        scores = _score_explicit_metadata(scores, metadata)
        scores = _score_tags(scores, metadata)
        scores = _score_status(scores, metadata)
        scores = _score_content(scores, content)
        scores = _score_recency(
            scores,
            created_at=created_at,
            reference_time=reference,
            recent_window=self.recent_window,
        )
        scores = _score_shape(scores, memory, metadata)

        timescale, score = _best_score(scores)
        return TimescaleClassification(
            memory=memory,
            timescale=timescale,
            confidence=_confidence(score.value),
            reasons=score.reasons,
        )

    def structure(
        self,
        memories: Iterable[Any],
        *,
        reference_time: datetime | None = None,
    ) -> TimescaleBuckets:
        memory_list = list(memories)
        reference = _normalize_datetime(reference_time) or _latest_created_at(memory_list)
        classifications = [
            self.classify(memory, reference_time=reference)
            for memory in memory_list
        ]
        return _bucket_classifications(classifications)


def classify_memory(
    memory: Any,
    *,
    reference_time: datetime | None = None,
    recent_window: timedelta = _DEFAULT_RECENT_WINDOW,
) -> TimescaleClassification:
    classifier = MemoryTimescaleClassifier(recent_window=recent_window)
    return classifier.classify(memory, reference_time=reference_time)


def classify_memories(
    memories: Iterable[Any],
    *,
    reference_time: datetime | None = None,
    recent_window: timedelta = _DEFAULT_RECENT_WINDOW,
) -> tuple[TimescaleClassification, ...]:
    classifier = MemoryTimescaleClassifier(recent_window=recent_window)
    memory_list = list(memories)
    reference = _normalize_datetime(reference_time) or _latest_created_at(memory_list)
    return tuple(
        classifier.classify(memory, reference_time=reference)
        for memory in memory_list
    )


def structure_memories(
    memories: Iterable[Any],
    *,
    reference_time: datetime | None = None,
    recent_window: timedelta = _DEFAULT_RECENT_WINDOW,
) -> TimescaleBuckets:
    classifier = MemoryTimescaleClassifier(recent_window=recent_window)
    return classifier.structure(memories, reference_time=reference_time)


def _empty_scores() -> dict[MemoryTimescale, _TimescaleScore]:
    return {timescale: _TimescaleScore() for timescale in MEMORY_TIMESCALES}


def _score_explicit_metadata(
    scores: dict[MemoryTimescale, _TimescaleScore],
    metadata: Mapping[str, Any],
) -> dict[MemoryTimescale, _TimescaleScore]:
    for key in _EXPLICIT_TIMESCALE_KEYS:
        value = _normalize_label(metadata.get(key))
        timescale = _EXPLICIT_VALUE_MAP.get(value)
        if timescale is not None:
            scores[timescale] = scores[timescale].add(4.0, f"metadata:{key}={value}")

    entry_type = _normalize_label(metadata.get("entry_type"))
    timescale = _EXPLICIT_VALUE_MAP.get(entry_type)
    if timescale is not None:
        scores[timescale] = scores[timescale].add(3.0, f"metadata:entry_type={entry_type}")

    return scores


def _score_tags(
    scores: dict[MemoryTimescale, _TimescaleScore],
    metadata: Mapping[str, Any],
) -> dict[MemoryTimescale, _TimescaleScore]:
    for tag in _extract_tags(metadata):
        timescale = _EXPLICIT_VALUE_MAP.get(_normalize_label(tag))
        if timescale is not None:
            scores[timescale] = scores[timescale].add(2.0, f"tag:{tag}")
    return scores


def _score_status(
    scores: dict[MemoryTimescale, _TimescaleScore],
    metadata: Mapping[str, Any],
) -> dict[MemoryTimescale, _TimescaleScore]:
    status = _normalize_label(metadata.get("status"))
    entry_type = _normalize_label(metadata.get("entry_type"))
    if status in _ACTIVE_STATUSES:
        if entry_type in {"promise", "running_project", "goal", "task", "todo"}:
            scores[ACTIVE_GOAL] = scores[ACTIVE_GOAL].add(3.0, f"active:{entry_type}")
        else:
            scores[ACTIVE_GOAL] = scores[ACTIVE_GOAL].add(1.0, f"status:{status}")
    return scores


def _score_content(
    scores: dict[MemoryTimescale, _TimescaleScore],
    content: str,
) -> dict[MemoryTimescale, _TimescaleScore]:
    if not content:
        return scores

    for timescale, patterns in _PATTERNS.items():
        for pattern in patterns:
            if pattern.search(content):
                scores[timescale] = scores[timescale].add(1.0, f"content:{timescale}")
                break
    return scores


def _score_recency(
    scores: dict[MemoryTimescale, _TimescaleScore],
    *,
    created_at: datetime | None,
    reference_time: datetime | None,
    recent_window: timedelta,
) -> dict[MemoryTimescale, _TimescaleScore]:
    if created_at is None or reference_time is None:
        return scores
    if created_at > reference_time:
        return scores
    if reference_time - created_at <= recent_window:
        scores[RECENT_CONVERSATIONAL_STATE] = scores[RECENT_CONVERSATIONAL_STATE].add(
            2.0,
            "created_at:recent",
        )
    return scores


def _score_shape(
    scores: dict[MemoryTimescale, _TimescaleScore],
    memory: Any,
    metadata: Mapping[str, Any],
) -> dict[MemoryTimescale, _TimescaleScore]:
    role = _normalize_label(_extract_role(memory) or metadata.get("role"))
    if role in {"user", "assistant", "system"}:
        scores[RECENT_CONVERSATIONAL_STATE] = scores[RECENT_CONVERSATIONAL_STATE].add(
            0.5,
            f"role:{role}",
        )

    if "confidence" in metadata:
        scores[STABLE_IDENTITY_FACT] = scores[STABLE_IDENTITY_FACT].add(
            0.5,
            "metadata:confidence",
        )

    return scores


def _best_score(
    scores: Mapping[MemoryTimescale, _TimescaleScore],
) -> tuple[MemoryTimescale, _TimescaleScore]:
    max_value = max(score.value for score in scores.values())
    if max_value <= 0:
        return RECENT_CONVERSATIONAL_STATE, _TimescaleScore()

    precedence = {
        ACTIVE_GOAL: 0,
        STABLE_IDENTITY_FACT: 1,
        LONG_TERM_NARRATIVE_ARC: 2,
        RECENT_CONVERSATIONAL_STATE: 3,
    }
    return max(
        scores.items(),
        key=lambda item: (item[1].value, -precedence[item[0]]),
    )


def _confidence(score: float) -> float:
    if score <= 0:
        return 0.0
    return min(1.0, score / 5.0)


def _bucket_classifications(
    classifications: Iterable[TimescaleClassification],
) -> TimescaleBuckets:
    buckets: dict[MemoryTimescale, list[TimescaleClassification]] = {
        timescale: [] for timescale in MEMORY_TIMESCALES
    }
    for classification in classifications:
        buckets[classification.timescale].append(classification)

    return TimescaleBuckets(
        recent_conversational_state=tuple(buckets[RECENT_CONVERSATIONAL_STATE]),
        stable_identity_facts=tuple(buckets[STABLE_IDENTITY_FACT]),
        long_term_narrative_arcs=tuple(buckets[LONG_TERM_NARRATIVE_ARC]),
        active_goals=tuple(buckets[ACTIVE_GOAL]),
    )


def _latest_created_at(memories: Iterable[Any]) -> datetime | None:
    latest: datetime | None = None
    for memory in memories:
        metadata = _extract_metadata(memory)
        created_at = _extract_created_at(memory, metadata)
        if created_at is not None and (latest is None or created_at > latest):
            latest = created_at
    return latest


def _extract_content(memory: Any) -> str:
    value = _extract_attr_or_key(memory, "content")
    if value is None:
        turn = _extract_attr_or_key(memory, "turn")
        if turn is not None:
            value = _extract_attr_or_key(turn, "content")
    if value is None:
        value = _extract_attr_or_key(memory, "text")
    if value is None:
        value = _extract_attr_or_key(memory, "fact_text")
    if value is None and isinstance(memory, str):
        value = memory
    return str(value or "")


def _extract_metadata(memory: Any) -> dict[str, Any]:
    metadata = _extract_attr_or_key(memory, "metadata")
    if metadata is None:
        turn = _extract_attr_or_key(memory, "turn")
        if turn is not None:
            metadata = _extract_attr_or_key(turn, "metadata")
    if isinstance(metadata, Mapping):
        return dict(metadata)
    return {}


def _extract_created_at(memory: Any, metadata: Mapping[str, Any]) -> datetime | None:
    value = _extract_attr_or_key(memory, "created_at")
    if value is None:
        turn = _extract_attr_or_key(memory, "turn")
        if turn is not None:
            value = _extract_attr_or_key(turn, "created_at")
    if value is None:
        value = metadata.get("created_at")
    return _coerce_datetime(value)


def _extract_role(memory: Any) -> Any:
    value = _extract_attr_or_key(memory, "role")
    if value is None:
        turn = _extract_attr_or_key(memory, "turn")
        if turn is not None:
            value = _extract_attr_or_key(turn, "role")
    return value


def _extract_tags(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    raw_tags = metadata.get("tags", ())
    if isinstance(raw_tags, str):
        return (raw_tags,)
    if isinstance(raw_tags, Iterable):
        return tuple(str(tag) for tag in raw_tags if tag is not None)
    return ()


def _extract_attr_or_key(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _normalize_label(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _normalize_datetime(value)
    if isinstance(value, str) and value:
        try:
            return _normalize_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
