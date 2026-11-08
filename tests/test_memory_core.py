from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.memory.context_compiler import ContextCompiler, PersonaStub
from chatbot.memory.episodic import EpisodicMemoryStore, EpisodicTurn
from chatbot.memory.retrieval import (
    RetrievalEngine,
    RetrievalFilter,
    _Candidate,
    _deduplicate,
    _l2_distance_relevance,
    _matches_filter,
    _rank_candidates,
)
from chatbot.memory.vector_store import SearchResult
from chatbot.memory.working_memory import (
    WorkingMemoryBuffer,
    WorkingMemoryTurn,
    WorkingMemoryWindow,
)


class FakeEmbeddings:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(text)), 1.0] for text in texts]

    @property
    def dimension(self) -> int:
        return 2


class FakeVectorStore:
    def __init__(self, search_results: list[SearchResult] | None = None) -> None:
        self.search_results = list(search_results or [])
        self.add_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []

    async def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        metadata: list[dict] | None,
    ) -> None:
        self.add_calls.append(
            {
                "ids": ids,
                "embeddings": embeddings,
                "metadata": metadata,
            }
        )

    async def search(
        self,
        embedding: list[float],
        top_k: int,
        filter: dict | None,
    ) -> list[SearchResult]:
        self.search_calls.append(
            {
                "embedding": embedding,
                "top_k": top_k,
                "filter": filter,
            }
        )
        return self.search_results[:top_k]

    async def delete(self, ids: list[str]) -> int:
        return len(ids)

    async def count(self) -> int:
        return len(self.search_results)


@pytest.fixture
def fake_embeddings() -> FakeEmbeddings:
    return FakeEmbeddings()


def episodic_result(
    *,
    id: str,
    session_id: str = "session-a",
    role: str = "user",
    content: str,
    score: float,
    created_at: datetime | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> SearchResult:
    result_metadata = dict(metadata or {})
    result_metadata.update(
        {
            "session_id": session_id,
            "role": role,
            "content": content,
            "created_at": created_at,
        }
    )
    return SearchResult(id=id, score=score, metadata=result_metadata)


def test_l2_distance_maps_monotonically_to_relevance():
    assert _l2_distance_relevance(0.0) == 1.0
    assert _l2_distance_relevance(0.25) > _l2_distance_relevance(1.0)
    assert _l2_distance_relevance(1.0) > _l2_distance_relevance(4.0)


def test_equal_context_factors_rank_lower_l2_distance_first_and_ties_by_id():
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candidates = [
        _Candidate(EpisodicTurn("z", "s", "user", "less relevant", created_at=timestamp), 1.0, {}),
        _Candidate(EpisodicTurn("b", "s", "user", "tie b", created_at=timestamp), 0.25, {}),
        _Candidate(EpisodicTurn("a", "s", "user", "tie a", created_at=timestamp), 0.25, {}),
    ]
    ranked = _rank_candidates(candidates, RetrievalFilter())
    assert [candidate.turn.id for candidate in ranked] == ["a", "b", "z"]


def test_working_memory_append_truncates_and_returns_recent_windows() -> None:
    buffer = WorkingMemoryBuffer(max_turns=3)

    for index in range(4):
        buffer.append(
            WorkingMemoryTurn(
                role="user",
                content=f"turn-{index}",
                metadata={"index": index},
            )
        )

    assert len(buffer) == 3
    assert [turn.content for turn in buffer] == ["turn-1", "turn-2", "turn-3"]
    assert [turn.content for turn in buffer.window(2).turns] == ["turn-2", "turn-3"]
    assert buffer.window(10).as_messages() == [
        {"role": "user", "content": "turn-1", "metadata": {"index": 1}},
        {"role": "user", "content": "turn-2", "metadata": {"index": 2}},
        {"role": "user", "content": "turn-3", "metadata": {"index": 3}},
    ]

    buffer.truncate(1)

    assert len(buffer) == 1
    assert [turn.content for turn in buffer.window().turns] == ["turn-3"]


def test_working_memory_edge_cases() -> None:
    with pytest.raises(ValueError, match="max_turns"):
        WorkingMemoryBuffer(max_turns=0)

    buffer = WorkingMemoryBuffer(
        max_turns=2,
        turns=[
            WorkingMemoryTurn(role="user", content="first"),
            WorkingMemoryTurn(role="assistant", content="second"),
        ],
    )

    assert buffer.window(0) == WorkingMemoryWindow(turns=(), max_turns=2)

    with pytest.raises(ValueError, match="size"):
        buffer.window(-1)
    with pytest.raises(ValueError, match="size"):
        buffer.truncate(-1)

    buffer.truncate(0)
    assert len(buffer) == 0

    buffer.append(WorkingMemoryTurn(role="user", content="after-clear"))
    buffer.clear()
    assert list(buffer) == []


def test_episodic_store_saves_turn_with_embedding_and_metadata(
    fake_embeddings: FakeEmbeddings,
) -> None:
    async def run() -> None:
        vector_store = FakeVectorStore()
        store = EpisodicMemoryStore(vector_store, fake_embeddings)
        created_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

        turn = await store.save_turn(
            session_id="session-a",
            role="user",
            content="remember this",
            turn_id="turn-1",
            token_count=3,
            created_at=created_at,
            metadata={"topic": "tests"},
        )

        assert turn == EpisodicTurn(
            id="turn-1",
            session_id="session-a",
            role="user",
            content="remember this",
            token_count=3,
            created_at=created_at,
            metadata={"topic": "tests"},
        )
        assert fake_embeddings.calls == [["user: remember this"]]
        assert vector_store.add_calls == [
            {
                "ids": ["turn-1"],
                "embeddings": [[19.0, 1.0]],
                "metadata": [
                    {
                        "topic": "tests",
                        "session_id": "session-a",
                        "role": "user",
                        "content": "remember this",
                        "created_at": created_at,
                        "token_count": 3,
                    }
                ],
            }
        ]

    asyncio.run(run())


def test_episodic_store_queries_by_session(
    fake_embeddings: FakeEmbeddings,
) -> None:
    async def run() -> None:
        created_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        vector_store = FakeVectorStore(
            [
                episodic_result(
                    id="turn-1",
                    content="session match",
                    score=0.25,
                    created_at=created_at.isoformat(),
                    metadata={"token_count": "4", "topic": "tests"},
                )
            ]
        )
        store = EpisodicMemoryStore(vector_store, fake_embeddings)

        results = await store.query_by_session("session-a", query="needle", top_k=3)

        assert fake_embeddings.calls == [["needle"]]
        assert vector_store.search_calls == [
            {
                "embedding": [6.0, 1.0],
                "top_k": 3,
                "filter": {"session_id": "session-a"},
            }
        ]
        assert len(results) == 1
        assert results[0].score == 0.25
        assert results[0].turn == EpisodicTurn(
            id="turn-1",
            session_id="session-a",
            role="user",
            content="session match",
            token_count=4,
            created_at=created_at,
            metadata={"topic": "tests"},
        )

    asyncio.run(run())


def test_episodic_store_queries_by_time_range(
    fake_embeddings: FakeEmbeddings,
) -> None:
    async def run() -> None:
        start = datetime(2026, 1, 2, tzinfo=timezone.utc)
        end = datetime(2026, 1, 4, tzinfo=timezone.utc)
        vector_store = FakeVectorStore(
            [
                episodic_result(
                    id="old",
                    content="too old",
                    score=0.1,
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                ),
                episodic_result(
                    id="inside-1",
                    content="inside one",
                    score=0.2,
                    created_at=start,
                ),
                episodic_result(
                    id="inside-2",
                    content="inside two",
                    score=0.3,
                    created_at=end,
                ),
                episodic_result(
                    id="new",
                    content="too new",
                    score=0.4,
                    created_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
                ),
            ]
        )
        store = EpisodicMemoryStore(vector_store, fake_embeddings)

        results = await store.query_by_time_range(
            start=start,
            end=end,
            query=None,
            top_k=2,
            session_id="session-a",
            candidate_k=4,
        )

        assert fake_embeddings.calls == [[""]]
        assert vector_store.search_calls[0]["top_k"] == 4
        assert vector_store.search_calls[0]["filter"] == {"session_id": "session-a"}
        assert [result.turn.id for result in results] == ["inside-1", "inside-2"]

        with pytest.raises(ValueError, match="start"):
            await store.query_by_time_range(start=end, end=start)

    asyncio.run(run())


def test_retrieval_engine_filters_ranks_deterministically_and_deduplicates(
    fake_embeddings: FakeEmbeddings,
) -> None:
    async def run() -> None:
        older = datetime(2026, 1, 1, tzinfo=timezone.utc)
        middle = datetime(2026, 1, 2, tzinfo=timezone.utc)
        newer = datetime(2026, 1, 3, tzinfo=timezone.utc)
        matching_metadata = {"topic": "billing", "tags": ["urgent", "vip"]}
        items = [
            episodic_result(
                id="dup-worse",
                content="same content",
                score=0.5,
                created_at=newer,
                metadata=matching_metadata,
            ),
            episodic_result(
                id="dup-best",
                content="same content",
                score=0.1,
                created_at=older,
                metadata=matching_metadata,
            ),
            episodic_result(
                id="same-b",
                content="same score b",
                score=0.2,
                created_at=middle,
                metadata=matching_metadata,
            ),
            episodic_result(
                id="same-a",
                content="same score a",
                score=0.2,
                created_at=middle,
                metadata=matching_metadata,
            ),
            episodic_result(
                id="fresh",
                content="newer result",
                score=0.1,
                created_at=newer,
                metadata=matching_metadata,
            ),
            episodic_result(
                id="wrong-topic",
                content="metadata mismatch",
                score=0.01,
                created_at=newer,
                metadata={"topic": "support", "tags": ["urgent", "vip"]},
            ),
            episodic_result(
                id="wrong-session",
                session_id="session-b",
                content="session mismatch",
                score=0.01,
                created_at=newer,
                metadata=matching_metadata,
            ),
        ]
        retrieval_filter = RetrievalFilter(
            session_id="session-a",
            start=older,
            end=newer,
            metadata={"topic": "billing"},
            tags=("vip", "urgent"),
        )
        raw_candidates = [_Candidate.from_search_result(r, retrieval_filter) for r in items]

        # A. Ownership and relational filtering:
        # Excludes wrong-session (session-b) and wrong-topic (support)
        filtered = [c for c in raw_candidates if _matches_filter(c, retrieval_filter)]
        assert [c.turn.id for c in filtered] == [
            "dup-worse",
            "dup-best",
            "same-b",
            "same-a",
            "fresh",
        ]

        # B. Deduplication:
        # Keeps best-scoring duplicate ("dup-best" with score 0.1 over "dup-worse" with score 0.5)
        deduped = _deduplicate(filtered)
        assert {c.turn.id for c in deduped} == {"fresh", "same-a", "same-b", "dup-best"}
        assert "dup-worse" not in {c.turn.id for c in deduped}

        # C. Candidate ranking:
        # Deterministic ranking (recency + monotonic L2 relevance + tie-breaking by id)
        ranked = _rank_candidates(deduped, retrieval_filter)
        assert [c.turn.id for c in ranked] == [
            "fresh",
            "same-a",
            "same-b",
            "dup-best",
        ]

        # D. Final confidence-filtered results:
        # Full retrieval pipeline applies production confidence threshold (0.50).
        # Confidence interprets vector scores as L2 distance, so all four
        # strong matches pass without changing their relevance-rank order.
        vector_store = FakeVectorStore(items)
        engine = RetrievalEngine(vector_store, fake_embeddings, default_candidate_k=7)

        results = await engine.retrieve(
            query="billing",
            limit=4,
            session_id="session-a",
            start=older,
            end=newer,
            metadata={"topic": "billing"},
            tags=["vip", "urgent"],
        )

        assert fake_embeddings.calls == [["billing"]]
        assert vector_store.search_calls == [
            {
                "embedding": [7.0, 1.0],
                "top_k": 7,
                "filter": {"owner_session_id": "session-a"},
            }
        ]
        assert [result.turn.id for result in results] == ["fresh", "same-a", "same-b", "dup-best"]
        assert results[0].rank == 1
        assert results[0].metadata_matches["topic"] == "billing"
        assert results[0].metadata_matches["tags"] == ("urgent", "vip")

    asyncio.run(run())


def test_context_compiler_enforces_budget_and_combines_sources() -> None:
    working_memory = WorkingMemoryWindow(
        turns=(
            WorkingMemoryTurn(role="user", content="old working"),
            WorkingMemoryTurn(role="assistant", content="recent"),
        ),
        max_turns=5,
    )
    retrieval_results = [
        retrieval_result(
            id="duplicate-working",
            content="recent",
            role="assistant",
            score=0.99,
            created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        ),
        retrieval_result(
            id="memory-later",
            content="memory",
            score=0.8,
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ),
        retrieval_result(
            id="memory-earlier",
            content="abc",
            score=0.7,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    ]
    compiler = ContextCompiler(budget=30)

    context = compiler.compile(
        persona_stubs=[
            PersonaStub(key="low", content="low persona", priority=0),
            PersonaStub(key="core", content="core", priority=10),
        ],
        working_memory=working_memory,
        retrieval_results=retrieval_results,
    )

    assert context.budget_used == 30
    assert context.budget_total == 30
    assert [stub.key for stub in context.persona_stubs] == ["core", "low"]
    assert [turn.content for turn in context.working_turns] == ["recent"]
    assert [turn.id for turn in context.episodic_turns] == ["memory-earlier", "memory-later"]
    assert context.source.persona_count == 2
    assert context.source.persona_chars == 15
    assert context.source.working_count == 1
    assert context.source.working_chars == 6
    assert context.source.episodic_count == 2
    assert context.source.episodic_chars == 9
    assert context.as_messages() == [
        {"role": "system", "content": "core"},
        {"role": "system", "content": "low persona"},
        {"role": "user", "content": "abc"},
        {"role": "user", "content": "memory"},
        {"role": "assistant", "content": "recent"},
    ]


def test_context_compiler_edge_cases() -> None:
    with pytest.raises(ValueError, match="budget"):
        ContextCompiler(budget=0)
    with pytest.raises(ValueError, match="key"):
        PersonaStub(key="", content="invalid")


def retrieval_result(
    *,
    id: str,
    content: str,
    role: str = "user",
    score: float,
    created_at: datetime,
) -> Any:
    return type(
        "FakeRetrievalResult",
        (),
        {
            "turn": EpisodicTurn(
                id=id,
                session_id="session-a",
                role=role,
                content=content,
                created_at=created_at,
            ),
            "score": score,
        },
    )()
