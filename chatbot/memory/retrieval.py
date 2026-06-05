from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import math
import uuid
from typing import Any, Literal, TYPE_CHECKING

import httpx

from chatbot.memory.embeddings import EmbeddingProvider
from chatbot.memory.episodic import EpisodicTurn
from chatbot.memory.vector_store import SearchResult, VectorStore

if TYPE_CHECKING:
    from chatbot.config import Settings
    from chatbot.rooms.room import Room
    from chatbot.rooms.scene import SceneState

logger = logging.getLogger(__name__)


_RESERVED_METADATA_KEYS = {"session_id", "role", "content", "token_count", "created_at"}


@dataclass(frozen=True, slots=True)
class RoomMemoryContext:
    """Room-scoped memory retrieval context.

    Captures the memory policy settings that control how retrieval
    filters, ranks, and scopes results for a particular room.
    """

    room_id: str | None = None
    room_type: str | None = None
    memory_scope: Literal['room', 'companion', 'global'] = 'global'
    persist_to_durable: bool = True
    max_context_turns: int = 50
    include_embeddings: bool = True
    companion_id: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievalFilter:
    session_id: str | uuid.UUID | None = None
    start: datetime | None = None
    end: datetime | None = None
    metadata: Mapping[str, Any] | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    turn: EpisodicTurn
    score: float
    vector_score: float
    rank: int
    metadata_matches: dict[str, Any] = field(default_factory=dict)


class RetrievalEngine:
    def __init__(
        self,
        vector_store: VectorStore,
        embeddings: EmbeddingProvider,
        *,
        default_candidate_k: int | None = None,
    ) -> None:
        if default_candidate_k is not None and default_candidate_k <= 0:
            raise ValueError("default_candidate_k must be greater than zero")

        self.vector_store = vector_store
        self.embeddings = embeddings
        self.default_candidate_k = default_candidate_k

    async def retrieve(
        self,
        *,
        query: str | None = None,
        limit: int = 10,
        filters: RetrievalFilter | None = None,
        session_id: str | uuid.UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
        tags: Iterable[str] | None = None,
        candidate_k: int | None = None,
        room_context: RoomMemoryContext | None = None,
    ) -> list[RetrievalResult]:
        return await retrieve(
            self.vector_store,
            self.embeddings,
            query=query,
            limit=limit,
            filters=filters,
            session_id=session_id,
            start=start,
            end=end,
            metadata=metadata,
            tags=tags,
            candidate_k=candidate_k if candidate_k is not None else self.default_candidate_k,
            room_context=room_context,
        )


async def retrieve(
    vector_store: VectorStore,
    embeddings: EmbeddingProvider,
    *,
    query: str | None = None,
    limit: int = 10,
    filters: RetrievalFilter | None = None,
    session_id: str | uuid.UUID | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
    tags: Iterable[str] | None = None,
    candidate_k: int | None = None,
    room_context: RoomMemoryContext | None = None,
) -> list[RetrievalResult]:
    """Retrieve episodic turns with deterministic relational filtering and ranking.

    When *room_context* is provided, results are filtered and ranked according
    to the room's memory policy: scope (room/companion/global), durability
    preferences, embedding inclusion, and context-turn limits.
    """
    _validate_limit(limit, "limit")
    if candidate_k is not None:
        _validate_limit(candidate_k, "candidate_k")

    retrieval_filter = _merge_filters(
        filters,
        session_id=session_id,
        start=start,
        end=end,
        metadata=metadata,
        tags=tags,
    )

    if room_context is not None:
        retrieval_filter = _apply_room_context_metadata(retrieval_filter, room_context)

    _validate_time_range(retrieval_filter.start, retrieval_filter.end)

    effective_limit = limit
    if room_context is not None and room_context.max_context_turns > 0:
        effective_limit = min(limit, room_context.max_context_turns)

    query_embedding = await _embed_one(embeddings, query or "")
    raw_results = await vector_store.search(
        embedding=query_embedding,
        top_k=max(candidate_k or _default_candidate_k(effective_limit), effective_limit),
        filter=_vector_store_filter(retrieval_filter),
    )

    candidates = [
        _Candidate.from_search_result(result, retrieval_filter)
        for result in raw_results
    ]

    if room_context is not None and not room_context.include_embeddings:
        candidates = _neutralize_vector_scores(candidates, query or "")

    filtered = [candidate for candidate in candidates if _matches_filter(candidate, retrieval_filter)]

    if room_context is not None:
        filtered = _apply_room_memory_policy(filtered, room_context)

    deduped = _deduplicate(filtered)
    ranked = _rank_candidates(deduped, retrieval_filter)

    return [
        RetrievalResult(
            turn=candidate.turn,
            score=candidate.rank_score,
            vector_score=candidate.vector_score,
            rank=index + 1,
            metadata_matches=candidate.metadata_matches,
        )
        for index, candidate in enumerate(ranked[:effective_limit])
    ]


@dataclass(frozen=True, slots=True)
class _Candidate:
    turn: EpisodicTurn
    vector_score: float
    metadata_matches: dict[str, Any]
    rank_score: float = 0.0

    @classmethod
    def from_search_result(
        cls,
        result: SearchResult,
        retrieval_filter: RetrievalFilter,
    ) -> _Candidate:
        turn = _turn_from_search_result(result)
        metadata_matches = {
            key: turn.metadata[key]
            for key in retrieval_filter.metadata or {}
            if key in turn.metadata
        }
        if retrieval_filter.tags:
            metadata_matches["tags"] = tuple(
                tag for tag in retrieval_filter.tags if tag in _metadata_tags(turn.metadata)
            )

        return cls(
            turn=turn,
            vector_score=float(result.score),
            metadata_matches=metadata_matches,
        )

    def with_rank_score(self, score: float) -> _Candidate:
        return _Candidate(
            turn=self.turn,
            vector_score=self.vector_score,
            metadata_matches=dict(self.metadata_matches),
            rank_score=score,
        )


def _merge_filters(
    filters: RetrievalFilter | None,
    *,
    session_id: str | uuid.UUID | None,
    start: datetime | None,
    end: datetime | None,
    metadata: Mapping[str, Any] | None,
    tags: Iterable[str] | None,
) -> RetrievalFilter:
    base_metadata = dict(filters.metadata or {}) if filters is not None else {}
    if metadata is not None:
        base_metadata.update(metadata)

    base_tags = list(filters.tags) if filters is not None else []
    if tags is not None:
        base_tags.extend(str(tag) for tag in tags)

    return RetrievalFilter(
        session_id=session_id
        if session_id is not None
        else (filters.session_id if filters else None),
        start=start if start is not None else (filters.start if filters else None),
        end=end if end is not None else (filters.end if filters else None),
        metadata=base_metadata or None,
        tags=_unique_sorted_tags(base_tags),
    )


def _vector_store_filter(filters: RetrievalFilter) -> dict | None:
    if filters.session_id is None:
        return None
    return {"session_id": str(filters.session_id)}


def _matches_filter(candidate: _Candidate, filters: RetrievalFilter) -> bool:
    turn = candidate.turn

    if filters.session_id is not None and turn.session_id != str(filters.session_id):
        return False

    if filters.start is not None or filters.end is not None:
        created_at = _normalize_datetime(turn.created_at)
        if created_at is None:
            return False
        start = _normalize_datetime(filters.start)
        end = _normalize_datetime(filters.end)
        if start is not None and created_at < start:
            return False
        if end is not None and created_at > end:
            return False

    for key, expected in (filters.metadata or {}).items():
        if key not in turn.metadata or not _metadata_value_matches(turn.metadata[key], expected):
            return False

    if filters.tags:
        actual_tags = _metadata_tags(turn.metadata)
        if not set(filters.tags).issubset(actual_tags):
            return False

    return True


def _deduplicate(candidates: list[_Candidate]) -> list[_Candidate]:
    retained: list[_Candidate] = []
    seen_ids: set[str] = set()
    seen_content: set[str] = set()

    for candidate in sorted(candidates, key=_pre_rank_key):
        content_key = _content_key(candidate.turn)
        if candidate.turn.id in seen_ids or content_key in seen_content:
            continue

        retained.append(candidate)
        seen_ids.add(candidate.turn.id)
        seen_content.add(content_key)

    return retained


def _rank_candidates(
    candidates: list[_Candidate],
    filters: RetrievalFilter,
) -> list[_Candidate]:
    if not candidates:
        return []

    timestamps = [
        _datetime_timestamp(candidate.turn.created_at)
        for candidate in candidates
        if _datetime_timestamp(candidate.turn.created_at) is not None
    ]
    min_timestamp = min(timestamps) if timestamps else None
    max_timestamp = max(timestamps) if timestamps else None

    scored = [
        candidate.with_rank_score(
            _ranking_score(
                candidate,
                filters,
                min_timestamp=min_timestamp,
                max_timestamp=max_timestamp,
            )
        )
        for candidate in candidates
    ]

    return sorted(
        scored,
        key=lambda candidate: (
            -round(candidate.rank_score, 12),
            candidate.vector_score,
            -(_datetime_timestamp(candidate.turn.created_at) or 0.0),
            candidate.turn.id,
        ),
    )


def _ranking_score(
    candidate: _Candidate,
    filters: RetrievalFilter,
    *,
    min_timestamp: float | None,
    max_timestamp: float | None,
) -> float:
    vector_component = 1.0 / (1.0 + max(candidate.vector_score, 0.0))
    recency_component = _recency_score(
        candidate.turn.created_at,
        min_timestamp=min_timestamp,
        max_timestamp=max_timestamp,
    )
    relational_component = _relational_score(candidate, filters)

    return (0.72 * vector_component) + (0.18 * recency_component) + (
        0.10 * relational_component
    )


def _relational_score(candidate: _Candidate, filters: RetrievalFilter) -> float:
    checks = 0
    matches = 0

    if filters.session_id is not None:
        checks += 1
        matches += int(candidate.turn.session_id == str(filters.session_id))

    if filters.start is not None or filters.end is not None:
        checks += 1
        matches += 1

    metadata = filters.metadata or {}
    checks += len(metadata)
    matches += len(candidate.metadata_matches.keys() & metadata.keys())

    if filters.tags:
        checks += 1
        matches += int(set(filters.tags).issubset(_metadata_tags(candidate.turn.metadata)))

    if checks == 0:
        return 0.0
    return matches / checks


def _recency_score(
    value: datetime | None,
    *,
    min_timestamp: float | None,
    max_timestamp: float | None,
) -> float:
    timestamp = _datetime_timestamp(value)
    if timestamp is None or min_timestamp is None or max_timestamp is None:
        return 0.0
    if math.isclose(min_timestamp, max_timestamp):
        return 1.0
    return (timestamp - min_timestamp) / (max_timestamp - min_timestamp)


def _turn_from_search_result(result: SearchResult) -> EpisodicTurn:
    metadata = _expanded_metadata(result.metadata or {})
    return EpisodicTurn(
        id=result.id,
        session_id=_metadata_text(metadata, "session_id"),
        role=_metadata_text(metadata, "role"),
        content=_metadata_text(metadata, "content"),
        token_count=_metadata_int(metadata.get("token_count")),
        created_at=_metadata_datetime(metadata.get("created_at")),
        metadata={
            key: value
            for key, value in metadata.items()
            if key not in _RESERVED_METADATA_KEYS
        },
    )


def _expanded_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    expanded = dict(metadata)
    for key in ("metadata", "metadata_json"):
        nested = expanded.get(key)
        if isinstance(nested, Mapping):
            for nested_key, nested_value in nested.items():
                expanded.setdefault(str(nested_key), nested_value)
    return expanded


async def _embed_one(embeddings: EmbeddingProvider, text: str) -> list[float]:
    vectors = await embeddings.embed([text])
    if len(vectors) != 1:
        raise ValueError("embedding provider must return one vector for one input")
    return vectors[0]


def _metadata_value_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, set | frozenset | list | tuple):
        if isinstance(actual, set | frozenset | list | tuple):
            return {str(item) for item in expected}.issubset(str(item) for item in actual)
        return len(expected) == 1 and str(next(iter(expected))) == str(actual)
    return actual == expected


def _metadata_tags(metadata: Mapping[str, Any]) -> set[str]:
    value = metadata.get("tags", ())
    if isinstance(value, str):
        return {value}
    if isinstance(value, Iterable) and not isinstance(value, bytes | Mapping):
        return {str(item) for item in value}
    return set()


def _metadata_text(metadata: Mapping[str, Any], key: str) -> str:
    value = metadata.get(key)
    return "" if value is None else str(value)


def _metadata_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _metadata_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"Unsupported datetime metadata value: {value!r}")


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _datetime_timestamp(value: datetime | None) -> float | None:
    normalized = _normalize_datetime(value)
    if normalized is None:
        return None
    return normalized.timestamp()


def _content_key(turn: EpisodicTurn) -> str:
    return "\x1f".join((turn.session_id, turn.role, " ".join(turn.content.split())))


def _pre_rank_key(candidate: _Candidate) -> tuple[float, float, str]:
    return (
        candidate.vector_score,
        -(_datetime_timestamp(candidate.turn.created_at) or 0.0),
        candidate.turn.id,
    )


def _unique_sorted_tags(tags: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(tag) for tag in tags if str(tag)}))


def _default_candidate_k(limit: int) -> int:
    return max(limit * 5, 50)


def _validate_limit(value: int, name: str) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")


def _validate_time_range(start: datetime | None, end: datetime | None) -> None:
    normalized_start = _normalize_datetime(start)
    normalized_end = _normalize_datetime(end)
    if (
        normalized_start is not None
        and normalized_end is not None
        and normalized_start > normalized_end
    ):
        raise ValueError("start must be before or equal to end")


def _apply_room_context_metadata(
    retrieval_filter: RetrievalFilter,
    room_context: RoomMemoryContext,
) -> RetrievalFilter:
    """Add room-scoped metadata constraints to the retrieval filter."""
    extra_metadata = dict(retrieval_filter.metadata or {})

    if room_context.memory_scope == 'room' and room_context.room_id is not None:
        extra_metadata.setdefault('room_id', room_context.room_id)
    elif room_context.memory_scope == 'companion' and room_context.companion_id is not None:
        extra_metadata.setdefault('companion_id', room_context.companion_id)

    return RetrievalFilter(
        session_id=retrieval_filter.session_id,
        start=retrieval_filter.start,
        end=retrieval_filter.end,
        metadata=extra_metadata or None,
        tags=retrieval_filter.tags,
    )


def _neutralize_vector_scores(
    candidates: list[_Candidate],
    query: str,
) -> list[_Candidate]:
    """Zero out vector scores and add keyword match info for text-based retrieval.

    When *query* is non-empty, candidates with no keyword overlap are dropped.
    """
    result: list[_Candidate] = []
    for c in candidates:
        kw_score = _compute_keyword_score(query, c.turn.content)
        mm = dict(c.metadata_matches)
        if kw_score > 0:
            mm['_keyword_match'] = kw_score
        result.append(_Candidate(
            turn=c.turn,
            vector_score=0.0,
            metadata_matches=mm,
        ))

    if query.strip():
        result = [c for c in result if c.metadata_matches.get('_keyword_match', 0) > 0]

    return result


def _apply_room_memory_policy(
    candidates: list[_Candidate],
    room_context: RoomMemoryContext,
) -> list[_Candidate]:
    """Filter candidates by room memory policy: durability and scene isolation."""
    result: list[_Candidate] = []
    for candidate in candidates:
        meta = candidate.turn.metadata

        if not room_context.persist_to_durable and _is_durable_memory(meta):
            continue

        if room_context.room_type == 'roleplay_scene':
            if _is_durable_memory(meta) and not _is_cross_scene_persistent(meta):
                continue

        result.append(candidate)
    return result


def _is_durable_memory(metadata: Mapping[str, Any]) -> bool:
    """Return True if memory metadata indicates a durable (factual) memory."""
    if metadata.get('memory_importance') == 'durable':
        return True
    if metadata.get('durable') is True:
        return True
    if 'durable' in _metadata_tags(metadata):
        return True
    return False


def _is_cross_scene_persistent(metadata: Mapping[str, Any]) -> bool:
    """Return True if a durable memory is marked for cross-scene persistence."""
    if metadata.get('memory_importance') == 'both':
        return True
    if metadata.get('cross_scene') is True:
        return True
    if 'cross_scene' in _metadata_tags(metadata):
        return True
    return False


def _compute_keyword_score(query: str, content: str) -> float:
    """Compute keyword overlap score between query terms and content."""
    if not query or not content:
        return 0.0
    query_terms = set(query.lower().split())
    if not query_terms:
        return 0.0
    content_lower = content.lower()
    matches = sum(1 for term in query_terms if term in content_lower)
    return matches / len(query_terms)


def build_room_context(room: Room, scene: SceneState | None = None) -> RoomMemoryContext:
    """Construct a RoomMemoryContext from a Room and optional Scene.

    Extracts memory policy settings from the room and returns a context
    suitable for passing to :func:`retrieve` or :meth:`RetrievalEngine.retrieve`.
    """
    policy = room.memory_policy
    return RoomMemoryContext(
        room_id=room.room_id,
        room_type=room.room_type.value if hasattr(room.room_type, 'value') else str(room.room_type),
        memory_scope=policy.scope,
        persist_to_durable=policy.persist_to_durable,
        max_context_turns=policy.max_context_turns,
        include_embeddings=policy.include_embeddings,
        companion_id=room.companion_id,
    )


class OpenRouterEmbeddingProvider(EmbeddingProvider):
    """Concrete embedding provider backed by the OpenRouter embeddings API."""

    __slots__ = ("_api_key", "_base_url", "_model", "_dimension")

    _DEFAULT_DIMENSION = 1536

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://openrouter.ai/api/v1",
        model: str = "openai/text-embedding-3-small",
        dimension: int = _DEFAULT_DIMENSION,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "input": texts,
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self._base_url}/embeddings",
                    headers=headers,
                    json=payload,
                    timeout=30.0,
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("OpenRouter embedding request failed: %s", exc)
            raise

        body = response.json()
        data = body.get("data")
        if not isinstance(data, list):
            raise ValueError("OpenRouter embeddings response missing 'data' list")

        return [
            list(item["embedding"])
            for item in data
            if isinstance(item, Mapping) and "embedding" in item
        ]


class SessionAwareVectorStore(VectorStore):
    """VectorStore adapter that creates PgvectorStore per-call using a session factory."""

    __slots__ = ("_session_factory", "_table")

    def __init__(
        self,
        session_factory: Callable[[], Any],
        table: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        # Defer PgvectorStore import to avoid circular imports at module level
        self._table = table

    async def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        metadata: list[dict] | None,
    ) -> None:
        from chatbot.memory.pgvector_store import PgvectorStore

        async with self._session_factory() as session:
            store = PgvectorStore(session, self._table)
            await store.add(ids, embeddings, metadata)

    async def search(
        self,
        embedding: list[float],
        top_k: int,
        filter: dict | None,
    ) -> list[SearchResult]:
        from chatbot.memory.pgvector_store import PgvectorStore

        async with self._session_factory() as session:
            store = PgvectorStore(session, self._table)
            return await store.search(embedding, top_k, filter)

    async def delete(self, ids: list[str]) -> int:
        from chatbot.memory.pgvector_store import PgvectorStore

        async with self._session_factory() as session:
            store = PgvectorStore(session, self._table)
            return await store.delete(ids)

    async def count(self) -> int:
        from chatbot.memory.pgvector_store import PgvectorStore

        async with self._session_factory() as session:
            store = PgvectorStore(session, self._table)
            return await store.count()


def build_retrieval_engine(
    session_factory: Callable[[], Any],
    settings: Settings,
    *,
    table: Any | None = None,
) -> RetrievalEngine:
    """Build a RetrievalEngine wired to the application session factory and settings.

    Uses *session_factory* (typically :data:`chatbot.db.async_session_factory`) to
    create database sessions per operation and *settings* to configure the embedding
    provider.
    """
    embedding_provider = OpenRouterEmbeddingProvider(
        api_key=settings.openrouter_api_key,
        base_url=getattr(settings, "embedding_base_url", settings.openrouter_base_url),
        model=getattr(settings, "embedding_model", "openai/text-embedding-3-small"),
    )
    vector_store = SessionAwareVectorStore(session_factory, table)
    return RetrievalEngine(vector_store, embedding_provider)


__all__ = [
    "RetrievalEngine",
    "RetrievalFilter",
    "RetrievalResult",
    "RoomMemoryContext",
    "OpenRouterEmbeddingProvider",
    "SessionAwareVectorStore",
    "build_retrieval_engine",
    "build_room_context",
    "retrieve",
]
