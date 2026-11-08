"""Focused tests for the durable memory extraction handler (Phase 2 design, Task 2.2).

The handler is NOT activated in the application yet; these tests drive it (and
the durable worker from 2.2b, which is the only registered caller) directly.
They initialize fresh PostgreSQL databases through the project's own
``alembic upgrade head`` (the same fixtures as ``test_timeline_migration``) and
need ``PHASE0_PG_URL`` to name a disposable instance.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from test_timeline_migration import fresh_database, needs_postgres  # noqa: F401

from chatbot.db import models
from chatbot.db.models import BackgroundJob, Episode, Message, Session, SemanticFact, User
from chatbot.perf.durable_handlers import make_durable_memory_extract_handler
from chatbot.perf.durable_worker import DurableJobWorker

@pytest.fixture(autouse=True)
def no_external_extraction(monkeypatch):
    from chatbot.perf import durable_handlers
    original = durable_handlers.LLMMemoryExtractor.from_settings
    from phase2_helpers import extraction_registry
    from chatbot.config import Settings
    registry, _ = extraction_registry()
    monkeypatch.setattr(durable_handlers.LLMMemoryExtractor, "from_settings",
                        lambda **kwargs: original(**{**kwargs, "registry": registry, "settings": Settings(_env_file=None)}))


# The existing extractor's heuristic facts: "I live in Seattle" / "I got a new job".
USER_MESSAGE = "I got a new job today! I live in Seattle and I am so thrilled."
ASSISTANT_MESSAGE = "Nice to hear. I'll remember that you live in Seattle."


async def seed(db: Any) -> tuple[Any, Any, Any]:
    uid, sid = uuid.uuid4(), uuid.uuid4()
    async with db.factory() as session:
        session.add(
            User(
                id=uid, external_id=f"ext_{uid.hex}", username=f"u_{uid.hex[:8]}",
                email=f"{uid.hex[:8]}@example.com", display_name="U", password_hash="h",
            )
        )
        session.add(Session(id=sid, user_id=uid, title="t"))
        await session.commit()
    return uid, sid, None


async def add_turn_messages(db: Any, session_id: Any, user_id: Any, turn_id: str) -> None:
    async with db.factory() as session:
        session.add(
            Message(
                session_id=session_id, user_id=user_id, role="user",
                content=USER_MESSAGE, turn_id=turn_id,
            )
        )
        session.add(
            Message(
                session_id=session_id, user_id=user_id, role="assistant",
                content=ASSISTANT_MESSAGE, turn_id=turn_id,
            )
        )
        await session.commit()


async def add_job(db: Any, session_id: Any, user_id: Any, turn_id: str) -> Any:
    async with db.factory() as session:
        job = BackgroundJob(
            session_id=session_id,
            user_id=user_id,
            task_type="memory_extract",
            turn_id=turn_id,
            payload={"session_id": str(session_id), "user_id": str(user_id), "turn_id": turn_id},
        )
        session.add(job)
        await session.commit()
        return job.job_id


async def get_job(db: Any, job_id: Any) -> Any:
    async with db.factory() as session:
        stored = await session.get(BackgroundJob, job_id)
        assert stored is not None
        return stored


async def run_until_terminal(
    worker: DurableJobWorker, db: Any, job_id: Any, timeout_seconds: float = 5.0
) -> None:
    worker_task = asyncio.create_task(worker.run())
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        async with db.factory() as session:
            stored = await session.get(BackgroundJob, job_id)
            if stored is not None and stored.status in ("succeeded", "failed"):
                break
        await asyncio.sleep(0.01)
    worker.stop_event.set()
    await worker_task


@needs_postgres
async def test_a_persisted_job_invokes_the_handler_and_loads_the_correct_turn(fresh_database):
    db = await fresh_database()
    uid, sid, _ = await seed(db)
    turn_id = f"turn_{uuid.uuid4().hex[:12]}"
    await add_turn_messages(db, sid, uid, turn_id)

    # A distractor turn in the same session: none of its messages may be loaded.
    await add_turn_messages(db, sid, uid, "turn_other")

    job_id = await add_job(db, sid, uid, turn_id)
    worker = DurableJobWorker(
        db.factory, handlers={"memory_extract": make_durable_memory_extract_handler(db.factory)},
        poll_interval_seconds=0.01,
    )
    await run_until_terminal(worker, db, job_id)

    stored = await get_job(db, job_id)
    assert stored.status == "succeeded"
    assert stored.completed_at is not None
    assert stored.attempt_count == 1
    assert worker.jobs_processed == 1

    async with db.factory() as session:
        facts = (
            await session.execute(
                select(SemanticFact).order_by(SemanticFact.created_at)
            )
        ).scalars().all()
        assert facts, "extracted memories were persisted"
        messages = (
            await session.execute(
                select(Message).where(
                    Message.session_id == sid,
                    Message.turn_id == turn_id,
                )
            )
        ).scalars().all()
        assert len(messages) == 2  # the turn's own messages are unchanged


@needs_postgres
async def test_extracted_memories_carry_source_message_and_turn_provenance(fresh_database):
    db = await fresh_database()
    uid, sid, _ = await seed(db)
    turn_id = f"turn_{uuid.uuid4().hex[:12]}"
    await add_turn_messages(db, sid, uid, turn_id)
    job_id = await add_job(db, sid, uid, turn_id)

    worker = DurableJobWorker(
        db.factory, handlers={"memory_extract": make_durable_memory_extract_handler(db.factory)},
        poll_interval_seconds=0.01,
    )
    await run_until_terminal(worker, db, job_id)

    async with db.factory() as session:
        facts = (
            await session.execute(
                select(SemanticFact)
                .where(SemanticFact.user_id == uid)
                .order_by(SemanticFact.created_at)
            )
        ).scalars().all()
        assert facts, "extracted memories were persisted"
        messages = (
            await session.execute(
                select(Message).where(
                    Message.session_id == sid,
                    Message.turn_id == turn_id,
                )
            )
        ).scalars().all()
        user_msg = next(m for m in messages if m.role == "user")
        assistant_msg = next(m for m in messages if m.role == "assistant")
        for fact in facts:
            assert fact.source_message_id in {user_msg.id, assistant_msg.id}
        assert any(f.source_message_id == user_msg.id for f in facts)

        episode = (
            await session.execute(select(Episode).where(Episode.session_id == sid))
        ).scalars().first()
        assert episode is not None
        assert episode.start_message_id == user_msg.id
        assert episode.end_message_id == assistant_msg.id


@needs_postgres
async def test_reprocessing_the_same_job_creates_no_duplicate_effects(fresh_database):
    db = await fresh_database()
    uid, sid, _ = await seed(db)
    turn_id = f"turn_{uuid.uuid4().hex[:12]}"
    await add_turn_messages(db, sid, uid, turn_id)
    job_id = await add_job(db, sid, uid, turn_id)

    worker = DurableJobWorker(
        db.factory, handlers={"memory_extract": make_durable_memory_extract_handler(db.factory)},
        poll_interval_seconds=0.01,
    )
    await run_until_terminal(worker, db, job_id)
    stored = await get_job(db, job_id)
    assert stored.status == "succeeded"

    # Simulate the crash-after-write recovery: the job is re-runnable after
    # its first attempt already wrote its results, and the worker re-runs the
    # same job (e.g. a worker restart saw the job still running).
    async with db.factory() as session:
        stored = await session.get(BackgroundJob, job_id)
        stored.status = "pending"
        stored.attempt_count = 0
        await session.commit()

    worker = DurableJobWorker(
        db.factory, handlers={"memory_extract": make_durable_memory_extract_handler(db.factory)},
        poll_interval_seconds=0.01,
    )
    await run_until_terminal(worker, db, job_id)

    async with db.factory() as session:
        facts = (
            await session.execute(select(SemanticFact).where(SemanticFact.user_id == uid))
        ).scalars().all()
        texts = [f.fact_text for f in facts]
        assert len(texts) == len(set(texts)), "no duplicate fact texts"
        messages = (
            await session.execute(
                select(Message).where(
                    Message.session_id == sid,
                    Message.turn_id == turn_id,
                )
            )
        ).scalars().all()
        user_msg = next(m for m in messages if m.role == "user")
        assistant_msg = next(m for m in messages if m.role == "assistant")
        for fact in facts:
            pairs = (
                await session.execute(
                    select(SemanticFact.id).where(
                        SemanticFact.source_message_id == fact.source_message_id,
                        SemanticFact.fact_text == fact.fact_text,
                    )
                )
            ).scalars().all()
            assert len(pairs) == 1, "no duplicate (source_message_id, fact_text) keys"
        assert len(facts) == 2
        episodes = (
            await session.execute(
                select(Episode).where(
                    Episode.start_message_id == user_msg.id,
                    Episode.end_message_id == assistant_msg.id,
                )
            )
        ).scalars().all()
        assert len(episodes) == 1, "no duplicate episode for the turn"


@needs_postgres
async def test_wrong_user_access_is_rejected(fresh_database):
    db = await fresh_database()
    uid, sid, _ = await seed(db)
    other_user = uuid.uuid4()
    async with db.factory() as session:
        session.add(
            User(
                id=other_user, external_id=f"ext_{other_user.hex}", username=f"u_{other_user.hex[:8]}",
                email=f"{other_user.hex[:8]}@example.com", display_name="U", password_hash="h",
            )
        )
        await session.commit()
    turn_id = f"turn_{uuid.uuid4().hex[:12]}"
    await add_turn_messages(db, sid, uid, turn_id)
    job_id = await add_job(db, sid, other_user, turn_id)

    worker = DurableJobWorker(
        db.factory, handlers={"memory_extract": make_durable_memory_extract_handler(db.factory)},
        poll_interval_seconds=0.01, max_attempts=1,
    )
    await run_until_terminal(worker, db, job_id)

    stored = await get_job(db, job_id)
    assert stored.status == "failed"
    assert stored.last_error is not None and "does not belong" in stored.last_error
    async with db.factory() as session:
        facts = (
            await session.execute(select(SemanticFact).where(SemanticFact.user_id == uid))
        ).scalars().all()
        assert facts == [], "wrong-user access rejected"
        episodes = (
            await session.execute(select(Episode).where(Episode.session_id == sid))
        ).scalars().all()
        assert episodes == [], "wrong-user access rejected"


@needs_postgres
async def test_wrong_companion_access_is_rejected(fresh_database):
    db = await fresh_database()
    uid, sid, _ = await seed(db)
    turn_id = f"turn_{uuid.uuid4().hex[:12]}"
    await add_turn_messages(db, sid, uid, turn_id)
    job_id = await add_job(db, sid, uid, turn_id)

    # A real companion exists (satisfying the job's FK) but does not own the session.
    async with db.factory() as session:
        session.add(models.Companion(companion_id="companion-other", user_id=uid, name="Other"))
        await session.commit()
    async with db.factory() as session:
        stored = await session.get(BackgroundJob, job_id)
        stored.companion_id = "companion-other"
        await session.commit()

    worker = DurableJobWorker(
        db.factory, handlers={"memory_extract": make_durable_memory_extract_handler(db.factory)},
        poll_interval_seconds=0.01, max_attempts=1,
    )
    await run_until_terminal(worker, db, job_id)

    stored = await get_job(db, job_id)
    assert stored.status == "failed"
    assert stored.last_error is not None and "does not belong" in stored.last_error
    async with db.factory() as session:
        facts = (
            await session.execute(select(SemanticFact).where(SemanticFact.user_id == uid))
        ).scalars().all()
        assert facts == [], "wrong-companion access rejected"
        episodes = (
            await session.execute(select(Episode).where(Episode.session_id == sid))
        ).scalars().all()
        assert episodes == [], "wrong-companion access rejected"


@needs_postgres
async def test_a_job_with_no_persisted_turn_messages_is_a_no_op(fresh_database):
    db = await fresh_database()
    uid, sid, _ = await seed(db)
    turn_id = f"turn_{uuid.uuid4().hex[:12]}"
    job_id = await add_job(db, sid, uid, turn_id)

    worker = DurableJobWorker(
        db.factory, handlers={"memory_extract": make_durable_memory_extract_handler(db.factory)},
        poll_interval_seconds=0.01,
    )
    await run_until_terminal(worker, db, job_id)

    stored = await get_job(db, job_id)
    assert stored.status == "succeeded"
    async with db.factory() as session:
        facts = (
            await session.execute(select(SemanticFact))
        ).scalars().all()
        assert facts == [], "nothing to extract"
        episodes = (
            await session.execute(select(Episode))
        ).scalars().all()
        assert episodes == [], "nothing to extract"
