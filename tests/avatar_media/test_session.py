"""Browser connection lifecycle (my_avatar da976ad) on a fake transport, plus the FastAPI adapter."""
import asyncio
import json

import numpy as np

from chatbot.avatar.config import REPLACED_CODE
from chatbot.avatar.media import AvatarMediaEngine
from chatbot.avatar.protocol import Interrupted, TranscriptReady, unpack_frame
from chatbot.avatar.session import FastAPIWebSocketTransport, MediaSession

from .helpers import Harness, wait_for


class FakeTransport:
    """A browser stand-in; with an engine it also acknowledges each audio frame's playback."""

    def __init__(self, engine=None):
        self.json, self.binary, self.close_args, self._closed = [], [], None, False
        self.engine = engine

    async def send_json(self, data):
        self.json.append(data)

    async def send_bytes(self, data):
        self.binary.append(data)
        if self.engine is not None and data[:1] == b"A":
            segment_id = unpack_frame(data)[1]
            asyncio.get_running_loop().call_later(0.01, lambda: asyncio.create_task(
                self.engine.handle_client_message({"type": "playback_end", "id": segment_id})))

    async def close(self, code, reason):
        self.close_args, self._closed = (code, reason), True

    @property
    def closed(self):
        return self._closed


async def session(**kw):
    h = await Harness(**kw).ready()
    events = []

    async def on_event(event):
        events.append(event)
    return h, MediaSession(h.engine, on_event), events


async def test_attach_announces_sync_buffer_and_listening():
    h, s, _ = await session()
    t = FakeTransport()
    await s.attach(t, tab="a")
    assert t.json == [{"type": "config", "sync_buffer_ms": 60}, {"type": "status", "value": "Listening"}]


async def test_newer_window_takes_over_and_older_is_told_why():
    h, s, _ = await session()
    old, new = FakeTransport(), FakeTransport()
    await s.attach(old)
    await s.attach(new)
    assert old.close_args == (REPLACED_CODE, "another window took over") and REPLACED_CODE == 4001
    assert not new.closed and s.transport is new


async def test_evicted_window_closing_does_not_drop_the_active_session():
    h, s, _ = await session()
    old, new = FakeTransport(), FakeTransport()
    await s.attach(old)
    await s.attach(new)
    turn = await h.engine.begin_response()
    await s.detach(old)
    assert s.transport is new and h.engine.turn is not None and h.engine.turn.turn_id == turn
    await h.engine.cancel()


async def test_active_window_disconnect_interrupts_the_turn_and_reconnect_is_accepted():
    h, s, events = await session(auto_playback=False)
    first = FakeTransport()
    await s.attach(first)
    turn = await h.engine.begin_response()
    await h.engine.feed_text(turn, "Hello there, my friend. ")
    await s.detach(first)
    assert s.transport is None and h.engine.turn is None
    assert (await h.engine.wait_turn(turn)).status == "cancelled"
    assert [e for e in events if isinstance(e, Interrupted)] == [Interrupted("disconnect", turn)]
    second = FakeTransport()
    await s.attach(second)
    assert s.transport is second and not second.closed


async def test_detach_while_idle_does_not_send_interrupts():
    h, s, _ = await session()
    t = FakeTransport()
    await s.attach(t)
    sent = len(h.avatar.sent)
    await s.detach(t)
    assert len(h.avatar.sent) == sent


async def test_engine_output_reaches_only_the_current_window_and_events_reach_the_coordinator():
    h, s, events = await session()
    old, new = FakeTransport(h.engine), FakeTransport(h.engine)
    await s.attach(old)
    await s.attach(new)
    h.transcripts = ["hi"]
    turn = await h.engine.begin_response()
    await h.engine.feed_text(turn, "Hello there, my friend.")
    await h.engine.end_response(turn)
    await asyncio.wait_for(h.engine.wait_turn(turn), 5)
    assert new.binary and not old.binary
    assert any(m.get("type") == "status" for m in new.json)
    assert {type(e).__name__ for e in events} >= {"TurnEnded", "Diagnostic"}


async def test_browser_messages_drive_the_engine_and_garbage_is_ignored():
    h, s, events = await session()
    t = FakeTransport()
    await s.attach(t)
    await s.handle_text("not json")
    await s.handle_text("[1, 2]")
    await s.handle_text(json.dumps({"type": "mic", "enabled": False}))
    assert not h.engine.mic_enabled
    await s.handle_text(json.dumps({"type": "mic", "enabled": True}))
    h.transcripts = ["from the browser"]
    packet = b"M" + np.full(640, 0.08, "<f4").tobytes()
    for _ in range(5):
        await s.handle_bytes(packet)
    for _ in range(20):
        await s.handle_bytes(b"M" + np.zeros(640, "<f4").tobytes())
    await wait_for(lambda: any(isinstance(e, TranscriptReady) for e in events))
    assert next(e for e in events if isinstance(e, TranscriptReady)).text == "from the browser"


async def test_a_dead_browser_socket_never_breaks_the_media_pumps():
    h, s, _ = await session()
    t = FakeTransport(h.engine)

    async def broken(data):
        asyncio.get_running_loop().call_later(0.01, lambda: asyncio.create_task(
            h.engine.handle_client_message({"type": "playback_end", "id": unpack_frame(data)[1]})))
        raise ConnectionResetError
    t.send_bytes = broken
    await s.attach(t)
    turn = await h.engine.begin_response()
    await h.engine.feed_text(turn, "Hello there, my friend.")
    await h.engine.end_response(turn)
    assert (await asyncio.wait_for(h.engine.wait_turn(turn), 5)).status == "completed"


def test_fastapi_adapter_round_trips_over_a_real_websocket():
    """MediaSession over starlette's TestClient: takeover closes the old socket with 4001."""
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.testclient import TestClient

    loop_engine = {}
    app = FastAPI()

    @app.websocket("/ws")
    async def endpoint(websocket: WebSocket):
        await websocket.accept()
        if "session" not in loop_engine:
            engine = AvatarMediaEngine(worker_factory=lambda _: (Harness().avatar, Harness().tts))
            loop_engine["session"] = MediaSession(engine)
        media, transport = loop_engine["session"], FastAPIWebSocketTransport(websocket)
        await media.attach(transport)
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("text") is not None:
                    await media.handle_text(message["text"])
        finally:
            await media.detach(transport)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as first:
            assert first.receive_json()["type"] == "config"
            assert first.receive_json()["value"] == "Listening"
            first.send_text(json.dumps({"type": "mic", "enabled": False}))
            with client.websocket_connect("/ws") as second:
                assert second.receive_json()["type"] == "config"
                try:
                    first.receive_json()
                    assert False, "older window should have been closed"
                except WebSocketDisconnect as closed:
                    assert closed.code == REPLACED_CODE
                assert not loop_engine["session"].engine.mic_enabled
