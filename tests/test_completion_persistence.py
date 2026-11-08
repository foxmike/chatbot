"""Turn completion against a real SQLAlchemy ``AsyncSession``.

The hand-written ``TrackingSession`` in ``test_conversation_lifecycle.py`` models
flush/commit/rollback bookkeeping but not SQLAlchemy's real transaction
behavior (autobegin, SAVEPOINT release, ``in_transaction()`` after a commit).
These tests drive the real application code (``_stream_turn``, ``chat``, the
real pipeline steps and models) through real sessions, so they observe what is
and is not committed.

The database is an on-disk SQLite file with the standard SQLAlchemy recipe that
makes pysqlite emit ``BEGIN``/``SAVEPOINT`` correctly. Without that recipe
SQLite would wrongly keep rows written inside a savepoint and hide the very
defect these tests guard. **This is not PostgreSQL**: it verifies SQLAlchemy
session semantics and the application's transaction ownership, not PostgreSQL
behavior.

Set ``PHASE0_PG_URL`` to run the same scenarios against a **disposable**
PostgreSQL database that was built with the project's migrations (plus the
``relationship_timeline_entries`` table, which no migration creates). The
fixture refuses any database that is not named ``phase0_accept`` on a
non-default port, because it truncates every table it uses.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from sqlalchemy import event, func, make_url, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from chatbot.api import chat as chat_api
from chatbot.api import conversation, streaming
from chatbot.auth.dependencies import CurrentUser
from chatbot.continuity import timeline as timeline_module
from chatbot.continuity.timeline import RelationshipTimeline, RelationshipTimelineEntryModel
from chatbot.core import turn as turn_module
from chatbot.core.steps import TurnContext
from chatbot.db import models
from chatbot.observability.metrics import MetricsCollector
from chatbot.observability.tracing import TraceCollector

# SQLite gives PostgreSQL UUID columns numeric affinity, which would coerce an
# all-digit id to a float; these start with a letter so they stay text.
SESSION_ID = uuid.UUID("abcdef01-2222-3333-4444-555555555555")
USER_ID = uuid.UUID("fedcba98-8888-7777-6666-555555555555")
USER = CurrentUser(
    user_id=str(USER_ID), username="u", email="u@example.com", display_name="U", token="t"
)

MILESTONE = "I got a new job today! I live in Seattle and I am so thrilled."

_TABLES = [
    models.User, models.Companion, models.Session, models.Message, models.SemanticFact, models.Episode,
    models.BotMoodLog, models.UserStateLog, models.IdentityStateModel,
    models.CharacterProfileModel, models.Room, RelationshipTimelineEntryModel,
    models.CompanionIdentityModel, models.CompanionRelationshipModel,
]


# --------------------------------------------------------------------------
# Environment
# --------------------------------------------------------------------------


class Env:
    def __init__(self, engine: Any, factory: Any) -> None:
        self.engine = engine
        self.factory = factory

    async def count(self, model: Any, *where: Any) -> int:
        async with self.factory() as db:
            return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar()

    async def roles(self) -> list[str]:
        async with self.factory() as db:
            result = await db.execute(
                select(models.Message).order_by(models.Message.created_at, models.Message.id)
            )
            return [m.role for m in result.scalars().all()]

    async def messages(self) -> list[models.Message]:
        async with self.factory() as db:
            result = await db.execute(
                select(models.Message).order_by(models.Message.created_at, models.Message.id)
            )
            return list(result.scalars().all())

    async def session_row(self) -> models.Session:
        async with self.factory() as db:
            return (
                await db.execute(select(models.Session).where(models.Session.id == SESSION_ID))
            ).scalar_one()

    async def effects(self) -> dict[str, int]:
        return {
            "facts": await self.count(models.SemanticFact),
            "mood_logs": await self.count(models.BotMoodLog),
            "user_state_logs": await self.count(models.UserStateLog),
            "timeline": await self.count(RelationshipTimelineEntryModel),
        }


PG_URL = os.environ.get("PHASE0_PG_URL")


def _require_disposable_postgres(url: str) -> None:
    target = make_url(url)
    if not (
        target.drivername.startswith("postgresql")
        and target.host in {"127.0.0.1", "localhost"}
        and target.port not in (None, 5432)
        and target.database == "phase0_accept"
    ):
        raise RuntimeError(
            "PHASE0_PG_URL must name database 'phase0_accept' on 127.0.0.1/localhost with a "
            "non-default port; refusing to truncate anything else"
        )


async def _empty_tables(factory: Any) -> None:
    async with factory() as db:
        await db.execute(text("TRUNCATE TABLE users CASCADE"))
        await db.commit()


@pytest.fixture
async def env(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Env]:
    if PG_URL:
        _require_disposable_postgres(PG_URL)
        engine = create_async_engine(PG_URL)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        await _empty_tables(factory)
    else:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'turn.db'}")

        @event.listens_for(engine.sync_engine, "connect")
        def _no_implicit_transaction_control(dbapi_connection: Any, _record: Any) -> None:
            dbapi_connection.isolation_level = None

        @event.listens_for(engine.sync_engine, "begin")
        def _explicit_begin(conn: Any) -> None:
            conn.exec_driver_sql("BEGIN")

        async with engine.begin() as connection:
            for model in _TABLES:
                await connection.run_sync(lambda sync, m=model: m.__table__.create(sync))
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db.add(
            models.User(
                id=USER_ID, external_id="ext", display_name="U", username="u",
                email="u@example.com", password_hash="h",
            )
        )
        db.add(models.Session(id=SESSION_ID, user_id=USER_ID, title="Existing"))
        await db.commit()

    # Every turn session comes from this engine; nothing else may reach a database or network.
    monkeypatch.setattr(streaming, "streaming_session_factory", factory)
    monkeypatch.setattr(turn_module, "trace_collector", TraceCollector(store="memory"))
    monkeypatch.setattr(turn_module, "metrics_collector", MetricsCollector(store="memory"))
    monkeypatch.setattr(turn_module, "structured_logger", None)
    monkeypatch.setattr(turn_module, "get_due_rituals", AsyncMock(return_value=[]))
    monkeypatch.setattr(turn_module, "generate_embedding", AsyncMock(return_value=[]))
    monkeypatch.setattr(conversation, "generate_recent_message_embeddings", AsyncMock())
    yield Env(engine, factory)
    if PG_URL:
        await _empty_tables(factory)
    await engine.dispose()


class FakeProvider:
    """Streams fixed chunks; ``hook(index)`` runs before each chunk is produced."""

    def __init__(self, chunks: tuple[str, ...] = ("Nice to hear. ", "Tell me more."), hook: Any = None):
        self.chunks = chunks
        self.hook = hook
        self.calls = 0

    async def stream(self, **kwargs: Any) -> AsyncIterator[str]:
        self.calls += 1
        for index, chunk in enumerate(self.chunks):
            if self.hook is not None:
                await self.hook(index)
            yield chunk

    async def complete(self, **kwargs: Any) -> Any:
        self.calls += 1
        return SimpleNamespace(text="".join(self.chunks), finish_reason="stop", model=None)


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    fake = FakeProvider()
    monkeypatch.setattr(turn_module, "_select_provider", lambda ctx: fake)
    return fake


def app_with_engine() -> FastAPI:
    app = FastAPI()
    app.state.retrieval_engine = SimpleNamespace(retrieve=AsyncMock(return_value=[]))
    return app


async def snapshot(env: Env) -> dict[str, Any]:
    return streaming._session_snapshot(await env.session_row())


def stream_kwargs(app: FastAPI, session_row: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = dict(
        http_request=Request({"type": "http", "app": app, "state": {}}),
        current_user=USER,
        session_id=SESSION_ID,
        session_title="Existing",
        session_companion_id=None,
        session_room_id=None,
        session_snapshot=session_row,
        character_profile={"name": "Companion"},
        identity_state=None,
        history=[],
    )
    values.update(overrides)
    return values


async def run_stream(env: Env, message: str, app: FastAPI | None = None, **overrides: Any) -> list[str]:
    app = app or app_with_engine()
    request = streaming.StreamRequest(session_id=str(SESSION_ID), message=message)
    return [
        frame
        async for frame in streaming._stream_turn(
            request, **stream_kwargs(app, await snapshot(env), **overrides)
        )
    ]


# --------------------------------------------------------------------------
# Streaming: a completed turn commits its effects
# --------------------------------------------------------------------------


async def test_completed_streaming_turn_commits_messages_and_every_effect(
    env: Env, provider: FakeProvider
) -> None:
    frames = await run_stream(env, MILESTONE)

    assert frames[-1] == "data: [DONE]\n\n"
    assert await env.roles() == ["user", "assistant"]
    effects = await env.effects()
    assert effects["facts"] >= 1  # extracted memory (previously rolled back)
    assert effects["mood_logs"] >= 1  # BotMoodLog (previously rolled back)
    assert effects["user_state_logs"] >= 1  # UserStateLog (previously rolled back)
    assert effects["timeline"] >= 1  # milestone, committed with the turn
    row = await env.session_row()
    assert row.mood_state is not None and row.user_state is not None  # session state saved
    assert await env.count(models.Episode) >= 1


async def test_effects_and_messages_share_the_turn_id_and_ordering(env: Env, provider: FakeProvider) -> None:
    await run_stream(env, MILESTONE)

    user, assistant = await env.messages()
    assert user.turn_id == assistant.turn_id and user.turn_id
    assert user.created_at < assistant.created_at
    async with env.factory() as db:
        entry = (await db.execute(select(RelationshipTimelineEntryModel))).scalars().first()
    assert str(entry.turn_id) == user.turn_id  # the milestone belongs to a saved turn


async def test_effects_are_discarded_when_the_messages_are_not_saved(
    env: Env, provider: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A realistic persistence failure: the real _persist_messages rolls back the
    # session it was given (and, being non-strict, logs and carries on).
    monkeypatch.setattr(
        conversation, "add_turn_messages", AsyncMock(side_effect=RuntimeError("database unavailable"))
    )

    frames = await run_stream(env, MILESTONE)

    assert any('"type": "response"' in frame for frame in frames)  # delivery is unaffected
    assert await env.count(models.Message) == 0
    assert await env.effects() == {"facts": 0, "mood_logs": 0, "user_state_logs": 0, "timeline": 0}


async def test_effects_stay_uncommitted_when_no_earlier_step_opened_the_transaction(
    env: Env, provider: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With emotional-state steps off, memory extraction is the first post-turn DB step.

    A session that has just committed reports no open transaction, and the
    extractor then commits on its own inside the turn. The effects transaction
    is therefore begun explicitly, so nothing is committed before the messages.
    """
    monkeypatch.setenv("CHATBOT_EMOTIONAL_INTELLIGENCE_ENABLED", "false")
    # A realistic persistence failure: the real _persist_messages rolls back the
    # session it was given (and, being non-strict, logs and carries on).
    monkeypatch.setattr(
        conversation, "add_turn_messages", AsyncMock(side_effect=RuntimeError("database unavailable"))
    )

    await run_stream(env, MILESTONE)

    assert await env.effects() == {"facts": 0, "mood_logs": 0, "user_state_logs": 0, "timeline": 0}


async def test_with_emotional_state_steps_off_a_saved_turn_still_commits_its_facts(
    env: Env, provider: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHATBOT_EMOTIONAL_INTELLIGENCE_ENABLED", "false")

    await run_stream(env, MILESTONE)

    effects = await env.effects()
    assert effects["facts"] >= 1 and effects["timeline"] >= 1
    assert effects["mood_logs"] == 0 and effects["user_state_logs"] == 0


async def test_a_failed_generation_saves_the_error_text_but_no_effects(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing(ctx: TurnContext) -> AsyncIterator[str]:
        ctx.provider_response = "Provider error: connection reset"
        yield "Provider stream error: connection reset"

    monkeypatch.setattr(streaming, "_stream_provider_tokens", failing)

    frames = await run_stream(env, MILESTONE)

    assert any('"type": "error"' in frame for frame in frames)
    messages = await env.messages()
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].content == "Provider error: connection reset"  # established behavior
    assert await env.effects() == {"facts": 0, "mood_logs": 0, "user_state_logs": 0, "timeline": 0}


async def test_a_disconnect_before_the_turn_is_saved_saves_nothing(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def cancelled(ctx: TurnContext) -> AsyncIterator[str]:
        yield "First token"
        raise asyncio.CancelledError

    monkeypatch.setattr(streaming, "_stream_provider_tokens", cancelled)

    with pytest.raises(asyncio.CancelledError):
        await run_stream(env, MILESTONE)

    assert await env.count(models.Message) == 0
    assert await env.effects() == {"facts": 0, "mood_logs": 0, "user_state_logs": 0, "timeline": 0}


async def test_a_disconnect_after_the_turn_is_saved_keeps_its_effects(env: Env, provider: FakeProvider) -> None:
    app = app_with_engine()
    request = streaming.StreamRequest(session_id=str(SESSION_ID), message=MILESTONE)
    generator = streaming._stream_turn(request, **stream_kwargs(app, await snapshot(env)))
    async for frame in generator:
        if '"type": "response"' in frame:
            break
    await generator.aclose()  # the browser closes the SSE connection at the `response` event

    assert await env.roles() == ["user", "assistant"]
    assert (await env.effects())["facts"] >= 1


# --------------------------------------------------------------------------
# Transaction ownership
# --------------------------------------------------------------------------


async def test_no_database_connection_is_held_while_the_reply_is_generated(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    checked_out: list[int] = []

    async def observe(index: int) -> None:
        checked_out.append(env.engine.pool.checkedout())

    fake = FakeProvider(chunks=("One. ", "Two. ", "Three."), hook=observe)
    monkeypatch.setattr(turn_module, "_select_provider", lambda ctx: fake)

    await run_stream(env, MILESTONE)

    # Before the first token the preparation transaction is still open; once the
    # first token is out it is released for the rest of generation.
    assert checked_out == [1, 0, 0]


async def test_preparation_embeddings_are_batched_committed_and_invisible_to_this_turn(
    env: Env, provider: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with env.factory() as db:
        for role, text in (("user", "earlier question"), ("assistant", "earlier answer")):
            db.add(models.Message(session_id=SESSION_ID, user_id=USER_ID, role=role, content=text))
        await db.commit()

    monkeypatch.undo()  # restore the real batch helper; patch only its provider and settings
    embedder = SimpleNamespace(dimension=1536, embed=AsyncMock(return_value=[[1.0] * 1536, [2.0] * 1536]))
    monkeypatch.setattr("chatbot.memory.retrieval.OpenRouterEmbeddingProvider", lambda **_: embedder)
    monkeypatch.setattr(
        "chatbot.config.get_settings",
        lambda: SimpleNamespace(
            openrouter_api_key="k", openrouter_base_url="https://x.test/v1",
            embedding_base_url="https://x.test/v1", embedding_model="m",
        ),
    )
    monkeypatch.setattr(streaming, "streaming_session_factory", env.factory)
    monkeypatch.setattr(turn_module, "trace_collector", TraceCollector(store="memory"))
    monkeypatch.setattr(turn_module, "metrics_collector", MetricsCollector(store="memory"))
    monkeypatch.setattr(turn_module, "structured_logger", None)
    monkeypatch.setattr(turn_module, "get_due_rituals", AsyncMock(return_value=[]))
    monkeypatch.setattr(turn_module, "generate_embedding", AsyncMock(return_value=[]))
    monkeypatch.setattr(turn_module, "_select_provider", lambda ctx: FakeProvider())

    visible_to_retrieval: list[int] = []

    async def retrieve(**kwargs: Any) -> list[Any]:
        visible_to_retrieval.append(await env.count(models.Message, models.Message.embedding.is_not(None)))
        return []

    app = FastAPI()
    app.state.retrieval_engine = SimpleNamespace(retrieve=retrieve)

    await run_stream(env, "Hello again", app=app, history=[
        {"role": "user", "content": "earlier question"}, {"role": "assistant", "content": "earlier answer"},
    ])

    embedder.embed.assert_awaited_once()  # one batched request, not one per message
    assert sorted(embedder.embed.await_args.args[0]) == [
        "assistant: earlier answer", "user: earlier question",
    ]
    assert visible_to_retrieval == [0]  # the same turn's retrieval never sees them (unchanged)
    assert await env.count(models.Message, models.Message.embedding.is_not(None)) == 2  # kept for the next turn


async def test_a_timeline_entry_that_fails_cannot_poison_the_turn_transaction(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed = uuid.uuid4()
    monkeypatch.setattr(timeline_module.uuid, "uuid4", lambda: fixed)
    timeline = RelationshipTimeline()

    async with env.factory() as db:
        await db.begin()
        await timeline.add_timeline_entry(str(SESSION_ID), "milestone", "a", "a", "a", str(uuid.uuid4()), session=db)
        with pytest.raises(Exception):  # duplicate primary key, rolled back to its SAVEPOINT
            await timeline.add_timeline_entry(
                str(SESSION_ID), "milestone", "b", "b", "b", str(uuid.uuid4()), session=db
            )
        db.add(models.Message(session_id=SESSION_ID, user_id=USER_ID, role="user", content="still works"))
        await db.commit()

    assert await env.count(RelationshipTimelineEntryModel) == 1
    assert await env.count(models.Message) == 1


# --------------------------------------------------------------------------
# Spoken: barge-in persistence is unchanged
# --------------------------------------------------------------------------


async def test_a_spoken_barge_in_saves_the_heard_prefix_and_no_effects(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_avatar_backend import CID, FakeRuntime, FakeSocket  # real coordinator, fake media

    from chatbot.avatar.coordinator import AvatarBinding, AvatarCoordinator

    async with env.factory() as db:  # messages.companion_id is a real foreign key on PostgreSQL
        db.add(models.Companion(companion_id=CID, user_id=USER_ID, name="Companion"))
        await db.commit()

    release = asyncio.Event()

    async def hold_after_first_chunk(index: int) -> None:
        if index == 1:
            await release.wait()

    fake = FakeProvider(chunks=("Heard. ", "Never heard."), hook=hold_after_first_chunk)
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
                task.result()  # surface a failed turn instead of waiting forever
            await asyncio.sleep(0)

    await asyncio.wait_for(first_segment_exists(), 5)
    await hub.control(binding, {"type": "playback_end", "id": next(iter(binding.turn.segments))})
    await hub.interrupt(binding)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)

    messages = await env.messages()
    assert [m.role for m in messages] == ["user", "assistant", "system"]
    assert messages[1].content == "Heard. "
    assert "interrupted" in messages[2].content
    assert await env.effects() == {"facts": 0, "mood_logs": 0, "user_state_logs": 0, "timeline": 0}
    await hub.detach(binding)


# --------------------------------------------------------------------------
# Standard chat: one request transaction, fast path intact
# --------------------------------------------------------------------------


async def standard_chat(env: Env, message: str) -> tuple[Any, int]:
    """Run POST /chat/ on a real session; also count real database commits.

    ``after_commit`` would also count SAVEPOINT releases, so commits are counted
    at the engine, where only a real COMMIT is reported.
    """
    commits: list[int] = []

    def count_commit(_connection: Any) -> None:
        commits.append(1)

    event.listen(env.engine.sync_engine, "commit", count_commit)
    try:
        async with env.factory() as db:
            response = await chat_api.chat(
                request=Request({"type": "http", "app": app_with_engine(), "state": {}}),
                body=chat_api.ChatRequest(session_id=str(SESSION_ID), message=message),
                current_user=USER,
                db=db,
            )
    finally:
        event.remove(env.engine.sync_engine, "commit", count_commit)
    return response, len(commits)


async def test_standard_deep_path_commits_messages_and_effects_in_one_transaction(
    env: Env, provider: FakeProvider
) -> None:
    response, commits = await standard_chat(
        env, "Can you help me plan my schedule? I live in Seattle and I have two golden retrievers."
    )

    assert commits == 1
    assert await env.roles() == ["user", "assistant"]
    assert (await env.messages())[1].content == response.response
    assert (await env.effects())["facts"] >= 1


async def test_standard_fast_path_is_intact_and_defers_extraction(env: Env, provider: FakeProvider) -> None:
    queue = chat_api._background_queue
    before = queue.pending_count

    response, commits = await standard_chat(env, "Hello! Good morning.")

    assert response.response
    assert commits == 1
    assert await env.roles() == ["user", "assistant"]
    assert await env.effects() == {"facts": 0, "mood_logs": 0, "user_state_logs": 0, "timeline": 0}
    assert queue.pending_count == before + 1  # extraction deferred, not run inline
    assert [step["name"] for step in response.step_results] == [
        "receive", "retrieve", "compile_context", "call_provider", "respond", "update_identity",
    ]


# --------------------------------------------------------------------------
# One implementation of the turn's rows and of title generation
# --------------------------------------------------------------------------


async def test_standard_and_streaming_store_the_same_rows(env: Env, provider: FakeProvider) -> None:
    await standard_chat(env, "Hello! Good morning.")
    standard_rows = await env.messages()
    async with env.factory() as db:
        await db.execute(models.Message.__table__.delete())
        await db.commit()
    await run_stream(env, "Hello! Good morning.")
    streamed_rows = await env.messages()

    assert [(m.role, m.content != "") for m in standard_rows] == [(m.role, m.content != "") for m in streamed_rows]
    for rows in (standard_rows, streamed_rows):
        assert rows[0].turn_id == rows[1].turn_id and rows[0].turn_id
        assert rows[0].created_at < rows[1].created_at
        assert rows[0].user_id == USER_ID and rows[0].session_id == SESSION_ID


async def test_add_turn_messages_orders_rows_and_marks_interruptions(env: Env) -> None:
    async with env.factory() as db:
        await conversation.add_turn_messages(
            db, session_id=SESSION_ID, user_id=USER_ID, companion_id=None, user_message="q",
            assistant_message="partial", turn_id="t1", interrupted=True,
        )
        await db.commit()
    rows = await env.messages()
    assert [m.role for m in rows] == ["user", "assistant", "system"]
    assert rows[0].created_at < rows[1].created_at < rows[2].created_at
    assert (await env.session_row()).updated_at is not None


async def test_empty_assistant_text_follows_each_modes_established_rule(env: Env) -> None:
    async with env.factory() as db:
        await conversation.add_turn_messages(
            db, session_id=SESSION_ID, user_id=USER_ID, companion_id=None, user_message="a",
            assistant_message="", turn_id="t-stream", omit_empty_assistant=True,
        )
        await conversation.add_turn_messages(
            db, session_id=SESSION_ID, user_id=USER_ID, companion_id=None, user_message="b",
            assistant_message="", turn_id="t-standard",
        )
        await db.commit()
    by_turn: dict[str, list[str]] = {}
    for message in await env.messages():
        by_turn.setdefault(message.turn_id, []).append(message.role)
    assert by_turn == {"t-stream": ["user"], "t-standard": ["user", "assistant"]}


async def test_title_generation_has_one_implementation_and_persists_through_the_given_factory(env: Env) -> None:
    for module in (chat_api, streaming):
        assert not hasattr(module, "_maybe_schedule_title_generation")
        assert not hasattr(module, "_generate_conversation_title")

    async with env.factory() as db:
        row = (await db.execute(select(models.Session))).scalar_one()
        row.title = None
        await db.commit()
    provider = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(text='"Weekend Plans Chat"')))
    registry = SimpleNamespace(get=lambda name: provider)

    await conversation.generate_conversation_title(
        provider_registry=registry, provider_name="p", model=None, session_id=SESSION_ID,
        user_message="u", assistant_message="a", session_factory=env.factory,
    )

    assert (await env.session_row()).title == "Weekend Plans Chat"
    provider.complete.assert_awaited_once()


def test_title_scheduling_skips_titled_sessions_and_missing_registries() -> None:
    request = Request({"type": "http", "app": FastAPI(), "state": {}})
    scheduled = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(conversation.asyncio, "create_task", scheduled)
        conversation.schedule_title_generation(
            request, session_id=SESSION_ID, session_title="Has a title", user_message="u",
            assistant_message="a", session_factory=MagicMock(),
        )
        conversation.schedule_title_generation(
            request, session_id=SESSION_ID, session_title=None, user_message="u",
            assistant_message="a", session_factory=MagicMock(),
        )
    scheduled.assert_not_called()
