"""Phase 2.5b — Durable Memory Job Acceptance.

Deterministic acceptance tests for transactional creation of durable memory jobs (Task 2.2).
Covers:
1. Successfully completed conversation turn creates one durable memory job.
2. Repeated scheduling for the same logical turn does not create duplicate jobs.
3. Rolling back the conversation transaction also removes the scheduled job.
4. A failed conversation does not create durable work.
5. Completed streaming conversation schedules work through the same transaction mechanism.
6. Interrupted spoken turns follow the approved completion and persistence semantics.

Uses real SQLAlchemy transactions, avoiding mocked transaction lifecycles.
Tests requiring unfinished Phase 2 models/hooks from phase2-core are conditionally
skipped with an explicit reason indicating they await integration.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any, AsyncIterator
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from starlette.requests import Request

# Ensure tests/ directory is on sys.path for fixture imports
_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from test_completion_persistence import (  # noqa: E402
    MILESTONE,
    SESSION_ID,
    USER,
    USER_ID,
    Env,
    FakeProvider,
    _TABLES,
    app_with_engine,
    env,  # noqa: F401 (pytest fixture)
    provider,  # noqa: F401 (pytest fixture)
    run_stream,
    snapshot,
    standard_chat,
    stream_kwargs,
)
from chatbot.api import chat as chat_api
from chatbot.api import conversation
from chatbot.api import streaming
from chatbot.core import turn as turn_module
from chatbot.core.steps import TurnContext
from chatbot.db import models

# Detect Task 2.2 implementation (models.BackgroundJob and conversation.schedule_memory_job)
HAS_BACKGROUND_JOB_MODEL = hasattr(models, "BackgroundJob")
HAS_SCHEDULE_MEMORY_JOB = hasattr(conversation, "schedule_memory_job")
HAS_DURABLE_JOBS = HAS_BACKGROUND_JOB_MODEL and HAS_SCHEDULE_MEMORY_JOB

# Register BackgroundJob in _TABLES if available
if HAS_BACKGROUND_JOB_MODEL:
    BackgroundJob = getattr(models, "BackgroundJob")
    if BackgroundJob not in _TABLES:
        _TABLES.append(BackgroundJob)
else:
    BackgroundJob = None  # type: ignore

MEMORY_JOB_TASK_TYPE = getattr(conversation, "MEMORY_JOB_TASK_TYPE", "memory_extract")
schedule_memory_job = getattr(conversation, "schedule_memory_job", None)


# ===========================================================================
# Scenario 1: Completed turn creates one durable memory job
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.skipif(
    not HAS_DURABLE_JOBS,
    reason="Awaiting integration of Task 2.2 BackgroundJob and schedule_memory_job from phase2-core",
)
async def test_scenario_1_completed_turn_creates_one_durable_memory_job(
    env: Env, provider: FakeProvider
) -> None:
    """Scenario 1: A successfully completed conversation turn creates one durable memory job.

    Task 2.2 lifecycle & transaction contract:
    - Exactly one job row is inserted in background_jobs.
    - task_type matches MEMORY_JOB_TASK_TYPE ('memory_extract').
    - turn_id matches persisted user and assistant message turn_id.
    - session_id and user_id match the conversation.
    - status is 'pending' and attempt_count is 0.
    - payload contains compact identifiers (session_id, user_id, companion_id, turn_id),
      never a copied unbounded conversation history.
    """
    await run_stream(env, MILESTONE)

    messages = await env.messages()
    assert len(messages) >= 2
    user_msg, assistant_msg = messages[0], messages[1]

    async with env.factory() as db:
        res = await db.execute(select(BackgroundJob))
        jobs = res.scalars().all()

    assert len(jobs) == 1, f"Expected exactly 1 durable job, got {len(jobs)}"
    job = jobs[0]
    assert job.task_type == MEMORY_JOB_TASK_TYPE
    assert job.turn_id == user_msg.turn_id == assistant_msg.turn_id
    assert job.session_id == SESSION_ID
    assert job.user_id == USER_ID
    assert job.status == "pending"
    assert job.attempt_count == 0
    assert job.payload == {
        "session_id": str(SESSION_ID),
        "user_id": str(USER_ID),
        "companion_id": None,
        "turn_id": job.turn_id,
    }


# ===========================================================================
# Scenario 2: Repeated scheduling for same logical turn does not duplicate
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.skipif(
    not HAS_DURABLE_JOBS,
    reason="Awaiting integration of Task 2.2 BackgroundJob and schedule_memory_job from phase2-core",
)
async def test_scenario_2_repeated_scheduling_is_idempotent(env: Env) -> None:
    """Scenario 2: Repeated scheduling for the same logical turn does not create duplicate jobs.

    Task 2.2 persistence contract:
    - Unique constraint uq_background_jobs_turn_identity on (task_type, turn_id).
    - Enqueueing uses conflict-safe insert (ON CONFLICT DO NOTHING).
    - Repeated calls within the same transaction or across subsequent transactions
      leave exactly 1 job row.
    """
    turn_id = "turn-idempotent-acceptance-1"

    # Same transaction repeated calls
    async with env.factory() as db:
        await schedule_memory_job(
            db, session_id=SESSION_ID, user_id=USER_ID, companion_id=None, turn_id=turn_id
        )
        await schedule_memory_job(
            db, session_id=SESSION_ID, user_id=USER_ID, companion_id=None, turn_id=turn_id
        )
        await db.commit()

    # Subsequent transaction call with identical (task_type, turn_id)
    async with env.factory() as db:
        await schedule_memory_job(
            db, session_id=SESSION_ID, user_id=USER_ID, companion_id=None, turn_id=turn_id
        )
        await db.commit()

    async with env.factory() as db:
        res = await db.execute(
            select(BackgroundJob).where(BackgroundJob.turn_id == turn_id)
        )
        jobs = res.scalars().all()

    assert len(jobs) == 1, f"Expected exactly 1 job for turn_id {turn_id}, found {len(jobs)}"


# ===========================================================================
# Scenario 3: Rolling back conversation transaction removes scheduled job
# ===========================================================================


@pytest.mark.asyncio
async def test_scenario_3a_transaction_rollback_discards_uncommitted_turn_state(env: Env) -> None:
    """Scenario 3a (Executable Now): Real SQLAlchemy transaction rollback discards uncommitted turn state.

    Verifies the baseline transaction mechanics used for durable job scheduling:
    Writing messages or records within an active session and invoking rollback()
    strictly leaves 0 persisted rows in a subsequent fresh session.
    """
    turn_id = "turn-rb-smoke-1"
    async with env.factory() as db:
        msg = models.Message(
            id=uuid.uuid4(),
            session_id=SESSION_ID,
            role="user",
            content="Rolling back turn test",
            turn_id=turn_id,
        )
        db.add(msg)
        await db.flush()

        # Row is visible within transaction
        res = await db.execute(select(models.Message).where(models.Message.turn_id == turn_id))
        assert res.scalar_one_or_none() is not None

        # Roll back
        await db.rollback()

    # Confirms row does not survive rollback
    async with env.factory() as db:
        res = await db.execute(select(models.Message).where(models.Message.turn_id == turn_id))
        assert res.scalar_one_or_none() is None


@pytest.mark.asyncio
@pytest.mark.skipif(
    not HAS_DURABLE_JOBS,
    reason="Awaiting integration of Task 2.2 BackgroundJob and schedule_memory_job from phase2-core",
)
async def test_scenario_3b_rolling_back_conversation_transaction_removes_scheduled_job(env: Env) -> None:
    """Scenario 3b: Rolling back the conversation transaction removes the scheduled job.

    Task 2.2 transaction contract:
    - Insert the job in the same database transaction as the completed messages/effects.
    - A rollback leaves neither completed turn nor job.
    - The job must not be committed via an autonomous or out-of-band transaction.
    """
    turn_id = "turn-rollback-acceptance-1"

    async with env.factory() as db:
        # Schedule work within the uncommitted transaction
        await schedule_memory_job(
            db, session_id=SESSION_ID, user_id=USER_ID, companion_id=None, turn_id=turn_id
        )
        # Verify row is visible in-transaction
        res_in_tx = await db.execute(
            select(BackgroundJob).where(BackgroundJob.turn_id == turn_id)
        )
        assert res_in_tx.scalar_one_or_none() is not None, "Job should be visible within transaction"

        # Explicitly roll back the transaction
        await db.rollback()

    # Verify row is absent after rollback
    async with env.factory() as db:
        res_post_rb = await db.execute(
            select(BackgroundJob).where(BackgroundJob.turn_id == turn_id)
        )
        assert res_post_rb.scalar_one_or_none() is None, "Job must not survive transaction rollback"


# ===========================================================================
# Scenario 4: A failed conversation does not create durable work
# ===========================================================================


@pytest.mark.asyncio
async def test_scenario_4a_persistence_failure_aborts_turn_without_committing_messages(
    env: Env, provider: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 4a (Executable Now): Persistence failure rolls back the turn transaction.

    Task 2.2 lifecycle rule:
    When a database persistence failure occurs, the turn transaction rolls back.
    No completed messages or durable work survive.
    """
    monkeypatch.setattr(
        conversation, "add_turn_messages", AsyncMock(side_effect=RuntimeError("database unavailable"))
    )

    frames = await run_stream(env, MILESTONE)

    assert any('"type": "response"' in frame for frame in frames)
    # The transaction rollback discarded the messages
    assert await env.count(models.Message) == 0


@pytest.mark.asyncio
@pytest.mark.skipif(
    not HAS_DURABLE_JOBS,
    reason="Awaiting integration of Task 2.2 BackgroundJob and schedule_memory_job from phase2-core",
)
async def test_scenario_4b_failed_generation_creates_no_durable_job(
    env: Env, provider: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 4b: A failed generation saves the error text but creates no durable memory job.

    Task 2.2 lifecycle rule:
    - Interrupted, failed, or unsaved turns schedule no job.
    - When generation produces a provider error, the error is preserved for chat history,
      but completed=False ensures no durable background extraction job is scheduled.
    """
    async def failing_stream(ctx: TurnContext) -> AsyncIterator[str]:
        ctx.provider_response = "Provider error: connection reset"
        yield "Provider stream error: connection reset"

    monkeypatch.setattr(streaming, "_stream_provider_tokens", failing_stream)

    frames = await run_stream(env, MILESTONE)

    assert any('"type": "error"' in frame for frame in frames), "Expected error event frame"
    messages = await env.messages()
    assert len(messages) == 2
    assert messages[1].content == "Provider error: connection reset"

    # Confirms no durable job is persisted
    async with env.factory() as db:
        res = await db.execute(select(BackgroundJob))
        jobs = res.scalars().all()
    assert len(jobs) == 0, f"Failed turn must not persist background jobs, found {len(jobs)}"


# ===========================================================================
# Scenario 5: Completed streaming conversation schedules work via turn transaction
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.skipif(
    not HAS_DURABLE_JOBS,
    reason="Awaiting integration of Task 2.2 BackgroundJob and schedule_memory_job from phase2-core",
)
async def test_scenario_5_completed_streaming_and_standard_share_transaction_scheduling(
    env: Env, provider: FakeProvider
) -> None:
    """Scenario 5: Streaming and standard chat both schedule work through their turn transaction.

    Task 2.2 contract:
    - HTTP, SSE and spoken share this rule.
    - Standard chat (single request transaction) schedules job before commit.
    - Streaming chat (_persist_messages completion hook) schedules job before commit.
    - Both modes result in distinct, valid pending jobs matching their respective turns.
    """
    # 1. Standard chat turn
    _, commits = await standard_chat(env, "Hello standard chat turn!")
    assert commits == 1, "Standard turn must commit exactly once (including job insert)"

    # 2. Streaming chat turn
    await run_stream(env, "Hello streaming chat turn!")

    async with env.factory() as db:
        res = await db.execute(select(BackgroundJob).order_by(BackgroundJob.created_at))
        jobs = res.scalars().all()

    assert len(jobs) == 2, f"Expected 2 jobs (1 per completed mode), found {len(jobs)}"
    messages = await env.messages()
    persisted_turn_ids = {m.turn_id for m in messages if m.turn_id}
    assert {j.turn_id for j in jobs} == persisted_turn_ids
    for job in jobs:
        assert job.status == "pending"
        assert job.task_type == MEMORY_JOB_TASK_TYPE
        assert job.attempt_count == 0


# ===========================================================================
# Scenario 6: Interrupted spoken turns follow approved persistence semantics
# ===========================================================================


@pytest.mark.asyncio
async def test_scenario_6_interrupted_spoken_turn_persists_no_durable_job(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 6 (Executable Now): Interrupted spoken turns persist no durable job or unearned effects.

    Task 2.2 & Phase 0 completion contract:
    - Spoken avatar chat with barge-in interruption saves the heard prefix and system interruption note.
    - In-turn effects (facts, mood, timeline) are NOT committed.
    - Interrupted turns schedule no durable background work.
    """
    from test_avatar_backend import CID, FakeRuntime, FakeSocket
    from chatbot.avatar.coordinator import AvatarBinding, AvatarCoordinator

    async with env.factory() as db:
        db.add(models.Companion(companion_id=CID, user_id=USER_ID, name="Companion"))
        await db.commit()

    release = asyncio.Event()

    async def hold_after_first_chunk(index: int) -> None:
        if index == 1:
            await release.wait()

    fake = FakeProvider(chunks=("Heard speech chunk. ", "Interrupted speech chunk."), hook=hold_after_first_chunk)
    monkeypatch.setattr(turn_module, "_select_provider", lambda ctx: fake)

    app = app_with_engine()
    hub = AvatarCoordinator()
    binding = AvatarBinding(str(SESSION_ID), str(USER_ID), CID, FakeSocket())
    await hub.connect(binding, FakeRuntime)
    app.state.avatar_coordinator = hub

    request = Request(
        {"type": "http", "app": app, "state": {"avatar_source": "spoken", "avatar_binding": binding}}
    )
    stream_request = streaming.StreamRequest(session_id=str(SESSION_ID), message=MILESTONE)
    kwargs = stream_kwargs(
        app, await snapshot(env), http_request=request, session_companion_id=CID, history=None
    )

    async def consume() -> None:
        async for _ in streaming._stream_turn(stream_request, **kwargs):
            pass

    task = asyncio.create_task(consume())

    async def first_segment_exists() -> None:
        while not binding.turn or not binding.turn.segments:
            if task.done():
                task.result()
            await asyncio.sleep(0)

    await asyncio.wait_for(first_segment_exists(), 5)
    await hub.control(binding, {"type": "playback_end", "id": next(iter(binding.turn.segments))})
    await hub.interrupt(binding)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)

    messages = await env.messages()
    assert [m.role for m in messages] == ["user", "assistant", "system"]
    assert messages[1].content == "Heard speech chunk. "
    assert "interrupted" in messages[2].content

    # Confirms no in-turn effects are committed on interrupted turns
    assert (await env.effects()) == {"facts": 0, "mood_logs": 0, "user_state_logs": 0, "timeline": 0}

    # When BackgroundJob model is present, confirms no job is persisted
    if HAS_BACKGROUND_JOB_MODEL:
        async with env.factory() as db:
            res = await db.execute(select(BackgroundJob))
            jobs = res.scalars().all()
            assert len(jobs) == 0, f"Interrupted spoken turn must not persist background jobs, found {len(jobs)}"

    await hub.detach(binding)
