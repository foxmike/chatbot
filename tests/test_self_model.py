"""Phase 3.2 — persistent companion self-model.

The accumulator is pure and tested directly. Persistence, ownership and
locking run against a throwaway database (SQLite by default; set
``PHASE0_PG_URL`` to a disposable PostgreSQL instance to also run the real
concurrent-write test). No model calls anywhere.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import uuid

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from chatbot.db import models
from chatbot.soul import self_model as sm
from chatbot.soul.self_model import (
    EvidenceSource,
    SelfModelTooLarge,
    SelfObservation,
    apply_self_observations,
    developed_self_text,
    held_items,
    load_self_model,
    next_self_model,
)

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
USER_A = uuid.UUID("abcdef01-0000-4000-8000-000000000001")
USER_B = uuid.UUID("abcdef02-0000-4000-8000-000000000002")


def obs(subject="modal jazz", statement="I find modal jazz more rewarding than bebop.", **kw):
    fields = dict(
        kind="opinion",
        subject=subject,
        statement=statement,
        excerpt="I think modal jazz rewards patience; the space between notes lets you think.",
        reason="the space between notes leaves room to think",
        significance="ordinary",
    )
    fields.update(kw)
    return SelfObservation(**fields)


def src(job="job-1", session="s1", day=0, **kw):
    return EvidenceSource(
        job_id=job, turn_id=f"turn-{job}", session_id=session, message_id=f"msg-{job}",
        source_at=T0 + timedelta(days=day), **kw,
    )


def run(doc, observations, source, **kw):
    return next_self_model(doc, observations, source, **kw)


def item(doc, key="opinion:modal jazz"):
    return doc["items"][key]


# --------------------------------------------------------------------------
# Opinion formation
# --------------------------------------------------------------------------


def test_a_reasoned_opinion_is_established_with_its_evidence():
    doc, change = run(None, [obs()], src())

    stored = item(doc)
    assert stored["status"] == "held" and stored["strength"] == 0.4
    assert stored["statement"] == "I find modal jazz more rewarding than bebop."
    assert stored["reason"] == "the space between notes leaves room to think"
    [ref] = stored["evidence"]
    assert ref["job_id"] == "job-1" and ref["turn_id"] == "turn-job-1"
    assert ref["session_id"] == "s1" and ref["message_id"] == "msg-job-1"
    assert ref["source_at"] == T0.isoformat()
    assert ref["excerpt"].startswith("I think modal jazz")
    assert [e["change"] for e in doc["log"]] == ["held"]
    assert change.applied == [{"key": "opinion:modal jazz", "change": "formed", "status": "held"}]


def test_an_unreasoned_opinion_stays_tentative_until_repeated_across_sessions():
    unreasoned = obs(reason="")
    doc, _ = run(None, [unreasoned], src("j1", "s1"))
    assert item(doc)["status"] == "tentative"
    assert developed_self_text(doc) is None  # tentative never reaches the prompt

    doc, _ = run(doc, [unreasoned], src("j2", "s1"))  # same session: still tentative
    assert item(doc)["status"] == "tentative"

    doc, _ = run(doc, [unreasoned], src("j3", "s2", day=1))
    assert item(doc)["status"] == "held"
    assert "modal jazz" in developed_self_text(doc)


def test_reinforcement_adds_support_and_strength_once_per_session():
    doc, _ = run(None, [obs()], src("j1", "s1"))
    doc, _ = run(doc, [obs()], src("j2", "s1"))
    assert item(doc)["strength"] == 0.4 and item(doc)["support"] == 2
    doc, _ = run(doc, [obs()], src("j3", "s2", day=1))
    assert item(doc)["strength"] == 0.5 and item(doc)["support"] == 3
    assert len(item(doc)["evidence"]) == 3


# --------------------------------------------------------------------------
# Opinion revision keeps history
# --------------------------------------------------------------------------

NEW_STATEMENT = "I now prefer bebop; its energy teaches me more."
NEW_REASON = "its speed forces you to listen differently"


def test_a_reasoned_change_of_mind_revises_the_opinion_and_keeps_the_old_one():
    doc, _ = run(None, [obs()], src("j1", "s1"))
    changed = obs(
        statement=NEW_STATEMENT, reason=NEW_REASON, relation="opposing", change_of_mind=True,
        excerpt="Having listened more, I have changed my mind: bebop's speed forces you to listen.",
    )
    doc, change = run(doc, [changed], src("j2", "s2", day=3))

    stored = item(doc)
    assert (stored["statement"], stored["reason"], stored["status"]) == (NEW_STATEMENT, NEW_REASON, "held")
    assert stored["strength"] == 0.4 and stored["opposition"] == 0
    [old] = stored["history"]
    assert old["statement"] == "I find modal jazz more rewarding than bebop."
    assert old["reason"] == "the space between notes leaves room to think"
    assert len(old["evidence_ids"]) == 1 and old["evidence_ids"][0] != stored["evidence"][0]["id"]
    assert [e["change"] for e in doc["log"]] == ["held", "revised"]
    assert change.applied[0]["change"] == "revised"


def test_pressure_without_the_companions_own_reasons_changes_nothing():
    doc, _ = run(None, [obs()], src("j1", "s1"))
    no_reason = obs(statement=NEW_STATEMENT, reason="", relation="opposing", change_of_mind=True)
    after, change = run(doc, [no_reason], src("j2", "s2", day=3))
    assert after == doc and not change.changed
    assert change.rejected[0][1] == "unreasoned_opposition"


def test_repeating_the_same_reason_is_not_a_change_of_mind():
    doc, _ = run(None, [obs()], src("j1", "s1"))
    same = obs(statement=NEW_STATEMENT, relation="opposing", change_of_mind=True)  # same reason
    doc, change = run(doc, [same], src("j2", "s2", day=3))
    assert item(doc)["statement"].startswith("I find modal jazz")  # not revised
    assert change.applied[0]["change"] == "challenged"


def test_opposition_revises_only_when_it_outweighs_support_across_sessions():
    doc, _ = run(None, [obs()], src("j1", "s1"))
    doc, _ = run(doc, [obs()], src("j1b", "s2", day=1))  # support 2 over two sessions
    opposing = obs(statement=NEW_STATEMENT, reason=NEW_REASON, relation="opposing")

    doc, _ = run(doc, [opposing], src("j2", "s3", day=2))
    assert item(doc)["statement"].startswith("I find") and item(doc)["opposition"] == 1
    doc, _ = run(doc, [opposing], src("j3", "s3", day=2))  # same session adds one more, still one session
    assert item(doc)["statement"].startswith("I find") and item(doc)["opposition"] == 2
    doc, change = run(doc, [opposing], src("j4", "s4", day=3))  # second session
    assert item(doc)["statement"] == NEW_STATEMENT
    assert item(doc)["history"][0]["statement"].startswith("I find")
    assert change.applied[0]["change"] == "revised"


def test_a_late_job_cannot_overturn_a_newer_position():
    doc, _ = run(None, [obs()], src("j1", "s1", day=10))
    late = obs(statement=NEW_STATEMENT, reason=NEW_REASON, relation="opposing", change_of_mind=True)
    doc, change = run(doc, [late], src("j0", "s2", day=2))  # happened before the stance
    assert item(doc)["statement"].startswith("I find")
    assert item(doc)["history"] == []
    assert change.applied[0]["change"] == "challenged"


# --------------------------------------------------------------------------
# Interests and preferences need repeated evidence
# --------------------------------------------------------------------------


def interest(**kw):
    return obs(kind="interest", subject="astronomy", statement="I keep returning to astronomy.",
               **{"reason": "", **kw})


def test_an_interest_needs_repeated_evidence_across_sessions():
    doc, _ = run(None, [interest(significance="significant")], src("j1", "s1"))
    assert item(doc, "interest:astronomy")["status"] == "tentative"

    third, change = run(doc, [interest()], src("j2", "s1"))  # session cap: 2 weight per session
    assert change.rejected[0][1] == "session_cap" and third == doc

    doc, _ = run(doc, [interest()], src("j3", "s2", day=2))  # support 3, two sessions
    assert item(doc, "interest:astronomy")["status"] == "held"
    assert "Interest — astronomy" in developed_self_text(doc)


def test_a_configured_favourite_topic_is_an_anchor_not_a_developed_interest():
    doc, change = run(None, [interest()], src(), anchors=["Astronomy"])
    assert doc["items"] == {} and change.rejected[0][1] == "configured_anchor"


def test_interests_are_not_revised_by_opposition():
    doc, _ = run(None, [interest(significance="significant")], src("j1", "s1"))
    doc, _ = run(doc, [interest()], src("j2", "s2", day=1))
    _, change = run(doc, [interest(relation="opposing", reason="bored")], src("j3", "s3", day=2))
    assert change.rejected[0][1] == "not_revisable"


def test_a_preference_follows_the_same_threshold():
    pref = obs(kind="preference", subject="tea", statement="I like tea in the evening.", reason="")
    doc, _ = run(None, [pref], src("j1", "s1"))
    doc, _ = run(doc, [pref], src("j2", "s2", day=1))
    assert item(doc, "preference:tea")["status"] == "tentative"  # support 2 < 3
    doc, _ = run(doc, [pref], src("j3", "s3", day=2))
    assert item(doc, "preference:tea")["status"] == "held"


def test_self_understanding_one_significant_reflection_counts_but_chatter_does_not():
    reflection = obs(kind="self_understanding", subject="listening",
                     statement="I have learned I listen better than I advise.", reason="")
    doc, _ = run(None, [reflection], src("j1", "s1"))
    assert item(doc, "self_understanding:listening")["status"] == "tentative"
    other, _ = run(None, [obs(kind="self_understanding", subject="listening", reason="",
                              statement="I have learned I listen better than I advise.",
                              significance="significant")], src("j1", "s1"))
    assert item(other, "self_understanding:listening")["status"] == "held"


# --------------------------------------------------------------------------
# Real-world evidence only; no arbitrary evolution
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("observation", "source", "reason"),
    [
        (obs(status="roleplay"), src(), "status_roleplay"),
        (obs(status="hypothetical"), src(), "status_hypothetical"),
        (obs(status="joke"), src(), "status_joke"),
        (obs(), src(in_roleplay=True), "roleplay"),
        (obs(excerpt="*leans in* As the pirate captain, I adore modal jazz."), src(), "roleplay_or_fiction"),
        (obs(excerpt="Let's pretend I hate jazz."), src(), "roleplay_or_fiction"),
        (obs(excerpt="In my story the captain loves modal jazz."), src(), "roleplay_or_fiction"),
        (obs(excerpt="If I were a musician I would love modal jazz."), src(), "roleplay_or_fiction"),
        (obs(excerpt="Just kidding, I love it."), src(), "roleplay_or_fiction"),
        (obs(excerpt=""), src(), "missing_evidence"),
        (obs(kind="value"), src(), "bad_kind"),
        (obs(significance="huge"), src(), "bad_significance"),
        (obs(subject="  !!  "), src(), "bad_subject"),
        (obs(statement="x" * 300), src(), "bad_statement"),
        (obs(relation="opposing"), src(), "no_such_item"),
    ],
)
def test_unreal_or_unsupported_observations_never_change_the_self_model(observation, source, reason):
    doc, change = run(None, [observation], source)
    assert doc["items"] == {} and doc["log"] == [] and not change.changed
    assert change.rejected[0][1] == reason


def test_no_observations_means_no_change_at_all():
    doc, _ = run(None, [obs()], src())
    again, change = run(doc, [], src("later", "s9", day=400))
    assert again == doc and not change.changed  # no decay, no drift, no maturity


def test_values_are_not_a_developed_kind():
    assert "value" not in sm.KINDS and "trait" not in sm.KINDS


# --------------------------------------------------------------------------
# Idempotency and bounds
# --------------------------------------------------------------------------


def test_replaying_the_same_job_changes_nothing():
    batch = [obs(), obs(kind="interest", subject="astronomy", statement="I keep returning to astronomy.",
                        reason="", significance="significant")]
    doc, first = run(None, batch, src("j1", "s1"))
    assert len(first.applied) == 2
    again, second = run(doc, batch, src("j1", "s1"))
    assert again == doc and not second.changed
    assert [r[1] for r in second.rejected] == ["already_applied"] * 2


def test_the_input_document_is_never_mutated():
    doc, _ = run(None, [obs()], src("j1", "s1"))
    before = json.dumps(doc, sort_keys=True)
    run(doc, [obs(statement=NEW_STATEMENT, reason=NEW_REASON, relation="opposing",
                  change_of_mind=True)], src("j2", "s2", day=1))
    assert json.dumps(doc, sort_keys=True) == before


def test_item_caps_hold_and_only_tentative_items_are_evicted():
    doc = None
    for index in range(sm.MAX_ITEMS["opinion"]):
        doc, _ = run(doc, [obs(subject=f"topic{index}")], src(f"j{index}", "s1", day=index))
    assert len(doc["items"]) == 24  # all held
    full, change = run(doc, [obs(subject="one too many")], src("jx", "s1", day=40))
    assert full == doc and change.rejected[0][1] == "kind_full"

    doc = None
    for index in range(sm.MAX_ITEMS["opinion"]):
        doc, _ = run(doc, [obs(subject=f"topic{index}", reason="")], src(f"j{index}", "s1", day=index))
    doc, change = run(doc, [obs(subject="newest", reason="")], src("jx", "s1", day=40))
    assert len(doc["items"]) == 24 and "opinion:newest" in doc["items"]
    assert "opinion:topic0" not in doc["items"]  # the oldest tentative item made room


def test_evidence_history_log_and_ledger_stay_bounded():
    doc, _ = run(None, [obs(reason="because a")], src("j0", "s0"))
    for index in range(1, 14):
        flip = obs(statement=f"Position {index}.", reason=f"reason {index}", relation="opposing",
                   change_of_mind=True)
        doc, _ = run(doc, [flip], src(f"j{index}", f"s{index}", day=index))
    stored = item(doc)
    assert len(stored["history"]) == sm.MAX_HISTORY
    assert stored["history"][0]["statement"] == "Position 12."  # newest first
    assert len(doc["log"]) <= sm.MAX_LOG

    doc, _ = run(None, [obs()], src("e0", "s0"))
    for index in range(1, 20):
        doc, _ = run(doc, [obs()], src(f"e{index}", f"s{index}", day=index))
    stored = item(doc)
    assert len(stored["evidence"]) == sm.MAX_EVIDENCE
    assert stored["evidence"][0]["job_id"] == "e0"  # the first and the most recent are kept
    assert stored["evidence"][-1]["job_id"] == "e19"
    assert len(stored["sessions"]) <= sm.MAX_SESSIONS
    assert len(doc["applied"]) <= sm.MAX_APPLIED


def test_an_oversized_document_fails_visibly_instead_of_truncating(monkeypatch):
    monkeypatch.setattr(sm, "MAX_DOCUMENT_BYTES", 200)
    with pytest.raises(SelfModelTooLarge):
        run(None, [obs()], src())


# --------------------------------------------------------------------------
# Prompt view: only firmly held development, deterministic and bounded
# --------------------------------------------------------------------------


def test_prompt_text_shows_only_held_items_deterministically_and_bounded():
    doc = None
    batch = [obs(subject=f"subject {i}", statement=f"Statement {i} " + "x" * 100) for i in range(10)]
    batch.append(obs(subject="maybe", reason="", statement="A tentative thought."))
    for index, observation in enumerate(batch):
        doc, _ = run(doc, [observation], src(f"j{index}", "s1", day=index))
    text = developed_self_text(doc)
    assert text == developed_self_text(json.loads(json.dumps(doc)))  # same state, same prompt
    assert "tentative thought" not in text
    assert text.count("- Opinion") <= sm.PROMPT_LIMITS["opinion"]
    assert len(text) <= sm.MAX_PROMPT_CHARS
    assert text.startswith("Views and interests you have developed")
    assert developed_self_text({}) is None and developed_self_text(None) is None
    assert [i["status"] for i in held_items(doc)] == ["held"] * 10


def test_known_items_text_lists_held_and_tentative_for_an_extractor():
    doc, _ = run(None, [obs(), obs(subject="tea", kind="preference", reason="", statement="I like tea.")],
                 src())
    text = sm.known_items_text(doc)
    assert "opinion:modal jazz: I find modal jazz" in text and "preference:tea: I like tea." in text


# --------------------------------------------------------------------------
# Database: persistence, versions, ownership, isolation, concurrency
# --------------------------------------------------------------------------

PG_URL = os.environ.get("PHASE0_PG_URL")
_TABLES = [models.User, models.Voice, models.Companion, models.CompanionIdentityModel]


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(PG_URL if PG_URL else f"sqlite+aiosqlite:///{tmp_path / 'sm.db'}")
    async with engine.begin() as connection:
        if PG_URL:
            for model in reversed(_TABLES):
                await connection.execute(sa.text(f'DROP TABLE IF EXISTS "{model.__tablename__}" CASCADE'))
        for model in _TABLES:
            await connection.run_sync(lambda sync, m=model: m.__table__.create(sync))
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        for user_id, tag in ((USER_A, "a"), (USER_B, "b")):
            db.add(models.User(id=user_id, external_id=tag, display_name=tag, username=tag,
                               email=f"{tag}@example.com", password_hash="h"))
        await db.commit()
    yield session_factory
    if PG_URL:
        async with engine.begin() as connection:
            for model in reversed(_TABLES):
                await connection.execute(sa.text(f'DROP TABLE IF EXISTS "{model.__tablename__}" CASCADE'))
    await engine.dispose()


async def make_companion(factory, companion_id, user_id=USER_A, topics=None, with_identity=True):
    async with factory() as db:
        db.add(models.Companion(companion_id=companion_id, user_id=user_id, name=companion_id,
                                persona="p", description="d", backstory="b",
                                personality_sliders={"openness": 0.7}, favorite_topics=topics))
        await db.flush()
        if with_identity:
            db.add(models.CompanionIdentityModel(companion_id=companion_id))
        await db.commit()


async def apply(factory, companion_id, observations, source, user_id=USER_A):
    async with factory() as db:
        change = await apply_self_observations(
            db, companion_id=companion_id, user_id=user_id, observations=observations, source=source
        )
        await db.commit()
        return change


async def identity(factory, companion_id):
    async with factory() as db:
        return await db.get(models.CompanionIdentityModel, companion_id)


async def test_the_self_model_persists_across_sessions_and_restarts(factory):
    await make_companion(factory, "c1")
    change = await apply(factory, "c1", [obs()], src("j1", "s1"))
    assert change.changed and change.version == 2

    row = await identity(factory, "c1")  # a brand-new database session
    assert row.self_model["items"]["opinion:modal jazz"]["status"] == "held"
    async with factory() as db:
        doc = await load_self_model(db, companion_id="c1", user_id=USER_A)
    assert "modal jazz" in developed_self_text(doc)

    await apply(factory, "c1", [obs()], src("j2", "s2", day=1))
    assert (await identity(factory, "c1")).self_model["items"]["opinion:modal jazz"]["support"] == 2


async def test_configuration_anchors_and_traits_are_never_touched(factory):
    await make_companion(factory, "c1", topics=["astronomy"])
    for index in range(30):
        await apply(factory, "c1", [obs(subject=f"s{index % 5}")], src(f"j{index}", f"s{index}", day=index))
    await apply(factory, "c1", [interest()], src("ji", "s1"))  # configured topic: ignored

    row = await identity(factory, "c1")
    assert row.trait_adjustments == {}  # automatic numeric trait changes are deferred
    assert "interest:astronomy" not in row.self_model["items"]
    async with factory() as db:
        companion = await db.get(models.Companion, "c1")
        assert (companion.persona, companion.backstory, companion.description) == ("p", "b", "d")
        assert companion.personality_sliders == {"openness": 0.7}
        assert companion.favorite_topics == ["astronomy"]


async def test_rejected_or_replayed_observations_write_nothing_and_keep_the_version(factory):
    await make_companion(factory, "c1")
    await apply(factory, "c1", [obs()], src("j1", "s1"))
    version = (await identity(factory, "c1")).version

    rejected = await apply(factory, "c1", [obs(status="roleplay", subject="x")], src("j2", "s1"))
    replay = await apply(factory, "c1", [obs()], src("j1", "s1"))
    assert not rejected.changed and not replay.changed
    assert (await identity(factory, "c1")).version == version


async def test_a_companion_without_an_identity_row_gets_one(factory):
    await make_companion(factory, "legacy", with_identity=False)
    await apply(factory, "legacy", [obs()], src())
    assert (await identity(factory, "legacy")).self_model["items"]


async def test_each_companion_has_its_own_self_model_and_ownership_is_enforced(factory):
    await make_companion(factory, "c1")
    await make_companion(factory, "c2")
    await make_companion(factory, "theirs", user_id=USER_B)
    await apply(factory, "c1", [obs()], src("j1", "s1"))

    assert (await identity(factory, "c2")).self_model == {}
    assert (await identity(factory, "theirs")).self_model == {}
    for companion_id, user in (("c1", USER_B), ("theirs", USER_A), ("missing", USER_A)):
        with pytest.raises(PermissionError):
            await apply(factory, companion_id, [obs()], src("jx", "s1"), user_id=user)
        async with factory() as db:
            with pytest.raises(PermissionError):
                await load_self_model(db, companion_id=companion_id, user_id=user)
    assert (await identity(factory, "theirs")).self_model == {}


async def test_an_update_re_reads_the_row_so_an_interleaved_commit_is_never_lost(factory):
    await make_companion(factory, "c1")
    async with factory() as stale:
        before = await stale.get(models.CompanionIdentityModel, "c1")  # loaded early
        assert before.self_model == {}
        await apply(factory, "c1", [obs(subject="tea", kind="preference", reason="", statement="Tea.")],
                    src("j1", "s1"))  # another transaction commits meanwhile
        change = await apply_self_observations(
            stale, companion_id="c1", user_id=USER_A, observations=[obs()], source=src("j2", "s1")
        )
        await stale.commit()
    assert change.changed
    items = (await identity(factory, "c1")).self_model["items"]
    assert set(items) == {"preference:tea", "opinion:modal jazz"}  # nothing overwritten


async def test_the_version_column_rejects_a_stale_write(factory):
    await make_companion(factory, "c1")
    async with factory() as first, factory() as second:
        a = await first.get(models.CompanionIdentityModel, "c1")
        b = await second.get(models.CompanionIdentityModel, "c1")
        a.self_model = {"schema": 1, "items": {}, "log": [], "applied": ["a"]}
        await first.commit()
        b.self_model = {"schema": 1, "items": {}, "log": [], "applied": ["b"]}
        with pytest.raises(StaleDataError):
            await second.commit()
    assert (await identity(factory, "c1")).self_model["applied"] == ["a"]


@pytest.mark.skipif(not PG_URL, reason="needs PHASE0_PG_URL (row locks are not available in SQLite)")
async def test_concurrent_updates_to_one_companion_serialize_without_loss(factory):
    await make_companion(factory, "c1")
    await make_companion(factory, "c2")

    async def one(companion_id, index):
        return await apply(
            factory, companion_id,
            [obs(subject=f"subject {index}", statement=f"Statement {index}.")],
            src(f"job-{companion_id}-{index}", f"s{index}", day=index),
        )

    results = await asyncio.gather(*[one("c1", i) for i in range(8)], *[one("c2", i) for i in range(3)])
    assert all(r.changed for r in results)
    first = await identity(factory, "c1")
    assert len(first.self_model["items"]) == 8 and first.version == 9
    assert len((await identity(factory, "c2")).self_model["items"]) == 3


# --------------------------------------------------------------------------
# Migration
# --------------------------------------------------------------------------


def _migration():
    path = next((ROOT / "migrations" / "versions").glob("f3a9d6b1c4e7_*.py"))
    spec = importlib.util.spec_from_file_location("self_model_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_the_migration_adds_an_empty_column_and_refuses_to_discard_a_developed_model(tmp_path):
    migration = _migration()
    assert migration.down_revision == "d41e8a7c502b"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mig.db'}")

    def upgrade_downgrade(connection):
        connection.execute(sa.text(
            "CREATE TABLE companion_identities (companion_id VARCHAR(36) PRIMARY KEY, "
            "trait_adjustments JSON NOT NULL DEFAULT '{}')"
        ))
        connection.execute(sa.text("INSERT INTO companion_identities (companion_id) VALUES ('c1')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        value = connection.execute(sa.text("SELECT CAST(self_model AS TEXT) FROM companion_identities")).scalar()
        connection.execute(sa.text("UPDATE companion_identities SET self_model = '{\"items\": {\"k\": 1}}'"))
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="Refusing"):
                migration.downgrade()
        connection.execute(sa.text("UPDATE companion_identities SET self_model = '{}'"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        columns = [c["name"] for c in sa.inspect(connection).get_columns("companion_identities")]
        return value, columns

    async with engine.begin() as connection:
        value, columns = await connection.run_sync(upgrade_downgrade)
    await engine.dispose()
    assert value == "{}"  # existing rows start empty: no backfill, no behaviour change
    assert columns == ["companion_id", "trait_adjustments"]
