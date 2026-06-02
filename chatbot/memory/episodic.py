from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid
from typing import Any

from chatbot.memory.embeddings import EmbeddingProvider
from chatbot.memory.vector_store import SearchResult, VectorStore


@dataclass(frozen=True, slots=True)
class EpisodicTurn:
    id: str
    session_id: str
    role: str
    content: str
    token_count: int | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": self.role,
            "content": self.content,
        }
        if self.metadata:
            message["metadata"] = dict(self.metadata)
        return message

    def embedding_text(self) -> str:
        return f"{self.role}: {self.content}"


@dataclass(frozen=True, slots=True)
class EpisodicMemoryResult:
    turn: EpisodicTurn
    score: float


class EpisodicMemoryStore:
    def __init__(
        self,
        vector_store: VectorStore,
        embeddings: EmbeddingProvider,
    ) -> None:
        self.vector_store = vector_store
        self.embeddings = embeddings

    async def save_turn(
        self,
        *,
        session_id: str | uuid.UUID,
        role: str,
        content: str,
        turn_id: str | uuid.UUID | None = None,
        token_count: int | None = None,
        created_at: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> EpisodicTurn:
        return await save_turn(
            self.vector_store,
            self.embeddings,
            session_id=session_id,
            role=role,
            content=content,
            turn_id=turn_id,
            token_count=token_count,
            created_at=created_at,
            metadata=metadata,
        )

    async def query_by_session(
        self,
        session_id: str | uuid.UUID,
        *,
        query: str | None = None,
        top_k: int = 20,
    ) -> list[EpisodicMemoryResult]:
        return await query_by_session(
            self.vector_store,
            self.embeddings,
            session_id=session_id,
            query=query,
            top_k=top_k,
        )

    async def query_by_time_range(
        self,
        *,
        start: datetime,
        end: datetime,
        query: str | None = None,
        top_k: int = 20,
        session_id: str | uuid.UUID | None = None,
        candidate_k: int | None = None,
    ) -> list[EpisodicMemoryResult]:
        return await query_by_time_range(
            self.vector_store,
            self.embeddings,
            start=start,
            end=end,
            query=query,
            top_k=top_k,
            session_id=session_id,
            candidate_k=candidate_k,
        )


async def save_turn(
    vector_store: VectorStore,
    embeddings: EmbeddingProvider,
    *,
    session_id: str | uuid.UUID,
    role: str,
    content: str,
    turn_id: str | uuid.UUID | None = None,
    token_count: int | None = None,
    created_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> EpisodicTurn:
    if not role:
        raise ValueError("role must not be empty")
    if not content:
        raise ValueError("content must not be empty")
    if token_count is not None and token_count < 0:
        raise ValueError("token_count must be non-negative")

    turn = EpisodicTurn(
        id=str(turn_id or uuid.uuid4()),
        session_id=str(session_id),
        role=role,
        content=content,
        token_count=token_count,
        created_at=created_at or datetime.now(timezone.utc),
        metadata=dict(metadata or {}),
    )
    embedding = await _embed_one(embeddings, turn.embedding_text())

    await vector_store.add(
        ids=[turn.id],
        embeddings=[embedding],
        metadata=[_metadata_for_turn(turn)],
    )
    return turn


async def query_by_session(
    vector_store: VectorStore,
    embeddings: EmbeddingProvider,
    *,
    session_id: str | uuid.UUID,
    query: str | None = None,
    top_k: int = 20,
) -> list[EpisodicMemoryResult]:
    _validate_top_k(top_k)
    embedding = await _embed_one(embeddings, query or "")
    results = await vector_store.search(
        embedding=embedding,
        top_k=top_k,
        filter={"session_id": str(session_id)},
    )
    return [_result_from_search_result(result) for result in results]


async def query_by_time_range(
    vector_store: VectorStore,
    embeddings: EmbeddingProvider,
    *,
    start: datetime,
    end: datetime,
    query: str | None = None,
    top_k: int = 20,
    session_id: str | uuid.UUID | None = None,
    candidate_k: int | None = None,
) -> list[EpisodicMemoryResult]:
    _validate_top_k(top_k)
    if start > end:
        raise ValueError("start must be before or equal to end")
    if candidate_k is not None and candidate_k <= 0:
        raise ValueError("candidate_k must be greater than zero")

    embedding = await _embed_one(embeddings, query or "")
    filter_ = {"session_id": str(session_id)} if session_id is not None else None
    search_limit = candidate_k or top_k
    results = await vector_store.search(
        embedding=embedding,
        top_k=search_limit,
        filter=filter_,
    )

    matching_results = []
    for result in results:
        episodic_result = _result_from_search_result(result)
        created_at = episodic_result.turn.created_at
        if created_at is not None and start <= created_at <= end:
            matching_results.append(episodic_result)
        if len(matching_results) >= top_k:
            break

    return matching_results


def _metadata_for_turn(turn: EpisodicTurn) -> dict[str, Any]:
    metadata = dict(turn.metadata)
    metadata.update(
        {
            "session_id": turn.session_id,
            "role": turn.role,
            "content": turn.content,
            "created_at": turn.created_at,
        }
    )
    if turn.token_count is not None:
        metadata["token_count"] = turn.token_count
    return metadata


async def _embed_one(embeddings: EmbeddingProvider, text: str) -> list[float]:
    vectors = await embeddings.embed([text])
    if len(vectors) != 1:
        raise ValueError("embedding provider must return one vector for one input")
    return vectors[0]


def _result_from_search_result(result: SearchResult) -> EpisodicMemoryResult:
    metadata = result.metadata or {}
    turn = EpisodicTurn(
        id=result.id,
        session_id=_metadata_text(metadata, "session_id"),
        role=_metadata_text(metadata, "role"),
        content=_metadata_text(metadata, "content"),
        token_count=_metadata_int(metadata.get("token_count")),
        created_at=_metadata_datetime(metadata.get("created_at")),
        metadata={
            key: value
            for key, value in metadata.items()
            if key not in {"session_id", "role", "content", "token_count", "created_at"}
        },
    )
    return EpisodicMemoryResult(turn=turn, score=result.score)


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


def _validate_top_k(top_k: int) -> None:
    if top_k <= 0:
        raise ValueError("top_k must be greater than zero")


__all__ = [
    "EpisodicMemoryResult",
    "EpisodicMemoryStore",
    "EpisodicTurn",
    "query_by_session",
    "query_by_time_range",
    "save_turn",
]
