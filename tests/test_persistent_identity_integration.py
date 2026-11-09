"""Task 1.3: durable identity and relationship reach the conversation, identically in every mode.

Drives the real application code (``chat``, ``stream_chat``, the avatar
``_spoken_turn``, the real pipeline, ``create_session``, ``create_room``) through
real SQLAlchemy sessions from the ``test_completion_persistence`` harness:
SQLite with correct SAVEPOINT semantics by default, or a disposable PostgreSQL
built by Alembic when ``PHASE0_PG_URL`` is set. Only the LLM provider is faked,
and it records exactly what it receives.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import event, select
from starlette.requests import Request

from test_avatar_backend import FakeRuntime, FakeSocket
from test_completion_persistence import USER, USER_ID, FakeProvider, app_with_engine, env  # noqa: F401

from chatbot.api import chat as chat_api
from chatbot.api import config as config_api
from chatbot.api import sessions as sessions_api
from chatbot.api import streaming
from chatbot.auth.dependencies import CurrentUser
from chatbot.avatar import router as avatar_router
from chatbot.avatar.coordinator import AvatarBinding, AvatarCoordinator
from chatbot.core import turn as turn_module
from chatbot.db import models

# A question routes standard chat to its full path, the path streaming always takes.
QUESTION = "Can you help me plan my week? I live in Seattle."


class RecordingProvider(FakeProvider):
    """Records the provider-ready inputs of every call (complete or stream)."""

    def __init__(self) -> None:
        super().__init__(chunks=("Glad to. ", "Let's start."))
        self.inputs: list[dict[str, Any]] = []

    async def stream(self, **kwargs: Any):
        self.inputs.append(kwargs)
        async for chunk in super().stream(**kwargs):
            yield chunk

    async def complete(self, **kwargs: Any) -> Any:
        self.inputs.append(kwargs)
        return await super().complete(**kwargs)

    def system(self, index: int = -1) -> str:
        return self.inputs[index]["history"][0]["content"]


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> RecordingProvider:
    fake = RecordingProvider()
    monkeypatch.setattr(turn_module, "_select_provider", lambda ctx: fake)
    return fake


# --------------------------------------------------------------------------
# Data helpers (real rows, no mocks)
# --------------------------------------------------------------------------


async def add_user(env: Any) -> CurrentUser:
    uid = uuid.UUID(f"b{uuid.uuid4().hex[1:]}")  # SQLite UUID columns: keep a leading letter
    async with env.factory() as db:
        db.add(models.User(id=uid, external_id=f"ext_{uid.hex}", username=uid.hex[:12],
                           email=f"{uid.hex[:12]}@example.com", display_name="B", password_hash="h"))
        await db.commit()
    return CurrentUser(user_id=str(uid), username="b", email="b@example.com", display_name="B", token="t")


async def add_companion(
    env: Any,
    *,
    user_id: uuid.UUID = USER_ID,
    name: str = "Mercury",
    sliders: dict[str, float] | None = None,
    adjustments: dict[str, float] | None = None,
    relationship: dict[str, Any] | None = None,
) -> str:
    companion_id = f"c{uuid.uuid4().hex[1:]}"[:36]
    async with env.factory() as db:
        db.add(models.Companion(
            companion_id=companion_id, user_id=user_id, name=name,
            persona=f"{name} is dry and observant.", description=f"{name} keeps the night archive.",
            backstory=f"{name} grew up above a bookshop.", personality_sliders=sliders or {"openness": 0.8},
            humor_style="witty", communication_style="concise", favorite_topics=["maps"],
        ))
        await db.flush()
        db.add(models.CompanionIdentityModel(companion_id=companion_id, trait_adjustments=adjustments or {}))
        db.add(models.CompanionRelationshipModel(companion_id=companion_id, **(relationship or {})))
        await db.commit()
    return companion_id


async def new_session(env: Any, companion_id: str, user: CurrentUser = USER) -> uuid.UUID:
    """A session created through the real POST /sessions handler (seeded)."""
    async with env.factory() as db:
        created = await sessions_api.create_session(
            sessions_api.SessionCreateRequest(companion_id=companion_id), user, db
        )
    return uuid.UUID(created.session_id)


async def standard_turn(env: Any, sid: uuid.UUID, message: str = QUESTION, user: CurrentUser = USER,
                        companion_id: str | None = None) -> Any:
    async with env.factory() as db:
        return await chat_api.chat(
            request=Request({"type": "http", "app": app_with_engine(), "state": {}}),
            body=chat_api.ChatRequest(session_id=str(sid), message=message, companion_id=companion_id),
            current_user=user,
            db=db,
        )


async def stream_turn(env: Any, sid: uuid.UUID, message: str = QUESTION, user: CurrentUser = USER) -> list[str]:
    async with env.factory() as db:
        response = await streaming.stream_chat(
            Request({"type": "http", "app": app_with_engine(), "state": {}}),
            streaming.StreamRequest(session_id=str(sid), message=message), user, db,
        )
        # End the endpoint's read transaction before streaming. PostgreSQL does not
        # need this; SQLite's file lock would otherwise block the turn's commit.
        await db.rollback()
        return [frame async for frame in response.body_iterator]


async def spoken_turn(env: Any, sid: uuid.UUID, companion_id: str, provider: RecordingProvider,
                      monkeypatch: pytest.MonkeyPatch, message: str = QUESTION) -> None:
    """A real spoken turn through the avatar router; ended by barge-in once generation ran."""
    monkeypatch.setattr(avatar_router, "async_session_factory", env.factory)
    app = app_with_engine()
    hub = AvatarCoordinator()
    binding = AvatarBinding(str(sid), str(USER_ID), companion_id, FakeSocket())
    await hub.connect(binding, FakeRuntime)
    app.state.avatar_coordinator = hub
    calls = len(provider.inputs)
    ws = SimpleNamespace(scope={"type": "websocket", "app": app, "headers": [], "path": "/api/avatar/ws",
                                "query_string": b""})
    task = asyncio.create_task(avatar_router._spoken_turn(ws, binding, USER, message))

    async def generated() -> None:
        while len(provider.inputs) == calls or not (binding.turn and binding.turn.segments):
            if task.done():
                task.result()
            await asyncio.sleep(0)

    await asyncio.wait_for(generated(), 5)
    await hub.interrupt(binding)
    await asyncio.gather(task, return_exceptions=True)
    await hub.detach(binding)


# --------------------------------------------------------------------------
# 1–3: configured personality, evolved identity and relationship reach the LLM
# --------------------------------------------------------------------------


async def test_configured_evolved_and_relationship_layers_reach_the_llm(env: Any, provider: RecordingProvider) -> None:
    cid = await add_companion(
        env, sliders={"openness": 0.8}, adjustments={"openness": 0.1},
        relationship={"type": "mentor", "tone": "playful", "directness": 0.8, "warmth": 0.6},
    )
    sid = await new_session(env, cid)

    await standard_turn(env, sid)
    system = provider.system()

    # 1. configured personality: name, persona, description, backstory, style
    for text in ("Identity: Mercury", "Mercury is dry and observant.", "Mercury keeps the night archive.",
                 "Mercury grew up above a bookshop.", "Communication style: concise", "openness 0.80"):
        assert text in system
    # 2. the durable adjustment (0.8 + 0.1) reaches the session working traits
    assert "- openness: 0.90" in system
    # 3. the durable relationship, with no invented stage (D7)
    for text in ("- Type: mentor", "- Tone: playful", "- Directness: 0.80", "- Warmth: 0.60"):
        assert text in system
    assert "Stage:" not in system and "Depth:" not in system and "Guidance:" not in system


async def test_no_generic_identity_values_or_duplicated_configuration(env: Any, provider: RecordingProvider) -> None:
    cid = await add_companion(env)
    sid = await new_session(env, cid)

    await standard_turn(env, sid)
    system = provider.system()

    assert "core values" not in system  # D8
    assert "careful listening" not in system
    assert "FIXED IDENTITY" not in system  # only configuration copies were left in it
    assert system.count("Mercury grew up above a bookshop.") == 1
    assert "relationship warmth" not in system and "relationship type" not in system


# --------------------------------------------------------------------------
# 4: new sessions reuse the established identity and relationship
# --------------------------------------------------------------------------


async def test_a_new_session_reuses_the_relationship_and_its_factual_history(
    env: Any, provider: RecordingProvider
) -> None:
    cid = await add_companion(env)
    first = await new_session(env, cid)
    async with env.factory() as db:
        await config_api.put_relationship_config(
            first, config_api.RelationshipConfigRequest(type="peer", tone="sarcastic"), USER, db
        )
    await standard_turn(env, first)
    await standard_turn(env, first, "And what about next week?")

    second = await new_session(env, cid)
    await standard_turn(env, second)
    system = provider.system()

    assert "- Type: peer" in system and "- Tone: sarcastic" in system
    assert "- History: 2 completed exchanges to date since" in system
    async with env.factory() as db:
        relationships = (await db.execute(select(models.CompanionRelationshipModel))).scalars().all()
    assert [r.completed_turns for r in relationships] == [3]  # one relationship, not one per session


async def test_the_first_conversation_states_no_history(env: Any, provider: RecordingProvider) -> None:
    cid = await add_companion(env)
    await standard_turn(env, await new_session(env, cid))
    assert "History:" not in provider.system()


# --------------------------------------------------------------------------
# 5–7: standard, streaming and spoken chat use the same effective personality
# --------------------------------------------------------------------------


async def test_standard_streaming_and_spoken_send_the_same_effective_personality(
    env: Any, provider: RecordingProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Equivalent state: three identically configured companions, one fresh session
    # each (one shared companion would rightly accumulate relationship history).
    def same_companion() -> Any:
        return add_companion(env, adjustments={"openness": 0.1},
                             relationship={"type": "mentor", "tone": "playful"})

    standard_cid, stream_cid, spoken_cid = [await same_companion() for _ in range(3)]
    standard_sid = await new_session(env, standard_cid)
    stream_sid = await new_session(env, stream_cid)
    spoken_sid = await new_session(env, spoken_cid)

    await standard_turn(env, standard_sid)
    await stream_turn(env, stream_sid)
    await spoken_turn(env, spoken_sid, spoken_cid, provider, monkeypatch)

    standard, streamed, spoken = provider.system(0), provider.system(1), provider.system(2)
    assert "- Type: mentor" in standard and "- openness: 0.90" in standard
    assert standard == streamed == spoken
    assert len(provider.inputs) == 3  # one generation per turn


# --------------------------------------------------------------------------
# 8: room sessions resolve the actual companion
# --------------------------------------------------------------------------


async def test_a_room_session_uses_the_companion_not_generic_defaults(
    env: Any, provider: RecordingProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chatbot.rooms import room as rooms
    from chatbot.rooms import scene as scenes

    # Rooms and scenes open their own sessions; keep both on the harness database.
    monkeypatch.setattr(rooms, "async_session_factory", env.factory)
    monkeypatch.setattr(scenes, "async_session_factory", env.factory)
    cid = await add_companion(env, adjustments={"openness": 0.1})
    room = await rooms.create_room(str(USER_ID), rooms.RoomType.ONE_ON_ONE_CHAT, "Study", cid)
    sid = uuid.UUID(room.session_id)  # created by create_room, never seeded

    await standard_turn(env, sid)
    await standard_turn(env, sid, "One more question?")

    for system in (provider.system(0), provider.system(1)):
        assert "Identity: Mercury" in system and "- openness: 0.90" in system
        assert "name: Companion" not in system and "A steady companion" not in system
    async with env.factory() as db:
        stored = (await db.execute(
            select(models.IdentityStateModel).where(models.IdentityStateModel.session_id == sid)
        )).scalar_one()
    assert stored.fixed["name"] == "Mercury"  # the working copy was seeded from the companion


# --------------------------------------------------------------------------
# 9–10: isolation between companions and between users
# --------------------------------------------------------------------------


async def test_different_companions_keep_their_own_identity_and_relationship(
    env: Any, provider: RecordingProvider
) -> None:
    mercury = await add_companion(env, name="Mercury", relationship={"type": "mentor"})
    vesta = await add_companion(env, name="Vesta", sliders={"openness": 0.3}, relationship={"type": "peer"})

    await standard_turn(env, await new_session(env, mercury))
    await standard_turn(env, await new_session(env, vesta))

    assert "Identity: Mercury" in provider.system(0) and "- Type: mentor" in provider.system(0)
    assert "Vesta" not in provider.system(0)
    assert "Identity: Vesta" in provider.system(1) and "- Type: peer" in provider.system(1)
    assert "Mercury" not in provider.system(1)
    async with env.factory() as db:
        counts = {r.companion_id: r.completed_turns
                  for r in (await db.execute(select(models.CompanionRelationshipModel))).scalars()}
    assert counts == {mercury: 1, vesta: 1}


async def test_another_user_cannot_reach_a_companion_it_does_not_own(env: Any, provider: RecordingProvider) -> None:
    mercury = await add_companion(env, name="Mercury")
    other = await add_user(env)
    their_companion = await add_companion(env, user_id=uuid.UUID(other.user_id), name="Juno")
    their_session = await new_session(env, their_companion, user=other)

    with pytest.raises(HTTPException) as refused:
        await standard_turn(env, their_session, user=other, companion_id=mercury)
    assert refused.value.status_code in (400, 404)

    await standard_turn(env, their_session, user=other)
    assert "Juno" in provider.system() and "Mercury" not in provider.system()


# --------------------------------------------------------------------------
# 11: session mood never becomes permanent identity
# --------------------------------------------------------------------------


async def test_session_mood_and_drift_never_change_durable_identity(env: Any, provider: RecordingProvider) -> None:
    cid = await add_companion(env)
    sid = await new_session(env, cid)
    for message in ("I'm so happy today!", "This is awful, I'm upset.", "Can you help me plan?"):
        await standard_turn(env, sid, message)

    async with env.factory() as db:
        identity = await db.get(models.CompanionIdentityModel, cid)
        session_rows = (await db.execute(
            select(models.IdentityStateModel).where(models.IdentityStateModel.session_id == sid)
        )).scalars().all()
    assert identity.trait_adjustments == {} and identity.version == 1
    assert session_rows  # the session working copy is where mood and drift live


# --------------------------------------------------------------------------
# 12: prompt inspection shows the actual provider input and its sources
# --------------------------------------------------------------------------


async def test_the_inspector_shows_the_prompt_sent_and_where_identity_came_from(
    env: Any, provider: RecordingProvider
) -> None:
    cid = await add_companion(env, adjustments={"openness": 0.1}, relationship={"type": "mentor"})
    sid = await new_session(env, cid)

    await standard_turn(env, sid)

    (trace,) = await turn_module.trace_collector.list_traces(session_id=str(sid))
    sent = provider.inputs[-1]
    rendered = "\n\n".join(
        f"{m['role']}: {m['content']}" for m in [*sent["history"], {"role": "user", "content": sent["prompt"]}]
    )
    assert trace.prompt == rendered  # the inspector shows exactly what the provider received
    (compile_step,) = [s for s in trace.steps if s.step_name == "compile_context"]
    sources = compile_step.metadata["identity_sources"]
    assert sources["configured"]["name"] == "Mercury"
    assert sources["evolved"]["trait_adjustments"] == {"openness": 0.1}
    assert sources["relationship"]["settings"]["type"] == "mentor"
    assert sources["relationship"]["stage_shown"] is False
    assert sources["session"]["identity_tiers"] == ["slow", "mood"]


# --------------------------------------------------------------------------
# 13, 15: fast path intact; no extra provider, embedding or database work
# --------------------------------------------------------------------------


async def test_the_fast_path_runs_with_the_same_identity_composition(env: Any, provider: RecordingProvider) -> None:
    cid = await add_companion(env, relationship={"type": "mentor"})
    response = await standard_turn(env, await new_session(env, cid), "Hello!")

    assert [s["name"] for s in response.step_results] == [
        "receive", "restore_mood", "retrieve", "compile_context", "call_provider",
        "update_mood", "persist_emotional_state", "respond", "update_identity",
    ]
    assert "- Type: mentor" in provider.system() and "Identity: Mercury" in provider.system()


async def test_one_generation_one_embedding_batch_and_one_durable_state_query_per_turn(
    env: Any, provider: RecordingProvider
) -> None:
    from chatbot.api import conversation

    cid = await add_companion(env)
    sid = await new_session(env, cid)
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        statements.append(statement.lower())

    event.listen(env.engine.sync_engine, "before_cursor_execute", record)
    try:
        await standard_turn(env, sid)
    finally:
        event.remove(env.engine.sync_engine, "before_cursor_execute", record)

    assert len(provider.inputs) == 1
    assert conversation.generate_recent_message_embeddings.await_count == 1  # the existing batch, once
    durable_reads = [s for s in statements if s.startswith("select") and "companion_identities" in s]
    assert len(durable_reads) == 1  # Task 1.2's single joined load, not repeated by Task 1.3
    assert "companion_relationships" in durable_reads[0]
