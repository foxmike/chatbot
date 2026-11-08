"""Focused Phase 5 integration checks across extraction, durable storage and prompts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
import json
from time import perf_counter
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa

from test_completion_persistence import USER_ID, env  # noqa: F401
from test_memory_llm_extraction import FakeProvider
from test_persistent_identity_integration import (
    RecordingProvider,
    add_companion,
    new_session,
    spoken_turn,
    standard_turn,
    stream_turn,
)

from chatbot.config import Settings
from chatbot.core import turn as turn_module
from chatbot.core.steps import TurnContext
from chatbot.db import models
from chatbot.memory.llm_extraction import LLMMemoryExtractor
from chatbot.perf.durable_handlers import make_durable_memory_extract_handler
from chatbot.perf.durable_worker import DurableJobWorker
from chatbot.soul.emotional_associations import (
    AssociationObservation,
    AssociationSource,
    association_key,
    apply_emotional_associations,
    retrieve_emotional_associations,
)
from chatbot.soul.mood_continuity import _candidate_sessions, load_restorable_mood
from chatbot.soul.mood import MoodState


def _all_categories_response() -> dict:
    return {
        "memories": [{
            "type": "personal_fact", "content": "User lives in Seattle.",
            "evidence": "I live in Seattle.", "status": "real", "confidence": 0.9,
            "temporal": "current", "corrects": None,
        }],
        "experiences": [{
            "title": "Finished outage repair", "narrative": "User and companion fixed the outage together.",
            "participants": ["user", "companion"], "significance": "significant",
            "evidence": "We fixed the outage together after a long night.", "status": "real",
            "origin": "conversation", "topics": ["outage", "repair"], "revises": None,
            "confidence": 0.8,
        }],
        "self": [{
            "kind": "opinion", "subject": "careful repair", "statement": "I value careful repair.",
            "reason": "It prevents another outage.", "evidence": "I value careful repair because it prevents another outage.",
            "status": "real", "significance": "significant", "relation": "consistent",
            "change_of_mind": False,
        }],
        "threads": [{
            "ownership": "shared", "kind": "subject", "subject": "Review the outage report",
            "topic": "outage report", "status": "open", "importance": "significant",
            "evidence": "Let's review the outage report tomorrow.", "supersedes": None,
        }],
        "emotions": [{
            "subject": "outage repair", "emotion": "relieved", "interpretation": "The repair mattered after a difficult night.",
            "source": "companion_expressed", "evidence": "I was relieved when we fixed it together.",
            "significance": "significant", "status": "real", "unresolved": False,
            "revises": None, "experience_ref": None,
        }],
    }


@pytest.mark.asyncio
async def test_one_extraction_response_preserves_all_categories_and_700_token_cap():
    response = _all_categories_response()
    provider = FakeProvider(response)
    extractor = LLMMemoryExtractor(storage=SimpleNamespace(persist=None), provider=provider, model="fake")
    outcome = await extractor.extract_entries(
        session_id="not-persisted", turn_id="turn-phase5",
        user_message="I live in Seattle. We fixed the outage together after a long night. Let's review the outage report tomorrow.",
        provider_response="I value careful repair because it prevents another outage. I was relieved when we fixed it together.",
        known_experiences="experience-1: outage repair",
        known_experience_ids=("experience-1",),
    )

    assert len(provider.calls) == 1
    assert provider.calls[0]["config"].max_tokens == 700
    assert len(outcome.entries) >= 2  # fact and meaningful experience remain eligible
    assert outcome.self_observations
    assert outcome.thread_updates
    assert outcome.emotional_observations
    assert outcome.output_limit_tokens == 700
    assert outcome.response_truncated is False


@pytest.mark.asyncio
async def test_durable_job_applies_association_once_and_reactivates_implicit_unresolved_context(
    env, monkeypatch
):
    companion_id = await add_companion(env, name="Theron", sliders={"agreeableness": 0.25, "conscientiousness": 0.85})
    session_id = await new_session(env, companion_id)
    turn_id = f"turn_{uuid.uuid4().hex}"
    user_text = "I know we disagreed about the release, but I want to talk it through."
    assistant_text = "I'm still frustrated by that disagreement, and I don't think we've settled it."
    async with env.factory() as db:
        db.add_all([
            models.Message(session_id=session_id, user_id=USER_ID, role="user", content=user_text, turn_id=turn_id),
            models.Message(session_id=session_id, user_id=USER_ID, role="assistant", content=assistant_text, turn_id=turn_id),
        ])
        job = models.BackgroundJob(
            session_id=session_id, user_id=USER_ID, companion_id=companion_id,
            task_type="memory_extract", turn_id=turn_id,
            payload={"session_id": str(session_id), "user_id": str(USER_ID), "turn_id": turn_id},
        )
        db.add(job)
        await db.commit()
        job_id = job.job_id

    response = {
        "memories": [], "experiences": [], "self": [], "threads": [],
        "emotions": [{
            "subject": "release disagreement", "emotion": "frustrated",
            "interpretation": "The disagreement remains unresolved.",
            "source": "companion_expressed", "evidence": assistant_text,
            "significance": "significant", "status": "real", "unresolved": True,
            "revises": None, "experience_ref": None,
        }],
    }
    from phase2_helpers import extraction_registry

    registry, fake = extraction_registry(response)
    handler = make_durable_memory_extract_handler(
        env.factory, registry=registry, settings=Settings(_env_file=None)
    )
    worker = DurableJobWorker(env.factory, handlers={"memory_extract": handler})
    claimed = await worker.claim_due_jobs()
    assert len(claimed) == 1
    await worker._run_one(claimed[0])
    assert len(fake.calls) == 1

    async with env.factory() as db:
        stored_job = await db.get(models.BackgroundJob, job_id)
        receipt = stored_job.payload["extraction_result"]
        assert receipt["extraction_status"]["output_limit_tokens"] == 700
        assert receipt["extraction_status"]["accepted"]["emotional_observations"] == 1
        associations = await retrieve_emotional_associations(
            db, session_id=session_id, message="Hey, how's it going?", include_unresolved=True
        )
        assert len(associations) == 1
        assert associations[0].unresolved is True
        assert associations[0].source_message_id is not None
        association_id = associations[0].id

    # Duplicate delivery reuses the saved receipt and evidence identity.
    async with env.factory() as db:
        stored_job = await db.get(models.BackgroundJob, job_id)
        stored_job.status = "pending"
        stored_job.completed_at = None
        await db.commit()
    claimed = await worker.claim_due_jobs()
    await worker._run_one(claimed[0])
    assert len(fake.calls) == 1
    async with env.factory() as db:
        associations = await retrieve_emotional_associations(
            db, session_id=session_id, message="Hey, how's it going?", include_unresolved=True
        )
        assert [item.id for item in associations] == [association_id]
        too_old = await retrieve_emotional_associations(
            db, session_id=session_id, message="Hey, how's it going?", include_unresolved=True,
            now=associations[0].last_at + timedelta(days=250),
        )
        assert too_old == []  # unresolved reminder is bounded; direct matches remain recallable


@pytest.mark.asyncio
async def test_relationship_evidence_and_emotional_context_share_standard_sse_and_spoken_prompt(
    env, monkeypatch
):
    companions = [
        await add_companion(
            env, name="Theron", sliders={"conscientiousness": 0.9, "agreeableness": 0.2},
            relationship={"type": "peer", "tone": "formal", "directness": 0.8, "warmth": 0.3},
        )
        for _ in range(3)
    ]
    sessions = [await new_session(env, companion_id) for companion_id in companions]
    at = datetime.now(timezone.utc)
    for index, session_id in enumerate(sessions):
        turn = f"seed_{index}_{uuid.uuid4().hex}"
        async with env.factory() as db:
            user_message = models.Message(
                session_id=session_id, user_id=USER_ID, role="user", content="We had a prior disagreement.", turn_id=turn
            )
            assistant_message = models.Message(
                session_id=session_id, user_id=USER_ID, role="assistant", content="I remain guarded after that disagreement.", turn_id=turn
            )
            db.add_all([user_message, assistant_message])
            await db.flush()
            await apply_emotional_associations(
                db,
                user_id=USER_ID,
                companion_id=companions[index],
                session_id=session_id,
                observations=[AssociationObservation(
                    subject="archival disagreement", emotion="guarded",
                    interpretation="A prior disagreement remains uncomfortable.",
                    source="companion_expressed", excerpt="I remain guarded after that disagreement.",
                    unresolved=True, significance="major",
                )],
                source=AssociationSource(
                    turn_id=turn, session_id=str(session_id), source_at=at,
                    user_message_id=str(user_message.id), assistant_message_id=str(assistant_message.id),
                ),
            )
            await db.commit()

    provider = RecordingProvider()
    monkeypatch.setattr(turn_module, "_select_provider", lambda _ctx: provider)
    inline_extract = AsyncMock(side_effect=AssertionError("durable turns must not extract inline"))
    monkeypatch.setattr(turn_module.MemoryExtractor, "extract", inline_extract)
    message = "Hey, how's it going?"
    standard_response = await standard_turn(env, sessions[0], message)
    stream_frames = await stream_turn(env, sessions[1], message)
    await spoken_turn(env, sessions[2], companions[2], provider, monkeypatch, message)

    prompts = [provider.system(index) for index in range(3)]
    association_lines = []
    for prompt in prompts:
        assert "EMOTIONAL ASSOCIATIONS" in prompt
        association_lines.append(next(
            line for line in prompt.splitlines() if "archival disagreement" in line
        ))
    assert association_lines[0] == association_lines[1] == association_lines[2]
    assert len(provider.inputs) == 3  # one generation per transport, no planner call
    assert standard_response.response == "Glad to. Let's start."
    assert any('"type": "token"' in frame for frame in stream_frames)
    assert any('"type": "response"' in frame for frame in stream_frames)
    inline_extract.assert_not_awaited()
    jobs = await env.jobs()
    jobs_by_session = {str(session_id): [job for job in jobs if job.session_id == session_id] for session_id in sessions}
    assert len(jobs_by_session[str(sessions[0])]) == 1
    assert len(jobs_by_session[str(sessions[1])]) == 1
    assert jobs_by_session[str(sessions[2])] == []  # spoken turn was interrupted before completion
    traces = await turn_module.trace_collector.list_traces(session_id=str(sessions[0]))
    [trace] = traces
    assert "EMOTIONAL ASSOCIATIONS" in trace.prompt
    (compile_step,) = [step for step in trace.steps if step.step_name == "compile_context"]
    sources = compile_step.metadata["identity_sources"]
    assert sources["emotional_associations"]["included"] == 1
    assert sources["social_context"]["relationship_signals"] >= 1


@pytest.mark.asyncio
async def test_mood_restoration_is_companion_scoped_and_does_not_mutate_personality(env):
    companion_a = await add_companion(env, name="A", sliders={"agreeableness": 0.2})
    companion_b = await add_companion(env, name="B", sliders={"agreeableness": 0.9})
    previous = await new_session(env, companion_a)
    target = await new_session(env, companion_a)
    other = await new_session(env, companion_b)
    old_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    async with env.factory() as db:
        session = await db.get(models.Session, previous)
        assert session.companion_id == companion_a
        session.mood_state = {
            "mood": "direct", "energy": 0.15, "closeness": 0.2,
            "last_updated": old_at.isoformat(), "recent_emotional_context": ["unresolved disagreement"],
        }
        await db.commit()
        candidates = await _candidate_sessions(
            db, user_id=USER_ID, companion_id=companion_a, room_id=None, exclude_session_id=target
        )
        assert candidates and candidates[0].mood_state["mood"] == "direct"
        restored = await load_restorable_mood(
            db, user_id=USER_ID, companion_id=companion_a, exclude_session_id=target,
        )
        assert restored is not None and restored.source_session_id == str(previous)
        isolated = await load_restorable_mood(
            db, user_id=USER_ID, companion_id=companion_b, exclude_session_id=other,
        )
        assert isolated is None

    original = {"agreeableness": 0.2, "identity": "steady"}
    before = json.dumps(original, sort_keys=True)
    async with env.factory() as db:
        ctx = TurnContext(
            session_id=str(target), user_message="Hello again", history=[], persona=original,
            retrieved_memories=[], compiled_context=None, provider_response=None,
            extracted_memories=[], final_response=None,
            metadata={
                "db": db, "user_id": USER_ID, "companion_id": companion_a,
                "room_id": None, "companion_identity": {"agreeableness": 0.2},
                "character_profile": {"name": "A", "personality_sliders": {"agreeableness": 0.2}},
                "emotional_intelligence_enabled": True,
            },
        )
        await turn_module.RestoreMoodStep().run(ctx)
        assert ctx.metadata["mood_restoration"]["source"] == "previous_session"
        assert ctx.metadata["mood_state"].mood == "direct"
        assert "unresolved disagreement" in ctx.metadata["mood_state"].recent_emotional_context
    assert json.dumps(original, sort_keys=True) == before


@pytest.mark.asyncio
async def test_postgres_association_concurrency_revision_and_room_companion_isolation(env):
    companion_a = await add_companion(env, name="A")
    companion_b = await add_companion(env, name="B")
    private_sessions = [await new_session(env, companion_a) for _ in range(2)]
    companion_b_session = await new_session(env, companion_b)
    room_ids = [f"phase5-room-{uuid.uuid4().hex}" for _ in range(2)]
    room_sessions = [uuid.uuid4(), uuid.uuid4()]
    async with env.factory() as db:
        for room_id in room_ids:
            db.add(models.Room(
                room_id=room_id, user_id=str(USER_ID), companion_id=companion_a,
                room_type="standard", display_name="Phase 5 isolated room",
                memory_scope="room", persist_to_durable=True,
            ))
        await db.flush()
        db.add_all([
            models.Session(id=room_sessions[i], user_id=USER_ID, companion_id=companion_a, room_id=room_ids[i], title="Room")
            for i in range(2)
        ])
        await db.commit()

    async def write(
        session_id, *, turn_id, emotion, text, revises="", unresolved=True,
        persisted_message_id=None,
    ):
        async with env.factory() as db:
            if persisted_message_id is None:
                msg = models.Message(
                    session_id=session_id, user_id=USER_ID, role="assistant", content=text,
                    turn_id=turn_id,
                )
                db.add(msg)
                await db.flush()
            else:
                msg = await db.get(models.Message, persisted_message_id)
                assert msg is not None and msg.turn_id == turn_id
            if turn_id.startswith("parallel-"):
                await concurrent_start.wait()
            change = await apply_emotional_associations(
                db,
                user_id=USER_ID,
                companion_id=companion_a,
                session_id=session_id,
                observations=[AssociationObservation(
                    subject="release conflict", emotion=emotion,
                    interpretation=text, source="companion_expressed", excerpt=text,
                    significance="significant", unresolved=unresolved, revises=revises,
                )],
                source=AssociationSource(
                    turn_id=turn_id, session_id=str(session_id), source_at=datetime.now(timezone.utc),
                    assistant_message_id=str(msg.id),
                ),
            )
            await db.commit()
            return change

    # Concurrent work from distinct sessions merges into one association and
    # retains each turn's immutable evidence.
    parallel_turn_ids = [f"parallel-{i}-{uuid.uuid4().hex}" for i in range(2)]
    parallel_message_ids = []
    async with env.factory() as db:
        messages = [models.Message(
            session_id=private_sessions[i], user_id=USER_ID, role="assistant",
            content="I remain guarded about the release conflict.", turn_id=parallel_turn_ids[i],
        ) for i in range(2)]
        db.add_all(messages)
        await db.flush()
        parallel_message_ids = [message.id for message in messages]
        await db.commit()
    concurrent_start = asyncio.Barrier(2)
    created = await asyncio.gather(*[
        write(
            private_sessions[i], turn_id=parallel_turn_ids[i], emotion="guarded",
            text="I remain guarded about the release conflict.",
            persisted_message_id=parallel_message_ids[i],
        )
        for i in range(2)
    ])
    assert all(change.applied for change in created)
    async with env.factory() as db:
        active = await retrieve_emotional_associations(
            db, session_id=private_sessions[0], message="release conflict"
        )
        assert len(active) == 1 and active[0].evidence_count == 2
        old_id = active[0].id
        evidence = list((await db.scalars(
            sa.select(models.MemoryEvidence).where(models.MemoryEvidence.memory_id == uuid.UUID(old_id))
        )).all())
        assert len(evidence) == 2
        assert len({item.turn_id for item in evidence}) == 2
        assert len({item.source_message_id for item in evidence}) == 2

    # An explicit revision supersedes without erasing the earlier evidence.
    revised = await write(
        private_sessions[0], turn_id=f"revision-{uuid.uuid4().hex}", emotion="relieved",
        text="I feel relieved that we resolved the release conflict.",
        revises=association_key("release conflict", "guarded", "companion_expressed"),
        unresolved=False,
    )
    assert revised.applied and revised.applied[0]["op"] == "create"
    async with env.factory() as db:
        history = await retrieve_emotional_associations(
            db, session_id=private_sessions[0], message="release conflict", limit=3
        )
        assert history and history[0].emotion == "relieved" and history[0].unresolved is False
        earlier = await db.get(models.SemanticFact, uuid.UUID(old_id))
        assert earlier.lifecycle_status == "superseded"
        assert earlier.extraction_metadata["association"]["evidence_count"] == 2
        assert await retrieve_emotional_associations(
            db, session_id=companion_b_session, message="release conflict"
        ) == []
        foreign_user_id, foreign_session_id = uuid.uuid4(), uuid.uuid4()
        db.add(models.User(
            id=foreign_user_id, external_id=f"ext_{foreign_user_id.hex}", username=f"u_{foreign_user_id.hex[:8]}",
            email=f"{foreign_user_id.hex[:8]}@example.com", display_name="Other", password_hash="h",
        ))
        await db.flush()
        db.add(models.Session(
            id=foreign_session_id, user_id=foreign_user_id, companion_id=companion_a, title="foreign-owner probe"
        ))
        await db.commit()
        assert await retrieve_emotional_associations(
            db, session_id=foreign_session_id, message="release conflict"
        ) == []

    # Room-scoped evidence remains private to its originating room.
    async with env.factory() as db:
        msg = models.Message(
            session_id=room_sessions[0], user_id=USER_ID, role="assistant",
            content="I am cautious about the amber archive shelf.", turn_id=f"room-{uuid.uuid4().hex}",
        )
        db.add(msg)
        await db.flush()
        await apply_emotional_associations(
            db, user_id=USER_ID, companion_id=companion_a, session_id=room_sessions[0],
            observations=[AssociationObservation(
                subject="amber archive shelf", emotion="cautious",
                interpretation="I am cautious about the amber archive shelf.",
                source="companion_expressed", excerpt=msg.content, unresolved=False,
            )],
            source=AssociationSource(
                turn_id=msg.turn_id, session_id=str(room_sessions[0]), source_at=datetime.now(timezone.utc),
                assistant_message_id=str(msg.id),
            ),
        )
        await db.commit()
    async with env.factory() as db:
        assert await retrieve_emotional_associations(
            db, session_id=room_sessions[0], message="amber archive shelf"
        )
        unrelated_room = await retrieve_emotional_associations(
            db, session_id=room_sessions[1], message="amber archive shelf"
        )
        assert all(item.subject != "amber archive shelf" for item in unrelated_room)


@pytest.mark.asyncio
async def test_phase5_association_context_lookup_does_not_request_embeddings(env, monkeypatch):
    companion_id = await add_companion(env, name="Lexical lookup")
    session_id = await new_session(env, companion_id)
    turn_id = f"seed_{uuid.uuid4().hex}"
    async with env.factory() as db:
        msg = models.Message(
            session_id=session_id, user_id=USER_ID, role="assistant",
            content="I remain wary of the archived map.", turn_id=turn_id,
        )
        db.add(msg)
        await db.flush()
        await apply_emotional_associations(
            db, user_id=USER_ID, companion_id=companion_id, session_id=session_id,
            observations=[AssociationObservation(
                subject="archived map", emotion="wary", interpretation="The archived map still matters.",
                source="companion_expressed", excerpt=msg.content, unresolved=False,
            )],
            source=AssociationSource(
                turn_id=turn_id, session_id=str(session_id), source_at=datetime.now(timezone.utc),
                assistant_message_id=str(msg.id),
            ),
        )
        await db.commit()

    embedding_request = AsyncMock()
    monkeypatch.setattr(turn_module, "generate_embedding", embedding_request)
    async with env.factory() as db:
        baseline_ctx = TurnContext(
            session_id=str(session_id), user_message="The archived map came up again.", history=[],
            persona={}, retrieved_memories=[], compiled_context=None, provider_response=None,
            extracted_memories=[], final_response=None, metadata={},
        )
        started = perf_counter()
        await turn_module._phase5_prompt_sections(baseline_ctx)
        baseline_ms = (perf_counter() - started) * 1_000
        ctx = TurnContext(
            session_id=str(session_id), user_message="The archived map came up again.", history=[],
            persona={"name": "Lexical lookup"}, retrieved_memories=[], compiled_context=None,
            provider_response=None, extracted_memories=[], final_response=None,
            metadata={
                "db": db, "user_id": USER_ID, "companion_id": companion_id,
                "companion_identity": {"conscientiousness": 0.7},
                "character_profile": {"name": "Lexical lookup", "humor_style": "dry",
                                      "communication_style": "concise", "personality_sliders": {"conscientiousness": 0.7}},
                "companion_relationship": {"type": "peer", "tone": "neutral", "directness": 0.5,
                                           "warmth": 0.5, "content_level": "moderate", "initiative_level": 0.5},
            },
        )
        started = perf_counter()
        sections = await turn_module._phase5_prompt_sections(ctx)
        with_context_ms = (perf_counter() - started) * 1_000
    embedding_request.assert_not_awaited()
    assert sections["emotional_associations"] and "archived map" in sections["emotional_associations"]
    print(f"phase5_prompt_preparation_incremental_ms={with_context_ms - baseline_ms:.2f}")


@pytest.mark.asyncio
async def test_roleplay_mode_does_not_restore_or_persist_real_mood_or_emotional_associations(monkeypatch):
    now = datetime.now(timezone.utc)
    state = MoodState(
        mood="direct", energy=0.3, closeness=0.1, last_updated=now,
        recent_emotional_context=["fictional scene"],
    )
    ctx = TurnContext(
        session_id=str(uuid.uuid4()), user_message="The captain enters the ship.", history=[],
        persona={"name": "A", "persona": "Configured identity."}, retrieved_memories=[],
        compiled_context=None, provider_response=None, extracted_memories=[], final_response=None,
        metadata={
            "companion_identity": {"agreeableness": 0.2}, "character_profile": {"name": "A"},
            "conversation_mode": "roleplay", "mood_state": state,
            "emotional_intelligence_enabled": True,
        },
    )
    step = turn_module.CompileContextStep(
        assembler=SimpleNamespace(assemble=lambda _persona, **_kwargs: "Configured identity."),
        intent_classifier=None,
    )
    await step.run(ctx)
    assert ctx.metadata["phase5_roleplay_context"] is True
    assert "EMOTIONAL ASSOCIATIONS" not in ctx.metadata["persona_prompt"]
    await turn_module.UpdateMoodStep().run(ctx)
    assert ctx.metadata["mood_state"] is state

    captured = {}

    async def capture(_db, **kwargs):
        captured.update(kwargs)

    import chatbot.soul.emotional_associations as association_module
    from chatbot.perf.durable_handlers import _apply_emotional_observations

    monkeypatch.setattr(association_module, "apply_emotional_associations", capture)
    stamp = datetime.now(timezone.utc)
    await _apply_emotional_observations(
        object(),
        SimpleNamespace(turn_id="roleplay-turn", session_id=uuid.uuid4(), job_id=uuid.uuid4()),
        SimpleNamespace(user_id=USER_ID, companion_id="companion-roleplay", conversation_mode="roleplay"),
        SimpleNamespace(id=uuid.uuid4(), content="Let's talk about the archive."),
        SimpleNamespace(id=uuid.uuid4(), content="That fictional scene mattered.", created_at=stamp),
        SimpleNamespace(history_allowed=True),
        {"emotional_observations": [{
            "subject": "fictional scene", "emotion": "moved", "interpretation": "The scene mattered.",
            "source": "companion_expressed", "excerpt": "That fictional scene mattered.",
            "role": "assistant", "target_kind": "event", "significance": "significant",
            "experience_ref": "", "unresolved": None, "revises": "", "status": "real",
        }]},
    )
    assert captured["source"].in_roleplay is True
