"""Phase 5.1a — durable emotional associations.

Scripted observations only: no model, no embedding, no network. Associations are
ordinary ``semantic_facts`` rows (``kind == "emotional_association"``) with
immutable ``memory_evidence``, so these tests cover only what Task 5.1a adds.
SQLite by default; set ``PHASE0_PG_URL`` to also run against PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import pytest
import sqlalchemy as sa

from test_completion_persistence import env, USER_ID  # noqa: F401  (pytest fixture)
from test_autobiographical_memory import (
    OTHER_USER_ID,
    add_companion,
    add_room,
    add_session,
    add_user,
)
from chatbot.db import models
from chatbot.soul import emotional_associations as ea

DAY = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
ATLAS_USER = "I just finished the first prototype of Project Atlas!"
ATLAS_REPLY = "I'm so excited about Project Atlas, you have worked on it for ages."
ATLAS_EVIDENCE = "I'm so excited about Project Atlas"


def raw(subject, emotion, interpretation, evidence, **overrides) -> dict:
    return {
        "subject": subject,
        "emotion": emotion,
        "interpretation": interpretation,
        "evidence": evidence,
        "source": "companion_expressed",
        "significance": "significant",
        "status": "real",
        **overrides,
    }


def atlas_excited(**overrides) -> dict:
    return {**raw("Project Atlas", "excitement", "it rewards the user's long effort", ATLAS_EVIDENCE), **overrides}


async def turn(env, session_id, user_text, assistant_text, *, at, companion_id=None, user_id=USER_ID):
    """Persist one completed turn; returns its source description."""
    turn_id = uuid.uuid4().hex
    ids = {}
    async with env.factory() as db:
        for role, text, offset in (("user", user_text, 0), ("assistant", assistant_text, 1)):
            message = models.Message(
                session_id=session_id, user_id=user_id, companion_id=companion_id, role=role,
                content=text, turn_id=turn_id, created_at=at + timedelta(seconds=offset),
            )
            db.add(message)
            await db.flush()
            ids[role] = str(message.id)
        await db.commit()
    return ea.AssociationSource(
        turn_id=turn_id,
        session_id=str(session_id),
        source_at=at + timedelta(seconds=1),
        job_id=f"job-{turn_id[:6]}",
        user_message_id=ids["user"],
        assistant_message_id=ids["assistant"],
    )


async def observe(
    env, session_id, companion_id, items, *, user_text=ATLAS_USER, reply=ATLAS_REPLY, at=DAY,
    experiences=(), user_id=USER_ID, source=None,
):
    """Validate scripted items and apply them in one transaction, as the durable job would."""
    source = source or await turn(env, session_id, user_text, reply, at=at, companion_id=companion_id, user_id=user_id)
    async with env.factory() as db:
        known = await ea.list_associations(db, session_id=session_id)
        accepted, rejected = ea.validate_emotional_observations(
            items,
            user_message=user_text,
            assistant_message=reply,
            known_keys=sorted(ea.known_association_keys(known)),
            known_experience_ids=experiences,
        )
        change = await ea.apply_emotional_associations(
            db, user_id=user_id, companion_id=companion_id, session_id=session_id,
            observations=accepted, source=source,
        )
        await db.commit()
    return change, rejected, source


async def rows(env) -> list[models.SemanticFact]:
    async with env.factory() as db:
        found = await db.scalars(
            sa.select(models.SemanticFact)
            .where(models.SemanticFact.extraction_metadata["kind"].as_string() == ea.ASSOCIATION_KIND)
            .order_by(models.SemanticFact.created_at, models.SemanticFact.id)
        )
        return list(found.all())


async def evidence_rows(env) -> list[models.MemoryEvidence]:
    async with env.factory() as db:
        return list((await db.scalars(sa.select(models.MemoryEvidence))).all())


async def recall(env, session_id, message, **kwargs):
    async with env.factory() as db:
        return await ea.retrieve_emotional_associations(db, session_id=session_id, message=message, **kwargs)


async def setup(env, companion_id="comp-a"):
    await add_companion(env, companion_id)
    return companion_id, await add_session(env, companion_id)


# --------------------------------------------------------------------------
# 1. A significant experience creates an association
# --------------------------------------------------------------------------


async def test_a_significant_experience_creates_a_grounded_association(env):
    companion, session = await setup(env)
    change, rejected, source = await observe(env, session, companion, [atlas_excited()])
    assert rejected == [] and change.applied[0]["op"] == "create"

    (fact,) = await rows(env)
    meta = fact.extraction_metadata
    assert (meta["companion_id"], meta["user_id"]) == (companion, str(USER_ID))
    assert meta["session_id"] == str(session) and meta["turn_id"] == source.turn_id
    assert fact.user_id == USER_ID and fact.embedding is None and fact.scope_key
    assert meta["association"]["emotion"] == "excitement"
    assert meta["association"]["interpretation"] == "it rewards the user's long effort"
    assert meta["association"]["holder"] == "companion" and meta["association"]["significance"] == "significant"
    assert fact.source_message_id == uuid.UUID(source.assistant_message_id)

    (evidence,) = await evidence_rows(env)
    assert evidence.memory_id == fact.id and evidence.user_id == USER_ID
    assert evidence.evidence == ATLAS_EVIDENCE and evidence.turn_id == source.turn_id
    assert evidence.session_id == session and evidence.provenance["source"] == "companion_expressed"


# --------------------------------------------------------------------------
# 2. Ordinary chatter creates nothing
# --------------------------------------------------------------------------


async def test_ordinary_chatter_creates_no_durable_association(env):
    companion, session = await setup(env)
    ordinary = atlas_excited(significance="ordinary")
    change, rejected, _ = await observe(env, session, companion, [ordinary])
    assert [item.reason for item in rejected] == ["not_significant"] and not change.applied

    greeting = raw("hello", "cheer", "a friendly hello", "Hi there", significance="significant")
    _, rejected, _ = await observe(env, session, companion, [greeting], user_text="Hi!", reply="Hi there, how are you?")
    assert rejected and rejected[0].reason in {
        "evidence_not_in_reply", "subject_not_in_turn", "question", "unsupported_content",
    }

    # The writer does not rely on validation having run.
    source = await turn(env, session, ATLAS_USER, ATLAS_REPLY, at=DAY + timedelta(hours=1), companion_id=companion)
    async with env.factory() as db:
        direct = ea.AssociationObservation(
            subject="Project Atlas", emotion="excitement", interpretation="x", source="companion_expressed",
            excerpt=ATLAS_EVIDENCE, significance="ordinary",
        )
        change = await ea.apply_emotional_associations(
            db, user_id=USER_ID, companion_id=companion, session_id=session, observations=[direct], source=source
        )
        await db.commit()
    assert change.rejected == [("Project Atlas", "not_significant")]
    assert await rows(env) == [] and await evidence_rows(env) == []


# --------------------------------------------------------------------------
# 3. Survives a new session
# --------------------------------------------------------------------------


async def test_association_survives_a_new_session_and_reactivates_on_the_subject(env):
    companion, first = await setup(env)
    await observe(env, first, companion, [atlas_excited()])

    second = await add_session(env, companion)
    found = await recall(env, second, "How is Project Atlas doing these days?", now=DAY + timedelta(days=30))
    assert [item.subject for item in found] == ["Project Atlas"]
    assert found[0].emotion == "excitement" and found[0].reasons == ("subject",)
    assert found[0].latest_excerpt == ATLAS_EVIDENCE and found[0].session_id == str(first)

    text = ea.render_associations(found)
    assert "Project Atlas" in text and "excitement" in text and "your own" in text
    assert await recall(env, second, "What should we have for dinner?") == []  # unrelated: nothing


async def test_a_recalled_shared_experience_and_an_open_thread_reactivate_it(env):
    companion, session = await setup(env)
    experience_id = str(uuid.uuid4())
    await observe(env, session, companion, [atlas_excited(experience_ref=experience_id, unresolved=True)],
                  experiences=[experience_id])
    other = await add_session(env, companion)
    by_experience = await recall(env, other, "Tell me about last spring", experience_ids=[experience_id])
    assert by_experience and by_experience[0].reasons == ("experience",)
    by_thread = await recall(env, other, "Anything new?", thread_subjects=["finish Atlas project"])
    assert by_thread and by_thread[0].reasons == ("unfinished",)
    assert await recall(env, other, "Anything new?", thread_subjects=["gardening"]) == []


# --------------------------------------------------------------------------
# 4. Related experience reinforces
# --------------------------------------------------------------------------


async def test_a_related_experience_reinforces_the_same_association(env):
    companion, session = await setup(env)
    await observe(env, session, companion, [atlas_excited()])
    later = "Atlas launched today! The whole team cheered for Project Atlas."
    reply = "I'm so excited about Project Atlas, this is wonderful."
    change, _, _ = await observe(
        env, session, companion, [atlas_excited()], user_text=later, reply=reply, at=DAY + timedelta(days=3)
    )
    assert change.applied[0]["op"] == "reinforce"
    (fact,) = await rows(env)
    assert fact.reinforcement_count == 2 and len(await evidence_rows(env)) == 2
    assert fact.last_reinforced_at.replace(tzinfo=timezone.utc) == DAY + timedelta(days=3, seconds=1)


# --------------------------------------------------------------------------
# 5. Conflicting experiences
# --------------------------------------------------------------------------


async def test_contradictory_feelings_about_one_subject_coexist_without_a_score(env):
    companion, session = await setup(env)
    await observe(env, session, companion, [atlas_excited()])
    worry_reply = "I'm worried about Project Atlas, the deadline feels tight."
    await observe(
        env, session, companion,
        [raw("Project Atlas", "anxiety", "the deadline may squeeze the user", "I'm worried about Project Atlas")],
        user_text="The Project Atlas deadline moved up.", reply=worry_reply, at=DAY + timedelta(days=2),
    )
    stored = await rows(env)
    assert {row.extraction_metadata["association"]["emotion"] for row in stored} == {"excitement", "anxiety"}
    assert all(row.lifecycle_status == "active" for row in stored)
    found = await recall(env, session, "Project Atlas status?", now=DAY + timedelta(days=3))
    assert {item.emotion for item in found} == {"excitement", "anxiety"}


async def test_a_conflicting_experience_can_revise_an_association_and_keeps_the_old_one(env):
    companion, session = await setup(env)
    await observe(env, session, companion, [atlas_excited()])
    key = ea.association_key("Project Atlas", "excitement", "companion_expressed")
    reply = "I feel let down about Project Atlas, it was cancelled after all that work."
    change, _, _ = await observe(
        env, session, companion,
        [raw("Project Atlas", "disappointment", "all that effort ended in cancellation",
             "I feel let down about Project Atlas", revises=key)],
        user_text="Project Atlas was cancelled today.", reply=reply, at=DAY + timedelta(days=5),
    )
    assert change.applied[0]["op"] == "create"
    by_emotion = {row.extraction_metadata["association"]["emotion"]: row for row in await rows(env)}
    assert by_emotion["excitement"].lifecycle_status == "superseded"
    assert by_emotion["excitement"].superseded_by == by_emotion["disappointment"].id
    assert by_emotion["disappointment"].lifecycle_status == "active"
    found = await recall(env, session, "Project Atlas?", now=DAY + timedelta(days=6))
    assert [item.emotion for item in found] == ["disappointment"]

    async with env.factory() as db:
        record = await ea.get_association(db, session_id=session, association_id=by_emotion["disappointment"].id)
    assert record.revises_id == str(by_emotion["excitement"].id)
    assert record.revision_history == (str(by_emotion["excitement"].id),)


async def test_a_new_interpretation_of_the_same_emotion_keeps_the_earlier_one_in_history(env):
    companion, session = await setup(env)
    await observe(env, session, companion, [atlas_excited()])
    reply = "I'm so excited about Project Atlas, it is the user's chance to lead."
    change, _, _ = await observe(
        env, session, companion,
        [raw("Project Atlas", "excitement", "it is the user's chance to lead", "I'm so excited about Project Atlas")],
        user_text="Atlas gets me a lead role.", reply=reply, at=DAY + timedelta(days=4),
    )
    assert change.applied[0]["op"] == "reinterpret"
    (fact,) = await rows(env)
    data = fact.extraction_metadata["association"]
    assert data["interpretation"] == "it is the user's chance to lead" and data["revision_count"] == 1
    assert data["interpretation_history"][0]["interpretation"] == "it rewards the user's long effort"


# --------------------------------------------------------------------------
# 6. Earlier evidence remains attributable
# --------------------------------------------------------------------------


async def test_earlier_evidence_stays_attributable_after_revision(env):
    companion, session = await setup(env)
    _, _, first = await observe(env, session, companion, [atlas_excited()])
    reply = "I'm so excited about Project Atlas, it is the user's chance to lead."
    _, _, second = await observe(
        env, session, companion,
        [raw("Project Atlas", "excitement", "it is the user's chance to lead", "I'm so excited about Project Atlas")],
        user_text="Atlas gets me a lead role.", reply=reply, at=DAY + timedelta(days=4),
    )
    (fact,) = await rows(env)
    async with env.factory() as db:
        record = await ea.get_association(db, session_id=session, association_id=fact.id)
    assert [item.turn_id for item in record.evidence] == [first.turn_id, second.turn_id]
    assert [item.source_message_id for item in record.evidence] == [
        first.assistant_message_id, second.assistant_message_id,
    ]
    assert [item.interpretation for item in record.evidence] == [
        "it rewards the user's long effort", "it is the user's chance to lead",
    ]
    assert all(item.excerpt == ATLAS_EVIDENCE and item.session_id == str(session) for item in record.evidence)


# --------------------------------------------------------------------------
# 7 and 8. Isolation
# --------------------------------------------------------------------------


async def test_associations_are_isolated_by_companion(env):
    first, session_a = await setup(env, "comp-a")
    await add_companion(env, "comp-b")
    session_b = await add_session(env, "comp-b")
    await observe(env, session_a, first, [atlas_excited()])
    assert await recall(env, session_b, "Project Atlas?") == []
    async with env.factory() as db:
        assert await ea.list_associations(db, session_id=session_b, include_history=True) == []

    # The other companion may feel differently about the same subject; both rows exist apart.
    reply = "Honestly Project Atlas makes me anxious, the scope keeps growing."
    await observe(
        env, session_b, "comp-b",
        [raw("Project Atlas", "anxiety", "the scope keeps growing", "Project Atlas makes me anxious")],
        user_text="Project Atlas needs more features.", reply=reply, at=DAY + timedelta(days=1),
    )
    assert [item.emotion for item in await recall(env, session_b, "Project Atlas?")] == ["anxiety"]
    assert [item.emotion for item in await recall(env, session_a, "Project Atlas?")] == ["excitement"]


async def test_associations_are_isolated_by_user_and_room(env):
    companion, session = await setup(env)
    await add_user(env, OTHER_USER_ID)
    await add_companion(env, "comp-other", user_id=OTHER_USER_ID)
    other_session = await add_session(env, "comp-other", user_id=OTHER_USER_ID)
    await observe(env, session, companion, [atlas_excited()])
    assert await recall(env, other_session, "Project Atlas?") == []

    # A companion owned by someone else cannot be written through this user's session.
    source = await turn(env, session, ATLAS_USER, ATLAS_REPLY, at=DAY, companion_id=companion)
    async with env.factory() as db:
        obs = ea.AssociationObservation(
            subject="Project Atlas", emotion="pride", interpretation="x", source="companion_expressed",
            excerpt=ATLAS_EVIDENCE,
        )
        with pytest.raises(PermissionError):
            await ea.apply_emotional_associations(
                db, user_id=USER_ID, companion_id="comp-other", session_id=other_session,
                observations=[obs], source=source,
            )

    room_one = await add_room(env, companion_id=companion)
    room_two = await add_room(env, companion_id=companion)
    in_room = await add_session(env, companion, room_id=room_one)
    elsewhere = await add_session(env, companion, room_id=room_two)
    reply = "I'm so excited about the garden, planting day was lovely."
    await observe(
        env, in_room, companion,
        [raw("garden", "excitement", "planting together was lovely", "I'm so excited about the garden")],
        user_text="We planted the garden today!", reply=reply, at=DAY + timedelta(days=1),
    )
    assert [item.subject for item in await recall(env, in_room, "the garden?")] == ["garden"]
    assert await recall(env, elsewhere, "the garden?") == []
    assert await recall(env, session, "the garden?") == []


# --------------------------------------------------------------------------
# 9. Roleplay, fiction and ineligible rooms
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "user_text, reply, evidence, reason",
    [
        ("Let's roleplay: you are a pirate and I found treasure on Project Atlas island.",
         "I'm so excited about Project Atlas, matey.", "I'm so excited about Project Atlas", "roleplay"),
        ("Imagine we shipped Project Atlas.", "If we shipped it I'd be so excited about Project Atlas.",
         "I'd be so excited about Project Atlas", "hypothetical"),
        ("Tell me a story about Project Atlas.", "In the story, I'm so excited about Project Atlas!",
         "I'm so excited about Project Atlas", None),
    ],
)
def test_roleplay_fiction_and_hypotheticals_are_not_real_emotional_history(user_text, reply, evidence, reason):
    item = raw("Project Atlas", "excitement", "x", evidence)
    accepted, rejected = ea.validate_emotional_observations([item], user_message=user_text, assistant_message=reply)
    assert accepted == [] and rejected
    if reason:
        assert rejected[0].reason == reason


async def test_roleplay_scene_and_non_durable_rooms_write_nothing(env):
    companion, session = await setup(env)
    source = await turn(env, session, ATLAS_USER, ATLAS_REPLY, at=DAY, companion_id=companion)
    obs = ea.AssociationObservation(
        subject="Project Atlas", emotion="excitement", interpretation="x", source="companion_expressed",
        excerpt=ATLAS_EVIDENCE,
    )
    async with env.factory() as db:
        roleplay = ea.AssociationSource(
            turn_id=source.turn_id, session_id=source.session_id, source_at=source.source_at, in_roleplay=True
        )
        change = await ea.apply_emotional_associations(
            db, user_id=USER_ID, companion_id=companion, session_id=session, observations=[obs], source=roleplay
        )
        assert change.rejected == [("Project Atlas", "roleplay")]

    for kwargs in ({"scene": True}, {"durable": False}):
        room = await add_room(env, companion_id=companion, **kwargs)
        room_session = await add_session(env, companion, room_id=room)
        room_source = await turn(env, room_session, ATLAS_USER, ATLAS_REPLY, at=DAY, companion_id=companion)
        async with env.factory() as db:
            change = await ea.apply_emotional_associations(
                db, user_id=USER_ID, companion_id=companion, session_id=room_session,
                observations=[obs], source=room_source,
            )
            await db.commit()
        assert change.rejected == [("Project Atlas", "room_not_durable")]
    assert await rows(env) == [] and await evidence_rows(env) == []


# --------------------------------------------------------------------------
# Evidence, holder and invention rules
# --------------------------------------------------------------------------


def reasons(items, user_text=ATLAS_USER, reply=ATLAS_REPLY, **kwargs):
    accepted, rejected = ea.validate_emotional_observations(
        items, user_message=user_text, assistant_message=reply, **kwargs
    )
    return accepted, [item.reason for item in rejected]


def test_the_users_feeling_is_not_the_companions_unless_it_is_the_companions_own_words():
    user_text = "I'm terrified about Project Atlas, honestly."
    reply = "That sounds like a lot. Let's go through it together."
    as_companion = raw("Project Atlas", "fear", "the user is scared", "I'm terrified about Project Atlas")
    accepted, why = reasons([as_companion], user_text, reply)
    assert accepted == [] and why == ["evidence_not_in_reply"]  # the user's words cannot ground the companion's feeling

    as_user = {**as_companion, "source": "user_reported"}
    accepted, why = reasons([as_user], user_text, reply)
    assert why == [] and accepted[0].source == "user_reported"
    assert ea.holder_of(accepted[0].source) == "user"
    assert ea.association_key("Project Atlas", "fear", "user_reported") != ea.association_key(
        "Project Atlas", "fear", "companion_expressed"
    )


def test_inferred_significance_needs_a_recorded_experience_and_invented_ones_are_rejected():
    item = raw("Project Atlas", "pride", "it mattered to them", "first prototype of Project Atlas", source="inferred")
    assert reasons([item])[1] == ["inferred_without_experience"]
    assert reasons([{**item, "experience_ref": "not-recorded"}], known_experience_ids=["abc"])[1] == [
        "unknown_experience"
    ]
    accepted, why = reasons([{**item, "experience_ref": "abc"}], known_experience_ids=["abc"])
    assert why == [] and accepted[0].experience_ref == "abc"


def test_unsupported_names_numbers_and_invented_subjects_are_rejected():
    invented_detail = atlas_excited(interpretation="it echoes the 2019 launch of Project Helios")
    assert reasons([invented_detail])[1] == ["unsupported_content"]
    invented_subject = raw("the lighthouse trip", "joy", "a happy memory", ATLAS_EVIDENCE)
    assert reasons([invented_subject])[1] == ["subject_not_in_turn"]
    assert reasons([raw("Project Atlas", "joy", "a happy memory", "I'm so excited about the lighthouse")])[1] == [
        "evidence_not_in_reply"
    ]
    assert reasons([atlas_excited(status="hypothetical")])[1] == ["status_hypothetical"]
    assert reasons([atlas_excited(emotion="so very extremely excited")])[1] == ["bad_emotion"]
    assert reasons([atlas_excited(interpretation="")])[1] == ["bad_interpretation"]


def test_no_emotion_is_privileged_and_at_most_three_per_turn():
    for emotion in ("anxiety", "frustration", "disappointment", "excitement", "grief"):
        accepted, why = reasons([atlas_excited(emotion=emotion)])
        assert why == [] and accepted[0].emotion == emotion
    items = [atlas_excited(emotion=e) for e in ("pride", "worry", "joy", "doubt")]
    accepted, why = reasons(items)
    assert len(accepted) == 3 and why == ["over_observation_limit"]


# --------------------------------------------------------------------------
# 10. Retry idempotency
# --------------------------------------------------------------------------


async def test_replaying_the_same_turn_changes_nothing(env):
    companion, session = await setup(env)
    _, _, source = await observe(env, session, companion, [atlas_excited()])
    before = [(r.id, r.reinforcement_count, r.extraction_metadata) for r in await rows(env)]
    for _ in range(2):
        change, _, _ = await observe(env, session, companion, [atlas_excited()], source=source)
        assert change.applied[0]["op"] == "replay" and not change.changed
    assert [(r.id, r.reinforcement_count, r.extraction_metadata) for r in await rows(env)] == before
    assert len(await evidence_rows(env)) == 1


async def test_replay_is_still_detected_after_the_evidence_cap(env):
    companion, session = await setup(env)
    last = None
    for index in range(ea.MAX_EVIDENCE_PER_ASSOCIATION + 3):
        _, _, last = await observe(
            env, session, companion, [atlas_excited()], at=DAY + timedelta(days=index)
        )
    (fact,) = await rows(env)
    assert fact.reinforcement_count == ea.MAX_EVIDENCE_PER_ASSOCIATION + 3
    assert len(await evidence_rows(env)) == ea.MAX_EVIDENCE_PER_ASSOCIATION  # evidence storage is bounded
    await observe(env, session, companion, [atlas_excited()], source=last)
    assert (await rows(env))[0].reinforcement_count == ea.MAX_EVIDENCE_PER_ASSOCIATION + 3


# --------------------------------------------------------------------------
# 11. Out-of-order processing
# --------------------------------------------------------------------------


async def revision_scenario(env, *, revision_first: bool):
    companion, session = await setup(env)
    key = ea.association_key("Project Atlas", "excitement", "companion_expressed")
    early = dict(items=[atlas_excited()], at=DAY)
    late = dict(
        items=[raw("Project Atlas", "disappointment", "all that effort ended in cancellation",
                   "I feel let down about Project Atlas", revises=key)],
        user_text="Project Atlas was cancelled today.",
        reply="I feel let down about Project Atlas, it was cancelled after all that work.",
        at=DAY + timedelta(days=5),
    )
    # Both turns exist before either job runs; ``revises`` is accepted because the key is known.
    sources = {}
    for name, spec in (("early", early), ("late", late)):
        sources[name] = await turn(
            env, session, spec.get("user_text", ATLAS_USER), spec.get("reply", ATLAS_REPLY),
            at=spec["at"], companion_id=companion,
        )
    order = ["late", "early"] if revision_first else ["early", "late"]
    for name in order:
        spec = {"early": early, "late": late}[name]
        source = sources[name]
        accepted, _ = ea.validate_emotional_observations(
            spec["items"], user_message=spec.get("user_text", ATLAS_USER),
            assistant_message=spec.get("reply", ATLAS_REPLY), known_keys=[key],
        )
        async with env.factory() as db:
            await ea.apply_emotional_associations(
                db, user_id=USER_ID, companion_id=companion, session_id=session,
                observations=accepted, source=source,
            )
            await db.commit()
    state = {}
    for row in await rows(env):
        data = row.extraction_metadata["association"]
        state[data["emotion"]] = (row.lifecycle_status, row.reinforcement_count)
    return state, session


async def test_a_late_job_for_an_earlier_experience_cannot_overturn_a_later_revision(env):
    in_order, _ = await revision_scenario(env, revision_first=False)
    assert in_order == {"excitement": ("superseded", 1), "disappointment": ("active", 1)}


async def test_reversed_job_order_gives_the_same_final_state(env):
    state, session = await revision_scenario(env, revision_first=True)
    assert state == {"excitement": ("superseded", 1), "disappointment": ("active", 1)}
    found = await recall(env, session, "Project Atlas?", now=DAY + timedelta(days=6))
    assert [item.emotion for item in found] == ["disappointment"]


async def test_a_late_reinforcement_cannot_rewrite_a_newer_interpretation(env):
    companion, session = await setup(env)
    reply = "I'm so excited about Project Atlas, it is the user's chance to lead."
    newer = [raw("Project Atlas", "excitement", "it is the user's chance to lead", "I'm so excited about Project Atlas")]
    # The newer turn is processed first; the older one arrives late.
    older_source = await turn(env, session, ATLAS_USER, ATLAS_REPLY, at=DAY, companion_id=companion)
    await observe(env, session, companion, newer, user_text="Atlas gets me a lead role.", reply=reply,
                  at=DAY + timedelta(days=4))
    change, _, _ = await observe(env, session, companion, [atlas_excited()], source=older_source)
    assert change.applied[0]["op"] == "reinforce"
    (fact,) = await rows(env)
    data = fact.extraction_metadata["association"]
    assert data["interpretation"] == "it is the user's chance to lead" and data["revision_count"] == 0
    assert fact.reinforcement_count == 2
    assert fact.last_reinforced_at.replace(tzinfo=timezone.utc) == DAY + timedelta(days=4, seconds=1)


# --------------------------------------------------------------------------
# 12. Bounded storage and retrieval
# --------------------------------------------------------------------------


async def test_storage_and_retrieval_are_bounded(env):
    companion, session = await setup(env)
    source = await turn(env, session, "x", "y", at=DAY, companion_id=companion)
    total = ea.MAX_ACTIVE_PER_SCOPE + 6
    async with env.factory() as db:
        for index in range(total):
            at = DAY + timedelta(days=index)
            observation = ea.AssociationObservation(
                subject=f"topic{index} atlas", emotion="curiosity", interpretation="i" * 400,
                source="companion_expressed", excerpt="e" * 500, significance="significant",
            )
            await ea.apply_emotional_associations(
                db, user_id=USER_ID, companion_id=companion, session_id=session, observations=[observation],
                source=ea.AssociationSource(
                    turn_id=f"t{index}", session_id=str(session), source_at=at,
                ),
            )
        await db.commit()
    stored = await rows(env)
    assert len(stored) == total
    assert sum(r.lifecycle_status == "active" for r in stored) == ea.MAX_ACTIVE_PER_SCOPE
    assert sum(r.lifecycle_status == "historical" for r in stored) == 6  # kept, not deleted
    assert all(len(r.fact_text) <= ea.MAX_FACT_CHARS for r in stored)
    assert all(len(r.extraction_metadata["association"]["interpretation"]) <= ea.MAX_INTERPRETATION_CHARS for r in stored)
    assert all(len(e.evidence) <= ea.MAX_EXCERPT_CHARS for e in await evidence_rows(env))

    found = await recall(env, session, "atlas atlas atlas", limit=50, now=DAY + timedelta(days=100))
    assert len(found) == ea.MAX_RETRIEVED_CEILING
    text = ea.render_associations(found)
    assert len(text) <= ea.MAX_CONTEXT_CHARS and text.startswith(ea.CONTEXT_HEADER)
    assert all(line.startswith("- ") for line in text.splitlines()[1:])
    assert len(await recall(env, session, "atlas", limit=2)) == 2
    assert await recall(env, session, "atlas", limit=0) == []


def test_render_labels_the_holder_and_never_dictates_a_reaction():
    now = DAY
    base = dict(
        id="1", key="k", target_kind="subject", significance="significant", salience=0.7, status="active",
        unresolved=False, reinforcement_count=1, first_at=now, last_at=now, user_id="u", companion_id="c",
        room_id=None, session_id=None, turn_id=None, source_message_id=None, experience_ref=None,
    )
    mine = ea.EmotionalAssociation(
        subject="Atlas", emotion="anxiety", interpretation="scope keeps growing", source="companion_expressed",
        holder="companion", **base,
    )
    theirs = ea.EmotionalAssociation(
        subject="the move", emotion="dread", interpretation="leaving friends", source="user_reported",
        holder="user", **base,
    )
    text = ea.render_associations([mine, theirs])
    assert "the user's feeling, not yours" in text and "your own" in text
    assert "should" not in text.lower() and ea.render_associations([]) == ""
