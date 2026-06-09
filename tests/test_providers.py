from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.providers.base import (
    Provider,
    ProviderAuthError,
    ProviderConfig,
    ProviderError,
    ProviderRateLimitError,
    ProviderRegistry,
    ProviderResponse,
    ProviderTimeoutError,
)
from chatbot.providers.modal_ephemeral import ModalEphemeralProvider
from chatbot.providers.openai_compat import OpenAICompatProvider
from chatbot.providers.router import ProviderRouter


class FakeProvider(Provider):
    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    async def complete(
        self,
        prompt: str,
        history: Sequence[Mapping[str, Any]],
        config: ProviderConfig,
    ) -> ProviderResponse:
        return ProviderResponse(text=prompt, finish_reason="stop", model=config.model)


class FakeHTTPClient:
    response: httpx.Response | None = None
    requests: list[dict[str, Any]] = []

    async def __aenter__(self) -> FakeHTTPClient:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append({"url": url, **kwargs})
        if self.response is None:
            raise AssertionError("FakeHTTPClient.response must be set")
        return self.response


class FakeStreamResponse:
    def __init__(self, lines: Sequence[str], status_code: int = 200) -> None:
        self._lines = list(lines)
        self.status_code = status_code
        self.read_called = False
        self.closed = False
        self.text = ""

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self) -> bytes:
        self.read_called = True
        return self.text.encode()


class FakeStreamContext:
    def __init__(self, response: FakeStreamResponse) -> None:
        self.response = response

    async def __aenter__(self) -> FakeStreamResponse:
        return self.response

    async def __aexit__(self, *_exc_info: object) -> None:
        self.response.closed = True


class FakeStreamingHTTPClient:
    response: FakeStreamResponse | None = None
    requests: list[dict[str, Any]] = []

    async def __aenter__(self) -> FakeStreamingHTTPClient:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        return None

    def stream(self, method: str, url: str, **kwargs: Any) -> FakeStreamContext:
        self.requests.append({"method": method, "url": url, **kwargs})
        if self.response is None:
            raise AssertionError("FakeStreamingHTTPClient.response must be set")
        return FakeStreamContext(self.response)


class FakeCall:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.get_timeouts: list[float] = []
        self.cancel_calls: list[dict[str, Any]] = []

    def get(self, *, timeout: float) -> Any:
        self.get_timeouts.append(timeout)
        return self.response

    def cancel(self, **kwargs: Any) -> None:
        self.cancel_calls.append(kwargs)


class FakeModalFunction:
    def __init__(self, responses: Sequence[Any]) -> None:
        self._responses = list(responses)
        self.spawn_payloads: list[dict[str, Any]] = []
        self.calls: list[FakeCall] = []
        self.hydrated = False
        self.stop_calls = 0

    def hydrate(self) -> FakeModalFunction:
        self.hydrated = True
        return self

    def spawn(self, payload: Mapping[str, Any]) -> FakeCall:
        self.spawn_payloads.append(dict(payload))
        if not self._responses:
            raise AssertionError("unexpected Modal spawn")
        call = FakeCall(self._responses.pop(0))
        self.calls.append(call)
        return call

    def stop(self) -> None:
        self.stop_calls += 1


async def run_immediately(function: Any, /, *args: Any, **kwargs: Any) -> Any:
    return function(*args, **kwargs)


def test_provider_abc_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        Provider()


def test_provider_response_fields() -> None:
    raw = {"choices": []}
    response = ProviderResponse(
        text="hello",
        finish_reason="stop",
        model="model-a",
        raw=raw,
    )

    assert response.text == "hello"
    assert response.finish_reason == "stop"
    assert response.model == "model-a"
    assert response.raw is raw


def test_provider_error_hierarchy() -> None:
    for error_type in (ProviderTimeoutError, ProviderAuthError, ProviderRateLimitError):
        assert isinstance(error_type("boom"), ProviderError)


def test_provider_registry_register_and_lookup_by_name() -> None:
    provider = FakeProvider()
    registry = ProviderRegistry()

    registry.register("fake", provider)

    assert registry.get("fake") is provider
    assert registry.names() == ("fake",)


def test_openai_compat_complete_maps_success_response(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeHTTPClient.response = httpx.Response(
        200,
        json={
            "model": "served-model",
            "choices": [
                {
                    "message": {"content": "completed text"},
                    "finish_reason": "stop",
                }
            ],
        },
    )
    FakeHTTPClient.requests = []
    monkeypatch.setattr("chatbot.providers.openai_compat.httpx.AsyncClient", FakeHTTPClient)

    provider = OpenAICompatProvider(
        "https://provider.example/v1/",
        api_key="token",
        default_model="default-model",
    )
    response = asyncio.run(
        provider.complete(
            "Tell me",
            [{"role": "system", "content": "Be direct"}],
            ProviderConfig(model="", temperature=0.2, max_tokens=12, stop=("END",)),
        )
    )

    assert response == ProviderResponse(
        text="completed text",
        finish_reason="stop",
        model="served-model",
        raw={
            "model": "served-model",
            "choices": [
                {
                    "message": {"content": "completed text"},
                    "finish_reason": "stop",
                }
            ],
        },
    )
    assert FakeHTTPClient.requests == [
        {
            "url": "https://provider.example/v1/chat/completions",
            "headers": {
                "Content-Type": "application/json",
                "Authorization": "Bearer token",
            },
            "json": {
                "model": "default-model",
                "messages": [
                    {"role": "system", "content": "Be direct"},
                    {"role": "user", "content": "Tell me"},
                ],
                "temperature": 0.2,
                "max_tokens": 12,
                "stop": ["END"],
            },
            "timeout": 60.0,
        }
    ]


def test_openai_compat_stream_yields_sse_delta_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    response = FakeStreamResponse(
        [
            "",
            'data: {"choices":[{"delta":{"role":"assistant"}}]}',
            'data: {"choices":[{"delta":{"content":"hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            "data: [DONE]",
            'data: {"choices":[{"delta":{"content":"ignored"}}]}',
        ]
    )
    FakeStreamingHTTPClient.response = response
    FakeStreamingHTTPClient.requests = []
    monkeypatch.setattr(
        "chatbot.providers.openai_compat.httpx.AsyncClient",
        FakeStreamingHTTPClient,
    )

    provider = OpenAICompatProvider(
        "https://provider.example/v1/",
        api_key="token",
        default_model="default-model",
    )

    async def collect_tokens() -> list[str]:
        return [
            token
            async for token in provider.stream(
                "Tell me",
                [{"role": "system", "content": "Be direct"}],
                ProviderConfig(model="", temperature=0.2, max_tokens=12),
            )
        ]

    assert asyncio.run(collect_tokens()) == ["hel", "lo"]
    assert response.closed is True
    assert FakeStreamingHTTPClient.requests == [
        {
            "method": "POST",
            "url": "https://provider.example/v1/chat/completions",
            "headers": {
                "Content-Type": "application/json",
                "Authorization": "Bearer token",
            },
            "json": {
                "model": "default-model",
                "messages": [
                    {"role": "system", "content": "Be direct"},
                    {"role": "user", "content": "Tell me"},
                ],
                "temperature": 0.2,
                "max_tokens": 12,
                "stream": True,
            },
            "timeout": 60.0,
        }
    ]


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (401, ProviderAuthError),
        (429, ProviderRateLimitError),
    ],
)
def test_openai_compat_translates_http_errors(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    expected_error: type[ProviderError],
) -> None:
    FakeHTTPClient.response = httpx.Response(
        status_code,
        json={"error": {"message": "provider rejected request"}},
    )
    FakeHTTPClient.requests = []
    monkeypatch.setattr("chatbot.providers.openai_compat.httpx.AsyncClient", FakeHTTPClient)

    provider = OpenAICompatProvider("https://provider.example", default_model="model-a")

    with pytest.raises(expected_error) as exc_info:
        asyncio.run(provider.complete("prompt", [], ProviderConfig(model="")))

    assert f"HTTP {status_code}" in str(exc_info.value)
    assert "provider rejected request" in str(exc_info.value)


def test_modal_ephemeral_cold_start_readiness_loop_and_response_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("chatbot.providers.modal_ephemeral.asyncio.to_thread", run_immediately)
    function = FakeModalFunction(
        responses=[
            {"text": "ready", "finish_reason": "warmup"},
            {
                "model": "modal-model",
                "choices": [
                    {
                        "message": {"content": "modal text"},
                        "finish_reason": "stop",
                    }
                ],
            },
            {"text": "second modal text", "finish_reason": "stop", "model": "modal-model"},
        ]
    )
    provider = ModalEphemeralProvider(
        function_ref=function,
        default_model="default-modal-model",
        readiness_payload={"kind": "readiness"},
    )

    async def exercise_provider() -> tuple[ProviderResponse, ProviderResponse]:
        first_response = await provider.complete(
            "Hello",
            [{"role": "assistant", "content": "Previous"}],
            ProviderConfig(model="modal-model", top_p=0.9),
        )
        second_provider_response = await provider.complete(
            "Again",
            [],
            ProviderConfig(model="modal-model", extra={"seed": 1}),
        )
        return first_response, second_provider_response

    response, second_response = asyncio.run(exercise_provider())

    assert response.text == "modal text"
    assert response.finish_reason == "stop"
    assert response.model == "modal-model"
    assert second_response.text == "second modal text"
    assert function.hydrated is True
    assert function.spawn_payloads == [
        {"kind": "readiness"},
        {
            "model": "modal-model",
            "messages": [
                {"role": "assistant", "content": "Previous"},
                {"role": "user", "content": "Hello"},
            ],
            "top_p": 0.9,
        },
        {
            "model": "modal-model",
            "messages": [{"role": "user", "content": "Again"}],
            "seed": 1,
        },
    ]


def test_modal_ephemeral_teardown_stops_resolved_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("chatbot.providers.modal_ephemeral.asyncio.to_thread", run_immediately)
    function = FakeModalFunction(responses=["modal text"])
    provider = ModalEphemeralProvider(function_ref=function, default_model="model-a")

    async def exercise_provider() -> None:
        await provider.complete("Hello", [], ProviderConfig(model=""))
        await provider.close()

    asyncio.run(exercise_provider())

    assert function.stop_calls == 1
    assert provider._function is None
    assert provider._ready is False


def test_provider_router_selects_default_provider() -> None:
    default = FakeProvider()
    fallback = FakeProvider()
    router = ProviderRouter(
        {"default": default, "fallback": fallback},
        {"default": "default", "fallback": "fallback"},
    )

    assert router.select("prompt", ProviderConfig(model="anything")) is default


def test_provider_router_matches_model_prefix_rule() -> None:
    default = FakeProvider()
    modal = FakeProvider()
    router = ProviderRouter(
        {"default": default, "modal": modal},
        {
            "model_prefix": {"modal/": "modal"},
            "default": "default",
        },
    )

    assert router.select("prompt", ProviderConfig(model="modal/model-a")) is modal


def test_provider_router_falls_back_when_primary_is_unavailable() -> None:
    primary = FakeProvider(available=False)
    fallback = FakeProvider()
    router = ProviderRouter(
        {"primary": primary, "fallback": fallback},
        {"default": "primary", "fallback": "fallback"},
    )

    assert router.select("prompt", ProviderConfig(model="model-a")) is fallback


def test_get_llm_config_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    from chatbot.config import get_settings, get_llm_config

    settings = get_settings()

    # Test openrouter config
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr(settings, "openrouter_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key")
    monkeypatch.setattr(settings, "default_model", "or-model")
    config = get_llm_config()
    assert config["base_url"] == "https://openrouter.ai/api/v1"
    assert config["api_key"] == "or-key"
    assert config["model"] == "or-model"

    # Test sglang config
    monkeypatch.setattr(settings, "llm_provider", "sglang")
    monkeypatch.setattr(settings, "sglang_base_url", "http://localhost:30000/v1")
    monkeypatch.setattr(settings, "sglang_api_key", "sg-key")
    monkeypatch.setattr(settings, "sglang_model", "sg-model")
    config = get_llm_config()
    assert config["base_url"] == "http://localhost:30000/v1"
    assert config["api_key"] == "sg-key"
    assert config["model"] == "sg-model"

    # Test vllm config
    monkeypatch.setattr(settings, "llm_provider", "vllm")
    monkeypatch.setattr(settings, "vllm_base_url", "http://localhost:8000/v1")
    monkeypatch.setattr(settings, "vllm_api_key", "vl-key")
    monkeypatch.setattr(settings, "vllm_model", "vl-model")
    config = get_llm_config()
    assert config["base_url"] == "http://localhost:8000/v1"
    assert config["api_key"] == "vl-key"
    assert config["model"] == "vl-model"

    # Test ollama config
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_base_url", "http://localhost:11434/v1")
    monkeypatch.setattr(settings, "ollama_api_key", "ol-key")
    monkeypatch.setattr(settings, "ollama_model", "ol-model")
    config = get_llm_config()
    assert config["base_url"] == "http://localhost:11434/v1"
    assert config["api_key"] == "ol-key"
    assert config["model"] == "ol-model"


def test_settings_default_model_tracks_ollama_provider() -> None:
    from chatbot.config import Settings

    settings = Settings(
        llm_provider="ollama",
        ollama_model="llama3.2:latest",
    )

    assert settings.default_provider == "ollama"
    assert settings.default_model == "llama3.2:latest"


def test_build_default_registry_with_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    from chatbot.config import get_settings
    from chatbot.providers.bootstrap import build_default_registry

    settings = get_settings()
    monkeypatch.setattr(settings, "llm_provider", "sglang")
    monkeypatch.setattr(settings, "sglang_base_url", "http://localhost:30000/v1")
    monkeypatch.setattr(settings, "sglang_api_key", "sg-key")
    monkeypatch.setattr(settings, "sglang_model", "sg-model")

    registry = build_default_registry(settings)
    assert "sglang" in registry.names()
    assert "openrouter" in registry.names()

    sglang_provider = registry.get("sglang")
    assert sglang_provider.base_url == "http://localhost:30000/v1"
    assert sglang_provider.api_key == "sg-key"
    assert sglang_provider.default_model == "sg-model"

    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_base_url", "http://localhost:11434/v1")
    monkeypatch.setattr(settings, "ollama_api_key", "ol-key")
    monkeypatch.setattr(settings, "ollama_model", "ol-model")

    registry = build_default_registry(settings)
    assert "ollama" in registry.names()
    assert "openrouter" in registry.names()

    ollama_provider = registry.get("ollama")
    assert ollama_provider.base_url == "http://localhost:11434/v1"
    assert ollama_provider.api_key == "ol-key"
    assert ollama_provider.default_model == "ol-model"


def test_validate_identifier_for_local_openai_compatible_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    from chatbot.providers.router import validate_identifier
    from chatbot.config import get_settings

    settings = get_settings()

    # Local OpenAI-compatible providers accept arbitrary served model names.
    monkeypatch.setattr(settings, "llm_provider", "sglang")
    assert validate_identifier("sglang", "any-random-model-string") is True
    assert validate_identifier("vllm", "some-other-model") is True
    assert validate_identifier("ollama", "llama3.2:latest") is True

    # When provider is openrouter, validation of unknown model should fail
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    assert validate_identifier("openrouter", "non-existent-model-name") is False
