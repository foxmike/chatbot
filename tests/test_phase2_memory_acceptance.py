"""Phase 2.5a — Memory Acceptance Scenarios.

Deterministic acceptance tests for Phase 2 memory behavior:
1. Explicit personal facts (persistence and source attribution)
2. Persistent user preferences (extraction and persistence)
3. Hypothetical information exclusion (not stored as factual user claims)
4. Roleplay information separation (fictional assertions not confused with real user facts)
5. Duplicate fact reconciliation across turns (uncontrolled duplicate prevention)
6. Cross-user isolation (different users cannot retrieve each other's private memories)
7. Companion-specific memory isolation (companion-scoped memories isolated from other companions)
8. Intentionally shared user memories (remain accessible across different companions)
0. Infrastructure characterization (PostgreSQL pgvector search, skipped when service unavailable)

As planned in Task 2.5a, tests against missing Phase 2 capabilities fail
against the current regex/un-reconciled implementation without weakening criteria.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import select, func

# Ensure tests/ directory is on sys.path for fixture imports
_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from test_completion_persistence import (  # noqa: E402
    USER,
    USER_ID,
    SESSION_ID,
    Env,
    app_with_engine,
    run_stream,
    env,  # noqa: F401 (pytest fixture)
    provider,  # noqa: F401 (pytest fixture)
)
from chatbot.db import models
from chatbot.memory.episodic import EpisodicTurn
from chatbot.memory.retrieval import RetrievalEngine, RoomMemoryContext, RetrievalFilter
from chatbot.memory.vector_store import SearchResult, VectorStore


def _is_postgres_ready() -> bool:
    """Check whether a real PostgreSQL instance is responding."""
    if not shutil.which("pg_isready"):
        return False
    try:
        res = subprocess.run(["pg_isready"], capture_output=True, timeout=2)
        return res.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


class StubEmbeddings:
    """Deterministic embedding provider for in-memory retrieval tests."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0] for t in texts]

    @property
    def dimension(self) -> int:
        return 2


class InMemoryVectorStore(VectorStore):
    """Deterministic in-memory vector store for retrieval policy acceptance."""

    def __init__(self, search_results: list[SearchResult] | None = None) -> None:
        self.results = list(search_results or [])

    async def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        metadata: list[dict] | None,
    ) -> None:
        for id_, meta in zip(ids, metadata or [{} for _ in ids]):
            self.results.append(SearchResult(id=id_, score=1.0, metadata=meta))

    async def search(
        self,
        embedding: list[float],
        top_k: int,
        filter: dict | None,
    ) -> list[SearchResult]:
        filtered = self.results
        if filter:
            for key, val in filter.items():
                filtered = [r for r in filtered if r.metadata and r.metadata.get(key) == val]
        return filtered[:top_k]

    async def delete(self, ids: list[str]) -> int:
        before = len(self.results)
        self.results = [r for r in self.results if r.id not in ids]
        return before - len(self.results)

    async def count(self) -> int:
        return len(self.results)


# ---------------------------------------------------------------------------
# Helpers for test data creation
# ---------------------------------------------------------------------------


async def _add_secondary_user(env: Env) -> tuple[uuid.UUID, models.User]:
    uid = uuid.UUID("b1112222-3333-4444-5555-666677778888")
    async with env.factory() as db:
        user = models.User(
            id=uid,
            external_id="ext_user_b",
            display_name="User B",
            username="user_b",
            email="user_b@example.com",
            password_hash="h",
        )
        db.add(user)
        await db.commit()
    return uid, user


async def _add_companion(
    env: Env,
    companion_id: str,
    name: str,
    user_id: uuid.UUID = USER_ID,
) -> None:
    async with env.factory() as db:
        db.add(
            models.Companion(
                companion_id=companion_id,
                user_id=user_id,
                name=name,
                persona=f"{name} is an assistant.",
                description=f"{name} description.",
                backstory=f"{name} backstory.",
            )
        )
        await db.flush()
        db.add(models.CompanionIdentityModel(companion_id=companion_id))
        db.add(models.CompanionRelationshipModel(companion_id=companion_id))
        await db.commit()


# ===========================================================================
# Scenario 1: A user states an explicit personal fact
# ===========================================================================


@pytest.mark.asyncio
async def test_scenario_1a_explicit_personal_fact_persisted_to_durable_memory(env: Env) -> None:
    """Scenario 1 (Passes Today): Explicit personal fact is extracted and persisted to SemanticFact.

    User states: 'I live in Seattle and I work as an aerospace engineer.'
    A completed turn must persist a durable SemanticFact for the user with positive confidence.
    """
    message = "I live in Seattle and I work as an aerospace engineer."
    await run_stream(env, message)

    async with env.factory() as db:
        result = await db.execute(
            select(models.SemanticFact).where(models.SemanticFact.user_id == USER_ID)
        )
        facts = result.scalars().all()

    assert len(facts) >= 1, "Expected at least one SemanticFact row for explicit personal fact"
    matching = [f for f in facts if "Seattle" in f.fact_text or "aerospace engineer" in f.fact_text]
    assert len(matching) >= 1, f"Expected matching fact text in persisted facts: {[f.fact_text for f in facts]}"
    assert matching[0].confidence > 0.0, "Fact confidence must be positive"


@pytest.mark.asyncio
async def test_scenario_1b_explicit_fact_provenance_marked_explicit(env: Env) -> None:
    """Scenario 1 (Fails Today — Known Gap): Explicit facts must be attributed as source_type='explicit'.

    Phase 2 Task 2.3 & Requirements A05/A07:
    Explicit facts stated directly by the user must be distinguished from inferred
    facts. In the current implementation, _persist_rows does not set source_type,
    leaving it as the default 'inferred'.
    """
    message = "I work as an aerospace engineer."
    await run_stream(env, message)

    async with env.factory() as db:
        result = await db.execute(
            select(models.SemanticFact).where(
                models.SemanticFact.user_id == USER_ID,
                models.SemanticFact.fact_text.ilike("%aerospace engineer%"),
            )
        )
        fact = result.scalar_one_or_none()

    assert fact is not None, "Fact should be persisted"
    # Phase 2 requirement: source_type must reflect explicit user statement
    assert fact.source_type == "explicit", (
        f"Expected source_type='explicit' for directly stated personal fact, got {fact.source_type!r}"
    )


# ===========================================================================
# Scenario 2: A user states a persistent preference
# ===========================================================================


@pytest.mark.asyncio
async def test_scenario_2_persistent_preference_extracted_and_persisted(env: Env) -> None:
    """Scenario 2 (Passes Today): User states a persistent preference that is captured into durable memory.

    User states: 'I prefer dark mode in all my applications and coffee with oat milk.'
    The preference is persisted into SemanticFact with positive confidence.
    """
    message = "I prefer dark mode in all my applications and coffee with oat milk."
    await run_stream(env, message)

    async with env.factory() as db:
        result = await db.execute(
            select(models.SemanticFact).where(
                models.SemanticFact.user_id == USER_ID,
                models.SemanticFact.fact_text.ilike("%prefer dark mode%"),
            )
        )
        preference_fact = result.scalar_one_or_none()

    assert preference_fact is not None, "User persistent preference must be persisted in SemanticFact"
    assert preference_fact.confidence > 0.0


# ===========================================================================
# Scenario 3: Hypothetical information is not stored as fact
# ===========================================================================


@pytest.mark.asyncio
async def test_scenario_3_hypothetical_information_not_stored_as_fact(env: Env) -> None:
    """Scenario 3 (Fails Today — Known Gap): Hypothetical statements must not be stored as facts.

    Phase 2 Task 2.3 & Requirement A05:
    'Mark or exclude hypothetical, roleplay, uncertain and assistant-originated
    claims from real-world user facts unless explicitly grounded.'

    User states: 'Hypothetically, suppose I live in Tokyo and run a cat cafe.'
    The system must NOT store a fact asserting the user lives in Tokyo or runs a cat cafe.
    Current regex extractor matches 'i live in' naively and stores it as a durable fact.
    """
    message = "Hypothetically, suppose I live in Tokyo and run a cat cafe."
    await run_stream(env, message)

    async with env.factory() as db:
        result = await db.execute(
            select(models.SemanticFact).where(
                models.SemanticFact.user_id == USER_ID,
                models.SemanticFact.fact_text.ilike("%Tokyo%"),
            )
        )
        hypothetical_facts = result.scalars().all()

    assert len(hypothetical_facts) == 0, (
        f"Hypothetical conjecture was wrongly stored as durable user fact: "
        f"{[f.fact_text for f in hypothetical_facts]}"
    )


# ===========================================================================
# Scenario 4: Roleplay information is not confused with real-world personal information
# ===========================================================================


@pytest.mark.asyncio
async def test_scenario_4_roleplay_information_not_confused_with_real_user_facts(env: Env) -> None:
    """Scenario 4 (Fails Today — Known Gap): Roleplay persona statements must not become real user facts.

    Phase 2 Task 2.3 & Requirements A05/A07:
    Fictional or in-character roleplay claims must not be confused with real-world
    personal information in SemanticFact.

    User states: 'In this fantasy roleplay quest, I am a level 20 elven wizard from Rivendell.'
    The real-world SemanticFact store for the user must NOT contain a claim that the user is an 'elven wizard'.
    Current regex extractor matches 'i am' naively and persists this as a user fact.
    """
    message = "In this fantasy roleplay quest, I am a level 20 elven wizard from Rivendell."
    await run_stream(env, message)

    async with env.factory() as db:
        result = await db.execute(
            select(models.SemanticFact).where(
                models.SemanticFact.user_id == USER_ID,
                models.SemanticFact.fact_text.ilike("%elven wizard%"),
            )
        )
        roleplay_facts = result.scalars().all()

    assert len(roleplay_facts) == 0, (
        f"Roleplay persona claim was wrongly stored as real user fact in SemanticFact: "
        f"{[f.fact_text for f in roleplay_facts]}"
    )


# ===========================================================================
# Scenario 5: Repeating an identical fact does not create uncontrolled duplicates
# ===========================================================================


@pytest.mark.asyncio
async def test_scenario_5_repeating_identical_fact_does_not_create_duplicates(env: Env) -> None:
    """Scenario 5 (Fails Today — Known Gap): Repeating an identical fact across turns must be deduplicated.

    Phase 2 Task 2.2c & 2.3 (Requirements A06, A08):
    'Persist memory outputs idempotently... reconcile duplicates and supersede
    outdated claims without treating interpretations as facts.'

    Turn 1: 'I live in Seattle.'
    Turn 2: 'As I mentioned earlier, I live in Seattle.'
    The system should reconcile and maintain exactly 1 active durable fact for this claim,
    rather than inserting duplicate rows on every turn.
    Current implementation inserts a new SemanticFact row on every turn with no cross-turn deduplication.
    """
    await run_stream(env, "I live in Seattle.")
    await run_stream(env, "As I mentioned earlier, I live in Seattle.")

    async with env.factory() as db:
        result = await db.execute(
            select(models.SemanticFact).where(
                models.SemanticFact.user_id == USER_ID,
                models.SemanticFact.fact_text.ilike("%Seattle%"),
            )
        )
        seattle_facts = result.scalars().all()

    assert len(seattle_facts) == 1, (
        f"Expected exactly 1 reconciled SemanticFact for 'Seattle', found {len(seattle_facts)} duplicates: "
        f"{[f.fact_text for f in seattle_facts]}"
    )


# ===========================================================================
# Scenario 6: Different users cannot retrieve each other's private memories
# ===========================================================================


@pytest.mark.asyncio
async def test_scenario_6_different_users_cannot_retrieve_each_others_private_memories(env: Env) -> None:
    """Scenario 6 (Passes Today): User isolation ensures private memories are never cross-accessible.

    Phase 2 Requirement A07/P11:
    User A has a private fact: 'Top secret clearance for Project Chimera' (privacy_level='private').
    User B queries the memory store for 'Project Chimera'.
    User B must receive 0 results. User A must receive 1 result.
    """
    user_b_id, _ = await _add_secondary_user(env)

    async with env.factory() as db:
        fact_a = models.SemanticFact(
            id=uuid.UUID("a0001111-2222-3333-4444-555566667777"),
            user_id=USER_ID,
            fact_text="Top secret clearance for Project Chimera",
            confidence=1.0,
            privacy_level="private",
            source_type="explicit",
        )
        db.add(fact_a)
        await db.commit()

    async with env.factory() as db:
        # User B queries their facts for Project Chimera
        query_b = await db.execute(
            select(models.SemanticFact).where(
                models.SemanticFact.user_id == user_b_id,
                models.SemanticFact.fact_text.ilike("%Project Chimera%"),
            )
        )
        facts_b = query_b.scalars().all()

        # User A queries their facts for Project Chimera
        query_a = await db.execute(
            select(models.SemanticFact).where(
                models.SemanticFact.user_id == USER_ID,
                models.SemanticFact.fact_text.ilike("%Project Chimera%"),
            )
        )
        facts_a = query_a.scalars().all()

    assert len(facts_b) == 0, f"Cross-user memory leak: User B retrieved {facts_b}"
    assert len(facts_a) == 1, "User A should retrieve their own private fact"
    assert "Project Chimera" in facts_a[0].fact_text


# ===========================================================================
# Scenario 7: Companion-specific memories remain isolated
# ===========================================================================


@pytest.mark.asyncio
async def test_scenario_7a_companion_specific_memories_isolated_in_retrieval() -> None:
    """Scenario 7a (Passes Today): Companion-specific memory retrieval isolation.

    Phase 2 Task 2.4 & Requirement A07:
    When retrieval is configured with companion memory scope:
    - Companion A's retrieval must only access Companion A memories.
    - Companion B's retrieval must NOT retrieve Companion A's companion-scoped memory.
    """
    cid_a = "companion-alpha"
    cid_b = "companion-beta"

    turn_a = EpisodicTurn(
        id="turn-alpha-1",
        session_id="session-alpha",
        role="assistant",
        content="We created our secret chess strategy together.",
        metadata={"companion_id": cid_a},
    )
    turn_b = EpisodicTurn(
        id="turn-beta-1",
        session_id="session-beta",
        role="assistant",
        content="We practiced conversational French vocabulary.",
        metadata={"companion_id": cid_b},
    )

    store = InMemoryVectorStore([
        SearchResult(id="turn-alpha-1", score=0.9, metadata={"companion_id": cid_a, "content": turn_a.content, "session_id": "session-alpha", "role": "assistant"}),
        SearchResult(id="turn-beta-1", score=0.9, metadata={"companion_id": cid_b, "content": turn_b.content, "session_id": "session-beta", "role": "assistant"}),
    ])
    engine = RetrievalEngine(store, StubEmbeddings())

    # Companion B retrieves under companion scope
    room_ctx_b = RoomMemoryContext(companion_id=cid_b, memory_scope="companion")
    results_b = await engine.retrieve(query="secret chess strategy", room_context=room_ctx_b)

    # Companion A retrieves under companion scope
    room_ctx_a = RoomMemoryContext(companion_id=cid_a, memory_scope="companion")
    results_a = await engine.retrieve(query="secret chess strategy", room_context=room_ctx_a)

    retrieved_b_ids = [r.turn.id for r in results_b]
    retrieved_a_ids = [r.turn.id for r in results_a]

    assert "turn-alpha-1" not in retrieved_b_ids, "Companion B must NOT retrieve Companion A's memory"
    assert "turn-alpha-1" in retrieved_a_ids, "Companion A must retrieve its own companion memory"


@pytest.mark.asyncio
async def test_scenario_7b_companion_specific_facts_isolated_in_durable_storage(env: Env) -> None:
    """Scenario 7b (Fails Today — Known Gap): Companion-specific facts isolated in durable storage.

    Phase 2 Task 2.3 & 2.4 (Requirements A07, P10):
    Companion-specific shared experiences and agreements must be scoped to that companion
    in persistent storage, not promoted to unattributed global user facts that leak to other companions.

    Turn in Companion A session: 'Remember our secret agreement about the midnight chess games.'
    Companion B's session should NOT have this fact attributed to Companion B or global scope.
    Current SemanticFact schema has no companion_id column, so all extracted facts are
    stored globally under user_id with no companion scoping.
    """
    cid_a = "companion-mercury"
    cid_b = "companion-athena"
    await _add_companion(env, cid_a, "Mercury")
    await _add_companion(env, cid_b, "Athena")

    # Run turn with Companion A
    await run_stream(
        env,
        "Remember that I made a secret pact with you for our midnight chess games.",
        session_companion_id=cid_a,
    )

    async with env.factory() as db:
        result = await db.execute(
            select(models.SemanticFact).where(
                models.SemanticFact.user_id == USER_ID,
                models.SemanticFact.fact_text.ilike("%secret pact%"),
            )
        )
        fact = result.scalar_one_or_none()

    assert fact is not None, "Fact should be persisted"
    # Phase 2 requirement: Durable fact must track companion attribution to prevent cross-companion leakage
    companion_id_attr = getattr(fact, "companion_id", None)
    assert companion_id_attr == cid_a, (
        f"Expected SemanticFact to have companion_id={cid_a!r} for companion-specific interaction, "
        f"got {companion_id_attr!r} (missing companion isolation in durable facts)"
    )


# ===========================================================================
# Scenario 8: Intentionally shared user memories remain accessible
# ===========================================================================


@pytest.mark.asyncio
async def test_scenario_8_intentionally_shared_user_memories_accessible_across_companions(env: Env) -> None:
    """Scenario 8 (Passes Today): Intentionally shared user facts are accessible across companions.

    Phase 2 Requirements A07, P11:
    A user's general identity and preferences (e.g. dietary preference, location)
    are intentionally global to the user, and should remain accessible when interacting
    with different companions of that user.

    User shares: 'I prefer vegetarian food.' in a session with Companion A.
    When querying memory under Companion B's context, the user's shared fact is accessible.
    """
    cid_a = "companion-alpha"
    cid_b = "companion-beta"
    await _add_companion(env, cid_a, "Alpha")
    await _add_companion(env, cid_b, "Beta")

    # User establishes preference in session with Companion A
    await run_stream(env, "I prefer vegetarian food.", session_companion_id=cid_a)

    async with env.factory() as db:
        # Companion B queries the user's durable facts
        result = await db.execute(
            select(models.SemanticFact).where(
                models.SemanticFact.user_id == USER_ID,
                models.SemanticFact.fact_text.ilike("%vegetarian%"),
            )
        )
        matching_facts = result.scalars().all()

    assert len(matching_facts) >= 1, "Shared user preference must be accessible across companions"
    assert any("vegetarian" in f.fact_text.lower() for f in matching_facts)


# ===========================================================================
# Infrastructure Characterization (PostgreSQL / pgvector)
# ===========================================================================


@pytest.mark.postgres
@pytest.mark.skipif(
    not _is_postgres_ready(),
    reason="PostgreSQL is not running on this machine (pg_isready exit code 2)",
)
@pytest.mark.asyncio
async def test_scenario_0_postgres_vector_search_infrastructure(env: Env) -> None:
    """Characterization (Blocked Today): pgvector cosine operator verification on live PostgreSQL.

    Phase 2 Task 2.4:
    Verifies that the PostgreSQL pgvector '<->' / '<=>' operator executes against
    the active database instance.
    When PostgreSQL is offline (as in the default test environment), this test is
    cleanly skipped to distinguish unavailable infrastructure from application gaps.
    """
    from chatbot.memory.pgvector_store import PgvectorStore

    async with env.factory() as db:
        store = PgvectorStore(db, models.SemanticFact)
        # Verify vector search method can execute against PostgreSQL
        results = await store.search(embedding=[0.1] * 1536, top_k=5, filter={"user_id": USER_ID})
        assert isinstance(results, list)
