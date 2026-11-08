"""Phase 4.3a — persistent conversational threads.

Focused tests: the pure reconciliation and prompt views, extraction validation
(mocked provider only), the SQLite-backed persistence/isolation/replay
behaviour, and one end-to-end acceptance run through the real durable handler.
No network, no real provider. A disposable PostgreSQL check of the additive
migration runs only when ``PHASE0_PG_URL`` is set.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from chatbot.db import models
from chatbot.memory import thread_observation as to_mod
from chatbot.memory.llm_extraction import LLMMemoryExtractor
from chatbot.memory.thread_observation import (
    validate_thread_observations,
)
from chatbot.soul import conversational_threads as ct
from chatbot.soul.conversational_threads import (
    ThreadObservation,
    apply_thread_observations,
    known_threads_text,
    load_open_threads,
    open_threads_text,
    reconcile_threads,
)
from test_timeline_migration import fresh_database, needs_postgres  # noqa: F401

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
USER_A = uuid.UUID("abcdef01-0000-4000-8000-000000000001")
USER_B = uuid.UUID("abcdef02-0000-4000-8000-000000000002")
COMPANION_A = "11111111-1111-4111-8111-111111111111"
COMPANION_B = "22222222-2222-4222-8222-222222222222"
ROOM_A = "room-a"
ROOM_B = "room-b"


def obs(subject="finishing the treehouse app", *, ownership="shared", kind="subject", **kw):
    fields = dict(
        ownership=ownership,
        kind=kind,
        subject=subject,
        status="open",
        topic="treehouse",
        importance="significant",
        excerpt="let's finish the treehouse app this week",
        role="user",
    )
    fields.update(kw)
    return ThreadObservation(**fields)


def open_thread(thread_id="t1", subject_key="finishing treehouse app", **kw):
    fields = dict(
        thread_id=thread_id,
        status="open",
        subject_key=subject_key,
        summary="Finishing the treehouse app",
        ownership="shared",
        kind="subject",
        topic="treehouse",
        importance="significant",
        source_evidence=[{"excerpt": "let's finish it", "source_at": T0.isoformat()}],
        last_discussed_at=T0.isoformat(),
    )
    fields.update(kw)
    return fields


# --------------------------------------------------------------------------
# Pure reconciliation
# --------------------------------------------------------------------------


def test_repeated_discussion_reinforces_instead_of_duplicating():
    ops, rejected = reconcile_threads([], [obs()], job_id="j1", source_at=T0)
    assert [op["op"] for op in ops] == ["create"]
    created = ops[0]["thread"]
    ops2, _ = reconcile_threads([open_thread(created["thread_id"])], [obs()], job_id="j2", source_at=T0)
    assert [op["op"] for op in ops2] == ["reinforce"]
    assert ops2[0]["thread_id"] == created["thread_id"]


def test_reworded_subject_is_not_a_duplicate():
    existing = [open_thread(subject_key="finishing treehouse app")]
    reworded = obs("the treehouse app we are finishing")
    ops, _ = reconcile_threads(existing, [reworded], job_id="j1", source_at=T0)
    assert [op["op"] for op in ops] == ["reinforce"]


def test_a_different_subject_creates_its_own_thread():
    existing = [open_thread(subject_key="finishing treehouse app")]
    ops, _ = reconcile_threads(existing, [obs("your new job at the bakery")], job_id="j1", source_at=T0)
    assert [op["op"] for op in ops] == ["create"]


def test_resolution_closes_the_matched_thread():
    existing = [open_thread(subject_key="finishing treehouse app")]
    ops, _ = reconcile_threads(
        existing,
        [obs(status="resolved", key="finishing treehouse app", excerpt="we shipped it!")],
        job_id="j1",
        source_at=T0,
    )
    assert [op["op"] for op in ops] == ["resolve"]


def test_resolution_without_a_matching_open_thread_is_rejected():
    ops, rejected = reconcile_threads([], [obs(status="resolved", key="finishing treehouse app")], job_id="j1", source_at=T0)
    assert ops == []
    assert rejected and rejected[0][1] == "no_such_open_thread"


def test_supersede_closes_the_old_thread_and_opens_a_new_one():
    existing = [open_thread(subject_key="finishing treehouse app")]
    ops, _ = reconcile_threads(
        existing,
        [obs("rewriting the treehouse app in rust", supersedes="finishing treehouse app")],
        job_id="j1",
        source_at=T0,
    )
    assert [op["op"] for op in ops] == ["supersede"]
    assert ops[0]["thread_id"] == "t1"
    assert ops[0]["superseded_by"] == ops[0]["thread"]["thread_id"]


def test_unknown_supersede_target_is_rejected():
    ops, rejected = reconcile_threads([], [obs("new framing", supersedes="ghost thread")], job_id="j1", source_at=T0)
    assert ops == []
    assert rejected[0][1] == "no_such_open_thread"


def test_thread_ids_are_deterministic_so_a_replayed_receipt_reapplies_the_same_create():
    ops_a, _ = reconcile_threads([], [obs()], job_id="job-9", source_at=T0)
    ops_b, _ = reconcile_threads([], [obs()], job_id="job-9", source_at=T0)
    assert ops_a[0]["thread_id"] == ops_b[0]["thread_id"]
    assert ops_a[0]["thread_id"] != str(uuid.uuid4())  # not a fresh random id


def test_two_observations_for_one_subject_in_one_batch_stay_one_thread():
    ops, _ = reconcile_threads([], [obs(), obs("finishing the treehouse app together")], job_id="j1", source_at=T0)
    created = [op for op in ops if op["op"] == "create"]
    reinforced = [op for op in ops if op["op"] == "reinforce"]
    assert len(created) == 1 and len(reinforced) == 1
    assert reinforced[0]["thread_id"] == created[0]["thread_id"]


# --------------------------------------------------------------------------
# Prompt and extractor views
# --------------------------------------------------------------------------


def test_known_threads_text_is_bounded_and_formatted():
    threads = [open_thread(thread_id=f"t{i}") for i in range(50)]
    text = known_threads_text(threads)
    assert text.startswith("finishing treehouse app: Finishing the treehouse app")
    assert len(text) <= ct.MAX_KNOWN_THREADS_CHARS


def test_open_threads_text_renders_bounded_business_and_none_when_empty():
    assert open_threads_text([]) is None
    text = open_threads_text([open_thread()], now=T0)
    assert "Finishing the treehouse app" in text
    assert "shared subject" in text
    assert len(text) <= ct.MAX_PROMPT_CHARS
    # Resolved threads never reach the renderer (retrieval filters them, but the
    # pure function must also refuse).
    assert open_threads_text([open_thread(status="resolved")]) is None


def test_a_superseded_thread_is_not_rendered_even_if_handed_over():
    old = open_thread(status="superseded")
    assert open_threads_text([old], now=T0) is None


# --------------------------------------------------------------------------
# Extraction validation (mocked provider only)
# --------------------------------------------------------------------------

USER_TURN = "I really want us to finish the treehouse app this week. What do you think about jazz?"
ASSISTANT_TURN = "I would love to explore jazz harmony with you sometime. Let's build that app."


def _validated(raw, *, user=USER_TURN, assistant=ASSISTANT_TURN, known=""):
    accepted, rejected = validate_thread_observations(
        [raw], user_message=user, assistant_message=assistant, known_threads=known
    )
    return accepted, rejected


def test_a_shared_open_thread_with_verbatim_user_evidence_is_accepted():
    accepted, rejected = _validated(
        {
            "ownership": "shared", "kind": "subject", "subject": "Finishing the treehouse app",
            "topic": "treehouse", "status": "open", "importance": "significant",
            "evidence": "finish the treehouse app this week",
        }
    )
    assert not rejected and accepted[0]["ownership"] == "shared"
    assert accepted[0]["key"] == ""


def test_a_companion_thread_must_be_grounded_in_the_companions_own_words():
    accepted, rejected = _validated(
        {
            "ownership": "companion", "kind": "interest", "subject": "Exploring jazz harmony",
            "evidence": "explore jazz harmony with you", "status": "open", "importance": "ordinary",
        }
    )
    assert not rejected and accepted[0]["role"] == "assistant"
    _, rejected = _validated(
        {
            "ownership": "companion", "kind": "interest", "subject": "Finishing the treehouse app",
            "evidence": "finish the treehouse app this week", "status": "open",
        }
    )
    assert rejected[0].reason == "evidence_not_in_assistant_reply"


def test_a_user_thread_must_be_grounded_in_the_users_words():
    _, rejected = _validated(
        {
            "ownership": "user", "kind": "request", "subject": "Exploring jazz harmony",
            "evidence": "explore jazz harmony with you", "status": "open",
        }
    )
    assert rejected[0].reason == "evidence_not_in_user_message"


def test_a_roleplay_frame_never_creates_threads():
    accepted, rejected = _validated(
        {
            "ownership": "shared", "kind": "subject", "subject": "The dragon quest",
            "evidence": "finish the treehouse app this week", "status": "open",
        },
        user="*draws sword* let's finish the treehouse app, brave knight!",
    )
    assert not accepted and rejected[0].reason == "roleplay"


def test_fiction_joke_and_hypothetical_evidence_is_rejected_but_questions_are_allowed():
    for user, reason in (
        ("In a story, we finish the treehouse app this week.", "fiction"),
        ("Haha just kidding, finish the treehouse app this week.", "joke"),
        ("If I had time, we would finish the treehouse app this week.", "hypothetical"),
    ):
        _, rejected = _validated(
            {
                "ownership": "shared", "kind": "subject", "subject": "Finishing the treehouse app",
                "evidence": "finish the treehouse app this week", "status": "open",
            },
            user=user,
        )
        assert rejected[0].reason == reason
    accepted, _ = _validated(
        {
            "ownership": "shared", "kind": "subject", "subject": "Whether the treehouse app ships",
            "evidence": "finish the treehouse app this week", "status": "open",
        },
        user="Will we finish the treehouse app this week?",
    )
    assert accepted


def test_known_keys_are_honoured_and_unknown_ones_dropped():
    known = "finishing treehouse app: Finishing the treehouse app\n"
    accepted, _ = _validated(
        {
            "ownership": "shared", "kind": "subject", "subject": "Finishing the treehouse app",
            "evidence": "finish the treehouse app this week", "status": "open", "key": "finishing treehouse app",
        },
        known=known,
    )
    assert accepted[0]["key"] == "finishing treehouse app"
    accepted, _ = _validated(
        {
            "ownership": "shared", "kind": "subject", "subject": "Finishing the treehouse app",
            "evidence": "finish the treehouse app this week", "status": "open", "key": "hallucinated key",
        },
        known=known,
    )
    assert accepted[0]["key"] == ""
    _, rejected = _validated(
        {
            "ownership": "shared", "kind": "subject", "subject": "A new framing",
            "evidence": "finish the treehouse app this week", "status": "open",
            "supersedes": "hallucinated key",
        },
        known=known,
    )
    assert rejected[0].reason == "unknown_supersedes"


def test_invented_details_are_rejected():
    _, rejected = _validated(
        {
            "ownership": "user", "kind": "subject", "subject": "Finishing the treehouse app in Tokyo",
            "evidence": "finish the treehouse app this week", "status": "open",
        }
    )
    assert rejected[0].reason == "unsupported_content"


def test_updates_are_bounded_per_turn():
    raw = {
        "ownership": "shared", "kind": "subject", "subject": "Finishing the treehouse app",
        "evidence": "finish the treehouse app this week", "status": "open",
    }
    accepted, rejected = validate_thread_observations(
        [raw] * 5, user_message=USER_TURN, assistant_message=ASSISTANT_TURN
    )
    assert len(accepted) == 1
    assert len(rejected) == 4


# --------------------------------------------------------------------------
# Extraction ride-along: one provider call carries everything
# --------------------------------------------------------------------------


async def test_threads_ride_on_the_same_single_extraction_call():
    from test_memory_llm_extraction import FakeProvider

    provider = FakeProvider(
        {
            "memories": [],
            "experiences": [],
            "self": [],
            "threads": [
                {
                    "ownership": "shared", "kind": "subject",
                    "subject": "Finishing the treehouse app", "topic": "treehouse",
                    "status": "open", "importance": "significant",
                    "evidence": "finish the treehouse app this week",
                }
            ],
        }
    )
    extractor = LLMMemoryExtractor(storage=_Recorder(), provider=provider, model="m")
    outcome = await extractor.extract_entries(
        session_id="s", user_message=USER_TURN, provider_response=ASSISTANT_TURN, turn_id="t"
    )
    assert len(provider.calls) == 1  # no extra model call for threads
    assert len(outcome.thread_updates) == 1
    assert outcome.thread_updates[0]["subject"] == "Finishing the treehouse app"
    # KNOWN THREADS reach the same prompt when supplied.
    await extractor.extract_entries(
        session_id="s", user_message=USER_TURN, provider_response=ASSISTANT_TURN,
        turn_id="t", known_threads="finishing treehouse app: Finishing the treehouse app",
    )
    assert "KNOWN THREADS" in provider.calls[-1]["prompt"]
    assert "finishing treehouse app" in provider.calls[-1]["prompt"]


class _Recorder:
    async def persist(self, session_id, entries):
        return list(entries)


# --------------------------------------------------------------------------
# Database: persistence, isolation, staleness, replay idempotence
# --------------------------------------------------------------------------

_TABLES = [
    models.User, models.Companion, models.CompanionIdentityModel,
    models.CompanionRelationshipModel, models.Room, models.ConversationalThread,
]


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'threads.db'}")
    async with engine.begin() as connection:
        for model in _TABLES:
            await connection.run_sync(lambda sync, m=model: m.__table__.create(sync))
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        db.add(models.User(id=USER_A, external_id="a", display_name="a", username="a",
                           email="a@example.com", password_hash="h"))
        db.add(models.User(id=USER_B, external_id="b", display_name="b", username="b",
                           email="b@example.com", password_hash="h"))
        for cid, user in ((COMPANION_A, USER_A), (COMPANION_B, USER_A), ("c-b-user", USER_B)):
            db.add(models.Companion(companion_id=cid, user_id=user, name=cid, persona="p",
                                    description="d", backstory="b",
                                    personality_sliders={"openness": 0.7}))
        db.add(models.Room(room_id=ROOM_A, user_id=str(USER_A), room_type="chat",
                           display_name="A", persist_to_durable=True, memory_scope="room"))
        db.add(models.Room(room_id=ROOM_B, user_id=str(USER_A), room_type="chat",
                           display_name="B", persist_to_durable=True, memory_scope="room"))
        await db.commit()
    yield session_factory
    await engine.dispose()


async def _apply(factory, observations, *, companion=COMPANION_A, user=USER_A,
                 room_id=None, job_id="j1", at=T0, context=None):
    async with factory() as db:
        change = await apply_thread_observations(
            db,
            user_id=user,
            companion_id=companion,
            room_id=room_id,
            observations=observations,
            job_id=job_id,
            turn_id="turn-1",
            session_id="session-1",
            message_id="msg-1",
            source_at=at,
            relationship_context=context,
        )
        await db.commit()
        return change


async def _open(factory, *, companion=COMPANION_A, user=USER_A, room_id=None, now=None):
    async with factory() as db:
        return await load_open_threads(
            db, user_id=user, companion_id=companion, room_id=room_id,
            now=now or (T0 + timedelta(days=1)),  # never the real clock: test data is fixed
            check_ownership=False,
        )


async def test_apply_creates_a_thread_with_evidence_and_relationship_context(factory):
    change = await _apply(factory, [obs()], context={"depth": 0.5, "completed_turns": 4})
    assert [a["op"] for a in change.applied] == ["created"]
    threads = await _open(factory)
    assert len(threads) == 1
    row = threads[0]
    assert row["ownership"] == "shared" and row["kind"] == "subject"
    assert row["summary"] == "finishing the treehouse app"
    assert row["source_evidence"][0]["turn_id"] == "turn-1"
    assert row["source_evidence"][0]["message_id"] == "msg-1"
    assert row["source_evidence"][0]["excerpt"].startswith("let's finish")
    assert row["relationship_context"] == {"depth": 0.5, "completed_turns": 4}


async def test_replaying_the_same_job_creates_no_duplicate(factory):
    await _apply(factory, [obs()], job_id="job-7")
    # A replayed receipt reinforces at most: it never creates a second row.
    change = await _apply(factory, [obs()], job_id="job-7")
    assert all(a["op"] != "created" for a in change.applied)
    assert len(await _open(factory)) == 1


async def test_a_second_discussion_reinforces_and_bounded_evidence_is_kept(factory):
    await _apply(factory, [obs()], job_id="job-1", at=T0)
    for index in range(10):
        await _apply(
            factory, [obs(subject="still finishing the treehouse app")],
            job_id=f"job-{index + 2}", at=T0 + timedelta(days=index + 1),
        )
    threads = await _open(factory, now=T0 + timedelta(days=30))
    assert len(threads) == 1
    assert len(threads[0]["source_evidence"]) == ct.MAX_EVIDENCE
    # SQLite drops the tz offset on round-trip; the instant is what matters.
    assert threads[0]["last_discussed_at"].replace("+00:00", "") == (
        (T0 + timedelta(days=10)).isoformat().replace("+00:00", "")
    )


async def test_other_users_companions_and_rooms_cannot_access_a_thread(factory):
    await _apply(factory, [obs()], room_id=ROOM_A)
    # Same room sees it.
    assert len(await _open(factory, room_id=ROOM_A)) == 1
    # Another room does not.
    assert await _open(factory, room_id=ROOM_B) == []
    # A private session does not see a room thread.
    assert await _open(factory, room_id=None) == []
    # Another companion does not.
    assert await _open(factory, companion=COMPANION_B, room_id=ROOM_A) == []
    # Another user does not (their own companion with the same id space).
    with pytest.raises(PermissionError):
        async with factory() as db:
            await load_open_threads(
                db, user_id=USER_B, companion_id=COMPANION_A, room_id=ROOM_A
            )


async def test_a_private_thread_follows_the_pair_but_a_room_conversation_reinforces_it(factory):
    await _apply(factory, [obs()], room_id=None)
    # Visible in a later private session...
    assert len(await _open(factory, room_id=None)) == 1
    # ...and in a durable room with the same companion...
    assert len(await _open(factory, room_id=ROOM_A)) == 1
    # ...and discussing it there reinforces the private thread, not a copy:
    # still exactly one row, still privately scoped (it follows the pair).
    await _apply(factory, [obs()], room_id=ROOM_A, job_id="job-2", at=T0 + timedelta(days=1))
    async with factory() as db:
        count = (
            await db.scalar(sa.select(sa.func.count()).select_from(models.ConversationalThread))
        )
    assert count == 1
    private = await _open(factory, room_id=None, now=T0 + timedelta(days=2))
    assert len(private) == 1 and private[0]["room_id"] is None


async def test_resolved_and_stale_threads_stop_appearing_as_unfinished(factory):
    await _apply(factory, [obs()], job_id="job-1")
    # Resolution in a later turn stops the presentation.
    await _apply(
        factory,
        [obs(status="resolved", key="finishing treehouse app", excerpt="we shipped it")],
        job_id="job-2", at=T0 + timedelta(days=1),
    )
    assert await _open(factory, now=T0 + timedelta(days=2)) == []
    # A resolved thread is not resurrected as a duplicate by later discussion.
    change = await _apply(factory, [obs()], job_id="job-3", at=T0 + timedelta(days=3))
    assert [a["op"] for a in change.applied] == ["created"]
    assert len(await _open(factory, now=T0 + timedelta(days=4))) == 1


async def test_staleness_window_depends_on_importance(factory):
    await _apply(factory, [obs(importance="ordinary")], job_id="job-1")
    await _apply(
        factory, [obs("planning the big trip", importance="significant")],
        job_id="job-2", at=T0 + timedelta(days=1),
    )
    later = T0 + timedelta(days=30)
    threads = await _open(factory, now=later)
    assert [t["importance"] for t in threads] == ["significant"]  # ordinary went stale
    later_still = T0 + timedelta(days=50)
    assert await _open(factory, now=later_still) == []  # even significant went stale


# --------------------------------------------------------------------------
# Acceptance: the real durable handler persists and replays threads
# --------------------------------------------------------------------------

_HANDLER_TABLES = _TABLES + [models.Session, models.Message, models.BackgroundJob, models.Episode]


def _respond(*threads):
    return {"memories": [], "experiences": [], "self": [], "threads": list(threads)}


@pytest.fixture
async def handler_env(tmp_path, monkeypatch):
    from test_memory_llm_extraction import FakeProvider

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'handler.db'}")
    async with engine.begin() as connection:
        for model in _HANDLER_TABLES:
            await connection.run_sync(lambda sync, m=model: m.__table__.create(sync))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        db.add(models.User(id=USER_A, external_id="a", display_name="a", username="a",
                           email="a@example.com", password_hash="h"))
        db.add(models.Companion(companion_id=COMPANION_A, user_id=USER_A, name="c",
                                persona="p", description="d", backstory="b",
                                personality_sliders={"openness": 0.7}))
        db.add(models.CompanionIdentityModel(companion_id=COMPANION_A))
        db.add(models.CompanionRelationshipModel(companion_id=COMPANION_A, depth=0.4,
                                                 completed_turns=3))
        await db.commit()

    state = {"provider": None}

    def set_response(response):
        from chatbot.perf import durable_handlers

        state["provider"] = FakeProvider(response)

        def fake_from_settings(**kwargs):
            return durable_handlers.LLMMemoryExtractor(
                storage=kwargs.get("storage"),
                provider=state["provider"],
                model="fake",
                propagate_errors=kwargs.get("propagate_errors", False),
            )

        monkeypatch.setattr(durable_handlers.LLMMemoryExtractor, "from_settings", fake_from_settings)

    set_response(_respond())
    yield {"factory": factory, "set_response": set_response, "provider": state}
    await engine.dispose()


async def _seed_turn(env, turn_id, *, user_message, assistant_message, companion=COMPANION_A):
    factory = env["factory"]
    sid = uuid.uuid5(uuid.NAMESPACE_URL, f"session:{turn_id}")
    async with factory() as db:
        session = models.Session(id=sid, user_id=USER_A, companion_id=companion, title="t")
        db.add(session)
        db.add(models.Message(session_id=sid, user_id=USER_A, role="user",
                              content=user_message, turn_id=turn_id))
        db.add(models.Message(session_id=sid, user_id=USER_A, role="assistant",
                              content=assistant_message, turn_id=turn_id,
                              companion_id=companion))
        job = models.BackgroundJob(
            session_id=sid, user_id=USER_A, companion_id=companion,
            task_type="memory_extract", turn_id=turn_id,
            payload={"session_id": str(sid), "user_id": str(USER_A),
                     "companion_id": companion, "turn_id": turn_id},
        )
        db.add(job)
        await db.commit()
        return job.job_id


async def _run_job(env, job_id):
    from chatbot.config import Settings
    from chatbot.perf.durable_handlers import make_durable_memory_extract_handler

    class _Job:
        pass

    async with env["factory"]() as db:
        stored = await db.get(models.BackgroundJob, job_id)
        job = _Job()
        job.job_id = stored.job_id
        job.session_id = stored.session_id
        job.user_id = stored.user_id
        job.companion_id = stored.companion_id
        job.turn_id = stored.turn_id
        job.attempt_count = stored.attempt_count
    handler = make_durable_memory_extract_handler(
        env["factory"], registry=None, settings=Settings(_env_file=None)
    )
    await handler(job)
    async with env["factory"]() as db:
        return await db.get(models.BackgroundJob, job_id)


async def test_the_acceptance_scenario_across_sessions(handler_env):
    env = handler_env
    # Turn 1: the companion and user discuss an ongoing project; the turn ends
    # before it is completed.
    env["set_response"](
        _respond(
            {
                "ownership": "shared", "kind": "subject",
                "subject": "Finishing the treehouse app", "topic": "treehouse",
                "status": "open", "importance": "significant",
                "evidence": "finish the treehouse app this week",
            }
        )
    )
    job1 = await _seed_turn(
        env, "turn-1",
        user_message="We should finish the treehouse app this week.",
        assistant_message="Yes! Let's finish the treehouse app this week.",
    )
    stored = await _run_job(env, job1)
    assert stored.status == "succeeded"

    # A later session: the unfinished subject remains available, with source.
    threads = await _open(env["factory"], now=T0)
    assert len(threads) == 1
    row = threads[0]
    assert row["summary"] == "Finishing the treehouse app"
    assert row["source_evidence"][0]["turn_id"] == "turn-1"
    assert row["status"] == "open" and row["importance"] == "significant"
    assert row["relationship_context"] == {"depth": 0.4, "completed_turns": 3}

    # Repeating the discussion does not create a duplicate.
    env["set_response"](
        _respond(
            {
                "ownership": "shared", "kind": "subject",
                "subject": "Finishing the treehouse app", "topic": "treehouse",
                "status": "open", "importance": "significant",
                "evidence": "finish the treehouse app this week",
                "key": "finishing treehouse app",
            }
        )
    )
    job2 = await _seed_turn(
        env, "turn-2",
        user_message="Still thinking about the treehouse app — should we finish it this week?",
        assistant_message="We will finish the treehouse app this week.",
    )
    await _run_job(env, job2)
    assert len(await _open(env["factory"], now=T0 + timedelta(days=1))) == 1

    # The KNOWN THREADS block reached the second extraction prompt.
    assert "finishing treehouse app" in env["provider"]["provider"].calls[-1]["prompt"]

    # A resolved subject stops appearing as unfinished business.
    env["set_response"](
        _respond(
            {
                "ownership": "shared", "kind": "subject",
                "subject": "Finishing the treehouse app",
                "status": "resolved", "key": "finishing treehouse app",
                "importance": "significant", "evidence": "we shipped it",
            }
        )
    )
    job3 = await _seed_turn(
        env, "turn-3",
        user_message="The treehouse app shipped today — we shipped it!",
        assistant_message="We finished the treehouse app — shipped it!",
    )
    await _run_job(env, job3)
    assert await _open(env["factory"], now=T0 + timedelta(days=2)) == []


async def test_roleplay_turns_never_contaminate_real_threads(handler_env):
    env = handler_env
    env["set_response"](
        _respond(
            {
                "ownership": "shared", "kind": "subject",
                "subject": "The dragon quest", "topic": "quest",
                "status": "open", "importance": "significant",
                "evidence": "finish the treehouse app this week",
            }
        )
    )
    job = await _seed_turn(
        env, "turn-rp",
        user_message="*draws sword* Let's finish the treehouse app this week, brave knight!",
        assistant_message="*nods* We will finish the treehouse app, my liege.",
    )
    await _run_job(env, job)
    assert await _open(env["factory"], now=T0) == []


# --------------------------------------------------------------------------
# Migration (PostgreSQL only)
# --------------------------------------------------------------------------


def test_conversational_threads_table_exists_in_the_schema():
    """The model is part of the metadata (SQLite create also proves this)."""
    assert models.ConversationalThread.__tablename__ in models.Base.metadata.tables


@needs_postgres
async def test_postgres_migration_creates_the_table(fresh_database):
    db = await fresh_database()
    async with db.factory() as session:
        rows = (
            await session.execute(
                sa.text("SELECT COUNT(*) FROM conversational_threads")
            )
        ).scalar()
        assert rows == 0
    # The migrated schema matches the model: no autogenerate diffs for the table.
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    def _diffs(sync_connection):
        context = MigrationContext.configure(sync_connection)
        return compare_metadata(context, models.Base.metadata)

    diffs = await db.run_sync(_diffs)
    assert not [d for d in diffs if "conversational_threads" in repr(d)]


@needs_postgres
async def test_postgres_downgrade_refuses_to_discard_threads_and_upgrade_restores(fresh_database):
    from test_timeline_migration import alembic as run_alembic
    from sqlalchemy import make_url

    db = await fresh_database()
    url = make_url(db.url).render_as_string(hide_password=False)
    async with db.factory() as session:
        session.add(
            models.User(id=USER_A, external_id="x", display_name="U", username="u",
                        email="u@example.com", password_hash="h")
        )
        session.add(
            models.Companion(companion_id=COMPANION_A, user_id=USER_A, name="C", persona="p",
                             description="d", backstory="b")
        )
        await session.flush()  # parents first: the thread's FKs need them persisted
        session.add(
            models.ConversationalThread(
                user_id=USER_A, companion_id=COMPANION_A, ownership="shared",
                kind="subject", subject_key="finishing treehouse app",
                summary="Finishing the treehouse app", status="open",
                importance="significant", source_evidence=[],
                last_discussed_at=T0,
            )
        )
        await session.commit()
    refused = run_alembic(url, "downgrade", "-1")
    assert refused.returncode != 0
    assert "Refusing to discard recorded conversational threads" in refused.stderr
    async with db.factory() as session:
        await session.execute(sa.text("DELETE FROM conversational_threads"))
        await session.commit()
    assert run_alembic(url, "downgrade", "-1").returncode == 0
    assert run_alembic(url, "upgrade", "head").returncode == 0

