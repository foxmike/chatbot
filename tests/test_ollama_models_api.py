"""Tests for GET /models/ollama, the model-discovery endpoint behind the picker."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.api import models as models_api
from chatbot.api.models import models_router, ollama_tags_url
from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.config import get_settings

HF_MODEL = "hf.co/KevinJK51/Qwen3.6-12B-IQ-Ultra-Heretic-Uncensored-Thinking-V2-Hightop-GGUF:Q4_K_M"
USER = CurrentUser(user_id="u1", username="mike", email="mike@example.com")


class FakeOllama:
    """Stands in for the Ollama server behind httpx; records requests."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.handler: Callable[[httpx.Request], httpx.Response] = lambda _r: httpx.Response(
            200, json={"models": []}
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


@pytest.fixture
def ollama(monkeypatch: pytest.MonkeyPatch) -> FakeOllama:
    fake = FakeOllama()
    real_client = httpx.AsyncClient

    def client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(fake)
        return real_client(*args, **kwargs)

    settings = get_settings()
    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama.test:11434/v1")
    monkeypatch.setattr(settings, "ollama_api_key", "")
    monkeypatch.setattr(models_api.httpx, "AsyncClient", client_factory)
    return fake


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(models_router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


def tags(*names: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"models": [{"name": n, "model": n, "size": 1, "digest": "d"} for n in names]},
    )


def test_zero_models_returns_empty_list(ollama: FakeOllama, client: TestClient) -> None:
    ollama.handler = lambda _r: tags()

    response = client.get("/models/ollama")

    assert response.status_code == 200
    assert response.json() == {"provider": "ollama", "models": []}


def test_one_model(ollama: FakeOllama, client: TestClient) -> None:
    ollama.handler = lambda _r: tags(HF_MODEL)

    response = client.get("/models/ollama")

    assert response.status_code == 200
    assert response.json()["models"] == [HF_MODEL]


def test_multiple_models_keep_exact_ids_and_order(ollama: FakeOllama, client: TestClient) -> None:
    names = ["llama3.2:latest", HF_MODEL, "Qwen3:8b-Q4_K_M"]
    ollama.handler = lambda _r: tags(*names)

    response = client.get("/models/ollama")

    assert response.status_code == 200
    assert response.json()["models"] == names


def test_duplicate_and_malformed_entries_are_skipped(ollama: FakeOllama, client: TestClient) -> None:
    ollama.handler = lambda _r: httpx.Response(
        200,
        json={"models": [{"name": "a:1"}, {"name": "a:1"}, "junk", {}, {"model": "b:2"}, {"name": ""}]},
    )

    assert client.get("/models/ollama").json()["models"] == ["a:1", "b:2"]


def test_server_unreachable_is_a_clear_error_not_a_fallback(
    ollama: FakeOllama, client: TestClient
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    ollama.handler = refuse

    response = client.get("/models/ollama")

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "Cannot connect to Ollama" in detail
    assert "http://ollama.test:11434/v1" in detail
    # Only Ollama was asked: no other provider is contacted.
    assert [r.url.host for r in ollama.requests] == ["ollama.test"]


def test_timeout_is_reported_as_unreachable(ollama: FakeOllama, client: TestClient) -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    ollama.handler = slow

    response = client.get("/models/ollama")

    assert response.status_code == 502
    assert "Cannot connect to Ollama" in response.json()["detail"]


def test_ollama_http_error_is_reported(ollama: FakeOllama, client: TestClient) -> None:
    ollama.handler = lambda _r: httpx.Response(500, text="boom")

    response = client.get("/models/ollama")

    assert response.status_code == 502
    assert "HTTP 500" in response.json()["detail"]


def test_unexpected_response_is_reported(ollama: FakeOllama, client: TestClient) -> None:
    ollama.handler = lambda _r: httpx.Response(200, json={"nope": True})

    assert client.get("/models/ollama").status_code == 502

    ollama.handler = lambda _r: httpx.Response(200, text="<html>not json</html>")

    assert client.get("/models/ollama").status_code == 502


def test_uses_configured_base_url_and_native_tags_endpoint(
    ollama: FakeOllama, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "ollama_base_url", "http://gpu-box.lan:9999/v1")
    ollama.handler = lambda _r: tags("m:1")

    client.get("/models/ollama")

    request = ollama.requests[0]
    assert request.method == "GET"
    assert str(request.url) == "http://gpu-box.lan:9999/api/tags"


def test_sends_ollama_api_key_when_configured(
    ollama: FakeOllama, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "ollama_api_key", "secret")
    ollama.handler = lambda _r: tags("m:1")

    client.get("/models/ollama")

    assert ollama.requests[0].headers["authorization"] == "Bearer secret"


def test_requires_authentication(ollama: FakeOllama) -> None:
    app = FastAPI()
    app.include_router(models_router)  # no auth override

    response = TestClient(app).get("/models/ollama")

    assert response.status_code == 401
    assert ollama.requests == []


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://localhost:11434/v1", "http://localhost:11434/api/tags"),
        ("http://localhost:11434/v1/", "http://localhost:11434/api/tags"),
        ("http://localhost:11434", "http://localhost:11434/api/tags"),
        ("https://host.example/ollama/v1", "https://host.example/ollama/api/tags"),
    ],
)
def test_ollama_tags_url(base_url: str, expected: str) -> None:
    assert ollama_tags_url(base_url) == expected


def test_app_registers_models_route() -> None:
    from chatbot.api.app import create_app

    paths = {route.path for route in create_app().routes}

    assert "/models/ollama" in paths
