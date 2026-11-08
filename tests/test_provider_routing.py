"""Regression tests for explicit provider/model routing.

Bug: the frontend sent ``provider_override="ollama"`` plus an Ollama model id,
but the backend only had the ``LLM_PROVIDER`` provider registered (plus a
misleading "openrouter" alias pointing at the same endpoint) and selected the
first registered provider, so the Ollama model id was sent to OpenRouter.

These tests drive the real registry, router and OpenAI-compatible adapter
with only the HTTP transport faked, and record which endpoint each request
reaches and with which model id.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.api import chat as chat_api
from chatbot.api import conversation as conversation_api
from chatbot.api import streaming as streaming_api
from chatbot.api.streaming import _stream_provider_tokens
from chatbot.config import Settings
from chatbot.core.steps import TurnContext
from chatbot.core.turn import CallProviderStep
from chatbot.providers import openai_compat, router
from chatbot.providers.base import (
    Provider,
    ProviderConfig,
    ProviderRegistry,
    ProviderResponse,
    ProviderSelectionError,
)
from chatbot.providers.bootstrap import build_default_registry
from chatbot.providers.router import ProviderRouter, apply_routing_overrides

OPENROUTER_URL = "https://openrouter.test/api/v1"
OLLAMA_URL = "http://ollama.test:11434/v1"
VLLM_URL = "http://vllm.test:8001/v1"
SGLANG_URL = "http://sglang.test:30000/v1"

OLLAMA_MODEL = "hf.co/KevinJK51/Qwen3.6-12B-IQ-Ultra-Heretic-Uncensored-Thinking-V2-Hightop-GGUF:Q4_K_M"
OPENROUTER_MODEL = "deepseek/deepseek-v4-pro"


def make_settings(llm_provider: str = "openrouter", **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "llm_provider": llm_provider,
        "openrouter_api_key": "or-key",
        "openrouter_base_url": OPENROUTER_URL,
        "default_model": OPENROUTER_MODEL,
        "ollama_base_url": OLLAMA_URL,
        "ollama_api_key": "ol-key",
        "ollama_model": "configured-ollama-model",
        "vllm_base_url": VLLM_URL,
        "vllm_model": "configured-vllm-model",
        "sglang_base_url": SGLANG_URL,
        "sglang_model": "configured-sglang-model",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeNetwork:
    """Records every HTTP request and answers per-host."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        # host -> (status_code, error message) for hosts that should fail.
        self.failures: dict[str, tuple[int, str]] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(
            {
                "host": request.url.host,
                "url": str(request.url),
                "model": body.get("model"),
                "stream": bool(body.get("stream")),
                "reasoning_effort": body.get("reasoning_effort"),
                "auth": request.headers.get("authorization"),
            }
        )
        failure = self.failures.get(request.url.host)
        if failure is not None:
            status, message = failure
            return httpx.Response(status, json={"error": {"message": message}})

        text = f"hello from {request.url.host}"
        if body.get("stream"):
            sse = (
                f'data: {json.dumps({"choices": [{"delta": {"content": text}}]})}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(
                200,
                content=sse.encode(),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            json={
                "model": body.get("model"),
                "choices": [
                    {"message": {"content": text}, "finish_reason": "stop"}
                ],
            },
        )

    @property
    def hosts(self) -> list[str]:
        return [request["host"] for request in self.requests]


@pytest.fixture
def network(monkeypatch: pytest.MonkeyPatch) -> FakeNetwork:
    fake = FakeNetwork()
    real_client = httpx.AsyncClient

    def client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(fake.handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(openai_compat.httpx, "AsyncClient", client_factory)
    return fake


def make_ctx(
    registry: ProviderRegistry,
    *,
    provider_override: str | None = None,
    model_override: str | None = None,
    default_provider: str = "openrouter",
    default_model: str = OPENROUTER_MODEL,
) -> TurnContext:
    """Build a context the way the chat and streaming endpoints do."""

    metadata: dict[str, Any] = {
        "provider_registry": registry,
        "provider_name": default_provider,
        "default_provider": default_provider,
        "model": default_model,
        "default_model": default_model,
    }
    apply_routing_overrides(metadata, provider_override, model_override)
    return TurnContext(
        session_id="s1",
        user_message="hi there",
        history=[],
        persona={},
        retrieved_memories=[],
        compiled_context=None,
        provider_response=None,
        extracted_memories=[],
        final_response=None,
        metadata=metadata,
    )


async def run_non_streaming(ctx: TurnContext) -> TurnContext:
    return await CallProviderStep().run(ctx)


async def run_streaming(ctx: TurnContext) -> TurnContext:
    tokens = [token async for token in _stream_provider_tokens(ctx)]
    ctx.metadata["_streamed_tokens"] = tokens
    return ctx


MODES = pytest.mark.parametrize(
    "run",
    [run_non_streaming, run_streaming],
    ids=["non-streaming", "streaming"],
)


# --------------------------------------------------------------------------
# Registry bootstrap
# --------------------------------------------------------------------------


@pytest.mark.parametrize("active", ["openrouter", "ollama", "vllm", "sglang"])
def test_registry_registers_every_provider_with_its_own_endpoint(active: str) -> None:
    registry = build_default_registry(make_settings(active))

    assert set(registry.names()) == {"openrouter", "ollama", "vllm", "sglang"}
    assert registry.get("openrouter").base_url == OPENROUTER_URL
    assert registry.get("openrouter").api_key == "or-key"
    assert registry.get("ollama").base_url == OLLAMA_URL
    assert registry.get("ollama").api_key == "ol-key"
    assert registry.get("vllm").base_url == VLLM_URL
    assert registry.get("sglang").base_url == SGLANG_URL
    # The active provider is registered first so unspecified requests keep using it.
    assert registry.names()[0] == active


def test_no_openrouter_alias_pointing_at_local_endpoint() -> None:
    """The old bootstrap registered "openrouter" -> the active (Ollama) endpoint."""

    registry = build_default_registry(make_settings("ollama"))

    assert registry.get("openrouter").base_url == OPENROUTER_URL
    assert registry.get("openrouter").base_url != registry.get("ollama").base_url
    # The default model of the active local provider is never given to OpenRouter.
    assert registry.get("openrouter").default_model == ""
    assert registry.get("ollama").default_model == "configured-ollama-model"


def test_provider_without_endpoint_is_not_registered() -> None:
    registry = build_default_registry(make_settings("openrouter", vllm_base_url=""))

    assert "vllm" not in registry.names()


# --------------------------------------------------------------------------
# Override metadata
# --------------------------------------------------------------------------


def test_apply_routing_overrides_records_exact_values() -> None:
    metadata: dict[str, Any] = {"default_provider": "openrouter", "default_model": "x"}

    apply_routing_overrides(metadata, "ollama", OLLAMA_MODEL)

    assert metadata["explicit_provider"] == "ollama"
    assert metadata["explicit_model"] == OLLAMA_MODEL
    assert metadata["provider_name"] == metadata["default_provider"] == "ollama"
    assert metadata["model"] == metadata["default_model"] == OLLAMA_MODEL


def test_apply_routing_overrides_without_overrides_changes_nothing() -> None:
    metadata: dict[str, Any] = {"default_provider": "openrouter", "default_model": "x"}

    apply_routing_overrides(metadata, None, None)

    assert metadata == {"default_provider": "openrouter", "default_model": "x"}


def test_provider_without_model_does_not_inherit_default_model() -> None:
    metadata: dict[str, Any] = {"default_provider": "openrouter", "default_model": OPENROUTER_MODEL}

    apply_routing_overrides(metadata, "ollama", None)

    assert metadata["model"] == ""
    assert metadata["default_model"] == ""


def test_chat_and_streaming_share_one_override_implementation() -> None:
    # Both adapters build their TurnContext through the shared preparation module.
    assert conversation_api.apply_routing_overrides is router.apply_routing_overrides
    assert not hasattr(chat_api, "apply_routing_overrides")
    assert not hasattr(streaming_api, "apply_routing_overrides")


# --------------------------------------------------------------------------
# Routing: the right provider is used (streaming and non-streaming)
# --------------------------------------------------------------------------


@MODES
async def test_ollama_selection_routes_to_ollama(network: FakeNetwork, run) -> None:
    # OpenRouter is the active/default provider: the bug scenario.
    registry = build_default_registry(make_settings("openrouter"))
    ctx = make_ctx(registry, provider_override="ollama", model_override=OLLAMA_MODEL)

    ctx = await run(ctx)

    assert network.hosts == ["ollama.test"]
    assert "openrouter.test" not in network.hosts
    assert network.requests[0]["url"] == f"{OLLAMA_URL}/chat/completions"
    assert network.requests[0]["auth"] == "Bearer ol-key"
    assert ctx.provider_response == "hello from ollama.test"


@MODES
async def test_openrouter_selection_routes_to_openrouter(network: FakeNetwork, run) -> None:
    # Ollama is the active/default provider; explicitly choosing OpenRouter must win.
    registry = build_default_registry(make_settings("ollama"))
    ctx = make_ctx(
        registry,
        provider_override="openrouter",
        model_override=OPENROUTER_MODEL,
        default_provider="ollama",
        default_model="configured-ollama-model",
    )

    ctx = await run(ctx)

    assert network.hosts == ["openrouter.test"]
    assert network.requests[0]["url"] == f"{OPENROUTER_URL}/chat/completions"
    assert network.requests[0]["auth"] == "Bearer or-key"
    assert ctx.provider_response == "hello from openrouter.test"


@MODES
@pytest.mark.parametrize(
    ("provider", "host"),
    [("vllm", "vllm.test"), ("sglang", "sglang.test")],
)
async def test_other_local_providers_route_to_their_own_endpoint(
    network: FakeNetwork, run, provider: str, host: str
) -> None:
    registry = build_default_registry(make_settings("openrouter"))
    ctx = make_ctx(registry, provider_override=provider, model_override="local/model:tag")

    await run(ctx)

    assert network.hosts == [host]


@MODES
@pytest.mark.parametrize(
    ("active", "host"),
    [("openrouter", "openrouter.test"), ("ollama", "ollama.test")],
)
async def test_no_override_uses_active_provider(
    network: FakeNetwork, run, active: str, host: str
) -> None:
    registry = build_default_registry(make_settings(active))
    ctx = make_ctx(registry, default_provider=active, default_model="default-model")

    await run(ctx)

    assert network.hosts == [host]
    assert network.requests[0]["model"] == "default-model"


# --------------------------------------------------------------------------
# Exact model IDs
# --------------------------------------------------------------------------


@MODES
@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("ollama", OLLAMA_MODEL),
        ("ollama", "llama3.2:latest"),
        ("ollama", "hf.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive"),
        ("openrouter", "openrouter/deepseek/deepseek-v4-pro"),
        ("openrouter", "google/gemma-4-31b-it"),
        ("openrouter", "some-vendor/not-in-any-known-list:free"),
    ],
)
async def test_exact_model_id_survives_routing_unchanged(
    network: FakeNetwork, run, provider: str, model: str
) -> None:
    registry = build_default_registry(make_settings("openrouter"))
    ctx = make_ctx(registry, provider_override=provider, model_override=model)

    await run(ctx)

    assert len(network.requests) == 1
    assert network.requests[0]["model"] == model


@MODES
async def test_provider_without_model_uses_that_providers_own_model(
    network: FakeNetwork, run
) -> None:
    registry = build_default_registry(make_settings("openrouter"))
    ctx = make_ctx(registry, provider_override="ollama")

    await run(ctx)

    assert network.hosts == ["ollama.test"]
    # Never the OpenRouter default model.
    assert network.requests[0]["model"] == "configured-ollama-model"


# --------------------------------------------------------------------------
# Provider failure: no other provider is ever tried
# --------------------------------------------------------------------------


@MODES
async def test_provider_http_failure_never_triggers_another_provider(
    network: FakeNetwork, run
) -> None:
    network.failures["ollama.test"] = (503, "ollama is down")
    registry = build_default_registry(make_settings("openrouter"))
    ctx = make_ctx(registry, provider_override="ollama", model_override=OLLAMA_MODEL)

    ctx = await run(ctx)

    assert network.hosts == ["ollama.test"]
    assert "ollama is down" in ctx.provider_response
    assert ctx.provider_response.startswith("Provider error:")


@MODES
async def test_unregistered_provider_returns_error_without_any_request(
    network: FakeNetwork, run
) -> None:
    registry = build_default_registry(make_settings("openrouter", ollama_base_url=""))
    assert "ollama" not in registry.names()
    ctx = make_ctx(registry, provider_override="ollama", model_override=OLLAMA_MODEL)

    ctx = await run(ctx)

    assert network.requests == []
    assert ctx.provider_response.startswith("Provider error:")
    assert "ollama" in ctx.provider_response


@MODES
async def test_unknown_provider_name_returns_error_without_any_request(
    network: FakeNetwork, run
) -> None:
    registry = build_default_registry(make_settings("openrouter"))
    ctx = make_ctx(registry, provider_override="does-not-exist", model_override="m")

    ctx = await run(ctx)

    assert network.requests == []
    assert ctx.provider_response.startswith("Provider error:")
    assert "does-not-exist" in ctx.provider_response


@MODES
async def test_unavailable_provider_returns_error_without_any_request(
    network: FakeNetwork, run
) -> None:
    class DownProvider(Provider):
        available = False

        async def complete(self, prompt, history, config):  # pragma: no cover - never called
            raise AssertionError("an unavailable provider must not be called")

    registry = build_default_registry(make_settings("openrouter"))
    registry.unregister("ollama")
    registry.register("ollama", DownProvider())
    ctx = make_ctx(registry, provider_override="ollama", model_override=OLLAMA_MODEL)

    ctx = await run(ctx)

    assert network.requests == []
    assert ctx.provider_response.startswith("Provider error:")
    assert "unavailable" in ctx.provider_response


@MODES
async def test_missing_registry_with_explicit_provider_returns_error(
    network: FakeNetwork, run
) -> None:
    ctx = make_ctx(build_default_registry(make_settings("openrouter")), provider_override="ollama")
    del ctx.metadata["provider_registry"]

    ctx = await run(ctx)

    assert network.requests == []
    assert ctx.provider_response.startswith("Provider error:")


def test_select_exact_ignores_policy_default_and_fallback() -> None:
    registry = build_default_registry(make_settings("openrouter"))
    policy = {"default": "openrouter", "fallback": ["openrouter", "vllm"]}
    policy_router = ProviderRouter(registry, policy)

    assert policy_router.select_exact("ollama") is registry.get("ollama")
    with pytest.raises(ProviderSelectionError):
        policy_router.select_exact("nope")


def test_select_exact_is_not_excluded_into_a_different_provider() -> None:
    registry = ProviderRegistry()

    class Echo(Provider):
        async def complete(self, prompt, history, config):  # pragma: no cover
            return ProviderResponse(text="", finish_reason="stop")

    registry.register("openrouter", Echo())
    with pytest.raises(ProviderSelectionError):
        ProviderRouter(registry).select_exact("ollama", ProviderConfig(model="m"))


# --------------------------------------------------------------------------
# Model failure: no other model is ever tried
# --------------------------------------------------------------------------


@MODES
async def test_model_failure_never_triggers_another_model(network: FakeNetwork, run) -> None:
    network.failures["ollama.test"] = (404, f"model '{OLLAMA_MODEL}' not found")
    registry = build_default_registry(make_settings("openrouter"))
    ctx = make_ctx(registry, provider_override="ollama", model_override=OLLAMA_MODEL)

    ctx = await run(ctx)

    # Exactly one attempt: same provider, same model, no retry, no substitution.
    assert len(network.requests) == 1
    assert network.requests[0]["host"] == "ollama.test"
    assert network.requests[0]["model"] == OLLAMA_MODEL
    assert ctx.provider_response.startswith("Provider error:")
    assert "not found" in ctx.provider_response


@MODES
async def test_openrouter_invalid_model_never_retries_with_another_model(
    network: FakeNetwork, run
) -> None:
    network.failures["openrouter.test"] = (400, "bogus/model is not a valid model ID")
    registry = build_default_registry(make_settings("ollama"))
    ctx = make_ctx(
        registry,
        provider_override="openrouter",
        model_override="bogus/model",
        default_provider="ollama",
        default_model="configured-ollama-model",
    )

    ctx = await run(ctx)

    assert [(r["host"], r["model"]) for r in network.requests] == [
        ("openrouter.test", "bogus/model")
    ]
    assert "not a valid model ID" in ctx.provider_response


# --------------------------------------------------------------------------
# Streaming and non-streaming behave identically
# --------------------------------------------------------------------------


SCENARIOS = {
    "ollama-ok": dict(settings="openrouter", provider="ollama", model=OLLAMA_MODEL, failures={}),
    "openrouter-ok": dict(
        settings="ollama", provider="openrouter", model=OPENROUTER_MODEL, failures={}
    ),
    "ollama-503": dict(
        settings="openrouter",
        provider="ollama",
        model=OLLAMA_MODEL,
        failures={"ollama.test": (503, "down")},
    ),
    "ollama-model-404": dict(
        settings="openrouter",
        provider="ollama",
        model=OLLAMA_MODEL,
        failures={"ollama.test": (404, "model not found")},
    ),
    "unregistered": dict(
        settings="openrouter",
        provider="vllm",
        model="m",
        failures={},
        overrides={"vllm_base_url": ""},
    ),
    "no-override": dict(settings="ollama", provider=None, model=None, failures={}),
}


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
async def test_streaming_and_non_streaming_route_identically(
    monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    spec = SCENARIOS[scenario]
    observed = {}
    real_client = httpx.AsyncClient  # captured once, before any patching

    for name, run in (("non-streaming", run_non_streaming), ("streaming", run_streaming)):
        fake = FakeNetwork()
        fake.failures.update(spec["failures"])

        def client_factory(*args: Any, _fake: FakeNetwork = fake, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(_fake.handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(openai_compat.httpx, "AsyncClient", client_factory)

        registry = build_default_registry(
            make_settings(spec["settings"], **spec.get("overrides", {}))
        )
        active = spec["settings"]
        ctx = make_ctx(
            registry,
            provider_override=spec["provider"],
            model_override=spec["model"],
            default_provider=active,
            default_model="default-model",
        )
        ctx = await run(ctx)
        observed[name] = {
            # Same endpoint(s), same model id(s), same number of attempts.
            "requests": [(r["host"], r["url"], r["model"], r["auth"]) for r in fake.requests],
            # Same terminal response / error text.
            "response": ctx.provider_response,
        }

    assert observed["streaming"] == observed["non-streaming"]
