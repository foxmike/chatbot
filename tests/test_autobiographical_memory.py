"""Phase 3.1 — autobiographical shared experiences.

The real durable worker and handler run against a throwaway database with a
mocked extraction model; no network and no paid inference. Experiences are
ordinary ``semantic_facts`` rows (``kind == "shared_experience"``) in the Phase 2
pipeline, so these tests cover only what Task 3.1 adds.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
import sqlalchemy as sa

from test_completion_persistence import env, USER_ID  # noqa: F401  (pytest fixture)
from phase2_helpers import extraction_registry
from chatbot.api import conversation
from chatbot.config import Settings
from chatbot.db import models
from chatbot.memory.autobiography import (
    EXPERIENCE_KIND,
    get_experience,
    list_experiences,
    validate_experiences,
)
from chatbot.memory.llm_extraction import parse_experiences
from chatbot.perf.durable_handlers import make_durable_memory_extract_handler
from chatbot.perf.durable_worker import DurableJobWorker

OTHER_USER_ID = uuid.UUID("abcdef02-0000-4000-8000-000000000002")
DAY = datetime(2026, 1, 10, 9, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Scenario builders
# --------------------------------------------------------------------------


def experience(narrative: str, evidence: str, **overrides) -> dict:
    return {
        "title": narrative,
        "narrative": narrative,
        "participants": ["user", "companion"],
        "significance": "significant",
        "evidence": evidence,
        "status": "real",
        "origin": "conversation",
        "topics": [],
        "revises": None,
        "confidence": 0.9,
        **overrides,
    }


def answer(*experiences: dict, memories: list | None = None) -> dict:
    return {"memories": memories or [], "experiences": list(experiences)}


async def add_companion(env, companion_id: str, user_id=USER_ID) -> None:
    async with env.factory() as db:
        db.add(
            models.Companion(
                companion_id=companion_id, user_id=user_id, name=companion_id,
                persona="p", description="d", backstory="b",
            )
        )
        await db.flush()
        db.add(models.CompanionIdentityModel(companion_id=companion_id))
        db.add(models.CompanionRelationshipModel(companion_id=companion_id))
        await db.commit()


async def add_user(env, user_id) -> None:
    async with env.factory() as db:
        db.add(
            models.User(
                id=user_id, external_id=f"x{user_id.hex}", display_name="O", username=f"o{user_id.hex[:6]}",
                email=f"{user_id.hex[:6]}@example.com", password_hash="h",
            )
        )
        await db.commit()


async def add_session(env, companion_id: str | None, *, user_id=USER_ID, room_id=None) -> uuid.UUID:
    session_id = uuid.UUID(f"b{uuid.uuid4().hex[1:]}")
    async with env.factory() as db:
        db.add(
            models.Session(
                id=session_id, user_id=user_id, title="s", companion_id=companion_id, room_id=room_id
            )
        )
        await db.commit()
    return session_id


async def add_room(env, *, scene: bool = False, durable: bool = True, companion_id=None) -> str:
    room_id = f"room-{uuid.uuid4().hex[:8]}"
    async with env.factory() as db:
        db.add(
            models.Room(
                room_id=room_id, user_id=str(USER_ID), companion_id=companion_id, room_type="chat",
                display_name="r", persist_to_durable=durable, memory_scope="room",
                scene_id="scene-1" if scene else None,
            )
        )
        await db.commit()
    return room_id


async def say(env, session_id, user_text, assistant_text, *, at, companion_id=None, user_id=USER_ID):
    """Persist one completed turn and its durable job, as the completion hook does."""
    turn_id = uuid.uuid4().hex
    async with env.factory() as db:
        for role, text, offset in (("user", user_text, 0), ("assistant", assistant_text, 1)):
            db.add(
                models.Message(
                    session_id=session_id, user_id=user_id, companion_id=companion_id, role=role,
                    content=text, turn_id=turn_id, created_at=at + timedelta(seconds=offset),
                )
            )
        await db.flush()
        await conversation.schedule_memory_job(
            db, session_id=session_id, user_id=user_id, companion_id=companion_id, turn_id=turn_id
        )
        await db.commit()
    return turn_id


def worker_for(env, response):
    registry, provider = extraction_registry(response)
    embeddings = SimpleNamespace(
        embed=AsyncMock(side_effect=lambda texts: [[0.0] * 1536 for _ in texts])
    )
    handler = make_durable_memory_extract_handler(
        env.factory, registry=registry, settings=Settings(_env_file=None), embeddings=embeddings
    )
    return DurableJobWorker(env.factory, handlers={"memory_extract": handler}), provider


async def process(env, response):
    worker, provider = worker_for(env, response)
    for job in await worker.claim_due_jobs():
        await worker._run_one(job)
    return provider


async def experiences_in(env) -> list[models.SemanticFact]:
    async with env.factory() as db:
        rows = await db.scalars(
            sa.select(models.SemanticFact)
            .where(models.SemanticFact.extraction_metadata["kind"].as_string() == EXPERIENCE_KIND)
            .order_by(models.SemanticFact.last_reinforced_at, models.SemanticFact.id)
        )
        return list(rows.all())


async def visible(env, session_id, **kwargs):
    async with env.factory() as db:
        return await list_experiences(db, session_id=session_id, **kwargs)


FATHER = "My father passed away last week and I wanted to talk it through with you."
FATHER_EVIDENCE = "My father passed away last week"
FATHER_NARRATIVE = "User told the companion that their father passed away last week."


# --------------------------------------------------------------------------
# Significant experience persistence and source attribution
# --------------------------------------------------------------------------


async def test_a_significant_experience_persists_with_owner_companion_chronology_and_evidence(env):
    await add_companion(env, "comp-a")
    session = await add_session(env, "comp-a")
    await say(env, session, FATHER, "I'm so sorry. I'm here.", at=DAY, companion_id="comp-a")

    provider = await process(
        env,
        answer(
            experience(FATHER_NARRATIVE, FATHER_EVIDENCE, topics=["father", "loss"]),
        ),
    )

    assert len(provider.calls) == 1  # the one existing extraction call; nothing extra
    [row] = await experiences_in(env)
    meta = row.extraction_metadata
    assert row.user_id == USER_ID
    assert row.fact_text == FATHER_NARRATIVE
    assert row.source_type == "explicit"
    assert row.embedding is not None  # retrievable through the existing vector store
    assert row.lifecycle_status == "active"
    assert meta["companion_id"] == "comp-a"
    assert meta["scope"]["level"] == "companion" and meta["scope"]["companion_id"] == "comp-a"
    assert meta["experience"]["participants"] == ["user", "companion"]
    assert meta["experience"]["significance"] == "significant"
    assert meta["experience"]["observed"] is True

    async with env.factory() as db:
        user_message = await db.scalar(
            sa.select(models.Message).where(
                models.Message.session_id == session, models.Message.role == "user"
            )
        )
        [evidence] = (await db.scalars(sa.select(models.MemoryEvidence))).all()
        assert row.source_message_id == user_message.id
        assert evidence.memory_id == row.id and evidence.user_id == USER_ID
        assert evidence.source_message_id == user_message.id
        assert evidence.session_id == session
        assert evidence.evidence == FATHER_EVIDENCE
        assert evidence.source_at.replace(tzinfo=timezone.utc) == DAY

        record = await get_experience(db, session_id=session, experience_id=row.id)
    assert record.occurred_at == DAY
    assert record.companion_id == "comp-a" and record.session_id == str(session)
    assert [e.excerpt for e in record.evidence] == [FATHER_EVIDENCE]
    assert record.evidence[0].source_message_id == str(user_message.id)


async def test_ordinary_chatter_is_not_autobiography(env):
    await add_companion(env, "comp-a")
    session = await add_session(env, "comp-a")
    await say(env, session, "I had a sandwich for lunch.", "Sounds good.", at=DAY, companion_id="comp-a")

    await process(
        env,
        answer(experience("User had a sandwich for lunch.", "I had a sandwich for lunch",
                          significance="ordinary")),
    )

    assert await experiences_in(env) == []
    async with env.factory() as db:  # the turn episode is still recorded by Phase 2
        assert await db.scalar(sa.select(sa.func.count()).select_from(models.Episode)) == 1


# --------------------------------------------------------------------------
# Cross-session continuity and related-experience linking
# --------------------------------------------------------------------------


async def test_experiences_connect_across_sessions_in_chronological_order(env):
    await add_companion(env, "comp-a")
    first = await add_session(env, "comp-a")
    await say(env, first, FATHER, "I'm so sorry.", at=DAY, companion_id="comp-a")
    await process(env, answer(experience(FATHER_NARRATIVE, FATHER_EVIDENCE, topics=["father", "loss"])))

    second = await add_session(env, "comp-a")
    await say(env, second, "Today was the funeral for my father and I felt numb.",
              "That sounds like a heavy day.", at=DAY + timedelta(days=6), companion_id="comp-a")
    await process(
        env,
        answer(experience("User attended the funeral for their father.",
                          "Today was the funeral for my father", topics=["funeral", "father"])),
    )

    third = await add_session(env, "comp-a")
    await say(env, third, "I ran my first marathon on Saturday and finished in four hours.",
              "Congratulations!", at=DAY + timedelta(days=20), companion_id="comp-a")
    await process(
        env,
        answer(experience("User ran their first marathon and finished in four hours.",
                          "I ran my first marathon on Saturday", significance="major",
                          topics=["marathon", "running"])),
    )

    records = await visible(env, third)  # a brand-new session of the same companion
    assert [r.narrative for r in records] == [
        FATHER_NARRATIVE,
        "User attended the funeral for their father.",
        "User ran their first marathon and finished in four hours.",
    ]
    father, funeral, marathon = records
    assert father.related_ids == (funeral.id,)  # linked in both directions
    assert funeral.related_ids == (father.id,)
    assert marathon.related_ids == ()  # no shared topic, no link
    assert marathon.salience == 0.95 and marathon.significance == "major"
    assert [r.occurred_at for r in records] == sorted(r.occurred_at for r in records)
    assert {r.session_id for r in records} == {str(first), str(second), str(third)}


# --------------------------------------------------------------------------
# Revision without destroying history
# --------------------------------------------------------------------------


async def seed_revision(env):
    await add_companion(env, "comp-a")
    first = await add_session(env, "comp-a")
    await say(env, first, "I got the job offer at the hospital!", "Congratulations!",
              at=DAY, companion_id="comp-a")
    await process(
        env,
        answer(experience("User got a job offer at the hospital.", "I got the job offer at the hospital",
                          topics=["job", "hospital"])),
    )
    second = await add_session(env, "comp-a")
    turn = await say(env, second, "Bad news: the hospital withdrew the job offer.",
                     "I'm sorry that fell through.", at=DAY + timedelta(days=21), companion_id="comp-a")
    response = answer(
        experience("User's job offer at the hospital was withdrawn.",
                   "the hospital withdrew the job offer", topics=["job", "hospital"],
                   revises="User got a job offer at the hospital.")
    )
    await process(env, response)
    return first, second, turn, response


async def test_new_information_revises_an_experience_and_keeps_its_history(env):
    first, second, _turn, _response = await seed_revision(env)

    current = await visible(env, second)
    assert [r.narrative for r in current] == ["User's job offer at the hospital was withdrawn."]
    revised = current[0]

    history = await visible(env, second, include_history=True)
    original = next(r for r in history if r.narrative == "User got a job offer at the hospital.")
    assert original.status == "superseded"
    assert original.superseded_by == revised.id
    assert revised.revises_id == original.id
    assert original.id in revised.related_ids

    async with env.factory() as db:
        detail = await get_experience(db, session_id=second, experience_id=revised.id)
        old = await get_experience(db, session_id=second, experience_id=original.id)
        relations = (await db.scalars(sa.select(models.MemoryRelation))).all()
        evidence = (await db.scalars(sa.select(models.MemoryEvidence))).all()
    assert detail.revision_history == (original.id,)
    assert [e.excerpt for e in old.evidence] == ["I got the job offer at the hospital"]  # kept
    assert [(r.relation, str(r.source_id), str(r.target_id)) for r in relations] == [
        ("supersedes", revised.id, original.id)
    ]
    assert len(evidence) == 2


async def test_a_revision_with_no_matching_earlier_experience_is_just_a_new_one(env):
    await add_companion(env, "comp-a")
    session = await add_session(env, "comp-a")
    await say(env, session, FATHER, "I'm so sorry.", at=DAY, companion_id="comp-a")
    await process(
        env,
        answer(experience(FATHER_NARRATIVE, FATHER_EVIDENCE, revises="User won a chess tournament.")),
    )
    [record] = await visible(env, session)
    assert record.status == "active" and record.revises_id is None
    async with env.factory() as db:
        assert await db.scalar(sa.select(sa.func.count()).select_from(models.MemoryRelation)) == 0


async def test_a_late_job_cannot_overturn_something_that_happened_later(env):
    await add_companion(env, "comp-a")
    later = await add_session(env, "comp-a")
    await say(env, later, "I got the job offer at the hospital!", "Congratulations!",
              at=DAY + timedelta(days=30), companion_id="comp-a")
    await process(
        env,
        answer(experience("User got a job offer at the hospital.", "I got the job offer at the hospital",
                          topics=["job", "hospital"])),
    )
    # Processed afterwards, but it happened earlier: it cannot revise the newer experience.
    earlier = await add_session(env, "comp-a")
    await say(env, earlier, "The hospital interview went badly and they ended the process.",
              "I'm sorry.", at=DAY, companion_id="comp-a")
    await process(
        env,
        answer(experience("User's hospital interview process was ended.",
                          "they ended the process", topics=["job", "hospital"],
                          revises="User got a job offer at the hospital.")),
    )

    records = await visible(env, earlier, include_history=True)
    assert [r.status for r in records] == ["active", "active"]
    assert all(r.revises_id is None and r.superseded_by is None for r in records)
    assert all(len(r.related_ids) == 1 for r in records)  # still connected by topic


async def test_an_experience_never_supersedes_a_user_fact(env):
    await add_companion(env, "comp-a")
    session = await add_session(env, "comp-a")
    await say(env, session, "I live in Oslo.", "Nice.", at=DAY, companion_id="comp-a")
    await process(
        env,
        answer(memories=[{"type": "personal_fact", "content": "User lives in Oslo.",
                          "evidence": "I live in Oslo", "status": "real", "confidence": 0.9}]),
    )
    await say(env, session, "I moved to Boston last month and it was a big change.", "That is big.",
              at=DAY + timedelta(days=2), companion_id="comp-a")
    await process(env, answer(experience("User moved to Boston.", "I moved to Boston last month")))

    async with env.factory() as db:
        facts = list(
            (await db.scalars(sa.select(models.SemanticFact).order_by(models.SemanticFact.fact_text))).all()
        )
        assert [(f.fact_text, f.lifecycle_status) for f in facts] == [
            ("User lives in Oslo.", "active"),
            ("User moved to Boston.", "active"),
        ]
        assert all(f.slot_key is None for f in facts if f.fact_text == "User moved to Boston.")
        assert await db.scalar(sa.select(sa.func.count()).select_from(models.MemoryRelation)) == 0


# --------------------------------------------------------------------------
# Observed conversation versus roleplay, fiction, hypotheticals and backstory
# --------------------------------------------------------------------------

TURN = dict(session_id="s", turn_id="t", model="m")


def reasons_for(user_message, item, assistant_message="Okay."):
    entries, rejected = validate_experiences(
        [item], user_message=user_message, assistant_message=assistant_message, **TURN
    )
    return entries, [r.reason for r in rejected]


def test_a_grounded_observed_experience_is_accepted():
    entries, reasons = reasons_for(FATHER, experience(FATHER_NARRATIVE, FATHER_EVIDENCE))
    assert reasons == []
    [entry] = entries
    assert entry["type"] == "fact" and entry["metadata"]["kind"] == EXPERIENCE_KIND
    assert entry["metadata"]["source"] == "user_message"
    assert "father" in entry["metadata"]["experience"]["topics"]  # derived when not given


@pytest.mark.parametrize(
    ("message", "item", "reason"),
    [
        ("Let's pretend my father passed away. *sobs quietly*",
         experience(FATHER_NARRATIVE, "my father passed away"), "roleplay"),
        ("Roleplay is fun.", experience(FATHER_NARRATIVE, FATHER_EVIDENCE, status="roleplay"),
         "status_roleplay"),
        ("In my novel my father passed away last week.",
         experience(FATHER_NARRATIVE, "my father passed away last week"), "fiction"),
        ("If my father passed away last week I would be devastated.",
         experience(FATHER_NARRATIVE, "my father passed away last week"), "hypothetical"),
        ("I dreamed my father passed away last week.",
         experience(FATHER_NARRATIVE, "my father passed away last week"), "fiction"),
        ("I am going to move to Boston next month.",
         experience("User is moving to Boston.", "I am going to move to Boston next month"),
         "not_yet_happened"),
        ("My father passed away last week. Just kidding.",
         experience(FATHER_NARRATIVE, FATHER_EVIDENCE), "retracted"),
        ("Did my father pass away last week?",
         experience(FATHER_NARRATIVE, "my father pass away last week"), "question"),
        ("Maybe my father passed away last week.",
         experience(FATHER_NARRATIVE, "my father passed away last week"), "uncertain"),
    ],
)
def test_roleplay_fiction_hypotheticals_plans_and_doubt_are_not_experiences(message, item, reason):
    entries, reasons = reasons_for(message, item)
    assert entries == [] and reasons == [reason]


@pytest.mark.parametrize(
    ("user_message", "assistant_message", "item", "reason"),
    [
        # The companion's own invented past is backstory, not observed history.
        ("Tell me about yourself.", "I grew up in Lisbon and sailed around the world.",
         experience("The companion sailed around the world.", "sailed around the world",
                    participants=["companion"], origin="backstory"), "fictional_backstory"),
        ("Tell me about yourself.", "I grew up in Lisbon.",
         experience("User grew up in Lisbon.", "grew up in Lisbon", origin="backstory"),
         "fictional_backstory"),
        # Assistant claims cannot ground a shared past.
        ("How are you?", "Remember when we hiked together last summer?",
         experience("User and the companion hiked together last summer.",
                    "we hiked together last summer"), "evidence_not_in_user_message"),
        # Details the turn never contained.
        (FATHER, "I'm so sorry.",
         experience("User told the companion that their father passed away in Lisbon.",
                    FATHER_EVIDENCE), "unsupported_content"),
        (FATHER, "I'm so sorry.", experience(FATHER_NARRATIVE, FATHER_EVIDENCE, significance="ordinary"),
         "not_significant"),
        (FATHER, "I'm so sorry.", experience(FATHER_NARRATIVE, FATHER_EVIDENCE, status="hypothetical"),
         "status_hypothetical"),
    ],
)
def test_backstory_assistant_claims_and_invented_details_are_rejected(
    user_message, assistant_message, item, reason
):
    entries, reasons = reasons_for(user_message, item, assistant_message)
    assert entries == [] and reasons == [reason]


def test_at_most_two_experiences_per_turn_most_significant_first():
    message = "I got married. My dog died. I graduated from college."
    items = [
        experience("User got married.", "I got married"),
        experience("User's dog died.", "My dog died", significance="major"),
        experience("User graduated from college.", "I graduated from college"),
    ]
    entries, rejected = validate_experiences(
        items, user_message=message, assistant_message="", **TURN
    )
    assert [e["content"] for e in entries] == ["User's dog died.", "User got married."]
    assert [r.reason for r in rejected] == ["over_experience_limit"]


def test_missing_or_malformed_experiences_in_a_response_mean_none():
    assert parse_experiences('{"memories": []}') == []
    assert parse_experiences('{"experiences": "none"}') == []
    assert parse_experiences("not json") == []
    assert parse_experiences('{"experiences": [{"narrative": "x"}]}') == [{"narrative": "x"}]


async def test_scene_and_non_durable_rooms_never_create_experiences(env):
    await add_companion(env, "comp-a")
    for kwargs in ({"scene": True}, {"durable": False}):
        room = await add_room(env, companion_id="comp-a", **kwargs)
        session = await add_session(env, "comp-a", room_id=room)
        await say(env, session, FATHER, "I'm so sorry.", at=DAY, companion_id="comp-a")
        await process(
            env,
            answer(
                experience(FATHER_NARRATIVE, FATHER_EVIDENCE),
                memories=[{
                    "type": "personal_fact", "content": "User's father passed away last week.",
                    "evidence": FATHER_EVIDENCE, "status": "real", "confidence": 0.9,
                }],
            ),
        )
    assert await experiences_in(env) == []
    async with env.factory() as db:  # only the experience is gated; Phase 2 facts are untouched
        assert await db.scalar(sa.select(sa.func.count()).select_from(models.SemanticFact)) == 2


async def test_a_normal_room_keeps_experiences_within_that_room(env):
    await add_companion(env, "comp-a")
    room = await add_room(env, companion_id="comp-a")
    in_room = await add_session(env, "comp-a", room_id=room)
    outside = await add_session(env, "comp-a")
    await say(env, in_room, FATHER, "I'm so sorry.", at=DAY, companion_id="comp-a")
    await process(env, answer(experience(FATHER_NARRATIVE, FATHER_EVIDENCE)))

    assert [r.narrative for r in await visible(env, in_room)] == [FATHER_NARRATIVE]
    assert await visible(env, outside) == []  # room-scoped memory does not leak out


# --------------------------------------------------------------------------
# Ownership and isolation
# --------------------------------------------------------------------------


async def test_experiences_are_isolated_by_companion_and_user_and_never_linked_across(env):
    await add_companion(env, "comp-a")
    await add_companion(env, "comp-b")
    a = await add_session(env, "comp-a")
    b = await add_session(env, "comp-b")
    await say(env, a, FATHER, "I'm so sorry.", at=DAY, companion_id="comp-a")
    await process(env, answer(experience(FATHER_NARRATIVE, FATHER_EVIDENCE, topics=["father"])))
    await say(env, b, "My father passed away last week and I miss him.", "I'm here.",
              at=DAY + timedelta(days=1), companion_id="comp-b")
    await process(
        env,
        answer(experience("User told the companion their father passed away last week.",
                          "My father passed away last week", topics=["father"])),
    )

    await add_user(env, OTHER_USER_ID)
    await add_companion(env, "comp-x", user_id=OTHER_USER_ID)
    stranger = await add_session(env, "comp-x", user_id=OTHER_USER_ID)

    [for_a] = await visible(env, a)
    [for_b] = await visible(env, b)
    assert for_a.companion_id == "comp-a" and for_b.companion_id == "comp-b"
    assert for_a.related_ids == () and for_b.related_ids == ()  # same topic, different companion
    assert await visible(env, stranger) == []
    async with env.factory() as db:
        assert await get_experience(db, session_id=stranger, experience_id=for_a.id) is None
        assert await get_experience(db, session_id=b, experience_id=for_a.id) is None
        assert (await get_experience(db, session_id=a, experience_id=for_a.id)).id == for_a.id


# --------------------------------------------------------------------------
# Retry idempotency
# --------------------------------------------------------------------------


async def test_replaying_a_completed_job_changes_nothing_and_calls_no_model(env):
    first, second, turn, response = await seed_revision(env)

    async def counts():
        async with env.factory() as db:
            return {
                "facts": len(await experiences_in(env)),
                "evidence": await db.scalar(sa.select(sa.func.count()).select_from(models.MemoryEvidence)),
                "relations": await db.scalar(sa.select(sa.func.count()).select_from(models.MemoryRelation)),
                "records": [
                    (r.id, r.status, r.related_ids, r.revises_id, r.superseded_by)
                    for r in await visible(env, second, include_history=True)
                ],
            }

    before = await counts()
    # A crash after the cached receipt but before success: effects are re-applied.
    async with env.factory() as db:
        job = await db.scalar(sa.select(models.BackgroundJob).where(models.BackgroundJob.turn_id == turn))
        assert job.payload["extraction_result"]["entries"]
        job.payload = {k: v for k, v in job.payload.items() if k != "effects_applied"}
        job.status, job.completed_at = "pending", None
        await db.commit()

    provider = await process(env, {"memories": [], "experiences": []})  # must not be consulted

    assert provider.calls == []
    assert await counts() == before
    assert all(job.status == "succeeded" for job in await env.jobs())
