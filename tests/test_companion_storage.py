"""Task 1.2 acceptance on disposable PostgreSQL built solely by Alembic."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm.exc import StaleDataError
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from test_timeline_migration import fresh_database as _fresh_database, alembic, needs_postgres
from chatbot.db import models as m
from chatbot.api import conversation, config, sessions, streaming
from chatbot.auth.dependencies import CurrentUser
from chatbot.companions.companion import CompanionManager, CompanionCreateRequest
from chatbot.soul.persistent_identity import effective_traits, initial_session_identity
from chatbot.soul.persona_prompt import PersonaPromptAssembler

REVISION = "a12f907bc341"
fresh_database = _fresh_database


async def seed(db, *, create=True):
    uid = uuid.uuid4()
    async with db.factory() as s:
        s.add(
            m.User(
                id=uid,
                external_id=str(uid),
                username=uid.hex,
                email=f"{uid.hex}@example.com",
                display_name="U",
                password_hash="h",
            )
        )
        await s.commit()
        if create:
            c = await CompanionManager(s).create(
                str(uid),
                CompanionCreateRequest(
                    name="Mercury",
                    persona="Dry and observant.",
                    personality_sliders={"openness": 0.8},
                ),
            )
            cid = c.companion_id
        else:
            cid = str(uuid.uuid4())
            s.add(
                m.Companion(
                    companion_id=cid,
                    user_id=uid,
                    name="Legacy",
                    personality_sliders={"openness": 0.8},
                )
            )
            await s.commit()
        sid = uuid.uuid4()
        s.add(m.Session(id=sid, user_id=uid, companion_id=cid, title="Original"))
        await s.commit()
    return uid, cid, sid


def user(uid):
    return CurrentUser(
        user_id=str(uid), username="u", email="u@example.com", display_name="U", token="t"
    )


@needs_postgres
async def test_schema_defaults_and_restart(fresh_database):
    db = await fresh_database()
    uid, cid, sid = await seed(db)
    assert await db.scalar("select version_num from alembic_version") == REVISION

    def compare(connection):
        def compare_default(ctx, inspected, metadata, reflected, default, rendered):
            # PostgreSQL JSON has no equality operator; compare its actual default as JSONB.
            if isinstance(metadata.type, sa.JSON):
                return not connection.scalar(
                    text(f"SELECT ({reflected})::jsonb = ({rendered})::jsonb")
                )
            return None

        diff = compare_metadata(
            MigrationContext.configure(
                connection, opts={"compare_type": True, "compare_server_default": compare_default}
            ),
            m.Base.metadata,
        )
        return [
            d
            for d in diff
            if "companion_identities" in repr(d) or "companion_relationships" in repr(d)
        ]

    assert await db.run_sync(compare) == []
    assert (
        await db.scalar(
            "select count(*) from information_schema.columns where table_name='companion_identities' and column_name='self_model'"
        )
        == 0
    )
    async with db.factory() as s:
        identity = await s.get(m.CompanionIdentityModel, cid)
        assert identity.trait_adjustments == {} and identity.version == 1
        # Simulate a future authorized durable change; no runtime evolution writer is introduced.
        identity.trait_adjustments = {"openness": 0.1}
        await s.commit()
    engine = create_async_engine(db.url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            identity = await s.get(m.CompanionIdentityModel, cid)
            c = await s.get(m.Companion, cid)
            assert effective_traits(c, identity)["openness"] == pytest.approx(0.9)
            assert c.personality_sliders == {"openness": 0.8}
            old = m.IdentityStateModel(
                session_id=sid, fixed={}, slow={"openness": 0.01}, mood={"mood": "angry"}
            )
            s.add(old)
            created = await sessions.create_session(
                sessions.SessionCreateRequest(companion_id=cid), user(uid), s
            )
            sid2 = uuid.UUID(created.session_id)
            stored = await s.scalar(
                select(m.IdentityStateModel).where(m.IdentityStateModel.session_id == sid2)
            )
            assert stored.slow["openness"] == pytest.approx(0.9)
            assert stored.mood != old.mood
            assert (await s.get(m.CompanionIdentityModel, cid)).trait_adjustments == {
                "openness": 0.1
            }
    finally:
        await engine.dispose()
    assert await db.scalar("select count(*) from companion_identities") == 1


@needs_postgres
async def test_relationship_api_shared_across_sessions_and_ownership(fresh_database):
    db = await fresh_database()
    uid, cid, sid = await seed(db)
    async with db.factory() as s:
        sid2 = uuid.uuid4()
        s.add(
            m.Session(
                id=sid2, user_id=uid, companion_id=cid, title="Other", mood_state={"mood": "sad"}
            )
        )
        await s.commit()
        result = await config.put_relationship_config(
            sid, config.RelationshipConfigRequest(tone="playful", warmth=0.9), user(uid), s
        )
        assert result.tone == "playful"
    async with db.factory() as s:
        result = await config.get_relationship_config(sid2, user(uid), s)
        assert result.tone == "playful" and result.warmth == 0.9
        assert (await s.get(m.CompanionRelationshipModel, cid)).depth == 0
        assert (await s.get(m.Session, sid2)).mood_state == {"mood": "sad"}
        with pytest.raises(Exception) as error:
            await config.get_relationship_config(sid, user(uuid.uuid4()), s)
        assert error.value.status_code == 404
        durable = {}
        profile, persona = await conversation.load_companion_inputs(
            s, await s.get(m.Session, sid), str(uid), durable_state=durable
        )
        assert (
            profile["name"] == "Mercury" and persona["custom_instructions"] == "Dry and observant."
        )
        assert durable["companion_relationship"]["tone"] == "playful"
        assert await conversation.load_companion_inputs(
            s, await s.get(m.Session, sid), str(uuid.uuid4())
        ) == (None, None)
    assert await db.scalar("select count(*) from companion_relationships") == 1
    assert await db.scalar("select count(*) from relationship_configs") == 0


@needs_postgres
async def test_atomic_counts_rollback_and_unrelated_companions(fresh_database):
    db = await fresh_database()
    uid, cid, sid = await seed(db)
    uid2, cid2, sid2 = await seed(db)

    async def completed():
        async with db.factory() as s:
            conversation_id = uuid.uuid4()
            s.add(m.Session(id=conversation_id, user_id=uid, companion_id=cid, title="Concurrent"))
            await s.flush()
            await conversation.add_turn_messages(
                s,
                session_id=conversation_id,
                user_id=uid,
                companion_id=cid,
                user_message="Hi",
                assistant_message="Hello",
                turn_id=str(uuid.uuid4()),
            )
            await conversation.record_completed_interaction(s, cid, uid)
            await s.commit()

    await asyncio.gather(*(completed() for _ in range(12)))
    assert (
        await db.scalar(
            "select completed_turns from companion_relationships where companion_id=:cid", cid=cid
        )
        == 12
    )
    async with db.factory() as s:
        await conversation.record_completed_interaction(s, cid, uid)
        await s.rollback()
        await conversation.record_completed_interaction(s, cid, uid2)
        await s.commit()
        await conversation.record_completed_interaction(s, cid, uid)
        # Unrelated companion can commit while first transaction retains its row lock.
        async with db.factory() as other:
            await asyncio.wait_for(conversation.record_completed_interaction(other, cid2, uid2), 2)
            await other.commit()
        await s.rollback()
    assert (
        await db.scalar(
            "select completed_turns from companion_relationships where companion_id=:cid", cid=cid
        )
        == 12
    )
    async with db.factory() as a, db.factory() as b:
        ia = await a.get(m.CompanionIdentityModel, cid)
        ib = await b.get(m.CompanionIdentityModel, cid)
        ia.trait_adjustments = {"openness": 0.1}
        await a.commit()
        ib.trait_adjustments = {"openness": -0.1}
        with pytest.raises(StaleDataError):
            await b.commit()


@needs_postgres
async def test_deletion_lifetimes(fresh_database):
    db = await fresh_database()
    uid, cid, sid = await seed(db)
    async with db.factory() as s:
        with pytest.raises(Exception) as error:
            await CompanionManager(s).delete(str(uid), cid)
        assert error.value.status_code == 409
        await s.rollback()
        await sessions.delete_session(str(sid), user(uid), s)
        assert await s.get(m.CompanionIdentityModel, cid) is not None
        assert await s.get(m.CompanionRelationshipModel, cid) is not None
        assert not await CompanionManager(s).delete(str(uuid.uuid4()), cid)
        assert await CompanionManager(s).delete(str(uid), cid)
        assert await s.get(m.CompanionIdentityModel, cid) is None
        assert await s.get(m.CompanionRelationshipModel, cid) is None


@needs_postgres
async def test_missing_records_and_companionless_compatibility(fresh_database):
    db = await fresh_database()
    uid, cid, sid = await seed(db, create=False)
    async with db.factory() as s:
        durable = {}
        await conversation.load_companion_inputs(
            s, await s.get(m.Session, sid), str(uid), durable_state=durable
        )
        assert durable["companion_relationship"]["type"] == "friend"
        assert await s.get(m.CompanionIdentityModel, cid) is None  # no read-side inserts
        assert await s.get(m.CompanionRelationshipModel, cid) is None
        # Lazy settings creation is serialized by the parent lock.
        await config.put_relationship_config(
            sid, config.RelationshipConfigRequest(tone="formal"), user(uid), s
        )
        legacy_sid = uuid.uuid4()
        s.add(m.Session(id=legacy_sid, user_id=uid, title="No companion"))
        await s.commit()
        await config.put_relationship_config(
            legacy_sid, config.RelationshipConfigRequest(tone="neutral"), user(uid), s
        )
        assert (await config.get_relationship_config(legacy_sid, user(uid), s)).tone == "neutral"
        assert len((await s.execute(select(m.RelationshipConfigModel))).scalars().all()) == 1


@needs_postgres
async def test_backfill_latest_conflicts_drift_and_interrupted_turns(fresh_database):
    db = await fresh_database(upgrade_to="f687a630a8d1")
    uid, cid, sid = await seed(db, create=False)
    uid2, cid2, sid2 = await seed(db, create=False)
    async with db.factory() as s:
        now = datetime.now(timezone.utc)
        for tone, offset in [("formal", -1), ("playful", 0)]:
            s.add(
                m.RelationshipConfigModel(
                    session_id=sid,
                    type="friend",
                    tone=tone,
                    directness=0.4,
                    warmth=0.8,
                    initiative_level=0.3,
                    content_level="deep",
                    created_at=now + timedelta(days=offset),
                    updated_at=now + timedelta(days=offset),
                )
            )
        s.add(
            m.IdentityStateModel(
                session_id=sid, fixed={}, slow={"openness": 0.01}, mood={"mood": "angry"}
            )
        )
        wrong_sid = uuid.uuid4()
        s.add(
            m.Session(
                id=wrong_sid, user_id=uid2, companion_id=cid, title="Inconsistent legacy ownership"
            )
        )
        await s.flush()
        s.add(
            m.RelationshipConfigModel(
                session_id=wrong_sid,
                type="friend",
                tone="sarcastic",
                directness=0.1,
                warmth=0.1,
                initiative_level=0.1,
                content_level="light",
                updated_at=now + timedelta(days=1),
            )
        )
        for role, turn, content in [
            ("user", "ok", "hello"),
            ("assistant", "ok", "reply"),
            ("assistant", None, "old"),
            ("assistant", "cut", "prefix"),
            ("system", "cut", conversation._INTERRUPTION_MARKER),
        ]:
            s.add(
                m.Message(
                    session_id=sid,
                    user_id=uid,
                    companion_id=cid,
                    role=role,
                    turn_id=turn,
                    content=content,
                )
            )
        await s.commit()
    done = alembic(db.url, "upgrade", "head")
    assert done.returncode == 0, done.stderr
    assert "Relationship conflict" in done.stderr and cid in done.stderr
    async with db.factory() as s:
        row = await s.get(m.CompanionRelationshipModel, cid)
        assert row.tone == "playful" and row.completed_turns == 2 and row.depth == 0
        assert (
            row.first_interaction_at is not None
            and row.last_interaction_at >= row.first_interaction_at
        )
        assert (await s.get(m.CompanionIdentityModel, cid)).trait_adjustments == {}
        assert (await s.get(m.CompanionRelationshipModel, cid2)).tone == "warm"
        assert (
            await s.scalar(
                select(m.IdentityStateModel).where(m.IdentityStateModel.session_id == sid)
            )
        ).slow == {"openness": 0.01}
    assert await db.scalar("select count(*) from relationship_configs") == 3
    assert alembic(db.url, "upgrade", "head").returncode == 0
    assert alembic(db.url, "downgrade", "f687a630a8d1").returncode == 0
    assert await db.scalar("select count(*) from relationship_configs") == 3
    assert alembic(db.url, "upgrade", "head").returncode == 0


@needs_postgres
async def test_migration_refuses_existing_table_without_data_changes(fresh_database):
    db = await fresh_database(upgrade_to="f687a630a8d1")
    await db.run_sync(
        lambda c: c.execute(text("create table companion_identities (sentinel text)"))
    )
    result = alembic(db.url, "upgrade", "head")
    assert result.returncode != 0 and "refusing to adopt" in result.stderr
    assert await db.scalar("select version_num from alembic_version") == "f687a630a8d1"


def test_empty_durable_seed_and_prompt_match_original():
    # Frozen from the pre-Task-1.2 seed and assembler at the recorded source commit.
    golden = json.loads(
        (Path(__file__).parent / "fixtures/companion_seed_phase12.json").read_text()
    )
    # Task 1.3 changes the seed only by the approved removals: the generic core
    # values applied to every companion (D8) and the relationship defaults, now
    # owned by the durable relationship (D7). Everything else is unchanged.
    removed_fixed = {"core_values", "relationship_type"}
    removed_slow = {"relationship_warmth", "relationship_directness", "initiative_level"}
    removed_lines = (
        "- core values:", "- relationship type:", "- relationship warmth:",
        "- relationship directness:", "- initiative level:",
    )
    c = m.Companion(name="Mercury", personality_sliders={"openness": 0.8}, persona="Dry.")
    state = initial_session_identity(c, SimpleNamespace(trait_adjustments={}))
    assert state.fixed == {k: v for k, v in golden["fixed"].items() if k not in removed_fixed}
    assert state.slow == {k: v for k, v in golden["slow"].items() if k not in removed_slow}
    assert state.mood == golden["mood"]
    expected_prompt = "\n".join(
        line for line in golden["prompt"].split("\n") if not line.startswith(removed_lines)
    )
    assert PersonaPromptAssembler().assemble({}, identity_state=state) == expected_prompt


@needs_postgres
async def test_completion_transactions_count_only_successful_turns(fresh_database, monkeypatch):
    from unittest.mock import AsyncMock, Mock
    from fastapi import FastAPI
    from starlette.requests import Request
    from chatbot.api import chat

    db = await fresh_database()
    uid, cid, sid = await seed(db)
    # Exercise the actual standard adapter with the shared DB preparation and persistence.
    monkeypatch.setattr(conversation, "prepare_memory_workflow", AsyncMock())
    monkeypatch.setattr(conversation, "schedule_title_generation", Mock())
    mode = {"failed": False}

    async def pipeline(ctx):
        assert "companion_identity" in ctx.metadata
        ctx.final_response = "Provider error: unavailable" if mode["failed"] else "Hello"
        ctx.provider_response = ctx.final_response
        return ctx

    monkeypatch.setattr(chat, "_run_pipeline", pipeline)
    request = Request({"type": "http", "app": FastAPI(), "headers": []})
    async with db.factory() as s:
        await chat.chat(
            request, chat.ChatRequest(session_id=str(sid), message="hello"), user(uid), s
        )
        mode["failed"] = True
        await chat.chat(
            request, chat.ChatRequest(session_id=str(sid), message="hello"), user(uid), s
        )
    assert (
        await db.scalar(
            "select completed_turns from companion_relationships where companion_id=:c", c=cid
        )
        == 1
    )
    async with db.factory() as s:
        for interrupted, completed in [(False, True), (True, True), (False, False)]:
            await streaming._persist_messages(
                session_id=sid,
                companion_id=cid,
                current_user=user(uid),
                user_message="hello",
                assistant_message="reply",
                turn_id=str(uuid.uuid4()),
                interrupted=interrupted,
                completed=completed,
                strict=True,
                db=s,
            )
    assert (
        await db.scalar(
            "select completed_turns from companion_relationships where companion_id=:c", c=cid
        )
        == 2
    )
    assert (
        await db.scalar(
            "select count(*) from messages where session_id=:sid and role='assistant'", sid=sid
        )
        == 5
    )


@needs_postgres
async def test_one_preparation_query_room_isolation_and_loading_overhead(fresh_database):
    from time import perf_counter
    from statistics import median

    db = await fresh_database()
    uid, cid, sid = await seed(db)
    async with db.factory() as s:
        room_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        for room in room_ids:
            s.add(m.Room(room_id=room, user_id=str(uid), display_name="Room", room_type="roleplay"))
        await s.flush()
        sid2 = uuid.uuid4()
        s.add(
            m.Session(id=sid2, user_id=uid, companion_id=cid, room_id=room_ids[1], title="Roleplay")
        )
        await s.flush()
        first = await s.get(m.Session, sid)
        first.room_id = room_ids[0]
        s.add_all(
            [
                m.Message(
                    session_id=sid, user_id=uid, companion_id=cid, role="user", content="room one"
                ),
                m.Message(
                    session_id=sid2, user_id=uid, companion_id=cid, role="user", content="room two"
                ),
            ]
        )
        await s.commit()
        assert [
            r["content"] for r in await conversation.load_db_session_history(s, sid, room_ids[0])
        ] == ["room one"]
        assert await conversation.load_db_session_history(s, sid, room_ids[1]) == []
        assert [
            r["content"] for r in await conversation.load_db_session_history(s, sid2, room_ids[1])
        ] == ["room two"]
        records = [await s.get(m.Session, value) for value in (sid, sid2)]
        counter = []

        def statement(*args):
            counter.append(args[2])

        sa.event.listen(db.engine.sync_engine, "before_cursor_execute", statement)
        try:
            values = []
            for record in records:
                start = len(counter)
                durable = {}
                await conversation.load_companion_inputs(s, record, str(uid), durable_state=durable)
                assert len(counter) == start + 1
                values.append(durable)
            assert values[0] == values[1]  # identity is companion-scoped across rooms
            for record, room in zip(records, room_ids):
                ctx = conversation.build_turn_context(
                    None,
                    session_id=str(record.id),
                    user_message="Hi",
                    history=[],
                    persona={},
                    db=s,
                    user_id=str(uid),
                    companion_id=cid,
                    room_id=room,
                    durable_state=values[0],
                )
                assert ctx.metadata["room_id"] == room
                assert ctx.metadata["session_id"] == str(record.id)
            # Compare the old single-companion fetch and the joined fetch on the same connection.
            timings = {"baseline": [], "joined": []}
            for _ in range(30):
                for kind in timings:
                    start = perf_counter()
                    if kind == "baseline":
                        await CompanionManager(s).get(str(uid), cid)
                    else:
                        await conversation.load_companion_inputs(
                            s, records[0], str(uid), durable_state={}
                        )
                    timings[kind].append((perf_counter() - start) * 1000)
            print(
                "PREPARATION_MEDIAN_MS",
                {key: round(median(value), 3) for key, value in timings.items()},
            )
        finally:
            sa.event.remove(db.engine.sync_engine, "before_cursor_execute", statement)


@needs_postgres
async def test_concurrent_settings_create_one_relationship(fresh_database):
    db = await fresh_database()
    uid, cid, sid = await seed(db, create=False)

    async def edit(tone):
        async with db.factory() as s:
            await config.put_relationship_config(
                sid, config.RelationshipConfigRequest(tone=tone), user(uid), s
            )

    await asyncio.gather(edit("formal"), edit("playful"))
    assert await db.scalar("select count(*) from companion_relationships") == 1
    assert await db.scalar("select tone from companion_relationships") in ("formal", "playful")
    assert await db.scalar("select version from companion_relationships") == 3
