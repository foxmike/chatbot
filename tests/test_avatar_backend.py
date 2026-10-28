"""GPU-free checks of transport security, shared generation and interruption."""

from __future__ import annotations

import asyncio
import json
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import uuid

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from starlette.requests import Request
from starlette.websockets import WebSocketDisconnect

from chatbot.api import streaming
from chatbot.auth.dependencies import CurrentUser
from chatbot.auth.security import create_access_token
from chatbot.avatar.coordinator import AvatarBinding, AvatarCoordinator
from chatbot.avatar import router
from chatbot.core.turn import CallProviderStep, RespondStep, RetrieveStep


SID = str(uuid.uuid4())
UID = str(uuid.uuid4())
CID = str(uuid.uuid4())
USER = CurrentUser(user_id=UID, username="test", email="test@example.com")
ORIGIN: dict[str, str] = {}
TOKEN = create_access_token(USER.model_dump() | {"sub": UID})
AUTH = {"type": "auth", "token": TOKEN, "session_id": SID, "companion_id": CID}


class FakeSocket:
    def __init__(self):
        self.sent = []
        self.closed = None

    async def send_json(self, data):
        self.sent.append(data)

    async def send_bytes(self, data):
        self.sent.append(data)

    async def close(self, **kwargs):
        self.closed = kwargs["code"]


class FakeRuntime:
    def __init__(self, binding, emit):
        self.binding, self.emit = binding, emit
        self.tokens = []
        self.controls = []
        self.cancelled = []
        self.started = False
        self.closed = False
        self.turn_id = None
        self.n = 0
        self.block = None

    async def start(self):
        self.started = True
        await self.emit({"type": "config", "data": {"type": "config", "sync_buffer_ms": 100}})

    async def begin_turn(self, turn_id):
        self.turn_id = turn_id
        self.n = 0

    async def feed_text(self, turn_id, text):
        if self.block:
            await self.block.wait()
        self.tokens.append(text)
        self.n += 1
        segment_id = f"{turn_id}:{self.n}"
        await self.emit({"type": "segment", "turn_id": turn_id, "id": segment_id, "text": text})
        encoded = segment_id.encode()
        packet = b"A" + struct.pack("<H", len(encoded)) + encoded + struct.pack("<If", 24000, 0.0)
        await self.emit({"type": "media", "turn_id": turn_id, "data": packet})

    async def end_turn(self, turn_id):
        await self.emit({"type": "drained", "turn_id": turn_id})

    async def cancel_turn(self, turn_id):
        self.cancelled.append(turn_id)

    async def microphone(self, pcm):
        self.pcm = pcm
        await self.emit({"type": "transcript", "text": "spoken question"})

    async def control(self, message):
        self.controls.append(message)

    async def aclose(self):
        self.closed = True


async def bound():
    hub = AvatarCoordinator()
    binding = AvatarBinding(SID, UID, CID, FakeSocket())
    await hub.connect(binding, FakeRuntime)
    return hub, binding


@pytest.fixture
def transport(monkeypatch):
    app = FastAPI()
    app.include_router(router.avatar_router)
    app.include_router(streaming.streaming_router)
    factory = Mock(side_effect=FakeRuntime)
    app.state.avatar_runtime_factory = factory
    authorize = AsyncMock()
    monkeypatch.setattr(router, "_authorize", authorize)
    with TestClient(app) as client:
        yield app, client, factory, authorize


@pytest.mark.parametrize("token", [None, "broken", create_access_token({"sub": UID})])
def test_invalid_authentication(transport, token):
    app, client, factory, authorize = transport
    with client.websocket_connect("/api/avatar/ws", headers=ORIGIN) as ws:
        ws.send_json(AUTH | {"token": token})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 1008
    factory.assert_not_called()
    authorize.assert_not_called()


def test_header_auth_binary_protocol_and_takeover(transport):
    app, client, factory, authorize = transport
    with client.websocket_connect(
        "/api/avatar/ws", headers=ORIGIN | {"authorization": f"Bearer {TOKEN}"}
    ) as first:
        first.send_json(AUTH | {"token": None})
        assert first.receive_json()["type"] == "config"
        assert first.receive_json()["type"] == "ready"
        runtime = app.state.avatar_coordinator.bindings[SID].runtime
        pcm = struct.pack("<ff", 0.0, 0.1)
        runtime.microphone = AsyncMock()
        first.send_bytes(b"M" + pcm)
        first.send_json({"type": "mic", "enabled": True})
        with client.websocket_connect("/api/avatar/ws", headers=ORIGIN) as second:
            second.send_json(AUTH)
            assert second.receive_json()["type"] == "config"
            assert second.receive_json()["type"] == "ready"
            with pytest.raises(WebSocketDisconnect) as exc:
                first.receive_json()
            assert exc.value.code == 4001
            assert runtime.closed
            runtime.microphone.assert_awaited_once_with(pcm)
            assert app.state.avatar_coordinator.bindings[SID].runtime is not runtime
    assert not app.state.avatar_coordinator.bindings


def test_runtime_is_lazy_and_unavailable_fails_explicitly(transport):
    app, client, factory, authorize = transport
    factory.assert_not_called()
    del app.state.avatar_runtime_factory
    with client.websocket_connect("/api/avatar/ws", headers=ORIGIN) as ws:
        ws.send_json(AUTH)
        assert ws.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 1013
    factory.assert_not_called()


@pytest.mark.parametrize(
    "owner,companion_owner,selected",
    [
        (False, True, CID),
        (True, False, CID),
        (True, True, str(uuid.uuid4())),
    ],
)
async def test_session_companion_authorization(monkeypatch, owner, companion_owner, selected):
    session = SimpleNamespace(companion_id=CID) if owner else None
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: session)
    db.__aenter__.return_value = db
    monkeypatch.setattr(router, "async_session_factory", lambda: db)
    manager = SimpleNamespace(get=AsyncMock(return_value=object() if companion_owner else None))
    monkeypatch.setattr(router, "CompanionManager", lambda _: manager)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await router._authorize(
            router.AvatarAuth.model_validate(AUTH | {"companion_id": selected}), USER
        )
    assert exc.value.status_code == 404
    params = db.execute.call_args.args[0].compile().params
    assert uuid.UUID(UID) in params.values()
    assert uuid.UUID(SID) in params.values()
    if owner and selected == CID:
        manager.get.assert_awaited_once_with(UID, CID)


async def test_incremental_fanout_and_stale_suppression():
    hub, binding = await bound()
    turn = await hub.begin(binding, source="typed")
    received = []

    async def provider():
        for token in ("Hello ", "world."):
            hub.publish(binding, turn, token)
            received.append(token)  # SSE consumer gets the same token immediately.
            await asyncio.sleep(0)

    await provider()
    hub.end(binding, turn)
    await turn.worker
    assert received == binding.runtime.tokens == ["Hello ", "world."]
    await hub.emit(binding, {"type": "segment", "turn_id": "old", "id": "old", "text": "stale"})
    before = list(binding.websocket.sent)
    turn.finished.set()
    await hub.interrupt(binding)
    await hub.emit(binding, {"type": "media", "turn_id": turn.turn_id, "data": b"stale"})
    assert binding.websocket.sent[:-1] == before
    assert binding.websocket.sent[-1]["type"] == "interrupt"
    await hub.detach(binding)


async def test_bounded_slow_media_does_not_block_text():
    hub, binding = await bound()
    turn = await hub.begin(binding, source="typed")
    binding.runtime.block = asyncio.Event()
    hub.publish(binding, turn, "first")
    await asyncio.sleep(0)
    for _ in range(200):
        hub.publish(binding, turn, "more")
    assert turn.queue.qsize() <= 128
    assert turn.speech_failed
    assert turn.generated_chars == len("first") + 200 * len("more")
    await turn.worker
    assert binding.runtime.cancelled == [turn.turn_id]
    turn.finished.set()
    await hub.detach(binding)


async def test_completed_prefix_only_and_unknown_playback_ack():
    hub, binding = await bound()
    turn = await hub.begin(binding, source="spoken")
    for segment_id, text in (("one", "Heard. "), ("two", "Partial. "), ("three", "Never heard.")):
        await hub.emit(
            binding, {"type": "segment", "turn_id": turn.turn_id, "id": segment_id, "text": text}
        )
    await hub.control(binding, {"type": "playback_end", "id": "unknown"})
    await hub.control(binding, {"type": "playback_end", "id": "three"})
    assert turn.heard_text == ""
    await hub.control(binding, {"type": "playback_end", "id": "one"})
    assert turn.heard_text == "Heard. "
    turn.finished.set()
    await hub.detach(binding)


@pytest.fixture
def pipeline(monkeypatch):
    history = []
    saves = []
    contexts = []
    engine = SimpleNamespace(retrieve=AsyncMock(return_value=[{"content": "persistent memory"}]))

    class Provider:
        def __init__(self):
            self.calls = 0
            self.complete = AsyncMock(side_effect=AssertionError("Duplicate generation"))
            self.pause = None
            self.first = asyncio.Event()

        async def stream(self, **kwargs):
            self.calls += 1
            yield "First. "
            self.first.set()
            if self.pause:
                await self.pause.wait()
            await asyncio.sleep(0)
            yield "Second."

    class ContextStep:
        name = "compile_context"

        async def run(self, ctx):
            contexts.append(ctx)
            ctx.compiled_context = "Compiled companion and memory prompt"
            return ctx

    provider = Provider()
    steps = [RetrieveStep(loader_cls=None), ContextStep(), CallProviderStep(), RespondStep()]
    monkeypatch.setattr(streaming, "build_default_pipeline", lambda: SimpleNamespace(_steps=steps))
    monkeypatch.setattr(streaming, "_select_provider", lambda ctx: provider)
    monkeypatch.setattr(
        streaming, "_generate_recent_message_embeddings", AsyncMock(), raising=False
    )
    monkeypatch.setattr("chatbot.api.chat._generate_recent_message_embeddings", AsyncMock())
    db = AsyncMock()
    db.__aenter__.return_value = db
    session = SimpleNamespace(
        id=uuid.UUID(SID), user_id=uuid.UUID(UID), companion_id=CID, title="Existing", room_id=None
    )
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: session)
    monkeypatch.setattr(streaming, "streaming_session_factory", lambda: db)
    monkeypatch.setattr(router, "async_session_factory", lambda: db)
    monkeypatch.setattr(streaming, "_session_snapshot", lambda _: {})
    monkeypatch.setattr(
        streaming, "_load_stream_companion_profile", AsyncMock(return_value={"name": "Companion"})
    )
    monkeypatch.setattr(streaming, "_load_stream_session_identity", AsyncMock(return_value=None))
    monkeypatch.setattr(
        streaming, "_load_db_session_history", AsyncMock(side_effect=lambda *a, **kw: list(history))
    )

    async def persist(**kwargs):
        saves.append(kwargs)
        history.append({"role": "user", "content": kwargs["user_message"]})
        if kwargs["assistant_message"]:
            history.append({"role": "assistant", "content": kwargs["assistant_message"]})
        if kwargs.get("interrupted"):
            history.append({"role": "system", "content": "[interrupted]"})

    monkeypatch.setattr(streaming, "_persist_messages", persist)
    monkeypatch.setattr(streaming, "_run_post_stream_db_write", AsyncMock())
    title = Mock()
    monkeypatch.setattr(streaming, "_maybe_schedule_title_generation", title)
    traces = SimpleNamespace(
        start=AsyncMock(return_value="trace"), record=AsyncMock(), complete=AsyncMock()
    )
    monkeypatch.setattr(streaming, "_start_turn_trace", traces.start)
    monkeypatch.setattr(streaming, "_record_turn_trace_step", traces.record)
    monkeypatch.setattr(streaming, "_complete_turn_trace", traces.complete)
    return SimpleNamespace(
        provider=provider,
        history=history,
        saves=saves,
        contexts=contexts,
        engine=engine,
        db=db,
        title=title,
        traces=traces,
    )


def stream_args(app, *, spoken=False):
    request = Request(
        {"type": "http", "app": app, "state": {"avatar_source": "spoken" if spoken else "typed"}}
    )
    return dict(
        request=streaming.StreamRequest(session_id=SID, message="question"),
        http_request=request,
        current_user=USER,
        session_id=uuid.UUID(SID),
        session_title="Existing",
        session_companion_id=CID,
        session_room_id=None,
        session_snapshot={},
        character_profile={"name": "Companion"},
        identity_state=None,
        history=[],
    )


async def test_one_generation_shared_tokens_memory_and_prompt_trace(pipeline):
    app = FastAPI()
    app.state.retrieval_engine = pipeline.engine
    hub, binding = await bound()
    app.state.avatar_coordinator = hub
    frames = [frame async for frame in streaming._stream_turn(**stream_args(app))]
    await binding.turn.worker
    tokens = [json.loads(f[6:])["text"] for f in frames if '"type": "token"' in f]
    assert tokens == binding.runtime.tokens == ["First. ", "Second."]
    assert pipeline.provider.calls == 1
    pipeline.provider.complete.assert_not_awaited()
    pipeline.title.assert_not_called()
    pipeline.engine.retrieve.assert_awaited_once_with(query="question", session_id=SID)
    assert pipeline.contexts[0].metadata["character_profile"]["name"] == "Companion"
    assert pipeline.contexts[0].metadata["llm_prompt"]
    assert pipeline.contexts[0].retrieved_memories == [{"content": "persistent memory"}]
    pipeline.traces.start.assert_awaited_once()
    pipeline.traces.complete.assert_awaited_once()
    assert pipeline.saves[0]["assistant_message"] == "First. Second."
    await hub.detach(binding)


async def test_spoken_barge_in_persists_heard_prefix_before_next_typed_turn(pipeline):
    app = FastAPI()
    app.state.retrieval_engine = pipeline.engine
    hub, binding = await bound()
    app.state.avatar_coordinator = hub
    pipeline.provider.pause = asyncio.Event()

    async def consume():
        async for _ in streaming._stream_turn(**stream_args(app, spoken=True)):
            pass

    task = asyncio.create_task(consume())
    await pipeline.provider.first.wait()
    while not binding.turn.segments:
        await asyncio.sleep(0)
    old = binding.turn
    segment_id = next(iter(old.segments))
    await hub.control(binding, {"type": "playback_end", "id": segment_id})
    await hub.interrupt(binding)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pipeline.saves[0]["assistant_message"] == "First. "
    assert pipeline.saves[0]["interrupted"]
    assert old.finished.is_set()
    assert binding.runtime.cancelled
    pipeline.provider.pause = None
    async for _ in streaming._stream_turn(**stream_args(app)):
        pass
    assert pipeline.provider.calls == 2  # one call for EACH user input
    assert pipeline.contexts[1].history == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "First. "},
        {"role": "system", "content": "[interrupted]"},
    ]
    assert binding.turn.turn_id != old.turn_id
    assert pipeline.saves[1]["turn_id"] != pipeline.saves[0]["turn_id"]
    await hub.detach(binding)


async def test_disconnect_cancel_scope_still_persists_interruption(pipeline):
    app = FastAPI()
    app.state.retrieval_engine = pipeline.engine
    hub, binding = await bound()
    app.state.avatar_coordinator = hub
    pipeline.provider.pause = asyncio.Event()
    with anyio.CancelScope() as scope:
        stream = streaming._stream_turn(**stream_args(app, spoken=True))
        async for frame in stream:
            if '"type": "token"' in frame:
                scope.cancel()
    assert pipeline.saves[0]["assistant_message"] == ""
    assert pipeline.saves[0]["interrupted"]
    assert binding.turn.finished.is_set()
    await hub.detach(binding)


async def test_no_avatar_keeps_existing_text_path(pipeline):
    app = FastAPI()
    app.state.retrieval_engine = pipeline.engine
    frames = [f async for f in streaming._stream_turn(**stream_args(app))]
    assert pipeline.provider.calls == 1
    assert frames[-1] == "data: [DONE]\n\n"
    assert pipeline.saves[0]["assistant_message"] == "First. Second."
    pipeline.title.assert_called_once()


def test_browser_spoken_and_typed_share_authenticated_session(transport, pipeline):
    app, client, factory, authorize = transport
    app.state.retrieval_engine = pipeline.engine
    from chatbot.auth.dependencies import get_current_user
    from chatbot.db import get_db

    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_db] = lambda: pipeline.db
    with client.websocket_connect("/api/avatar/ws", headers=ORIGIN) as ws:
        ws.send_json(AUTH)
        assert ws.receive_json()["type"] == "config"
        assert ws.receive_json()["type"] == "ready"
        typed = client.post("/chat/stream", json={"session_id": SID, "message": "typed question"})
        assert typed.status_code == 200
        assert '"text": "First. "' in typed.text
        # The fake STT emits its transcript when it receives a microphone packet.
        ws.send_bytes(b"M" + struct.pack("<f", 0.0))
        while True:
            packet = ws.receive()
            if packet.get("bytes"):
                data = packet["bytes"]
                n = struct.unpack_from("<H", data, 1)[0]
                assert data[0:1] == b"A"
                assert struct.unpack_from("<I", data, 3 + n)[0] == 24000
                ws.send_json({"type": "playback_end", "id": data[3 : 3 + n].decode()})
            else:
                event = json.loads(packet["text"])
                if event.get("type") == "response":
                    assert event["text"] == "First. Second."
                    break
        assert pipeline.provider.calls == 2
        assert [s["user_message"] for s in pipeline.saves] == ["typed question", "spoken question"]
        assert all(s["session_id"] == uuid.UUID(SID) for s in pipeline.saves)
        assert pipeline.contexts[1].history[0]["content"] == "typed question"
        assert pipeline.engine.retrieve.await_count == 2


async def test_real_interrupted_persistence_omits_unheard_assistant(monkeypatch):
    db = AsyncMock()
    db.add = Mock()
    monkeypatch.setattr(streaming, "streaming_session_factory", lambda: db)
    await streaming._persist_messages(
        session_id=uuid.UUID(SID),
        companion_id=CID,
        current_user=USER,
        user_message="hello",
        assistant_message="",
        turn_id="turn",
        interrupted=True,
        strict=True,
    )
    rows = [call.args[0] for call in db.add.call_args_list]
    assert [row.role for row in rows] == ["user", "system"]
    assert "interrupted" in rows[-1].content
    assert all(row.turn_id == "turn" for row in rows)
    db.commit.assert_awaited_once()
    db.commit.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError):
        await streaming._persist_messages(
            session_id=uuid.UUID(SID),
            companion_id=CID,
            current_user=USER,
            user_message="hello",
            assistant_message="Heard",
            strict=True,
        )


async def test_cancel_during_sse_send_explicitly_closes_and_persists(pipeline):
    app = FastAPI()
    app.state.retrieval_engine = pipeline.engine
    hub, binding = await bound()
    app.state.avatar_coordinator = hub
    sending = asyncio.Event()
    response = streaming.ChatStreamingResponse(
        streaming._stream_turn(**stream_args(app, spoken=True))
    )

    async def send(message):
        if b'"type": "token"' in message.get("body", b""):
            sending.set()
            await asyncio.Event().wait()  # simulate a slow/disconnected SSE socket

    task = asyncio.create_task(response.stream_response(send))
    await sending.wait()
    await hub.interrupt(binding)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(pipeline.saves) == 1
    assert pipeline.saves[0]["interrupted"]
    assert pipeline.saves[0]["assistant_message"] == ""
    assert binding.turn.finished.is_set()
    await hub.detach(binding)


async def test_cancel_during_commit_does_not_duplicate_exchange(pipeline, monkeypatch):
    app = FastAPI()
    app.state.retrieval_engine = pipeline.engine
    hub, binding = await bound()
    app.state.avatar_coordinator = hub
    committing = asyncio.Event()
    release = asyncio.Event()
    save = streaming._persist_messages

    async def delayed_save(**kwargs):
        committing.set()
        await release.wait()
        await save(**kwargs)

    monkeypatch.setattr(streaming, "_persist_messages", delayed_save)

    async def consume():
        async for _ in streaming._stream_turn(**stream_args(app)):
            pass

    task = asyncio.create_task(consume())
    await committing.wait()
    stop = asyncio.create_task(hub.interrupt(binding))
    await asyncio.sleep(0)
    assert not stop.done()
    release.set()
    await stop
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(pipeline.saves) == 1
    assert pipeline.saves[0]["assistant_message"] == "First. Second."
    assert binding.turn.persisted
    await hub.detach(binding)


async def test_failed_persistence_blocks_subsequent_avatar_turn(pipeline, monkeypatch):
    app = FastAPI()
    app.state.retrieval_engine = pipeline.engine
    hub, binding = await bound()
    app.state.avatar_coordinator = hub
    monkeypatch.setattr(
        streaming, "_persist_messages", AsyncMock(side_effect=RuntimeError("DB failed"))
    )
    frames = [f async for f in streaming._stream_turn(**stream_args(app))]
    assert any('"type": "error"' in f for f in frames)
    assert binding.turn.persistence_error
    with pytest.raises(RuntimeError, match="could not be persisted"):
        await hub.begin(binding, source="typed")
    await hub.detach(binding)


async def test_stale_frame_waiting_for_send_lock_is_suppressed():
    hub, binding = await bound()
    turn = await hub.begin(binding, source="typed")
    await binding.send_lock.acquire()
    delivery = asyncio.create_task(
        hub.emit(
            binding,
            {
                "type": "media",
                "turn_id": turn.turn_id,
                "data": b"queued stale frame",
            },
        )
    )
    await asyncio.sleep(0)
    turn.interrupted = True
    binding.send_lock.release()
    await delivery
    assert b"queued stale frame" not in binding.websocket.sent
    turn.finished.set()
    await hub.stop_speech(binding, turn)
    await hub.detach(binding)


async def test_zero_token_provider_does_not_trigger_second_generation(pipeline):
    async def empty_stream(**kwargs):
        pipeline.provider.calls += 1
        if False:
            yield ""

    pipeline.provider.stream = empty_stream
    app = FastAPI()
    app.state.retrieval_engine = pipeline.engine
    frames = [f async for f in streaming._stream_turn(**stream_args(app))]
    assert frames[-1] == "data: [DONE]\n\n"
    assert pipeline.provider.calls == 1
    pipeline.provider.complete.assert_not_awaited()


async def test_companion_change_rejects_spoken_generation(pipeline):
    from fastapi import HTTPException

    app = FastAPI()
    app.state.retrieval_engine = pipeline.engine
    hub, binding = await bound()
    app.state.avatar_coordinator = hub
    binding.companion_id = str(uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        async for _ in streaming._stream_turn(**stream_args(app, spoken=True)):
            pass
    assert exc.value.status_code == 409
    assert pipeline.provider.calls == 0
    assert not pipeline.saves
    await hub.detach(binding)


async def test_takeover_refuses_to_overtake_unsettled_persistence(monkeypatch):
    hub, original = await bound()
    interrupt = hub.interrupt
    monkeypatch.setattr(
        hub, "interrupt", AsyncMock(side_effect=TimeoutError("persistence pending"))
    )
    replacement = AvatarBinding(SID, UID, CID, FakeSocket())
    factory = Mock(side_effect=FakeRuntime)
    with pytest.raises(TimeoutError):
        await hub.connect(replacement, factory)
    assert hub.bindings[SID] is original
    assert not original.closed
    factory.assert_not_called()
    monkeypatch.setattr(hub, "interrupt", interrupt)
    await hub.detach(original)
