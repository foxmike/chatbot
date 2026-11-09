"""TTS speech-mode selection over the real avatar WebSocket endpoint."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_avatar_backend import AUTH, CID, ORIGIN, SID, FakeRuntime, authenticate_transport  # noqa: E402

from chatbot.avatar import router  # noqa: E402
from chatbot.avatar.coordinator import get_coordinator  # noqa: E402


@pytest.fixture
def avatar(monkeypatch):
    app = FastAPI()
    app.include_router(router.avatar_router)
    factory = Mock(side_effect=FakeRuntime)
    app.state.avatar_runtime_factory = factory
    monkeypatch.setattr(router, "_authorize", AsyncMock())
    monkeypatch.setattr(router, "_authenticate", authenticate_transport)
    with TestClient(app) as client:
        yield app, client, factory


def connect(client, **fields):
    return client.websocket_connect("/api/avatar/ws", headers=ORIGIN)


def handshake(ws, **fields):
    ws.send_json(AUTH | fields)
    assert ws.receive_json()["type"] == "config"
    assert ws.receive_json()["type"] == "ready"


def mode_of(app, session_id=SID):
    return get_coordinator(app).bindings[session_id].speech_mode


def test_natural_is_the_default_when_the_handshake_names_no_mode(avatar):
    app, client, _ = avatar
    with connect(client) as ws:
        handshake(ws)
        assert mode_of(app) == "natural"


@pytest.mark.parametrize("mode", ["natural", "fast"])
def test_the_saved_mode_in_the_handshake_is_established_before_any_turn(avatar, mode):
    app, client, _ = avatar
    with connect(client) as ws:
        handshake(ws, tts_mode=mode)
        assert mode_of(app) == mode


@pytest.mark.parametrize("bad", ["slow", "Natural", "", "natural ", "fast;drop"])
def test_an_invalid_handshake_mode_is_rejected_with_a_clear_error(avatar, bad):
    app, client, factory = avatar
    with connect(client) as ws:
        ws.send_json(AUTH | {"tts_mode": bad})
        frame = ws.receive_json()
        assert frame["type"] == "error" and "Invalid TTS mode" in frame["error"]
        assert "natural, fast" in frame["error"]
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008
    factory.assert_not_called()          # no media runtime was started for a rejected connection


def test_changing_the_dropdown_applies_to_the_binding_and_can_change_back(avatar):
    app, client, _ = avatar
    with connect(client) as ws:
        handshake(ws)
        ws.send_json({"type": "tts_mode", "mode": "fast"})
        ws.send_json({"type": "mic", "enabled": True})   # a round trip: the previous message was handled
        assert wait(lambda: mode_of(app) == "fast")
        ws.send_json({"type": "tts_mode", "mode": "natural"})
        assert wait(lambda: mode_of(app) == "natural")


def test_an_invalid_live_mode_reports_an_error_and_keeps_the_current_mode(avatar):
    app, client, _ = avatar
    with connect(client) as ws:
        handshake(ws, tts_mode="fast")
        ws.send_json({"type": "tts_mode", "mode": "turbo"})
        frame = ws.receive_json()
        assert frame["type"] == "error" and "Invalid TTS mode" in frame["error"]
        assert mode_of(app) == "fast"
        ws.send_json({"type": "tts_mode"})                  # missing value
        assert ws.receive_json()["type"] == "error"
        assert mode_of(app) == "fast"
        ws.send_json({"type": "tts_mode", "mode": "natural"})   # the connection still works
        assert wait(lambda: mode_of(app) == "natural")


def test_a_reconnect_uses_the_mode_sent_in_its_own_handshake(avatar):
    app, client, _ = avatar
    with connect(client) as first:
        handshake(first, tts_mode="fast")
        assert mode_of(app) == "fast"
    # The popup reconnects and, as it always does, sends its saved selection.
    with connect(client) as second:
        handshake(second, tts_mode="fast")
        assert mode_of(app) == "fast"
    with connect(client) as third:
        handshake(third, tts_mode="natural")
        assert mode_of(app) == "natural"


def test_one_avatar_session_cannot_change_another_sessions_mode(avatar):
    app, client, _ = avatar
    other = str(uuid.uuid4())
    with connect(client) as first, connect(client) as second:
        handshake(first, tts_mode="fast")
        handshake(second, session_id=other, tts_mode="natural")
        assert (mode_of(app, SID), mode_of(app, other)) == ("fast", "natural")
        first.send_json({"type": "tts_mode", "mode": "natural"})
        assert wait(lambda: mode_of(app, SID) == "natural")
        second.send_json({"type": "tts_mode", "mode": "fast"})
        assert wait(lambda: mode_of(app, other) == "fast")
        assert (mode_of(app, SID), mode_of(app, other)) == ("natural", "fast")


def wait(predicate, timeout=3.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False
