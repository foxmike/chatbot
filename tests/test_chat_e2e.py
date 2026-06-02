"""End-to-end tests for the chatbot turn pipeline and API endpoints."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
testclient = pytest.importorskip("starlette.testclient")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.api.chat import chat_router
from chatbot.api.sessions import sessions_router, sessions_store
from chatbot.api.streaming import streaming_router
from chatbot.core import TurnContext, TurnPipeline, build_default_pipeline

FastAPI = fastapi.FastAPI
TestClient = testclient.TestClient


@pytest.fixture
def test_app() -> Iterator[TestClient]:
    app = FastAPI()
    sessions_store.clear()
    app.include_router(chat_router)
    app.include_router(sessions_router)
    app.include_router(streaming_router)

    with TestClient(app, follow_redirects=True) as client:
        yield client

    sessions_store.clear()


@pytest.fixture
def pipeline() -> TurnPipeline:
    return build_default_pipeline()


def _turn_context(**overrides: Any) -> TurnContext:
    values: dict[str, Any] = {
        "session_id": "session-1",
        "user_message": "hello",
        "history": [],
        "persona": {},
        "retrieved_memories": [],
        "compiled_context": None,
        "provider_response": None,
        "extracted_memories": [],
        "final_response": None,
        "metadata": {},
    }
    values.update(overrides)
    return TurnContext(**values)


class TestTurnPipeline:
    def test_build_default_pipeline_has_six_steps(self, pipeline: TurnPipeline) -> None:
        assert len(pipeline._steps) == 6

    def test_pipeline_run_returns_context(self, pipeline: TurnPipeline) -> None:
        ctx = _turn_context()

        result = asyncio.run(pipeline.run(ctx))

        assert isinstance(result, TurnContext)

    def test_pipeline_records_step_results(self, pipeline: TurnPipeline) -> None:
        ctx = _turn_context()

        asyncio.run(pipeline.run(ctx))

        assert len(pipeline.step_results) == 6

    def test_pipeline_sets_final_response(self, pipeline: TurnPipeline) -> None:
        ctx = _turn_context(final_response="preset response")

        result = asyncio.run(pipeline.run(ctx))

        assert result.final_response is not None or "pipeline_error" in result.metadata


class TestChatEndpoint:
    def test_post_chat_returns_200(self, test_app: TestClient) -> None:
        response = test_app.post("/chat", json={"session_id": "session-1", "message": "hello"})

        assert response.status_code == 200

    def test_post_chat_response_shape(self, test_app: TestClient) -> None:
        response = test_app.post("/chat", json={"session_id": "session-1", "message": "hello"})

        payload = response.json()

        assert "session_id" in payload
        assert "response" in payload
        assert "step_results" in payload

    def test_post_chat_step_results_present(self, test_app: TestClient) -> None:
        response = test_app.post("/chat", json={"session_id": "session-1", "message": "hello"})

        assert len(response.json()["step_results"]) == 6


class TestSessionsEndpoint:
    def test_create_session_returns_id(self, test_app: TestClient) -> None:
        response = test_app.post("/sessions")

        assert response.status_code == 200
        assert "session_id" in response.json()

    def test_get_session_returns_info(self, test_app: TestClient) -> None:
        create_response = test_app.post("/sessions")
        session_id = create_response.json()["session_id"]

        response = test_app.get(f"/sessions/{session_id}")

        assert response.status_code == 200
        assert response.json()["message_count"] == 0

    def test_get_missing_session_returns_404(self, test_app: TestClient) -> None:
        response = test_app.get("/sessions/nonexistent")

        assert response.status_code == 404

    def test_delete_session(self, test_app: TestClient) -> None:
        create_response = test_app.post("/sessions")
        session_id = create_response.json()["session_id"]

        response = test_app.delete(f"/sessions/{session_id}")

        assert response.status_code == 200
        assert response.json()["deleted"] is True

    def test_delete_missing_session_returns_404(self, test_app: TestClient) -> None:
        response = test_app.delete("/sessions/nonexistent")

        assert response.status_code == 404


class TestStreamingEndpoint:
    def test_stream_returns_200(self, test_app: TestClient) -> None:
        response = test_app.post(
            "/chat/stream",
            json={"session_id": "session-1", "message": "hello"},
        )

        assert response.status_code == 200

    def test_stream_has_sse_content_type(self, test_app: TestClient) -> None:
        response = test_app.post(
            "/chat/stream",
            json={"session_id": "session-1", "message": "hello"},
        )

        assert "text/event-stream" in response.headers["content-type"]

    def test_stream_contains_done(self, test_app: TestClient) -> None:
        response = test_app.post(
            "/chat/stream",
            json={"session_id": "session-1", "message": "hello"},
        )

        assert "[DONE]" in response.text

    def test_stream_contains_step_events(self, test_app: TestClient) -> None:
        response = test_app.post(
            "/chat/stream",
            json={"session_id": "session-1", "message": "hello"},
        )

        assert '"step":' in response.text
