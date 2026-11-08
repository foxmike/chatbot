"""Phase 3.3 — behavioral acceptance through the real durable pipeline.

Drives the eight scenarios of ``fixtures/phase3_behavioral_acceptance.json``
(conversation text taken from the fixture) through the real durable worker and
handler, the real prompt pipeline and the real database layer. Only model
responses are scripted: the extraction call returns what a well-behaved (and,
where the scenario calls for it, an over-eager) model would; the conversation
provider records exactly what it receives. No network, no paid inference.

Set ``PHASE0_PG_URL`` to a disposable Alembic-built PostgreSQL/pgvector
instance to run the same scenarios there (and the PostgreSQL-only checks).
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import uuid

import pytest
import sqlalchemy as sa

from test_autobiographical_memory import (
    add_companion,
    add_room,
    add_session,
    answer,
    experience,
    experiences_in,
    process,
    say,
    visible,
)
from test_completion_persistence import env, USER_ID  # noqa: F401  (pytest fixture)
from test_persistent_identity_integration import (  # noqa: F401  (provider is a pytest fixture)
    add_companion as add_prompt_companion,
    new_session,
    provider,
    spoken_turn,
    standard_turn,
    stream_turn,
)
from test_timeline_migration import alembic, fresh_database, needs_postgres  # noqa: F401

from chatbot.core import turn as turn_module
from chatbot.db import models
from chatbot.soul import evolution
from chatbot.soul.self_model import (
    EvidenceSource,
    SelfObservation,
    apply_self_observations,
    developed_self_text,
    held_items,
    next_self_model,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "phase3_behavioral_acceptance.json").read_text()
)
SCENARIOS = {s["scenario_id"].split("_")[1]: s for s in FIXTURE["scenarios"]}
EMPTY = {"memories": [], "experiences": [], "self": []}
BASE = datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Fixture and scenario helpers
# --------------------------------------------------------------------------


def sessions_of(number: str) -> dict[str, list[tuple[str | None, str]]]:
    """``session_id -> [(user text, assistant text)]`` from the fixture's turns."""
    result: dict[str, list[tuple[str | None, str]]] = {}
    for sequence in SCENARIOS[number]["conversation_sequence"]:
        users: list[str] = []
        pairs: list[tuple[str | None, str]] = []
        for turn in sequence.get("turns", []):
            if turn["role"] == "user":
                users.append(turn["content"])
            else:
                pairs.append((" ".join(users) or None, turn["content"]))
                users = []
        result[sequence["session_id"]] = pairs
    return result


def at(days: float = 0, hours: float = 0) -> datetime:
    return BASE + timedelta(days=days, hours=hours)


def view(**overrides):
    base = {
        "kind": "opinion", "subject": "", "statement": "", "reason": "", "evidence": "",
        "status": "real", "significance": "ordinary", "relation": "consistent",
        "change_of_mind": False,
    }
    base.update(overrides)
    return base


def extraction(*, experiences=(), selfs=(), memories=()):
    return {"memories": list(memories), "experiences": list(experiences), "self": list(selfs)}


async def converse(env, cid, sid, pair, when, response=None):
    """One completed turn, then its durable job, with the scripted extraction response."""
    user, assistant = pair
    await say(env, sid, user or "Tell me what you think.", assistant, at=when, companion_id=cid)
    provider = await process(env, response or EMPTY)
    assert len(provider.calls) == 1  # one extraction call per completed turn, nothing more
    return provider


async def identity_row(env, cid):
    async with env.factory() as db:
        return await db.get(models.CompanionIdentityModel, cid)


async def self_model(env, cid):
    return (await identity_row(env, cid)).self_model


def items(doc):
    return doc.get("items", {})


async def companion_config(env, cid):
    async with env.factory() as db:
        c = await db.get(models.Companion, cid)
        return (c.persona, c.description, c.backstory, c.personality_sliders, c.favorite_topics)


# --------------------------------------------------------------------------
# 1. Meaningful experiences persist across sessions
# --------------------------------------------------------------------------


async def test_scenario_1_a_shared_crisis_survives_into_a_new_session(env):
    sessions = sessions_of("1")
    night, morning = list(sessions)
    await add_companion(env, "mercury")
    night_sid = await add_session(env, "mercury")
    saved = "we just saved six months of stratospheric ozone data"
    assert saved in sessions[night][2][0]

    for index, pair in enumerate(sessions[night]):
        response = EMPTY
        if index == 2:
            response = extraction(experiences=[experience(
                "User and the companion saved six months of stratospheric ozone data after fixing the telemetry link.",
                saved, significance="major", topics=["telemetry", "ozone"],
            )])
        await converse(env, "mercury", night_sid, pair, at(0, index), response)

    morning_sid = await add_session(env, "mercury")  # session 102: a brand-new conversation
    [record] = await visible(env, morning_sid)

    assert "stratospheric ozone data" in record.narrative
    assert record.participants == ("user", "companion")  # mutual attribution
    assert record.salience == 0.95 and record.companion_id == "mercury"
    assert record.session_id == str(night_sid)  # provenance: where it happened
    async with env.factory() as db:
        from chatbot.memory.autobiography import get_experience
        detail = await get_experience(db, session_id=morning_sid, experience_id=record.id)
        row = await db.get(models.SemanticFact, uuid.UUID(record.id))
    assert [e.excerpt for e in detail.evidence] == [saved]
    assert detail.evidence[0].session_id == str(night_sid)
    assert row.embedding is not None and row.lifecycle_status == "active"  # retrievable memory


# --------------------------------------------------------------------------
# 2. Related experiences form a coherent history
# --------------------------------------------------------------------------


async def test_scenario_2_related_experiences_form_one_chronological_arc(env):
    sessions = sessions_of("2")
    discovery, consultation, restoration = list(sessions)
    await add_companion(env, "kore")
    scripted = {
        discovery: ("User found two weathered wooden hives tucked in the brush at the rail yard.",
                    "I found two weathered wooden Langstroth hives tucked in the brush", ["hives", "rail yard"], 0),
        consultation: ("User met a beekeeper named Martha who advised on the rail yard hives.",
                       "I met an experienced beekeeper named Martha at the market this morning", ["hives", "martha"], 14),
        restoration: ("User spent three hours clearing brush and planting aster around the rail yard hives.",
                      "I spent three hours this morning clearing brush with heavy loppers and planting aster starts "
                      "around the perimeter of the rail yard hives", ["hives", "aster"], 21),
    }
    for sid_name, (narrative, evidence, topics, day) in scripted.items():
        sid = await add_session(env, "kore")
        response = extraction(experiences=[experience(narrative, evidence, topics=topics)])
        await converse(env, "kore", sid, sessions[sid_name][0], at(day), response)

    fresh = await add_session(env, "kore")
    first, second, third = await visible(env, fresh)

    assert [first.occurred_at, second.occurred_at, third.occurred_at] == [at(0), at(14), at(21)]
    assert "Martha" in second.narrative
    assert set(first.related_ids) == {second.id, third.id}  # one arc, linked both ways
    assert set(second.related_ids) == {first.id, third.id}
    assert set(third.related_ids) == {first.id, second.id}
    assert len({first.session_id, second.session_id, third.session_id}) == 3
    assert all(r.status == "active" for r in (first, second, third))


# --------------------------------------------------------------------------
# 3. Repeated evidence develops an interest
# --------------------------------------------------------------------------

THERON_ANCHORS = ["19th-century cast-iron bridge structures", "technical drafting", "municipal infrastructure"]


async def test_scenario_3_an_interest_develops_only_from_repeated_evidence(env):
    sessions = sessions_of("3")
    first, second, third, fourth = list(sessions)
    await add_companion(env, "theron")
    async with env.factory() as db:
        companion = await db.get(models.Companion, "theron")
        companion.favorite_topics = THERON_ANCHORS
        await db.commit()
    before = await companion_config(env, "theron")

    def interest(statement, evidence, significance):
        return extraction(selfs=[view(kind="interest", subject="fermentation biochemistry",
                                      statement=statement, evidence=evidence, significance=significance)])

    key = "interest:fermentation biochemistry"
    sid = await add_session(env, "theron")
    await converse(env, "theron", sid, sessions[first][0], at(0))  # a passing mention: nothing
    assert items(await self_model(env, "theron")) == {}

    sid = await add_session(env, "theron")
    await converse(env, "theron", sid, sessions[second][0], at(5), interest(
        "I find microbial succession and symbiotic balance compelling.",
        "Viewing microbial succession and symbiotic balance as living, self-regulating infrastructure "
        "rather than a mere recipe", "ordinary"))
    doc = await self_model(env, "theron")
    assert items(doc)[key]["status"] == "tentative"  # one sighting is not an interest
    assert developed_self_text(doc) is None

    sid = await add_session(env, "theron")
    await converse(env, "theron", sid, sessions[third][0], at(10), interest(
        "I am drawn to enzymatic kinetics.", "That aligns with enzymatic kinetics", "significant"))
    doc = await self_model(env, "theron")
    assert items(doc)[key]["status"] == "held"  # repeated, across sessions
    assert "fermentation biochemistry" in developed_self_text(doc)

    sid = await add_session(env, "theron")
    await converse(env, "theron", sid, sessions[fourth][0], at(15), interest(
        "I enjoy how koji and proteolytic enzymes shape savory flavour.",
        "30°C promotes sweeter koji via amylase, while 38°C favors proteolytic enzymes for savory amino acids",
        "significant"))
    item = items(await self_model(env, "theron"))[key]
    assert item["status"] == "held" and item["support"] == 5 and len(item["sessions"]) == 3
    assert len({ref["session_id"] for ref in item["evidence"]}) == 3  # three independent sessions
    assert all(ref["excerpt"] and ref["message_id"] and ref["job_id"] for ref in item["evidence"])
    assert await companion_config(env, "theron") == before  # anchors untouched
    assert (await identity_row(env, "theron")).trait_adjustments == {}


# --------------------------------------------------------------------------
# 4. A reasoned opinion becomes established
# --------------------------------------------------------------------------


async def test_scenario_4_a_reasoned_opinion_is_established_with_its_reasons(env):
    sessions = sessions_of("4")
    dilemma, synthesis = list(sessions)
    await add_companion(env, "selene")
    sid = await add_session(env, "selene")
    for index, pair in enumerate(sessions[dilemma]):  # "I don't have a settled view": no opinion yet
        await converse(env, "selene", sid, pair, at(0, index))
    assert items(await self_model(env, "selene")) == {}

    reason = "Full-cutoff amber fixtures preserve pedestrian visual acuity, protect biological cycles, and achieve energy savings"
    evidence = "high-kelvin unshielded illumination is bad civic design masquerading as security"
    sid = await add_session(env, "selene")
    await converse(env, "selene", sid, sessions[synthesis][0], at(7), extraction(selfs=[view(
        subject="civic lighting policy",
        statement="High-kelvin unshielded illumination is bad civic design.",
        reason=reason, evidence=evidence, significance="significant")]))

    doc = await self_model(env, "selene")
    item = items(doc)["opinion:civic lighting policy"]
    assert item["status"] == "held" and item["reason"] == reason
    [ref] = item["evidence"]
    assert ref["excerpt"] == evidence and ref["session_id"] == str(sid)
    assert "civic lighting policy" in developed_self_text(doc)


# --------------------------------------------------------------------------
# 5. New evidence can revise an opinion; the earlier one is preserved
# --------------------------------------------------------------------------


async def test_scenario_5_new_evidence_revises_an_opinion_and_keeps_the_old_one(env):
    sessions = sessions_of("5")
    prior, evidence_session = list(sessions)
    await add_companion(env, "mercury5")
    old_reason = "Quartz vibrates at 32,768 Hz with zero moving friction"
    sid = await add_session(env, "mercury5")
    pair = ("Do you ever wear a mechanical watch?", sessions[prior][0][1])  # fixture turn has no user line
    await converse(env, "mercury5", sid, pair, at(0), extraction(selfs=[view(
        subject="mechanical horology",
        statement="Carrying a spring-driven clockwork mechanism is purely sentimental nostalgia.",
        reason=old_reason,
        evidence="Carrying a spring-driven clockwork mechanism that gains five seconds a day and requires "
                 "a $500 overhaul every four years is purely sentimental nostalgia")]))
    key = "opinion:mechanical horology"
    assert items(await self_model(env, "mercury5"))[key]["status"] == "held"

    new_reason = "independence from supply chains and semiconductor infrastructure"
    sid = await add_session(env, "mercury5")
    await converse(env, "mercury5", sid, sessions[evidence_session][0], at(30), extraction(selfs=[view(
        subject="mechanical horology",
        statement="A timekeeper that functions on human kinetic energy alone is an autonomous, EMP-immune instrument.",
        reason=new_reason,
        evidence="A timekeeper that functions for two hundred years on human kinetic energy alone, without "
                 "reliance on a power grid or satellite constellation, isn't just an ornamental craft—it's an "
                 "autonomous, EMP-immune thermodynamic instrument",
        relation="opposing", change_of_mind=True, significance="significant")]))

    doc = await self_model(env, "mercury5")
    item = items(doc)[key]
    assert item["status"] == "held" and item["reason"] == new_reason
    assert "EMP-immune" in item["statement"]
    [old] = item["history"]  # narrative self-reflection: the earlier position is still known
    assert "sentimental nostalgia" in old["statement"] and old["reason"] == old_reason
    assert [entry["change"] for entry in doc["log"]] == ["held", "revised"]
    text = developed_self_text(doc)
    assert "EMP-immune" in text and "sentimental nostalgia" not in text  # only the current view speaks


# --------------------------------------------------------------------------
# 6. Casual conversation does not arbitrarily change personality
# --------------------------------------------------------------------------


async def test_scenario_6_casual_chatter_creates_no_established_traits_or_interests(env):
    sessions = sessions_of("6")
    await add_companion(env, "kore6")
    before = await companion_config(env, "kore6")
    # An over-eager model proposes memories from small talk; the guards and the
    # evidence thresholds must keep every one of them from becoming personality.
    eager = {
        "session-601-mundane-morning": extraction(
            experiences=[experience("User is grabbing toast before work.", "Just grabbing toast before work",
                                    significance="ordinary")],
            selfs=[view(kind="interest", subject="overcast mornings", statement="I like overcast mornings.",
                        evidence="Overcast mornings have their own rhythm")]),
        "session-602-commute-small-talk": extraction(
            selfs=[view(kind="opinion", subject="traffic", statement="I find road hazards fascinating.",
                        evidence="That's an expensive morning for whoever's tires found them first")]),
        "session-603-dinner-banter": extraction(
            selfs=[view(kind="interest", subject="noodles", statement="I like wide rice noodles and charred greens.",
                        evidence="Hard to go wrong with wide rice noodles and charred greens")]),
        "session-604-library-hours": EMPTY,
    }
    for day, (name, pairs) in enumerate(sessions.items()):
        sid = await add_session(env, "kore6")
        await converse(env, "kore6", sid, pairs[0], at(day), eager[name])

    doc = await self_model(env, "kore6")
    assert held_items(doc) == []  # nothing is established
    assert {i["status"] for i in items(doc).values()} <= {"tentative"}
    assert "traffic" not in " ".join(items(doc))  # the unsupported claim never got in
    assert developed_self_text(doc) is None
    assert await experiences_in(env) == []
    assert (await identity_row(env, "kore6")).trait_adjustments == {}
    assert await companion_config(env, "kore6") == before


def test_scenario_6_random_trait_drift_is_retired(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("random drift must not run")

    monkeypatch.setattr(evolution.random, "uniform", forbidden)
    state = evolution.IdentityState.defaults()
    slow_before = deepcopy(state.slow)
    for _ in range(3 * state.drift_interval):  # well past several former drift steps
        state.apply_evolution({"sentiment": "positive", "drift_interval": 1, "drift_rate": 1.0})
    assert state.slow == slow_before
    assert state.mood["energy"] != 0 and state.interaction_count == 3 * state.drift_interval  # mood still lives


def test_scenario_6_the_live_pipeline_leaves_session_traits_unchanged(monkeypatch):
    monkeypatch.setattr(evolution.random, "uniform", lambda *_a: 1 / 0)  # any random step would raise
    identity = models.IdentityStateModel(
        id=None, session_id=None, fixed={"name": "Kore"},
        slow={"openness": 0.8, "calmness": 0.85}, mood={"energy": 0.5, "warmth": 0.5},
    )
    conversation = [
        models.Message(session_id=None, role=role, content=f"{role} {n}")
        for n in range(3 * evolution.DEFAULT_DRIFT_INTERVAL) for role in ("user", "assistant")
    ]
    updated = evolution.calculate_drift(identity, conversation)  # the call UpdateIdentityStep makes
    assert updated.slow == identity.slow and updated.fixed == identity.fixed


# --------------------------------------------------------------------------
# 7. Stable identity anchors persist through growth
# --------------------------------------------------------------------------


async def test_scenario_7_growth_never_displaces_the_configured_anchors(env, provider):
    anchors = {"conscientiousness": 0.85, "openness": 0.8, "extraversion": 0.45, "agreeableness": 0.65}
    cid = await add_prompt_companion(env, name="Mercury", sliders=anchors)
    async with env.factory() as db:
        companion = await db.get(models.Companion, cid)
        before = (companion.persona, companion.description, companion.backstory,
                  companion.personality_sliders, companion.favorite_topics)

    # Four months of development: many sessions, several established views.
    subjects = ["balloon telemetry", "dark sky astronomy", "mechanical horology", "over-engineering"]
    async with env.factory() as db:
        for session_number in range(25):
            subject = subjects[session_number % len(subjects)]
            await apply_self_observations(
                db, companion_id=cid, user_id=USER_ID,
                observations=[SelfObservation(
                    kind="opinion", subject=subject, statement=f"I value {subject} for good reasons.",
                    reason=f"{subject} rewards careful work", excerpt=f"I value {subject}.",
                    significance="significant")],
                source=EvidenceSource(job_id=f"job-{session_number}", turn_id=f"t{session_number}",
                                      session_id=f"s{session_number}", source_at=at(session_number * 5),
                                      message_id=f"m{session_number}"),
            )
        await db.commit()

    sid = await new_session(env, cid)
    await standard_turn(env, sid)
    system = provider.system()

    # The configured character still defines who this is; development sits beside it.
    for text in ("Identity: Mercury", "Mercury is dry and observant.", "Mercury keeps the night archive.",
                 "Mercury grew up above a bookshop.", "conscientiousness 0.85", "openness 0.80"):
        assert text in system
    assert system.index("Mercury keeps the night archive.") < system.index("DEVELOPED SELF")
    assert "takes precedence" in system
    assert system.count("- Opinion") == 4  # the established views
    async with env.factory() as db:
        companion = await db.get(models.Companion, cid)
        identity = await db.get(models.CompanionIdentityModel, cid)
    assert (companion.persona, companion.description, companion.backstory,
            companion.personality_sliders, companion.favorite_topics) == before
    assert identity.trait_adjustments == {}  # no automatic numeric trait change


# --------------------------------------------------------------------------
# 8. Companion and room isolation
# --------------------------------------------------------------------------


async def test_scenario_8_companions_and_roleplay_scenes_keep_separate_histories(env):
    sessions = sessions_of("8")
    private, roleplay, aura = sessions["session-mercury-private"], sessions["session-roleplay-freighter"], sessions["session-aura-music"]
    await add_companion(env, "mercury8")
    await add_companion(env, "aura8")
    private_sid = await add_session(env, "mercury8")
    scene_room = await add_room(env, scene=True, companion_id="mercury8")
    scene_sid = await add_session(env, "mercury8", room_id=scene_room)
    aura_sid = await add_session(env, "aura8")

    stoic = "I spent time studying Marcus Aurelius's Meditations last night"
    await converse(env, "mercury8", private_sid, private[0], at(0), extraction(
        experiences=[experience("User spent time studying Marcus Aurelius's Meditations last night.", stoic,
                                topics=["stoicism"])],
        selfs=[view(subject="stoicism",
                    statement="Distinguishing what is within your voluntary control from external chaos is the ultimate mental architecture.",
                    evidence="Aurelius's emphasis on distinguishing what is within your voluntary control from external chaos is the ultimate mental architecture")]))

    # The model forgets the fiction flag: the scene room still keeps it out of real history.
    pair = (" ".join(["Navi, status of the hyperdrive engines! We have two pirate frigates locking on our sublight wake!",
                      "Dump the emergency plasma coolant into the manifold and initiate jump on my mark!"]),
            roleplay[0][1])
    await converse(env, "mercury8", scene_sid, pair, at(1), extraction(
        experiences=[experience("User ordered the plasma coolant dumped to initiate the jump.",
                                "Dump the emergency plasma coolant into the manifold and initiate jump on my mark",
                                significance="major")],
        selfs=[view(kind="interest", subject="hyperdrive", significance="significant",
                    statement="Hyperdrive core temperature is critical.",
                    evidence="Hyperdrive core temperature is critical at 850 Kelvin")]))

    await converse(env, "aura8", aura_sid, aura[0], at(2), extraction(
        selfs=[view(kind="interest", subject="d minor", statement="D minor has such a gorgeous, reflective weight.",
                    evidence="D minor has such a gorgeous, reflective weight")]))

    mercury_experiences = await visible(env, private_sid)
    assert [r.narrative for r in mercury_experiences] == ["User spent time studying Marcus Aurelius's Meditations last night."]
    assert len(await experiences_in(env)) == 1  # the roleplay scene promoted nothing to real history
    assert await visible(env, aura_sid) == []  # Aura has no Stoicism, no Mercury conversations

    mercury_doc, aura_doc = await self_model(env, "mercury8"), await self_model(env, "aura8")
    assert set(items(mercury_doc)) == {"opinion:stoicism"}  # no hyperdrive, no pirates
    assert set(items(aura_doc)) == {"interest:d minor"}  # no Marcus Aurelius
    async with env.factory() as db:
        facts = (await db.scalars(sa.select(models.SemanticFact.fact_text))).all()
    assert not any("hyperdrive" in f.lower() or "navi" in f.lower() for f in facts)


async def test_ineligible_turns_never_reach_the_self_model(env):
    await add_companion(env, "comp-a")
    await add_companion(env, "comp-b")
    view_of = extraction(selfs=[view(kind="interest", subject="maps", significance="significant",
                                     statement="I love maps.", evidence="I love maps")])
    reply = "I love maps and what they say about people."

    non_durable = await add_room(env, durable=False, companion_id="comp-a")
    scene = await add_room(env, scene=True, companion_id="comp-a")
    for room in (non_durable, scene):
        sid = await add_session(env, "comp-a", room_id=room)
        await converse(env, "comp-a", sid, ("Tell me about yourself.", reply), at(0), view_of)

    # A companion-less session has no self-model to develop.
    sid = await add_session(env, None)
    await converse(env, None, sid, ("Tell me about yourself.", reply), at(1), view_of)

    # An assistant reply attributed to another companion in a shared room is not comp-a's own speech.
    sid = await add_session(env, "comp-a")
    await say(env, sid, "Tell me about yourself.", reply, at=at(2), companion_id="comp-a")
    async with env.factory() as db:
        await db.execute(sa.update(models.Message).where(
            models.Message.session_id == sid, models.Message.role == "assistant"
        ).values(companion_id="comp-b"))
        await db.commit()
    await process(env, view_of)

    for cid in ("comp-a", "comp-b"):
        row = await identity_row(env, cid)
        assert row.self_model == {} and row.version == 1  # nothing written, no version bump


# --------------------------------------------------------------------------
# Prompt integration: one evolved identity through every chat mode
# --------------------------------------------------------------------------

HELD = "I find modal jazz more rewarding than bebop."
TENTATIVE = "I might enjoy gardening."


def developed_document():
    document, _ = next_self_model(
        None,
        [
            SelfObservation(kind="opinion", subject="modal jazz", statement=HELD,
                            reason="the space between notes leaves room to think",
                            excerpt="I think modal jazz rewards patience."),
            SelfObservation(kind="interest", subject="gardening", statement=TENTATIVE,
                            excerpt="Gardening comes up a lot."),
        ],
        EvidenceSource(job_id="j", turn_id="t", session_id="s", source_at=at(0), message_id="m"),
    )
    return document


async def set_self_model(env, cid, document):
    async with env.factory() as db:
        row = await db.get(models.CompanionIdentityModel, cid)
        row.self_model = document
        await db.commit()


async def test_standard_sse_and_spoken_turns_share_the_established_views(env, provider, monkeypatch):
    document = developed_document()
    cids = [await add_prompt_companion(env) for _ in range(3)]
    for cid in cids:
        await set_self_model(env, cid, document)
    sids = [await new_session(env, cid) for cid in cids]

    await standard_turn(env, sids[0])
    await stream_turn(env, sids[1])
    await spoken_turn(env, sids[2], cids[2], provider, monkeypatch)
    standard, streamed, spoken = provider.system(0), provider.system(1), provider.system(2)

    assert standard == streamed == spoken
    assert HELD in standard and "(because the space between notes leaves room to think)" in standard
    assert TENTATIVE not in standard and "gardening" not in standard  # tentative never speaks
    assert standard.index("Mercury keeps the night archive.") < standard.index("DEVELOPED SELF")
    assert len(provider.inputs) == 3  # one conversation generation per turn; no extra inference


async def test_an_empty_or_tentative_only_self_model_leaves_the_prompt_unchanged(env, provider):
    tentative_only, _ = next_self_model(
        None,
        [SelfObservation(kind="interest", subject="gardening", statement=TENTATIVE, excerpt="Gardening.")],
        EvidenceSource(job_id="j", turn_id="t", session_id="s", source_at=at(0)),
    )
    plain, with_tentative = await add_prompt_companion(env), await add_prompt_companion(env)
    await set_self_model(env, with_tentative, tentative_only)

    await standard_turn(env, await new_session(env, plain))
    await standard_turn(env, await new_session(env, with_tentative))

    assert provider.system(0) == provider.system(1)
    assert "DEVELOPED SELF" not in provider.system(0)


async def test_prompt_inspector_shows_which_developed_views_reached_the_prompt(env, provider):
    cid = await add_prompt_companion(env)
    await set_self_model(env, cid, developed_document())
    sid = await new_session(env, cid)

    await standard_turn(env, sid)

    (trace,) = await turn_module.trace_collector.list_traces(session_id=str(sid))
    (compile_step,) = [s for s in trace.steps if s.step_name == "compile_context"]
    sources = compile_step.metadata["identity_sources"]
    row = await identity_row(env, cid)
    assert sources["self_model"] == {"version": row.version, "shown": ["opinion:modal jazz"]}
    assert HELD in trace.prompt  # the inspector shows exactly what the provider received


# --------------------------------------------------------------------------
# Durable processing: retry safety, ordering, concurrency
# --------------------------------------------------------------------------


async def test_replaying_a_reflection_job_changes_nothing_and_calls_no_model(env):
    await add_companion(env, "comp-r")
    sid = await add_session(env, "comp-r")
    reply = "I think modal jazz rewards patience, because the space between notes leaves room to think."
    response = extraction(selfs=[view(
        subject="modal jazz", statement="I think modal jazz rewards patience.",
        reason="the space between notes leaves room to think", evidence="modal jazz rewards patience")])
    await say(env, sid, "What do you think of jazz?", reply, at=at(0), companion_id="comp-r")
    first = await process(env, response)
    assert len(first.calls) == 1
    before = await identity_row(env, "comp-r")
    snapshot = (deepcopy(before.self_model), before.version)
    assert items(before.self_model)["opinion:modal jazz"]["status"] == "held"

    async with env.factory() as db:  # a crash after the receipt, before success: effects replay
        job = await db.scalar(sa.select(models.BackgroundJob))
        assert job.payload["extraction_result"]["self_observations"]
        job.payload = {k: v for k, v in job.payload.items() if k != "effects_applied"}
        job.status, job.completed_at = "pending", None
        await db.commit()
    second = await process(env, EMPTY)

    after = await identity_row(env, "comp-r")
    assert second.calls == []  # the cached receipt is replayed; no second model call
    assert (after.self_model, after.version) == snapshot
    assert all(job.status == "succeeded" for job in await env.jobs())


async def test_out_of_order_processing_never_lets_an_older_turn_overturn_a_newer_position(env):
    await add_companion(env, "comp-o")
    reply_new = "I think modal jazz rewards patience, because the space between notes leaves room to think."
    reply_old = "I now think bebop is better, because its speed forces you to listen differently."
    sid = await add_session(env, "comp-o")
    await converse(env, "comp-o", sid, ("Jazz?", reply_new), at(10), extraction(selfs=[view(
        subject="modal jazz", statement="I think modal jazz rewards patience.",
        reason="the space between notes leaves room to think", evidence="modal jazz rewards patience")]))
    # A turn from before that, processed afterwards, claims to change the companion's mind.
    old_sid = await add_session(env, "comp-o")
    await converse(env, "comp-o", old_sid, ("Jazz?", reply_old), at(2), extraction(selfs=[view(
        subject="modal jazz", statement="I now think bebop is better.",
        reason="its speed forces you to listen differently", evidence="bebop is better",
        relation="opposing", change_of_mind=True)]))

    item = items(await self_model(env, "comp-o"))["opinion:modal jazz"]
    assert item["statement"] == "I think modal jazz rewards patience." and item["history"] == []


@needs_postgres
async def test_concurrent_reflection_jobs_for_one_companion_lose_nothing(env):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from chatbot.config import Settings
    from chatbot.perf.durable_handlers import make_durable_memory_extract_handler
    from chatbot.perf.durable_worker import DurableJobWorker
    from phase2_helpers import extraction_registry

    await add_companion(env, "comp-c")
    sid = await add_session(env, "comp-c")
    topics = ["maps", "tea", "chess"]
    for index, topic in enumerate(topics):
        await say(env, sid, "Tell me something.", f"I really enjoy {topic} a great deal.",
                  at=at(index), companion_id="comp-c")

    def handler_for(topic):
        registry, _ = extraction_registry(extraction(selfs=[view(
            kind="interest", subject=topic, significance="significant", statement=f"I enjoy {topic}.",
            evidence=f"I really enjoy {topic} a great deal")]))
        embeddings = SimpleNamespace(embed=AsyncMock(side_effect=lambda texts: [[0.0] * 1536 for _ in texts]))
        return make_durable_memory_extract_handler(
            env.factory, registry=registry, settings=Settings(_env_file=None), embeddings=embeddings)

    claimed = await DurableJobWorker(env.factory).claim_due_jobs()
    assert len(claimed) == 3
    ordered = sorted(claimed, key=lambda job: job.created_at)
    await asyncio.gather(*[handler_for(topic)(job) for job, topic in zip(ordered, topics)])  # all at once

    row = await identity_row(env, "comp-c")
    assert set(items(row.self_model)) == {f"interest:{topic}" for topic in topics}  # none lost
    assert row.version == 4  # one version step per applied job
    assert all(job.status == "succeeded" for job in await env.jobs())


# --------------------------------------------------------------------------
# PostgreSQL migration
# --------------------------------------------------------------------------


@needs_postgres
async def test_the_self_model_migration_on_postgres_upgrades_empty_and_refuses_to_discard(fresh_database):
    database = await fresh_database()  # built only by `alembic upgrade head`
    column = await database.scalar(
        "SELECT is_nullable || ':' || coalesce(column_default, '') FROM information_schema.columns "
        "WHERE table_name = 'companion_identities' AND column_name = 'self_model'"
    )
    assert column and column.startswith("NO:") and "{}" in column
    assert alembic(database.url, "current").stdout.split()[0] == "e5f2a7c9b8d4"

    async with database.factory() as db:
        user = models.User(id=USER_ID, external_id="x", display_name="U", username="u",
                           email="u@example.com", password_hash="h")
        db.add(user)
        await db.flush()
        db.add(models.Companion(companion_id="c1", user_id=USER_ID, name="C", persona="p",
                                description="d", backstory="b"))
        await db.flush()
        db.add(models.CompanionIdentityModel(companion_id="c1"))
        await db.commit()
    assert await database.scalar("SELECT CAST(self_model AS TEXT) FROM companion_identities") == "{}"

    async with database.factory() as db:
        row = await db.get(models.CompanionIdentityModel, "c1")
        row.self_model = developed_document()
        await db.commit()
    # Step below the conversational-threads revision (Phase 4.3a) first; the
    # refusal under test is the self-model revision's own downgrade guard.
    threads_down = alembic(database.url, "downgrade", "f3a9d6b1c4e7")
    assert threads_down.returncode == 0, threads_down.stderr[-2000:]
    refused = alembic(database.url, "downgrade", "-1")
    assert refused.returncode != 0 and "Refusing to discard a developed companion self-model" in refused.stderr

    async with database.factory() as db:
        row = await db.get(models.CompanionIdentityModel, "c1")
        row.self_model = {}
        await db.commit()
    assert alembic(database.url, "downgrade", "-1").returncode == 0
    assert await database.scalar(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'companion_identities' AND column_name = 'self_model'"
    ) == 0
    assert alembic(database.url, "upgrade", "head").returncode == 0
