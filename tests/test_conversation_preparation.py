"""Shared conversation preparation: personality, provider inputs, preserved differences.

Covers Task 0.2. Standard, streaming and spoken chat prepare a turn through
``chatbot.api.conversation`` and ``chatbot.core.turn.prepare_provider_call``.
Provider payload tests send real requests through the OpenAI-compatible adapter
into a recording fake transport, so what is asserted is what would be sent.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent))
sys.path.insert(0, str(TESTS_DIR))

from test_avatar_backend import SID, UID, USER, bound  # noqa: E402
from test_provider_routing import FakeNetwork, make_settings  # noqa: E402

from chatbot.api import chat as chat_api  # noqa: E402
from chatbot.api import conversation, streaming  # noqa: E402
from chatbot.core import steps as core_steps  # noqa: E402
from chatbot.core import turn as turn_module  # noqa: E402
from chatbot.core.steps import TurnContext, TurnStep  # noqa: E402
from chatbot.core.turn import (  # noqa: E402
    FAST_PATH_STEPS,
    ROOM_FAST_PATH_STEPS,
    CallProviderStep,
    TurnPipeline,
    prepare_provider_call,
)
from chatbot.perf.background import BackgroundQueue  # noqa: E402
from chatbot.providers import openai_compat  # noqa: E402
from chatbot.providers.bootstrap import build_default_registry  # noqa: E402
from chatbot.providers.router import apply_routing_overrides  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_observability(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real collectors persist to Postgres; tests must never write to the live database."""
    from chatbot.observability.metrics import MetricsCollector
    from chatbot.observability.tracing import TraceCollector

    monkeypatch.setattr(turn_module, "trace_collector", TraceCollector(store="memory"))
    monkeypatch.setattr(turn_module, "metrics_collector", MetricsCollector(store="memory"))


# --------------------------------------------------------------------------
# Real provider payloads
# --------------------------------------------------------------------------


class RecordingNetwork(FakeNetwork):
    """FakeNetwork that also keeps every full JSON request body."""

    def __init__(self) -> None:
        super().__init__()
        self.bodies: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        return super().handler(request)


@pytest.fixture
def network(monkeypatch: pytest.MonkeyPatch) -> RecordingNetwork:
    fake = RecordingNetwork()
    real_client = openai_compat.httpx.AsyncClient

    def client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(fake.handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(openai_compat.httpx, "AsyncClient", client_factory)
    return fake


COMPILED = "Persona\nIdentity: Mercury\n\nConversation history:\nuser: hi"
HISTORY = [
    {"role": "user", "content": "hi  "},
    {"role": "assistant", "content": "hello"},
    {"role": "assistant", "content": ""},  # the model formatter drops empty content
    {"role": "system", "content": "[interrupted]"},  # never a provider chat message
]


def make_ctx(provider: str, model: str, *, thinking: bool = False, message: str = "  next  ") -> TurnContext:
    registry = build_default_registry(make_settings("openrouter"))
    metadata: dict[str, Any] = {
        "provider_registry": registry,
        "default_provider": "openrouter",
        "default_model": make_settings("openrouter").default_model,
    }
    apply_routing_overrides(metadata, provider, model)
    metadata["thinking"] = thinking
    return TurnContext(
        session_id=SID,
        user_message=message,
        history=[dict(m) for m in HISTORY],
        persona={},
        retrieved_memories=[],
        compiled_context=COMPILED,
        provider_response=None,
        extracted_memories=[],
        final_response=None,
        metadata=metadata,
    )


async def send_complete(ctx: TurnContext) -> None:
    await CallProviderStep().run(ctx)
    assert ctx.provider_response.startswith("hello from")


async def send_stream(ctx: TurnContext) -> list[str]:
    tokens = [token async for token in streaming._stream_provider_tokens(ctx)]
    assert tokens and not tokens[0].startswith("Provider stream error")
    return tokens


SCENARIOS = [
    pytest.param("openrouter", "meta-llama/llama-3.3-70b-instruct", False, id="openrouter-default-family"),
    pytest.param("openrouter", "anthropic/claude-3.5-sonnet", False, id="openrouter-anthropic-family"),
    pytest.param("openrouter", "google/gemini-2.0-flash-001", False, id="openrouter-gemini-family"),
    pytest.param("ollama", "some-local-model", True, id="ollama-thinking-on"),
    pytest.param("ollama", "some-local-model", False, id="ollama-thinking-off"),
]


@pytest.mark.parametrize("provider,model,thinking", SCENARIOS)
def test_complete_and_stream_send_identical_provider_payloads(
    network: RecordingNetwork, provider: str, model: str, thinking: bool
) -> None:
    standard = make_ctx(provider, model, thinking=thinking)
    streamed = make_ctx(provider, model, thinking=thinking)

    asyncio.run(send_complete(standard))
    asyncio.run(send_stream(streamed))

    assert len(network.bodies) == 2
    complete_body, stream_body = network.bodies
    assert stream_body.pop("stream") is True
    stream_body.pop("stream_options", None)
    complete_body.pop("stream", None)
    assert stream_body == complete_body

    messages = complete_body["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[0]["content"] == COMPILED
    assert messages[-1] == {"role": "user", "content": "next"}  # stripped once, in both modes
    assert complete_body["model"] == model
    if provider == "ollama":
        assert complete_body["reasoning_effort"] == ("high" if thinking else "none")
    else:
        assert "reasoning_effort" not in complete_body


@pytest.mark.parametrize("provider,model,thinking", SCENARIOS)
def test_prompt_inspection_is_the_prompt_that_was_sent(
    network: RecordingNetwork, provider: str, model: str, thinking: bool
) -> None:
    ctx = make_ctx(provider, model, thinking=thinking)
    asyncio.run(send_stream(ctx))

    sent = network.bodies[0]["messages"]
    rendered = "\n\n".join(f"{m['role']}: {m['content']}" for m in sent)
    assert ctx.metadata["llm_prompt"] == rendered
    assert ctx.metadata["llm_history"] == sent[:-1]


def test_streaming_applies_the_model_specific_formatter(network: RecordingNetwork) -> None:
    complete_ctx = make_ctx("openrouter", "anthropic/claude-3.5-sonnet")
    stream_ctx = make_ctx("openrouter", "anthropic/claude-3.5-sonnet")
    asyncio.run(send_complete(complete_ctx))
    asyncio.run(send_stream(stream_ctx))

    for key in ("llm_prompt_family", "llm_prompt_formatter"):
        assert complete_ctx.metadata[key] == "anthropic"
        assert stream_ctx.metadata[key] == "anthropic"
    assert stream_ctx.metadata["llm_prompt_payload"] == complete_ctx.metadata["llm_prompt_payload"]


def test_preparing_provider_inputs_makes_no_provider_or_network_call(network: RecordingNetwork) -> None:
    ctx = make_ctx("openrouter", "meta-llama/llama-3.3-70b-instruct")
    inputs = prepare_provider_call(ctx)

    assert network.requests == []
    assert inputs.error is None and inputs.provider is not None
    assert inputs.prompt == "next"
    assert inputs.history[0] == {"role": "system", "content": COMPILED}


def test_explicit_provider_that_cannot_be_used_is_reported_never_substituted() -> None:
    ctx = make_ctx("no-such-provider", "m")
    inputs = prepare_provider_call(ctx)
    assert inputs.provider is None and "no-such-provider" in (inputs.error or "")

    asyncio.run(CallProviderStep().run(ctx))
    assert ctx.provider_response == f"Provider error: {inputs.error}"

    streamed = make_ctx("no-such-provider", "m")
    tokens = asyncio.run(_collect(streaming._stream_provider_tokens(streamed)))
    assert tokens == [f"Provider stream error: {inputs.error}"]
    assert streamed.provider_response == f"Provider error: {inputs.error}"


async def _collect(stream: Any) -> list[str]:
    return [item async for item in stream]


def test_streaming_text_is_incremental_and_not_buffered(monkeypatch: pytest.MonkeyPatch) -> None:
    release = asyncio.Event()

    class Provider:
        async def stream(self, **kwargs: Any):
            yield "first "
            await release.wait()
            yield "second"

    monkeypatch.setattr(turn_module, "_select_provider", lambda ctx: Provider())

    async def scenario() -> None:
        ctx = make_ctx("openrouter", "m")
        stream = streaming._stream_provider_tokens(ctx)
        assert await asyncio.wait_for(stream.__anext__(), 1) == "first "
        assert not release.is_set()  # delivered while generation is still pending
        release.set()
        assert await stream.__anext__() == "second"

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# Canonical personality
# --------------------------------------------------------------------------


def companion(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = dict(
        name="Mercury",
        persona="Dry and observant.",
        description="A night-shift archivist.",
        backstory="Grew up above a bookshop.",
        personality_sliders={"warmth": 0.7},
        humor_style="witty",
        communication_style="concise",
        favorite_topics=["maps"],
        punctuation_habits={"ellipsis": "rare"},
        sentence_length_profile=None,
        emoji_style={},
        vocabulary_density=0.0,
        slang_profile=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_persona_comes_from_the_companions_own_configuration() -> None:
    persona = conversation.companion_to_persona(companion())

    assert persona == {
        "name": "Mercury",
        "custom_instructions": "Dry and observant.",
        "description": "A night-shift archivist.",
        "punctuation_habits": {"ellipsis": "rare"},
        "vocabulary_density": 0.0,  # a real configured value, not "empty"
    }
    # Attributes the character profile already carries are not rendered twice.
    assert {"backstory", "personality_sliders", "humor_style"}.isdisjoint(persona)


def test_the_companion_persona_reaches_the_prompt_identically_in_every_mode() -> None:
    from chatbot.soul.persona_prompt import PersonaPromptAssembler

    persona = conversation.companion_to_persona(companion())
    profile = conversation.companion_to_profile_dict(companion())
    in_standard = conversation.resolve_persona(
        persona, conversation.standard_persona_fallback(None, SID)
    )
    in_streaming = conversation.resolve_persona(persona, {})
    assert in_standard == in_streaming == persona

    prompt = turn_module._assemble_persona_prompt(
        PersonaPromptAssembler(),
        in_standard,
        character_profile=turn_module._coerce_character_profile(profile),
    )
    assert "Identity: Mercury" in prompt
    assert "Dry and observant." in prompt
    assert "A night-shift archivist." in prompt
    assert "Grew up above a bookshop" in prompt  # backstory still arrives via the profile
    assert "Companion" not in prompt.split("IDENTITY", 1)[0]  # no generic default persona


def test_each_mode_keeps_its_established_fallback_when_no_companion_is_configured() -> None:
    standard = conversation.resolve_persona(None, conversation.standard_persona_fallback(None, SID))
    streaming_persona = conversation.resolve_persona(None, {})

    assert standard["name"] == "Companion" and standard["core_traits"]  # DEFAULT_PERSONALITY
    assert streaming_persona == {}


async def _companion_inputs(db_session: Any, requested: str | None, found: Any) -> Any:
    db = AsyncMock()
    manager = Mock()
    manager.get = AsyncMock(return_value=found)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(conversation, "CompanionManager", lambda _db: manager)
        return await conversation.load_companion_inputs(db, db_session, UID, requested)


def test_profile_and_persona_come_from_one_companion_lookup() -> None:
    session = SimpleNamespace(companion_id="c1")
    profile, persona = asyncio.run(_companion_inputs(session, None, companion()))

    assert profile["name"] == "Mercury" and profile["backstory"] == "Grew up above a bookshop."
    assert persona["custom_instructions"] == "Dry and observant."
    assert asyncio.run(_companion_inputs(SimpleNamespace(companion_id=None), None, companion())) == (None, None)
    assert asyncio.run(_companion_inputs(session, None, None)) == (None, None)


def test_requested_companion_validation_is_preserved() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as missing:
        asyncio.run(_companion_inputs(SimpleNamespace(companion_id=None), "c9", None))
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as mismatch:
        asyncio.run(_companion_inputs(SimpleNamespace(companion_id="c1"), "c2", companion()))
    assert mismatch.value.status_code == 400


# --------------------------------------------------------------------------
# Preserved differences
# --------------------------------------------------------------------------


def _build(**kwargs: Any) -> TurnContext:
    return conversation.build_turn_context(
        None,
        session_id=SID,
        user_message="hello",
        history=[],
        persona={},
        db=MagicMock(),
        user_id=UID,
        companion_id=None,
        **kwargs,
    )


def test_standard_chat_does_not_gain_saved_session_state() -> None:
    """Standard chat has never read persisted mood/user/mode; streaming has. Both preserved."""
    assert "session_record" not in _build().metadata
    saved = {"mood_state": {"mood": "warm"}}
    assert _build(session_record=saved).metadata["session_record"] is saved


def test_room_prompt_stays_a_history_message_for_standard_chat_only() -> None:
    history = chat_api._inject_room_system_prompt([], "Room rules", "r1")
    assert history[0]["role"] == "system" and history[0]["metadata"]["room_type_prompt"] is True
    assert not hasattr(streaming, "_inject_room_system_prompt")
    assert "room_system_prompt" not in inspect.getsource(streaming)


def test_fast_path_is_intact() -> None:
    assert FAST_PATH_STEPS == ["receive", "retrieve", "compile_context", "call_provider", "respond", "update_identity"]
    assert "room_context" in ROOM_FAST_PATH_STEPS
    assert turn_module.DEFERRED_STEPS == ["extract_memory", "consolidate"]
    for name in ("_route_by_intent", "_run_fast_path", "run_fast", "enqueue_deferred"):
        assert callable(getattr(TurnPipeline, name))


def test_standard_fast_path_still_skips_full_steps_and_defers_extraction() -> None:
    ran: list[str] = []

    def step(step_name: str) -> TurnStep:
        class Recorded(TurnStep):
            name = step_name

            async def run(self, ctx: TurnContext) -> TurnContext:
                ran.append(step_name)
                return ctx

        return Recorded()

    names = [
        "receive", "room_context", "retrieve", "infer_user_state", "select_conversation_mode",
        "compile_context", "call_provider", "update_mood", "persist_emotional_state",
        "extract_memory", "scene_memory_gate", "timeline_update", "respond", "update_identity",
    ]
    queue = BackgroundQueue()
    pipeline = TurnPipeline([step(n) for n in names], background_queue=queue)
    ctx = TurnContext(
        session_id=SID, user_message="hey", history=[], persona={}, retrieved_memories=[],
        compiled_context=None, provider_response="x", extracted_memories=[], final_response=None,
        metadata={"intent": "greeting"},
    )

    asyncio.run(pipeline.execute_turn(ctx))

    assert ran == FAST_PATH_STEPS
    assert queue.pending_count == 1  # deferred memory extraction


def test_streaming_still_runs_its_own_full_step_loop() -> None:
    source = inspect.getsource(streaming._stream_turn)
    assert 'getattr(pipeline, "_steps", [])' in source
    for fast_path_name in ("execute_turn", "enqueue_deferred", "_route_by_intent", "FAST_PATH_STEPS"):
        assert fast_path_name not in source


# --------------------------------------------------------------------------
# Streaming endpoint preparation
# --------------------------------------------------------------------------


def _endpoint_request(app: FastAPI) -> Request:
    return Request({"type": "http", "app": app, "state": {}})


def _endpoint_db() -> AsyncMock:
    db = AsyncMock()
    session = SimpleNamespace(
        id=uuid.UUID(SID), user_id=uuid.UUID(UID), companion_id=None, title="Existing", room_id=None
    )
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: session)
    return db


def _patch_loaders(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    loaders = SimpleNamespace(
        history=AsyncMock(return_value=[{"role": "user", "content": "earlier"}]),
        identity=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(conversation, "load_companion_inputs", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(conversation, "load_db_session_history", loaders.history)
    monkeypatch.setattr(conversation, "load_session_identity", loaders.identity)
    monkeypatch.setattr(streaming, "_session_snapshot", lambda _: {})
    return loaders


def test_endpoint_skips_loads_an_avatar_bound_turn_would_discard(monkeypatch: pytest.MonkeyPatch) -> None:
    loaders = _patch_loaders(monkeypatch)

    async def scenario() -> None:
        app = FastAPI()
        hub, binding = await bound()
        app.state.avatar_coordinator = hub
        response = await streaming.stream_chat(
            _endpoint_request(app), streaming.StreamRequest(session_id=SID, message="hi"), USER, _endpoint_db()
        )
        await response.body_iterator.aclose()
        await hub.detach(binding)

    asyncio.run(scenario())
    loaders.history.assert_not_awaited()
    loaders.identity.assert_not_awaited()


def test_endpoint_loads_state_once_when_no_avatar_is_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    loaders = _patch_loaders(monkeypatch)

    async def scenario() -> None:
        response = await streaming.stream_chat(
            _endpoint_request(FastAPI()),
            streaming.StreamRequest(session_id=SID, message="hi"),
            USER,
            _endpoint_db(),
        )
        await response.body_iterator.aclose()

    asyncio.run(scenario())
    loaders.history.assert_awaited_once()
    loaders.identity.assert_awaited_once()


def test_turn_loads_state_itself_when_the_endpoint_expected_an_avatar(monkeypatch: pytest.MonkeyPatch) -> None:
    loaders = _patch_loaders(monkeypatch)
    captured: list[TurnContext] = []

    class Capture(TurnStep):
        name = "capture"

        async def run(self, ctx: TurnContext) -> TurnContext:
            captured.append(ctx)
            return ctx

    db = AsyncMock()
    monkeypatch.setattr(streaming, "build_default_pipeline", lambda: TurnPipeline([Capture()]))
    monkeypatch.setattr(streaming, "streaming_session_factory", lambda: db)
    monkeypatch.setattr(streaming, "_persist_messages", AsyncMock())
    monkeypatch.setattr(streaming, "_run_post_stream_db_write", AsyncMock())
    monkeypatch.setattr(conversation, "schedule_title_generation", Mock())
    monkeypatch.setattr(conversation, "prepare_memory_workflow", AsyncMock())

    async def scenario() -> None:
        async for _ in streaming._stream_turn(
            streaming.StreamRequest(session_id=SID, message="hi"),
            http_request=_endpoint_request(FastAPI()),
            current_user=USER,
            session_id=uuid.UUID(SID),
            session_title="Existing",
            session_companion_id=None,
            session_room_id=None,
            session_snapshot={},
            character_profile=None,
            identity_state=None,
            history=None,
        ):
            pass

    asyncio.run(scenario())
    loaders.history.assert_awaited_once()
    loaders.identity.assert_awaited_once()
    assert captured[0].history == [{"role": "user", "content": "earlier"}]
    assert captured[0].persona == {}  # streaming's established fallback
    assert captured[0].metadata["session_record"] == {}
