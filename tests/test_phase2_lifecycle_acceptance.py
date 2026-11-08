"""Focused Phase 2 lifecycle acceptance, with mocked inference and real sessions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI

from test_completion_persistence import env, provider, run_stream, USER, USER_ID, SESSION_ID
from test_memory_llm_extraction import memory, respond
from phase2_helpers import extraction_registry
from chatbot.config import Settings
from chatbot.db import models, get_db
from chatbot.auth.dependencies import get_current_user
from chatbot.consolidation.decay import MemoryDecayEngine
from chatbot.consolidation.worker import ConsolidationWorker
from chatbot.memory.pgvector_store import PgvectorStore
from chatbot.memory.controls import delete_memory, correct_misunderstanding, pin_memory
from chatbot.perf.durable_handlers import make_durable_memory_extract_handler
from chatbot.perf.durable_worker import DurableJobWorker
from chatbot.api.memory import memory_router


async def complete(
    env, statement, *, kind="personal_fact", content=None, temporal="current", **kwargs
):
    await run_stream(env, statement, **kwargs)
    registry, fake = extraction_registry(
        respond(memory(content or statement, statement, type=kind, temporal=temporal))
    )
    embeddings = SimpleNamespace(
        embed=AsyncMock(side_effect=lambda texts: [[0.0] * 1536 for _ in texts])
    )
    worker = DurableJobWorker(
        env.factory,
        handlers={
            "memory_extract": make_durable_memory_extract_handler(
                env.factory,
                registry=registry,
                settings=Settings(_env_file=None),
                embeddings=embeddings,
            )
        },
    )
    assert not fake.calls
    for job in await worker.claim_due_jobs():
        await worker._run_one(job)
    assert all(job.status == "succeeded" for job in await env.jobs())
    return worker, fake


async def facts(env):
    async with env.factory() as db:
        return list(
            (
                await db.scalars(
                    sa.select(models.SemanticFact).order_by(
                        models.SemanticFact.created_at, models.SemanticFact.id
                    )
                )
            ).all()
        )


async def recall(env, *, history=False):
    async with env.factory() as db:
        return await PgvectorStore(db).search(
            [0.0] * 1536, 50, {"owner_session_id": SESSION_ID, "include_history": history}
        )


async def test_duplicate_turns_keep_one_canonical_fact_and_all_original_evidence(env, provider):
    await complete(env, "I live in Seattle.", content="User lives in Seattle")
    worker, fake = await complete(
        env, "As I mentioned earlier, I live in Seattle.", content="User lives in Seattle"
    )
    rows = await facts(env)
    assert len(rows) == 1 and rows[0].reinforcement_count == 2
    async with env.factory() as db:
        evidence = list((await db.scalars(sa.select(models.MemoryEvidence))).all())
        assert len(evidence) == 2 and len({e.turn_id for e in evidence}) == 2
        assert len({e.source_message_id for e in evidence}) == 2
        assert {e.memory_id for e in evidence} == {rows[0].id}
        assert all(e.provenance["scope"]["level"] == "user" for e in evidence)
        assert "As I mentioned earlier" in next(
            e.evidence for e in evidence if "mentioned" in e.evidence
        )
        job = (await env.jobs())[-1]
        row = await db.get(models.BackgroundJob, job.job_id)
        row.status = "pending"
        await db.commit()
    for job in await worker.claim_due_jobs():
        await worker._run_one(job)
    assert (await facts(env))[0].reinforcement_count == 2
    assert await env.count(models.MemoryEvidence) == 2 and len(fake.calls) == 1
    assert len(await recall(env)) == 1


@pytest.mark.parametrize(
    "statement,kind,relation",
    [
        ("Actually, I live in Orlando.", "correction", "supersedes"),
        ("I moved to Orlando.", "experience", "changed_from"),
    ],
)
async def test_corrections_and_moves_supersede_without_erasing_history(
    env, provider, statement, kind, relation
):
    await complete(env, "I live in Tampa.", content="User lives in Tampa")
    await complete(env, statement, kind=kind)
    rows = await facts(env)
    assert len(rows) == 2
    old = next(f for f in rows if "Tampa" in f.fact_text)
    new = next(f for f in rows if "Orlando" in f.fact_text)
    assert old.lifecycle_status == "superseded" and old.superseded_by == new.id
    assert new.lifecycle_status == "active"
    assert {r.metadata["content"] for r in await recall(env)} == {new.fact_text}
    assert len(await recall(env, history=True)) == 2
    async with env.factory() as db:
        edge = await db.scalar(sa.select(models.MemoryRelation))
        assert edge.source_id == new.id and edge.target_id == old.id and edge.relation == relation
        assert await db.get(models.MemoryEvidence, edge.evidence_id)
        assert await db.get(models.Message, old.source_message_id)
    assert await env.count(models.MemoryEvidence) == 2


async def test_conflict_context_past_and_fiction_remain_distinct(env, provider):
    await complete(env, "I live in Tampa.")
    await complete(env, "I live in Orlando.")
    assert all(f.lifecycle_status == "active" for f in await facts(env))
    assert (
        await env.count(models.MemoryRelation, models.MemoryRelation.relation == "conflicts_with")
        == 1
    )
    assert len(await recall(env)) == 2
    await complete(env, "I live in Tampa during winter.")
    await complete(env, "I live in Orlando during summer.")
    assert await env.count(models.MemoryRelation) == 1
    await complete(env, "I used to live in Tampa.", temporal="past")
    assert any(f.lifecycle_status == "historical" for f in await facts(env))
    before = await env.count(models.MemoryEvidence)
    await complete(env, "In this fantasy roleplay, actually I live in Atlantis.", kind="correction")
    assert await env.count(models.MemoryEvidence) == before
    assert not any("Atlantis" in f.fact_text for f in await facts(env))


async def test_owner_companion_room_and_session_scopes_do_not_consolidate(env, provider):
    async with env.factory() as db:
        db.add_all(
            [
                models.Companion(companion_id="a", user_id=USER_ID, name="A"),
                models.Companion(companion_id="b", user_id=USER_ID, name="B"),
            ]
        )
        await db.commit()
    for companion in ("a", "b"):
        async with env.factory() as db:
            session = await db.get(models.Session, SESSION_ID)
            session.companion_id = companion
            await db.commit()
        await complete(env, "I prefer tea.", kind="preference", session_companion_id=companion)
    assert len(await facts(env)) == 2
    assert len(await recall(env)) == 1
    for room in ("room-a", "room-b"):
        async with env.factory() as db:
            db.add(
                models.Room(
                    room_id=room,
                    user_id=str(USER_ID),
                    companion_id="b",
                    display_name=room,
                    room_type="chat",
                    memory_scope="room",
                )
            )
            await db.flush()
            session = await db.get(models.Session, SESSION_ID)
            session.room_id = room
            await db.commit()
        await complete(
            env, "I prefer tea.", kind="preference", session_companion_id="b", session_room_id=room
        )
    assert len(await facts(env)) == 4
    visible = await recall(env)
    assert all(
        r.metadata["scope"]["level"] != "room" or r.metadata["scope"]["room_id"] == "room-b"
        for r in visible
    )
    other = uuid.uuid4()
    async with env.factory() as db:
        db.add(
            models.User(
                id=other,
                external_id="other",
                username="other",
                email="other@example.com",
                display_name="Other",
                password_hash="h",
            )
        )
        await db.flush()
        await db.execute(
            sa.insert(models.SemanticFact).values(
                user_id=other, fact_text="Secret", confidence=1, embedding=[0.0] * 1536
            )
        )
        await db.commit()
    assert not any(r.metadata["content"] == "Secret" for r in await recall(env))


async def test_concurrent_turns_serialize_canonicalization_and_preserve_significant_events(
    env, provider
):
    for _ in range(2):
        await run_stream(env, "I prefer tea.")
    registry, _ = extraction_registry(
        respond(memory("User prefers tea", "I prefer tea.", type="preference"))
    )
    worker = DurableJobWorker(
        env.factory,
        handlers={
            "memory_extract": make_durable_memory_extract_handler(
                env.factory, registry=registry, settings=Settings(_env_file=None)
            )
        },
    )
    await asyncio.gather(*(worker._run_one(job) for job in await worker.claim_due_jobs()))
    assert len(await facts(env)) == 1 and await env.count(models.MemoryEvidence) == 2
    # Separate significant experiences on different dates must not be compacted.
    for year in (2020, 2026):
        await run_stream(env, "I graduated from university.")
        jobs = [j for j in await env.jobs() if j.status == "pending"]
        async with env.factory() as db:
            await db.execute(
                sa.update(models.Message)
                .where(models.Message.turn_id == jobs[0].turn_id)
                .values(created_at=datetime(year, 1, 1, tzinfo=timezone.utc))
            )
            await db.commit()
        registry, _ = extraction_registry(
            respond(
                memory(
                    "User graduated from university",
                    "I graduated from university.",
                    type="experience",
                )
            )
        )
        worker = DurableJobWorker(
            env.factory,
            handlers={
                "memory_extract": make_durable_memory_extract_handler(
                    env.factory, registry=registry, settings=Settings(_env_file=None)
                )
            },
        )
        for job in await worker.claim_due_jobs():
            await worker._run_one(job)
    assert len(await facts(env)) == 3
    assert await env.count(models.Episode) == 4


async def test_safe_forgetting_protects_important_evidence_and_explicit_delete_never_deletes_messages(
    env, provider
):
    await complete(env, "I prefer tea.", kind="preference")
    fact = (await facts(env))[0]
    decay = MemoryDecayEngine(reference_time=datetime(2026, 11, 8, tzinfo=timezone.utc))
    low = {
        "created_at": datetime(2020, 1, 1, tzinfo=timezone.utc),
        "metadata": {"importance": 0.1},
        "reinforcement_count": 1,
    }
    assert 0.75 <= decay.retrieval_weight(low) < 1
    assert decay.retrieval_weight({**low, "pinned": True}) == 1
    assert decay.retrieval_weight({**low, "metadata": {}}) == 1  # do not invent importance
    assert decay.retrieval_weight({**low, "reinforcement_count": 100}) > decay.retrieval_weight(low)
    async with env.factory() as db:
        await pin_memory(db, fact.id)
        await db.commit()
        pinned = await db.get(models.SemanticFact, fact.id)
        assert pinned.pinned
        await delete_memory(db, fact.id)
        await db.commit()
    assert not await recall(env)
    assert await env.count(models.Message) == 2 and await env.count(models.MemoryEvidence) == 1
    # Legacy automatic decay must never invoke a deletion-capable adapter.
    adapter = SimpleNamespace(delete=AsyncMock())
    legacy = ConsolidationWorker(provider=None, memory_store=adapter)
    assert await legacy._persist_pruned_entries(str(SESSION_ID), [{"id": str(fact.id)}], []) == 0
    adapter.delete.assert_not_awaited()


async def test_explicit_memory_correction_api_is_owned_committed_and_preserves_lineage(
    env, provider
):
    await complete(env, "I live in Tampa.")
    original = (await facts(env))[0]
    app = FastAPI()
    app.include_router(memory_router)
    app.dependency_overrides[get_current_user] = lambda: USER

    async def database():
        async with env.factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.patch(
            f"/memories/{original.id}/correct", json={"correction": "I live in Orlando."}
        )
        assert response.status_code == 200, response.text
        changed = response.json()["id"]
        assert changed != str(original.id)
        provenance = await client.get(f"/memories/{changed}/provenance")
        assert provenance.status_code == 200 and provenance.json()["relations"]
        assert provenance.json()["evidence"][0]["provenance"]["origin"] == "user_memory_edit"
        assert (
            await client.get("/memories/search", params={"user_id": str(uuid.uuid4())})
        ).status_code == 403
        app.dependency_overrides[get_current_user] = lambda: USER.model_copy(
            update={"user_id": str(uuid.uuid4())}
        )
        assert (await client.delete(f"/memories/{changed}")).status_code == 404
    rows = await facts(env)
    assert next(f for f in rows if f.id == original.id).lifecycle_status == "superseded"
    assert await env.count(models.MemoryEvidence) == 2 and await env.count(models.Message) == 2


async def test_session_scope_cannot_escape_its_session(env, provider):
    from chatbot.consolidation.lifecycle import reconcile_fact

    other_sid = uuid.uuid4()
    async with env.factory() as db:
        db.add(models.Session(id=other_sid, user_id=USER_ID))
        await db.commit()
        for sid in (SESSION_ID, other_sid):
            mid = uuid.uuid4()
            db.add(
                models.Message(
                    id=mid, session_id=sid, user_id=USER_ID, role="user", content="I prefer tea."
                )
            )
            await db.flush()
            meta = {
                "scope": {"level": "session", "session_id": str(sid), "user_id": str(USER_ID)},
                "session_id": str(sid),
                "turn_id": str(uuid.uuid4()),
                "evidence": "I prefer tea.",
                "source_created_at": datetime.now(timezone.utc).isoformat(),
                "kind": "preference",
            }
            await reconcile_fact(
                db,
                dict(
                    id=uuid.uuid4(),
                    user_id=USER_ID,
                    fact_text="I prefer tea.",
                    confidence=0.9,
                    source_type="explicit",
                    source_message_id=mid,
                    extraction_metadata=meta,
                    embedding=[0.0] * 1536,
                ),
            )
        await db.commit()
    assert len(await facts(env)) == 2
    assert len(await recall(env)) == 1


async def test_reconciliation_failure_rolls_back_evidence_and_reinforcement(
    env, provider, monkeypatch
):
    await complete(env, "I live in Tampa.")
    before = (await facts(env))[0]
    from chatbot.perf import durable_handlers

    original = durable_handlers.TurnMemoryStorage._persist

    async def fail(self, entries):
        await original(self, entries)
        raise RuntimeError("lifecycle crash")

    monkeypatch.setattr(durable_handlers.TurnMemoryStorage, "_persist", fail)
    await run_stream(env, "I moved to Orlando.")
    registry, fake = extraction_registry(
        respond(memory("User moved to Orlando", "I moved to Orlando.", type="experience"))
    )
    worker = DurableJobWorker(
        env.factory,
        handlers={
            "memory_extract": make_durable_memory_extract_handler(
                env.factory, registry=registry, settings=Settings(_env_file=None)
            )
        },
    )
    for job in await worker.claim_due_jobs():
        await worker._run_one(job)
    assert len(await facts(env)) == 1 and (await facts(env))[0].lifecycle_status == "active"
    assert (
        await env.count(models.MemoryEvidence) == 1 and await env.count(models.MemoryRelation) == 0
    )
    async with env.factory() as db:
        await db.execute(
            sa.update(models.BackgroundJob)
            .where(models.BackgroundJob.status == "pending")
            .values(available_at=datetime.now(timezone.utc))
        )
        await db.commit()
    monkeypatch.setattr(durable_handlers.TurnMemoryStorage, "_persist", original)
    for job in await worker.claim_due_jobs():
        await worker._run_one(job)
    assert len(await facts(env)) == 2 and len(fake.calls) == 1
    assert next(f for f in await facts(env) if f.id == before.id).lifecycle_status == "superseded"


from test_timeline_migration import fresh_database, needs_postgres, alembic


@needs_postgres
async def test_lifecycle_migration_is_additive_without_historical_backfill(fresh_database):
    db = await fresh_database(upgrade_to="c93a14e2786b")
    from test_durable_memory_extract_handler import seed

    uid, _, _ = await seed(db)
    memory_id = uuid.uuid4()
    async with db.factory() as session:
        await session.execute(
            sa.text(
                "INSERT INTO semantic_facts (id, user_id, fact_text, confidence) VALUES (:id, :uid, 'Legacy history', 0.8)"
            ),
            {"id": memory_id, "uid": uid},
        )
        await session.commit()
    result = alembic(db.url, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    async with db.factory() as session:
        fact = await session.get(models.SemanticFact, memory_id)
        assert fact.fact_text == "Legacy history" and fact.lifecycle_status == "active"
        assert fact.scope_key is None
        assert (
            await session.scalar(sa.select(sa.func.count()).select_from(models.MemoryEvidence)) == 0
        )
