"""End-to-end provider routing, starting from the frontend's real selections.

The scenario is produced by the real frontend code (picker state logic, API
client and avatar protocol builders, run under node) and replayed against the
real FastAPI endpoints, provider registry, provider selection and
OpenAI-compatible HTTP adapter. Only the network and the database are faked, so
what is verified is the actual destination URL and model ID of each request.

Regression: voice (avatar) turns carried no selection and always went to the
default provider (OpenRouter) no matter what the model picker showed.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS_DIR))

from test_avatar_backend import (  # noqa: E402  (shared WS fakes)
    CID,
    ORIGIN,
    SID,
    TOKEN,
    UID,
    USER,
    FakeRuntime,
)
from test_provider_routing import (  # noqa: E402  (shared network fake)
    OLLAMA_MODEL,
    FakeNetwork,
    make_settings,
)

from chatbot.api import chat as chat_api  # noqa: E402
from chatbot.api import conversation  # noqa: E402
from chatbot.api import streaming  # noqa: E402
from chatbot.auth.dependencies import get_current_user  # noqa: E402
from chatbot.avatar import router as avatar_router_module  # noqa: E402
from chatbot.core import TurnPipeline  # noqa: E402
from chatbot.core.turn import CallProviderStep, RespondStep  # noqa: E402
from chatbot.db import get_db  # noqa: E402
from chatbot.providers import openai_compat  # noqa: E402
from chatbot.providers.bootstrap import build_default_registry  # noqa: E402

@pytest.fixture(autouse=True)
def isolate_observability(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real collectors persist to Postgres; tests must never write to the live database."""
    from chatbot.core import turn
    from chatbot.observability.metrics import MetricsCollector
    from chatbot.observability.tracing import TraceCollector

    monkeypatch.setattr(turn, "trace_collector", TraceCollector(store="memory"))
    monkeypatch.setattr(turn, "metrics_collector", MetricsCollector(store="memory"))


NODE = shutil.which("node")
EMITTER = ROOT / "react_ui" / "tests" / "helpers" / "emitFrontendRequests.ts"
OPENROUTER_HOST, OLLAMA_HOST = "openrouter.test", "ollama.test"
FRONTEND_OLLAMA_MODEL = (
    "hf.co/KevinJK51/Qwen3.6-12B-IQ-Ultra-Heretic-Uncensored-Thinking-V2-Hightop-GGUF:Q4_K_M"
)
assert FRONTEND_OLLAMA_MODEL == OLLAMA_MODEL


@pytest.fixture(scope="module")
def frontend() -> list[dict[str, Any]]:
    """What the real frontend sends for: OpenRouter -> Ollama(model) -> OpenRouter -> Ollama down."""
    if NODE is None:
        pytest.skip("node is required to run the frontend")
    done = subprocess.run(
        [NODE, "--experimental-strip-types", "--no-warnings", str(EMITTER)],
        cwd=ROOT / "react_ui",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def steps(frontend: list[dict[str, Any]], *, streaming_on: bool) -> list[dict[str, Any]]:
    return [step for step in frontend if step["streaming"] is streaming_on]


class Network(FakeNetwork):
    """FakeNetwork that can also refuse connections, like a stopped Ollama."""

    def __init__(self) -> None:
        super().__init__()
        self.refused: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host in self.refused:
            self.requests.append({"host": request.url.host, "refused": True, "model": None})
            raise httpx.ConnectError("connection refused", request=request)
        return super().handler(request)


@pytest.fixture
def network(monkeypatch: pytest.MonkeyPatch) -> Network:
    fake = Network()
    real_client = httpx.AsyncClient

    def client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(fake.handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(openai_compat.httpx, "AsyncClient", client_factory)
    return fake


def make_app(*routers: Any) -> FastAPI:
    app = FastAPI()
    for router in routers:
        app.include_router(router)
    # OpenRouter is the default provider, exactly like the running application.
    settings = make_settings("openrouter")
    app.state.provider_registry = build_default_registry(settings)
    app.state.default_provider = settings.default_provider
    app.state.default_model = settings.default_model
    app.dependency_overrides[get_current_user] = lambda: USER
    return app


def fake_db(companion_id: str | None = None) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    session = SimpleNamespace(
        id=uuid.UUID(SID),
        user_id=uuid.UUID(UID),
        companion_id=companion_id,
        title="New Chat",
        room_id=None,
        updated_at=None,
    )
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: session)
    return db


@pytest.fixture
def captured_ctx() -> list[Any]:
    return []


@pytest.fixture
def chat_client(monkeypatch: pytest.MonkeyPatch, captured_ctx: list[Any]) -> TestClient:
    """The real POST /chat/ endpoint with only database helpers stubbed."""
    app = make_app(chat_api.chat_router)
    db = fake_db()
    app.dependency_overrides[get_db] = lambda: db

    monkeypatch.setattr(conversation, "load_companion_inputs", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(conversation, "load_session_identity", AsyncMock(return_value=None))
    monkeypatch.setattr(chat_api, "_load_chat_room", AsyncMock(return_value=None))
    monkeypatch.setattr(conversation, "load_db_session_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(conversation, "prepare_memory_workflow", AsyncMock())
    monkeypatch.setattr(conversation, "schedule_title_generation", Mock())
    # The real selection/provider steps; no memory, persona or DB-backed steps.
    monkeypatch.setattr(
        chat_api,
        "build_default_pipeline",
        lambda: TurnPipeline([CallProviderStep(), RespondStep()]),
    )

    real_run = chat_api._run_pipeline

    async def capture(ctx):
        ctx = await real_run(ctx)
        captured_ctx.append(ctx)
        return ctx

    monkeypatch.setattr(chat_api, "_run_pipeline", capture)
    return TestClient(app)


def patch_stream_internals(
    monkeypatch: pytest.MonkeyPatch, captured_ctx: list[Any], *, companion: bool = False
) -> AsyncMock:
    """Stub only database/persistence around the real streaming endpoint."""
    steps_ = [CallProviderStep(), RespondStep()]
    monkeypatch.setattr(streaming, "build_default_pipeline", lambda: SimpleNamespace(_steps=steps_))
    db = fake_db(CID if companion else None)
    db.__aenter__.return_value = db
    monkeypatch.setattr(streaming, "streaming_session_factory", lambda: db)
    monkeypatch.setattr(avatar_router_module, "async_session_factory", lambda: db)
    monkeypatch.setattr(streaming, "_session_snapshot", lambda _: {})
    monkeypatch.setattr(
        conversation,
        "load_companion_inputs",
        AsyncMock(return_value=({"name": "Companion"} if companion else None, None)),
    )
    monkeypatch.setattr(conversation, "load_session_identity", AsyncMock(return_value=None))
    monkeypatch.setattr(conversation, "load_db_session_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(streaming, "_persist_messages", AsyncMock())
    monkeypatch.setattr(streaming, "_run_post_stream_db_write", AsyncMock())
    monkeypatch.setattr(conversation, "schedule_title_generation", Mock())
    monkeypatch.setattr(conversation, "generate_recent_message_embeddings", AsyncMock())
    monkeypatch.setattr(streaming, "_start_turn_trace", AsyncMock(return_value="trace"))
    monkeypatch.setattr(streaming, "_record_turn_trace_step", AsyncMock())

    async def complete(ctx, trace_id, *args, **kwargs):
        captured_ctx.append(ctx)

    monkeypatch.setattr(streaming, "_complete_turn_trace", complete)
    return db


@pytest.fixture
def stream_client(monkeypatch: pytest.MonkeyPatch, captured_ctx: list[Any]) -> TestClient:
    """The real POST /chat/stream endpoint with only database helpers stubbed."""
    app = make_app(streaming.streaming_router)
    db = patch_stream_internals(monkeypatch, captured_ctx)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def post_frontend(client: TestClient, http: dict[str, Any]) -> httpx.Response:
    body = dict(http["body"])
    body["session_id"] = SID  # the real frontend uses the live session ID
    return client.post(http["path"], json=body)


def destinations(network: Network) -> list[tuple[str, str | None]]:
    return [(request["host"], request.get("model")) for request in network.requests]


def expected_destination(step: dict[str, Any]) -> tuple[str, str]:
    host = {"openrouter": OPENROUTER_HOST, "ollama": OLLAMA_HOST}[step["picker"]["provider"]]
    return host, step["picker"]["model"]


# --------------------------------------------------------------------------
# The frontend scenario itself
# --------------------------------------------------------------------------


def test_frontend_emits_the_scenario(frontend: list[dict[str, Any]]) -> None:
    for streaming_on in (False, True):
        ordered = steps(frontend, streaming_on=streaming_on)
        assert [s["step"] for s in ordered] == [
            "openrouter-initial",
            "ollama-selected",
            "back-to-openrouter",
            "ollama-unavailable",
        ]
        path = "/chat/stream" if streaming_on else "/chat/"
        assert [s["http"]["path"] for s in ordered[:3]] == [path] * 3
        ollama = ordered[1]["http"]["body"]
        assert ollama["provider_override"] == "ollama"
        assert ollama["model_override"] == FRONTEND_OLLAMA_MODEL
        # With Ollama unreachable the frontend refuses to send at all.
        assert ordered[3]["http"] is None
        assert "Cannot connect to Ollama" in ordered[3]["blocker"]


# --------------------------------------------------------------------------
# Typed turns: real endpoints, real destinations
# --------------------------------------------------------------------------

@pytest.mark.parametrize("streaming_on", [False, True])
@pytest.mark.parametrize("thinking", [False, True])
@pytest.mark.parametrize("provider", ["ollama", "openrouter", "vllm", "sglang"])
def test_thinking_reaches_only_ollama_adapter(
    streaming_on, thinking, provider, network, chat_client, stream_client, captured_ctx
) -> None:
    client = stream_client if streaming_on else chat_client
    response = client.post("/chat/stream" if streaming_on else "/chat/", json={
        "session_id": SID, "message": "hello", "provider_override": provider,
        "model_override": FRONTEND_OLLAMA_MODEL, "thinking": thinking,
    })
    assert response.status_code == 200
    assert network.requests[0]["reasoning_effort"] == (
        ("high" if thinking else "none") if provider == "ollama" else None
    )
    assert network.requests[0]["model"] == FRONTEND_OLLAMA_MODEL
    assert captured_ctx[-1].metadata["routing_decision"]["thinking"] is thinking


def test_non_streaming_turns_reach_the_selected_provider(
    frontend: list[dict[str, Any]],
    network: Network,
    chat_client: TestClient,
    captured_ctx: list[Any],
) -> None:
    for step in steps(frontend, streaming_on=False)[:3]:
        network.requests.clear()
        response = post_frontend(chat_client, step["http"])

        assert response.status_code == 200, response.text
        host, model = expected_destination(step)
        assert destinations(network) == [(host, model)], step["step"]
        assert response.json()["response"] == f"hello from {host}"

        # Observable: the trace records the real destination, not just a name.
        decision = captured_ctx[-1].metadata["routing_decision"]
        assert decision["provider"] == step["picker"]["provider"]
        assert decision["model"] == model
        assert decision["endpoint"].startswith("http")
        assert host in decision["endpoint"]
        assert decision["explicit_provider"] is True
        assert decision["explicit_model"] is True


def test_streaming_turns_reach_the_selected_provider(
    frontend: list[dict[str, Any]],
    network: Network,
    stream_client: TestClient,
    captured_ctx: list[Any],
) -> None:
    for step in steps(frontend, streaming_on=True)[:3]:
        network.requests.clear()
        response = post_frontend(stream_client, step["http"])

        assert response.status_code == 200, response.text
        host, model = expected_destination(step)
        assert destinations(network) == [(host, model)], step["step"]
        assert network.requests[0]["stream"] is True
        assert f"hello from {host}" in response.text

        decision = captured_ctx[-1].metadata["routing_decision"]
        assert decision["provider"] == step["picker"]["provider"]
        assert decision["model"] == model
        assert host in decision["endpoint"]


def test_streaming_and_non_streaming_hit_identical_destinations(
    frontend: list[dict[str, Any]],
    network: Network,
    chat_client: TestClient,
    stream_client: TestClient,
) -> None:
    seen: dict[bool, list[tuple[str, str | None]]] = {}
    for streaming_on, client in ((False, chat_client), (True, stream_client)):
        seen[streaming_on] = []
        for step in steps(frontend, streaming_on=streaming_on)[:3]:
            network.requests.clear()
            post_frontend(client, step["http"])
            seen[streaming_on].extend(destinations(network))

    assert seen[False] == seen[True]
    assert [host for host, _ in seen[True]] == [OPENROUTER_HOST, OLLAMA_HOST, OPENROUTER_HOST]


@pytest.mark.parametrize("streaming_on", [False, True], ids=["non-streaming", "streaming"])
def test_ollama_unavailable_never_falls_back(
    frontend: list[dict[str, Any]],
    network: Network,
    chat_client: TestClient,
    stream_client: TestClient,
    streaming_on: bool,
) -> None:
    """Even if a request is made while Ollama is down, OpenRouter is never contacted."""
    network.refused.add(OLLAMA_HOST)
    client = stream_client if streaming_on else chat_client
    ollama_step = steps(frontend, streaming_on=streaming_on)[1]

    response = post_frontend(client, ollama_step["http"])

    assert response.status_code == 200
    assert [r["host"] for r in network.requests] == [OLLAMA_HOST]  # a single attempt
    assert OPENROUTER_HOST not in [r["host"] for r in network.requests]
    assert "Provider error" in response.text or "Provider stream error" in response.text
    assert "connection refused" in response.text.lower() or "request failed" in response.text.lower()


@pytest.mark.parametrize("streaming_on", [False, True], ids=["non-streaming", "streaming"])
def test_missing_ollama_model_reports_actual_error_without_fallback(
    frontend: list[dict[str, Any]],
    network: Network,
    chat_client: TestClient,
    stream_client: TestClient,
    streaming_on: bool,
) -> None:
    network.failures[OLLAMA_HOST] = (404, f"model '{FRONTEND_OLLAMA_MODEL}' not found")
    client = stream_client if streaming_on else chat_client
    ollama_step = steps(frontend, streaming_on=streaming_on)[1]

    response = post_frontend(client, ollama_step["http"])

    assert [(r["host"], r["model"]) for r in network.requests] == [
        (OLLAMA_HOST, FRONTEND_OLLAMA_MODEL)
    ]
    assert "not found" in response.text


# --------------------------------------------------------------------------
# Spoken (avatar) turns: the path that used to ignore the selection
# --------------------------------------------------------------------------


@pytest.fixture
def avatar_client(monkeypatch: pytest.MonkeyPatch, captured_ctx: list[Any]):
    app = make_app(avatar_router_module.avatar_router, streaming.streaming_router)
    patch_stream_internals(monkeypatch, captured_ctx, companion=True)
    app.state.avatar_runtime_factory = Mock(side_effect=FakeRuntime)
    monkeypatch.setattr(avatar_router_module, "_authorize", AsyncMock())
    with TestClient(app) as client:
        yield app, client


MIC_PACKET = b"M" + b"\x00" * 16


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def open_avatar(client: TestClient, auth: dict[str, Any]):
    ws = client.websocket_connect("/api/avatar/ws", headers=ORIGIN)
    session = ws.__enter__()
    try:
        session.send_json(
            {"type": "auth", "token": TOKEN, "session_id": SID, "companion_id": CID} | auth
            | {"token": TOKEN, "session_id": SID, "companion_id": CID}
        )
        assert session.receive_json()["type"] == "config"
        assert session.receive_json()["type"] == "ready"
    except BaseException:
        ws.__exit__(None, None, None)
        raise
    return ws, session


def speak(session) -> None:
    session.send_bytes(MIC_PACKET)  # the fake STT answers with a transcript


def test_spoken_thinking_handshake_and_update(network: Network, avatar_client) -> None:
    _app, client = avatar_client
    ws, session = open_avatar(client, {
        "provider_override": "ollama", "model_override": FRONTEND_OLLAMA_MODEL,
        "thinking": True,
    })
    try:
        speak(session)
        assert wait_for(lambda: len(network.requests) >= 1)
        assert network.requests[0]["reasoning_effort"] == "high"
        session.send_json({"type": "routing", "provider_override": "ollama",
                           "model_override": FRONTEND_OLLAMA_MODEL, "thinking": False})
        time.sleep(0.1)
        speak(session)
        assert wait_for(lambda: len(network.requests) >= 2)
        assert network.requests[1]["reasoning_effort"] == "none"
    finally:
        ws.__exit__(None, None, None)


def test_spoken_turn_uses_the_frontend_handshake_selection(
    frontend: list[dict[str, Any]], network: Network, avatar_client, captured_ctx: list[Any]
) -> None:
    _app, client = avatar_client
    ollama_step = steps(frontend, streaming_on=True)[1]
    ws, session = open_avatar(client, ollama_step["avatar"]["auth"])
    try:
        speak(session)
        assert wait_for(lambda: network.requests), "spoken turn made no LLM request"
    finally:
        ws.__exit__(None, None, None)

    assert destinations(network) == [(OLLAMA_HOST, FRONTEND_OLLAMA_MODEL)]
    assert OPENROUTER_HOST not in [r["host"] for r in network.requests]


def test_spoken_turn_follows_live_picker_changes(
    frontend: list[dict[str, Any]], network: Network, avatar_client
) -> None:
    """OpenRouter -> Ollama -> OpenRouter over one avatar connection."""
    _app, client = avatar_client
    first, second, third = steps(frontend, streaming_on=True)[:3]
    ws, session = open_avatar(client, first["avatar"]["auth"])
    try:
        for step in (first, second, third):
            network.requests.clear()
            session.send_json(step["avatar"]["routing"])  # what the popup sends on a picker change
            speak(session)
            assert wait_for(lambda: network.requests), step["step"]
            assert wait_for(lambda: len(network.requests) >= 1)
            time.sleep(0.1)  # a second, unexpected request would show up here
            assert destinations(network) == [expected_destination(step)], step["step"]
    finally:
        ws.__exit__(None, None, None)


def test_spoken_turn_without_a_selection_uses_the_default_as_before(
    network: Network, avatar_client
) -> None:
    _app, client = avatar_client
    ws, session = open_avatar(client, {})  # an older popup that sends no selection
    try:
        speak(session)
        assert wait_for(lambda: network.requests)
    finally:
        ws.__exit__(None, None, None)

    assert [r["host"] for r in network.requests] == [OPENROUTER_HOST]


def test_spoken_turn_with_ollama_down_does_not_fall_back(
    frontend: list[dict[str, Any]], network: Network, avatar_client
) -> None:
    _app, client = avatar_client
    network.refused.add(OLLAMA_HOST)
    ollama_step = steps(frontend, streaming_on=True)[1]
    ws, session = open_avatar(client, ollama_step["avatar"]["auth"])
    try:
        speak(session)
        assert wait_for(lambda: network.requests)
        time.sleep(0.2)
    finally:
        ws.__exit__(None, None, None)

    assert [r["host"] for r in network.requests] == [OLLAMA_HOST]


def test_blocked_selection_makes_no_request_for_a_spoken_turn(
    network: Network, avatar_client
) -> None:
    _app, client = avatar_client
    ws, session = open_avatar(
        client,
        {"provider_override": "ollama", "model_override": None, "blocked": "Select an Ollama model"},
    )
    try:
        speak(session)
        frames = []
        assert wait_for(lambda: frames.append(session.receive_json()) or frames[-1].get("type") == "error")
    finally:
        ws.__exit__(None, None, None)

    assert frames[-1]["error"] == "Select an Ollama model"
    assert network.requests == []


def test_routing_update_is_validated(avatar_client) -> None:
    from starlette.websockets import WebSocketDisconnect

    _app, client = avatar_client
    ws, session = open_avatar(client, {})
    try:
        session.send_json(
            {"type": "routing", "provider_override": "x" * 1000, "model_override": None, "blocked": None}
        )
        with pytest.raises(WebSocketDisconnect) as exc:
            session.receive_json()
        assert exc.value.code == 1008
    finally:
        try:
            ws.__exit__(None, None, None)
        except Exception:
            pass
