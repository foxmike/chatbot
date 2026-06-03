"""Narrative threading engine producing grounded conversational callbacks using temporal and semantic continuity."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import inspect
import math
import re
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.continuity.timeline import RelationshipTimelineEntryModel
from chatbot.db import models
from chatbot.db.session import async_session_factory
from chatbot.memory.pgvector_store import PgvectorStore


NARRATIVE_ENTRY_TYPE = "narrative_thread"
_THREAD_TAG = "narrative_thread"
_TOPIC_SIMILARITY_THRESHOLD = 0.58
_LEXICAL_SIMILARITY_THRESHOLD = 0.26
_MAX_TURNS_FOR_CLUSTERING = 200
_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "before",
    "being",
    "could",
    "didn",
    "does",
    "don",
    "down",
    "from",
    "have",
    "just",
    "last",
    "like",
    "more",
    "much",
    "need",
    "night",
    "over",
    "really",
    "said",
    "should",
    "that",
    "them",
    "then",
    "there",
    "thing",
    "this",
    "through",
    "today",
    "turn",
    "want",
    "week",
    "were",
    "what",
    "when",
    "with",
    "worried",
    "would",
    "you",
    "your",
}


@dataclass(slots=True)
class NarrativeThread:
    thread_id: str
    session_id: str
    label: str
    turns: list[str]
    created_at: str
    last_referenced_at: str
    relevance_score: float
    summary: str


@dataclass(slots=True)
class ThreadCallback:
    thread_id: str
    label: str
    callback_text: str
    turn_id: str
    confidence: float
    thread_summary: str


@dataclass(slots=True)
class NarrativeReferencePoint:
    turn_id: str
    turn_text: str
    thread_label: str | None
    narrative_summary: str | None
    created_at: str
    emotional_significance: float


@dataclass(slots=True)
class _TurnRecord:
    turn_id: str
    session_id: str
    role: str
    content: str
    created_at: datetime
    embedding: list[float] | None


async def build_narrative_threads(session_id: str) -> list[NarrativeThread]:
    """Build recurring conversational threads for a session."""
    async with async_session_factory() as db:
        turns = await _load_session_turns(db, session_id)
        if len(turns) < 2:
            return []

        timeline_by_turn = await _load_timeline_by_turn(db, session_id)
        components = await _cluster_turns(db, session_id, turns, timeline_by_turn)
        threads = [
            _component_to_thread(session_id, component, timeline_by_turn)
            for component in components
            if len(component) >= 2
        ]
        threads = sorted(
            threads,
            key=lambda thread: (-thread.relevance_score, thread.last_referenced_at, thread.thread_id),
        )
        await _persist_threads(db, threads, timeline_by_turn)
        return threads


async def generate_thread_callback(
    session_id: str,
    current_message: str,
    relevance_threshold: float = 0.6,
) -> list[ThreadCallback]:
    """Return grounded narrative callbacks relevant to the current message."""
    if relevance_threshold < 0.0:
        raise ValueError("relevance_threshold must be non-negative")
    if not current_message.strip():
        return []

    threads = await _load_persisted_threads(session_id)
    if not threads:
        threads = await build_narrative_threads(session_id)
    if not threads:
        return []

    turn_ids = sorted({turn_id for thread in threads for turn_id in thread.turns})
    reference_points = await get_reference_points(turn_ids)
    points_by_turn = {point.turn_id: point for point in reference_points}

    async with async_session_factory() as db:
        vector_scores = await _message_vector_scores(db, session_id, current_message)

    callbacks: list[ThreadCallback] = []
    for thread in threads:
        thread_points = [
            points_by_turn[turn_id]
            for turn_id in thread.turns
            if turn_id in points_by_turn
        ]
        if not thread_points:
            continue

        similarity = _thread_similarity(current_message, thread, thread_points, vector_scores)
        recency_factor = _thread_recency_factor(thread, thread_points)
        relevance = similarity * recency_factor
        if relevance < relevance_threshold:
            continue

        emotional_factor = _emotional_weight_factor(thread_points)
        confidence = _clamp(relevance * emotional_factor)
        indicating_point = max(
            thread_points,
            key=lambda point: (
                _turn_similarity(current_message, thread, point, vector_scores),
                _parse_datetime(point.created_at).timestamp(),
                point.turn_id,
            ),
        )
        callbacks.append(
            ThreadCallback(
                thread_id=thread.thread_id,
                label=thread.label,
                callback_text=_callback_text(thread, indicating_point),
                turn_id=indicating_point.turn_id,
                confidence=confidence,
                thread_summary=thread.summary,
            )
        )

    callbacks.sort(key=lambda callback: (-callback.confidence, callback.thread_id))
    return callbacks[:3]


async def get_reference_points(turn_ids: list[str]) -> list[NarrativeReferencePoint]:
    """Return message text and narrative metadata for the requested turns."""
    if not turn_ids:
        return []

    valid_turn_ids = [_to_uuid(turn_id, "turn_id") for turn_id in turn_ids]
    order = {str(turn_id): index for index, turn_id in enumerate(valid_turn_ids)}

    async with async_session_factory() as db:
        result = await db.execute(
            sa.select(models.Message)
            .where(models.Message.id.in_(valid_turn_ids))
            .order_by(models.Message.created_at.asc(), models.Message.id.asc())
        )
        messages = list(result.scalars().all())
        narrative_entries = await _load_narrative_entries_for_turns(db, valid_turn_ids)
        timeline_by_turn = await _load_timeline_for_turn_ids(db, valid_turn_ids)

    narrative_by_turn = _narrative_metadata_by_turn(narrative_entries)
    points = [
        NarrativeReferencePoint(
            turn_id=str(message.id),
            turn_text=message.content,
            thread_label=narrative_by_turn.get(str(message.id), {}).get("label"),
            narrative_summary=narrative_by_turn.get(str(message.id), {}).get("summary"),
            created_at=_dt_iso(message.created_at),
            emotional_significance=_turn_emotional_significance(
                timeline_by_turn.get(str(message.id), [])
            ),
        )
        for message in messages
    ]
    return sorted(points, key=lambda point: order.get(point.turn_id, len(order)))


async def mark_thread_referenced(thread_id: str) -> None:
    """Update a persisted narrative thread's last-reference timestamp."""
    now = _now()
    metadata_now = _dt_iso(now)
    try:
        thread_uuid = _to_uuid(thread_id, "thread_id")
    except ValueError:
        return

    async with async_session_factory() as db:
        try:
            result = await db.execute(
                sa.select(RelationshipTimelineEntryModel).where(
                    RelationshipTimelineEntryModel.id == thread_uuid,
                    RelationshipTimelineEntryModel.entry_type == NARRATIVE_ENTRY_TYPE,
                )
            )
            model = result.scalar_one_or_none()
            if model is None:
                return

            metadata = dict(model.metadata_json or {})
            metadata["last_referenced_at"] = metadata_now
            model.metadata_json = metadata
            model.updated_at = now
            await _flush_or_commit(db)
        except SQLAlchemyError:
            await db.rollback()


async def _load_session_turns(db: AsyncSession, session_id: str) -> list[_TurnRecord]:
    result = await db.execute(
        sa.select(models.Message)
        .where(
            models.Message.session_id == _to_uuid(session_id, "session_id"),
            models.Message.role == "user",
        )
        .order_by(models.Message.created_at.asc(), models.Message.id.asc())
        .limit(_MAX_TURNS_FOR_CLUSTERING)
    )
    return [
        _TurnRecord(
            turn_id=str(message.id),
            session_id=str(message.session_id),
            role=message.role,
            content=message.content,
            created_at=_normalize_datetime(message.created_at),
            embedding=_embedding_list(message.embedding),
        )
        for message in result.scalars().all()
        if message.content.strip()
    ]


async def _load_timeline_by_turn(
    db: AsyncSession,
    session_id: str,
) -> dict[str, list[RelationshipTimelineEntryModel]]:
    try:
        result = await db.execute(
            sa.select(RelationshipTimelineEntryModel).where(
                RelationshipTimelineEntryModel.session_id == _to_uuid(session_id, "session_id")
            )
        )
    except SQLAlchemyError:
        await db.rollback()
        return {}

    timeline_by_turn: dict[str, list[RelationshipTimelineEntryModel]] = defaultdict(list)
    for entry in result.scalars().all():
        timeline_by_turn[str(entry.turn_id)].append(entry)
    return dict(timeline_by_turn)


async def _load_timeline_for_turn_ids(
    db: AsyncSession,
    turn_ids: list[uuid.UUID],
) -> dict[str, list[RelationshipTimelineEntryModel]]:
    if not turn_ids:
        return {}
    try:
        result = await db.execute(
            sa.select(RelationshipTimelineEntryModel).where(
                RelationshipTimelineEntryModel.turn_id.in_(turn_ids)
            )
        )
    except SQLAlchemyError:
        await db.rollback()
        return {}

    timeline_by_turn: dict[str, list[RelationshipTimelineEntryModel]] = defaultdict(list)
    for entry in result.scalars().all():
        timeline_by_turn[str(entry.turn_id)].append(entry)
    return dict(timeline_by_turn)


async def _load_persisted_threads(session_id: str) -> list[NarrativeThread]:
    async with async_session_factory() as db:
        try:
            result = await db.execute(
                sa.select(RelationshipTimelineEntryModel)
                .where(
                    RelationshipTimelineEntryModel.session_id == _to_uuid(
                        session_id,
                        "session_id",
                    ),
                    RelationshipTimelineEntryModel.entry_type == NARRATIVE_ENTRY_TYPE,
                )
                .order_by(RelationshipTimelineEntryModel.updated_at.desc())
            )
        except SQLAlchemyError:
            await db.rollback()
            return []
        models_ = list(result.scalars().all())

    threads = []
    for model in models_:
        thread = _timeline_model_to_thread(model)
        if thread is not None:
            threads.append(thread)
    return sorted(
        threads,
        key=lambda thread: (-thread.relevance_score, thread.last_referenced_at, thread.thread_id),
    )


async def _load_narrative_entries_for_turns(
    db: AsyncSession,
    turn_ids: list[uuid.UUID],
) -> list[RelationshipTimelineEntryModel]:
    if not turn_ids:
        return []
    try:
        result = await db.execute(
            sa.select(RelationshipTimelineEntryModel).where(
                RelationshipTimelineEntryModel.entry_type == NARRATIVE_ENTRY_TYPE
            )
        )
    except SQLAlchemyError:
        await db.rollback()
        return []

    requested = {str(turn_id) for turn_id in turn_ids}
    entries = []
    for entry in result.scalars().all():
        metadata = dict(entry.metadata_json or {})
        entry_turns = {str(turn_id) for turn_id in metadata.get("turns", [])}
        if requested & (entry_turns | {str(entry.turn_id)}):
            entries.append(entry)
    return entries


async def _cluster_turns(
    db: AsyncSession,
    session_id: str,
    turns: list[_TurnRecord],
    timeline_by_turn: dict[str, list[RelationshipTimelineEntryModel]],
) -> list[list[_TurnRecord]]:
    index_by_turn = {turn.turn_id: index for index, turn in enumerate(turns)}
    parent = list(range(len(turns)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    await _add_vector_edges(db, session_id, turns, index_by_turn, union)

    for left_index, left_turn in enumerate(turns):
        for right_index in range(left_index + 1, len(turns)):
            right_turn = turns[right_index]
            lexical = _lexical_similarity(left_turn.content, right_turn.content)
            timeline_boost = _timeline_relatedness(
                timeline_by_turn.get(left_turn.turn_id, []),
                timeline_by_turn.get(right_turn.turn_id, []),
            )
            if lexical + timeline_boost >= _LEXICAL_SIMILARITY_THRESHOLD:
                union(left_index, right_index)

    components_by_root: dict[int, list[_TurnRecord]] = defaultdict(list)
    for index, turn in enumerate(turns):
        components_by_root[find(index)].append(turn)

    return [
        sorted(component, key=lambda turn: (turn.created_at, turn.turn_id))
        for component in components_by_root.values()
        if len(component) >= 2
    ]


async def _add_vector_edges(
    db: AsyncSession,
    session_id: str,
    turns: list[_TurnRecord],
    index_by_turn: dict[str, int],
    union: Any,
) -> None:
    vector_store = PgvectorStore(db, models.Message)
    for turn in turns:
        if not turn.embedding:
            continue
        try:
            results = await vector_store.search(
                embedding=turn.embedding,
                top_k=8,
                filter={"session_id": session_id},
            )
        except (SQLAlchemyError, ValueError):
            await db.rollback()
            return

        for result in results:
            other_index = index_by_turn.get(result.id)
            if other_index is None or result.id == turn.turn_id:
                continue
            if _distance_to_similarity(result.score) >= _TOPIC_SIMILARITY_THRESHOLD:
                union(index_by_turn[turn.turn_id], other_index)


def _component_to_thread(
    session_id: str,
    component: list[_TurnRecord],
    timeline_by_turn: dict[str, list[RelationshipTimelineEntryModel]],
) -> NarrativeThread:
    all_entries = [
        entry
        for turn in component
        for entry in timeline_by_turn.get(turn.turn_id, [])
        if entry.entry_type != NARRATIVE_ENTRY_TYPE
    ]
    label = _thread_label(component, all_entries)
    summary = _thread_summary(label, component, all_entries)
    first_turn = component[0]
    last_turn = component[-1]
    thread_id = str(_thread_uuid(session_id, [turn.turn_id for turn in component]))
    return NarrativeThread(
        thread_id=thread_id,
        session_id=session_id,
        label=label,
        turns=[turn.turn_id for turn in component],
        created_at=_dt_iso(first_turn.created_at),
        last_referenced_at=_dt_iso(last_turn.created_at),
        relevance_score=_thread_relevance(component, all_entries),
        summary=summary,
    )


async def _persist_threads(
    db: AsyncSession,
    threads: list[NarrativeThread],
    timeline_by_turn: dict[str, list[RelationshipTimelineEntryModel]],
) -> None:
    for thread in threads:
        try:
            thread_uuid = _to_uuid(thread.thread_id, "thread_id")
            result = await db.execute(
                sa.select(RelationshipTimelineEntryModel).where(
                    RelationshipTimelineEntryModel.id == thread_uuid
                )
            )
            model = result.scalar_one_or_none()
            if model is None:
                model = RelationshipTimelineEntryModel(
                    id=thread_uuid,
                    session_id=_to_uuid(thread.session_id, "session_id"),
                    entry_type=NARRATIVE_ENTRY_TYPE,
                    title=thread.label,
                    description=thread.summary,
                    content=thread.summary,
                    turn_id=_to_uuid(thread.turns[-1], "turn_id"),
                    status="active",
                    tags=[_THREAD_TAG, thread.label],
                    metadata_json={},
                    created_at=_parse_datetime(thread.created_at),
                    updated_at=_now(),
                )
                db.add(model)

            model.entry_type = NARRATIVE_ENTRY_TYPE
            model.title = thread.label
            model.description = thread.summary
            model.content = thread.summary
            model.turn_id = _to_uuid(thread.turns[-1], "turn_id")
            model.tags = [_THREAD_TAG, thread.label]
            model.metadata_json = _thread_metadata(thread, timeline_by_turn)
            model.updated_at = _now()
        except ValueError:
            continue

    try:
        await _flush_or_commit(db)
    except SQLAlchemyError:
        await db.rollback()


def _thread_metadata(
    thread: NarrativeThread,
    timeline_by_turn: dict[str, list[RelationshipTimelineEntryModel]],
) -> dict[str, Any]:
    return {
        "thread_id": thread.thread_id,
        "session_id": thread.session_id,
        "label": thread.label,
        "turns": list(thread.turns),
        "summary": thread.summary,
        "created_at": thread.created_at,
        "last_referenced_at": thread.last_referenced_at,
        "relevance_score": thread.relevance_score,
        "emotional_significance_by_turn": {
            turn_id: _turn_emotional_significance(timeline_by_turn.get(turn_id, []))
            for turn_id in thread.turns
        },
    }


async def _message_vector_scores(
    db: AsyncSession,
    session_id: str,
    current_message: str,
) -> dict[str, float]:
    current_embedding = await _current_message_embedding(db, session_id, current_message)
    if not current_embedding:
        return {}

    vector_store = PgvectorStore(db, models.Message)
    try:
        results = await vector_store.search(
            embedding=current_embedding,
            top_k=50,
            filter={"session_id": session_id},
        )
    except (SQLAlchemyError, ValueError):
        await db.rollback()
        return {}

    return {
        result.id: _distance_to_similarity(result.score)
        for result in results
    }


async def _current_message_embedding(
    db: AsyncSession,
    session_id: str,
    current_message: str,
) -> list[float] | None:
    result = await db.execute(
        sa.select(models.Message.embedding)
        .where(
            models.Message.session_id == _to_uuid(session_id, "session_id"),
            models.Message.role == "user",
            models.Message.content == current_message,
            models.Message.embedding.is_not(None),
        )
        .order_by(models.Message.created_at.desc())
        .limit(1)
    )
    return _embedding_list(result.scalar_one_or_none())


def _thread_similarity(
    current_message: str,
    thread: NarrativeThread,
    points: list[NarrativeReferencePoint],
    vector_scores: dict[str, float],
) -> float:
    scores = [
        _turn_similarity(current_message, thread, point, vector_scores)
        for point in points
    ]
    return max(scores, default=0.0)


def _turn_similarity(
    current_message: str,
    thread: NarrativeThread,
    point: NarrativeReferencePoint,
    vector_scores: dict[str, float],
) -> float:
    vector_score = vector_scores.get(point.turn_id)
    lexical = max(
        _lexical_similarity(current_message, point.turn_text),
        _lexical_similarity(current_message, thread.label),
        _lexical_similarity(current_message, thread.summary),
    )
    if vector_score is None:
        return lexical
    return max(vector_score, (0.7 * vector_score) + (0.3 * lexical))


def _callback_text(thread: NarrativeThread, point: NarrativeReferencePoint) -> str:
    time_phrase = _relative_time_phrase(_parse_datetime(point.created_at), _now())
    connection = thread.summary.rstrip(".")
    return f"{time_phrase} you were talking about {thread.label}; {connection}."


def _thread_label(
    component: list[_TurnRecord],
    entries: list[RelationshipTimelineEntryModel],
) -> str:
    titles = [entry.title.strip().lower() for entry in entries if entry.title.strip()]
    if titles:
        title = Counter(titles).most_common(1)[0][0]
        return _short_label(title)

    keywords = _component_keywords(component)
    if not keywords:
        return "ongoing topic"
    return _short_label(" ".join(keywords[:3]))


def _thread_summary(
    label: str,
    component: list[_TurnRecord],
    entries: list[RelationshipTimelineEntryModel],
) -> str:
    emotional_entries = [
        entry for entry in entries if entry.entry_type == "emotional_event"
    ]
    if emotional_entries:
        notable = max(
            emotional_entries,
            key=lambda entry: abs(_entry_emotional_significance(entry)),
        )
        return _one_sentence(
            notable.description
            or notable.content
            or f"The conversation kept returning to {label}."
        )

    first = _brief_text(component[0].content)
    last = _brief_text(component[-1].content)
    if first == last:
        return _one_sentence(f"The conversation kept returning to {label}: {first}.")
    return _one_sentence(
        f"The conversation kept returning to {label}, from {first} to {last}."
    )


def _thread_relevance(
    component: list[_TurnRecord],
    entries: list[RelationshipTimelineEntryModel],
) -> float:
    recency = _recency_factor(component[-1].created_at, _now())
    recurrence = min(1.0, 0.45 + (len(component) * 0.12))
    emotional = 1.0 + min(
        0.2,
        max((abs(_entry_emotional_significance(entry)) for entry in entries), default=0.0) * 0.2,
    )
    return _clamp((0.62 * recency + 0.38 * recurrence) * emotional)


def _thread_recency_factor(
    thread: NarrativeThread,
    points: list[NarrativeReferencePoint],
) -> float:
    now = _now()
    latest_source = max(
        (_parse_datetime(point.created_at) for point in points),
        default=_parse_datetime(thread.created_at),
    )
    recency = _recency_factor(latest_source, now)
    last_referenced = _parse_datetime(thread.last_referenced_at)
    if last_referenced > latest_source:
        recency *= _reference_cooldown_factor(last_referenced, now)
    return _clamp(recency)


def _reference_cooldown_factor(last_referenced: datetime, now: datetime) -> float:
    hours = max((now - _normalize_datetime(last_referenced)).total_seconds() / 3600.0, 0.0)
    if hours <= 6.0:
        return 0.35
    if hours <= 24.0:
        return 0.55
    if hours <= 72.0:
        return 0.75
    return 1.0


def _recency_factor(moment: datetime, now: datetime) -> float:
    days = max((now - _normalize_datetime(moment)).total_seconds() / 86400.0, 0.0)
    if days <= 1.0:
        return 1.0
    if days <= 7.0:
        return 0.9
    if days <= 30.0:
        return 0.74
    if days <= 90.0:
        return 0.56
    return 0.42


def _emotional_weight_factor(points: list[NarrativeReferencePoint]) -> float:
    emotional = max((abs(point.emotional_significance) for point in points), default=0.0)
    return 1.0 + min(0.25, emotional * 0.25)


def _turn_emotional_significance(entries: list[RelationshipTimelineEntryModel]) -> float:
    if not entries:
        return 0.0
    values = [_entry_emotional_significance(entry) for entry in entries]
    return _clamp_signed(max(values, key=abs))


def _entry_emotional_significance(entry: RelationshipTimelineEntryModel) -> float:
    metadata = dict(entry.metadata_json or {})
    for key in (
        "emotional_significance",
        "emotional_weight",
        "sentiment",
        "valence",
    ):
        if key in metadata:
            return _clamp_signed(_safe_float(metadata[key]))
    if entry.entry_type == "emotional_event":
        return 0.75
    return 0.0


def _timeline_relatedness(
    left_entries: list[RelationshipTimelineEntryModel],
    right_entries: list[RelationshipTimelineEntryModel],
) -> float:
    if not left_entries or not right_entries:
        return 0.0

    left_tags = {
        str(tag).lower()
        for entry in left_entries
        for tag in (entry.tags or [])
    }
    right_tags = {
        str(tag).lower()
        for entry in right_entries
        for tag in (entry.tags or [])
    }
    shared_tags = left_tags & right_tags
    shared_types = {entry.entry_type for entry in left_entries} & {
        entry.entry_type for entry in right_entries
    }
    return min(0.18, (0.08 * bool(shared_types)) + (0.04 * len(shared_tags)))


def _timeline_model_to_thread(
    model: RelationshipTimelineEntryModel,
) -> NarrativeThread | None:
    metadata = dict(model.metadata_json or {})
    turns = [str(turn_id) for turn_id in metadata.get("turns", []) if str(turn_id)]
    if len(turns) < 2:
        return None
    return NarrativeThread(
        thread_id=str(metadata.get("thread_id") or model.id),
        session_id=str(metadata.get("session_id") or model.session_id),
        label=str(metadata.get("label") or model.title),
        turns=turns,
        created_at=str(metadata.get("created_at") or _dt_iso(model.created_at)),
        last_referenced_at=str(
            metadata.get("last_referenced_at")
            or metadata.get("updated_at")
            or _dt_iso(model.updated_at)
        ),
        relevance_score=_clamp(_safe_float(metadata.get("relevance_score"), 0.0)),
        summary=str(metadata.get("summary") or model.description or model.content),
    )


def _narrative_metadata_by_turn(
    entries: list[RelationshipTimelineEntryModel],
) -> dict[str, dict[str, str]]:
    metadata_by_turn: dict[str, dict[str, str]] = {}
    for entry in entries:
        thread = _timeline_model_to_thread(entry)
        if thread is None:
            continue
        for turn_id in thread.turns:
            metadata_by_turn[turn_id] = {
                "label": thread.label,
                "summary": thread.summary,
            }
    return metadata_by_turn


def _component_keywords(component: list[_TurnRecord]) -> list[str]:
    tokens = [
        token
        for turn in component
        for token in _tokens(turn.content)
        if token not in _STOPWORDS and len(token) > 2
    ]
    return [token for token, _count in Counter(tokens).most_common(5)]


def _lexical_similarity(left: str, right: str) -> float:
    left_tokens = set(_tokens(left))
    right_tokens = set(_tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    if union == 0:
        return 0.0
    return intersection / union


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-zA-Z0-9']+", text.lower())
        if token and token not in _STOPWORDS
    ]


def _short_label(label: str) -> str:
    words = [word for word in _tokens(label) if len(word) > 2]
    if not words:
        return "ongoing topic"
    return " ".join(words[:3])


def _brief_text(text: str) -> str:
    words = text.strip().split()
    if len(words) <= 12:
        return " ".join(words)
    return " ".join(words[:12])


def _one_sentence(text: str) -> str:
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return "The conversation kept returning to an ongoing topic."
    sentence = cleaned.split(". ", 1)[0].rstrip(".")
    return f"{sentence}."


def _relative_time_phrase(moment: datetime, now: datetime) -> str:
    days = int(max((now - _normalize_datetime(moment)).total_seconds() // 86400, 0))
    if days == 0:
        return "earlier today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return "the other day"
    if days < 14:
        return "last week"
    if days < 31:
        return "a few weeks ago"
    if days < 61:
        return "last month"
    return "a while back"


def _distance_to_similarity(distance: float) -> float:
    return _clamp(1.0 / (1.0 + max(float(distance), 0.0)))


def _embedding_list(value: Any) -> list[float] | None:
    if value is None:
        return None
    return [float(item) for item in value]


def _thread_uuid(session_id: str, turn_ids: list[str]) -> uuid.UUID:
    key = f"narrative_thread:{session_id}:{','.join(sorted(turn_ids))}"
    return uuid.uuid5(uuid.NAMESPACE_URL, key)


def _to_uuid(value: str | uuid.UUID, field_name: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a UUID string") from exc


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _dt_iso(dt: datetime) -> str:
    return _normalize_datetime(dt).isoformat()


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return _normalize_datetime(value)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return _normalize_datetime(datetime.fromisoformat(normalized))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float) -> float:
    if math.isnan(value):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _clamp_signed(value: float) -> float:
    if math.isnan(value):
        return 0.0
    return max(-1.0, min(1.0, float(value)))


async def _flush_or_commit(db: Any) -> None:
    in_transaction = getattr(db, "in_transaction", None)
    if callable(in_transaction) and in_transaction():
        flush = getattr(db, "flush", None)
        if callable(flush):
            await _maybe_await(flush())
        return

    commit = getattr(db, "commit", None)
    if callable(commit):
        await _maybe_await(commit())
        return

    flush = getattr(db, "flush", None)
    if callable(flush):
        await _maybe_await(flush())


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = [
    "NarrativeReferencePoint",
    "NarrativeThread",
    "ThreadCallback",
    "build_narrative_threads",
    "generate_thread_callback",
    "get_reference_points",
    "mark_thread_referenced",
]
