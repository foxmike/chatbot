# ruff: noqa: F401, F811
"""Actual completion -> claim -> bounded mocked LLM -> atomic memory effects."""

import importlib
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
import sqlalchemy as sa

from test_timeline_migration import fresh_database, needs_postgres, alembic
from test_completion_persistence import env, provider, run_stream, USER_ID, SESSION_ID
from phase2_helpers import extraction_registry
from test_memory_llm_extraction import memory, respond
from chatbot.config import Settings
from chatbot.db import models
from chatbot.perf import durable_handlers
from chatbot.perf.durable_worker import DurableJobWorker
from chatbot.memory.pgvector_store import PgvectorStore

MESSAGE = "I live in Seattle and I work as an aerospace engineer."
RESPONSE = respond(
    memory("User lives in Seattle", MESSAGE),
    memory("User works as an aerospace engineer", MESSAGE),
)


def worker_for(env, registry, embeddings=None):
    return DurableJobWorker(
        env.factory,
        batch_size=1,
        handlers={
            "memory_extract": durable_handlers.make_durable_memory_extract_handler(
                env.factory,
                registry=registry,
                settings=Settings(_env_file=None),
                embeddings=embeddings,
            ),
        },
    )


async def test_completion_restart_llm_receipt_atomic_retry_and_distinct_same_evidence(
    env, provider, monkeypatch
):
    from chatbot.core import turn

    # Any inline extraction would fail, rather than being hidden by a mock.
    monkeypatch.setattr(
        turn.MemoryExtractor, "extract", AsyncMock(side_effect=AssertionError("inline extraction"))
    )
    await run_stream(env, MESSAGE)
    jobs = await env.jobs()
    assert len(jobs) == 1 and jobs[0].status == "pending"
    assert await env.count(models.SemanticFact) == 0
    turn.MemoryExtractor.extract.assert_not_awaited()
    registry, fake = extraction_registry(RESPONSE)
    original = durable_handlers.TurnMemoryStorage._persist

    async def crash_after_writes(self, entries):
        await original(self, entries)
        raise RuntimeError("crash after memory writes")

    monkeypatch.setattr(durable_handlers.TurnMemoryStorage, "_persist", crash_after_writes)
    worker = worker_for(env, registry)
    claimed = await worker.claim_due_jobs()
    await worker._run_one(claimed[0])
    assert await env.count(models.SemanticFact) == 0  # all writes rolled back
    job = (await env.jobs())[0]
    assert job.status == "pending" and "crash after" in job.last_error
    assert job.payload["extraction_result"]["entries"]
    assert len(fake.calls) == 1
    async with env.factory() as db:
        row = await db.get(models.BackgroundJob, job.job_id)
        row.available_at = datetime.now(timezone.utc)
        await db.commit()
    monkeypatch.setattr(durable_handlers.TurnMemoryStorage, "_persist", original)
    fake.response = respond(memory("User resides in Seattle", MESSAGE))  # wording would differ
    embeddings = SimpleNamespace(embed=AsyncMock(return_value=[[0.0] * 1536, [0.0] * 1536]))
    restarted = worker_for(env, registry, embeddings)
    await restarted._run_one((await restarted.claim_due_jobs())[0])
    assert len(fake.calls) == 1  # durable receipt avoids another extraction call
    embeddings.embed.assert_awaited_once()
    assert len(embeddings.embed.call_args.args[0]) == 2
    assert (await env.jobs())[0].status == "succeeded"
    assert await env.count(models.SemanticFact) == 2  # same evidence, distinct assertions retained
    async with env.factory() as db:
        facts = list((await db.scalars(sa.select(models.SemanticFact))).all())
        assert len({fact.id for fact in facts}) == 2
        for fact in facts:
            assert fact.embedding is not None
            assert fact.source_type == "explicit"
            assert fact.source_message_id is not None
            assert str(fact.source_turn_id) == job.turn_id
            assert fact.extraction_metadata["evidence"] == MESSAGE
            assert fact.extraction_metadata["turn_id"] == job.turn_id
        row = await db.get(models.BackgroundJob, job.job_id)
        row.status = "pending"  # simulate delivery after a result was already committed
        await db.commit()
    await restarted._run_one((await restarted.claim_due_jobs())[0])
    assert await env.count(models.SemanticFact) == 2 and len(fake.calls) == 1
    embeddings.embed.assert_awaited_once()
    assert fake.calls[0]["config"].max_tokens == 700
    assert fake.calls[0]["config"].timeout_seconds == 20


async def test_real_pgvector_query_filters_owners_companions_rooms_and_keeps_shared_user_facts(env):
    other_uid, other_sid, same_companion_sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with env.factory() as db:
        db.add(
            models.User(
                id=other_uid,
                external_id="other",
                username="other",
                email="other@example.com",
                display_name="Other",
                password_hash="h",
            )
        )
        db.add_all(
            [
                models.Companion(companion_id="a", user_id=USER_ID, name="A"),
                models.Companion(companion_id="b", user_id=USER_ID, name="B"),
            ]
        )
        await db.flush()
        current = await db.get(models.Session, SESSION_ID)
        current.companion_id = "a"
        db.add_all(
            [
                models.Session(id=other_sid, user_id=other_uid),
                models.Session(id=same_companion_sid, user_id=USER_ID, companion_id="a"),
            ]
        )
        await db.flush()
        shared = models.SemanticFact(
            user_id=USER_ID, fact_text="shared", confidence=1, embedding=[0.0] * 1536
        )
        foreign = models.SemanticFact(
            user_id=other_uid, fact_text="foreign", confidence=1, embedding=[0.0] * 1536
        )
        msg = models.Message(
            session_id=same_companion_sid,
            user_id=USER_ID,
            companion_id="a",
            role="user",
            content="owned",
        )
        db.add(msg)
        await db.flush()
        owned = models.SemanticFact(
            user_id=USER_ID,
            fact_text="owned",
            confidence=1,
            embedding=[0.0] * 1536,
            source_message_id=msg.id,
        )
        db.add_all([shared, foreign, owned])
        await db.commit()
        results = await PgvectorStore(db).search([0.0] * 1536, 10, {"owner_session_id": SESSION_ID})
        assert {r.metadata["content"] for r in results} == {"shared", "owned"}
        current.companion_id = "b"
        await db.commit()
        results = await PgvectorStore(db).search([0.0] * 1536, 10, {"owner_session_id": SESSION_ID})
        assert {r.metadata["content"] for r in results} == {"shared"}
        current.companion_id = None
        await db.commit()
        results = await PgvectorStore(db).search([0.0] * 1536, 10, {"owner_session_id": SESSION_ID})
        assert {r.metadata["content"] for r in results} == {"shared"}
        current.companion_id = "a"
        room_id = str(uuid.uuid4())
        db.add(
            models.Room(
                room_id=room_id,
                user_id=str(USER_ID),
                companion_id="a",
                display_name="Private room",
                room_type="chat",
            )
        )
        await db.flush()
        source_session = await db.get(models.Session, same_companion_sid)
        source_session.room_id = room_id
        await db.commit()
        results = await PgvectorStore(db).search([0.0] * 1536, 10, {"owner_session_id": SESSION_ID})
        assert {r.metadata["content"] for r in results} == {"shared"}
        current.room_id = room_id
        await db.commit()
        results = await PgvectorStore(db).search([0.0] * 1536, 10, {"owner_session_id": SESSION_ID})
        assert {r.metadata["content"] for r in results} == {"shared", "owned"}


async def test_app_lifespan_starts_and_stops_durable_worker_without_provider_calls(monkeypatch):
    appmod = importlib.import_module("chatbot.api.app")
    settings = Settings(_env_file=None)
    from fastapi import FastAPI

    app = FastAPI()
    registry, fake = extraction_registry(RESPONSE)
    monkeypatch.setattr(appmod, "get_settings", lambda: settings)
    monkeypatch.setattr(appmod, "build_default_registry", lambda _: registry)
    volatile = SimpleNamespace(shutdown=AsyncMock())
    durable = SimpleNamespace(shutdown=AsyncMock())
    start = AsyncMock(return_value=durable)
    monkeypatch.setattr(appmod, "start_durable_job_worker", start)
    monkeypatch.setattr(appmod, "start_worker", AsyncMock(return_value=volatile))
    monkeypatch.setattr(appmod, "_start_scheduler", AsyncMock(return_value=None))
    monkeypatch.setattr(appmod, "get_coordinator", lambda _: SimpleNamespace(close=AsyncMock()))
    async with appmod.lifespan(app):
        assert app.state.durable_memory_worker is durable
        assert "memory_extract" in start.call_args.kwargs["handlers"]
    durable.shutdown.assert_awaited_once()
    assert not fake.calls


def test_invalid_extraction_model_fails_instead_of_substituting_default():
    from chatbot.memory.llm_extraction import resolve_extraction_model
    from chatbot.providers.base import ProviderSelectionError

    with pytest.raises(ProviderSelectionError):
        resolve_extraction_model(Settings(_env_file=None, task_class_extract_model="unknown/model"))


async def test_stale_attempt_cannot_overwrite_reclaimed_job_and_crash_retries_are_bounded(
    env, provider
):
    await run_stream(env, MESSAGE)
    first = worker_for(env, extraction_registry(RESPONSE)[0])
    old = (await first.claim_due_jobs())[0]
    async with env.factory() as db:
        row = await db.get(models.BackgroundJob, old.job_id)
        row.claimed_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.commit()
    second = worker_for(env, extraction_registry(RESPONSE)[0])
    current = (await second.claim_due_jobs())[0]
    assert current.attempt_count == 2
    await first._mark_failure(old, "stale failure")
    await first._mark_succeeded(old)
    assert (await env.jobs())[0].status == "running"
    async with env.factory() as db:
        row = await db.get(models.BackgroundJob, old.job_id)
        row.attempt_count = second.max_attempts
        row.claimed_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.commit()
    assert not await second.claim_due_jobs()
    terminal = (await env.jobs())[0]
    assert terminal.status == "failed" and terminal.completed_at


@needs_postgres
async def test_additive_provenance_migration_preserves_legacy_memory(fresh_database):
    from test_durable_memory_extract_handler import seed

    db = await fresh_database(upgrade_to="b8d5e3f7a9c1")
    uid, _, _ = await seed(db)
    async with db.factory() as session:
        await session.execute(
            sa.insert(models.SemanticFact.__table__).values(
                user_id=uid,
                fact_text="Existing fact",
                confidence=1.0,
            )
        )
        await session.commit()
    upgraded = alembic(db.url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    async with db.factory() as session:
        fact = await session.scalar(sa.select(models.SemanticFact))
        assert fact.fact_text == "Existing fact" and fact.extraction_metadata is None
