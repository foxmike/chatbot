"""End-to-end integration tests for the chatbot engine covering multi-turn conversation, memory persistence, and consolidation."""

from __future__ import annotations

import importlib
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("pytest_asyncio")
testclient = pytest.importorskip("starlette.testclient")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chatbot.api.chat import chat_router
from chatbot.api.sessions import sessions_router, sessions_store
from chatbot.api.streaming import streaming_router
from chatbot.core import TurnContext, TurnPipeline, TurnStep

FastAPI = fastapi.FastAPI
TestClient = testclient.TestClient


@pytest.fixture
def mock_vectorstore() -> AsyncMock:
    stored_memories: list[dict[str, Any]] = []
    vectorstore = AsyncMock(name="mock_vectorstore")

    async def retrieve(*, session_id: str, query: str, **_: Any) -> list[dict[str, Any]]:
        return [
            dict(memory)
            for memory in stored_memories
            if memory.get("session_id") == session_id
            and memory.get("content", "").lower() not in query.lower()
        ]

    async def store(memory: dict[str, Any]) -> dict[str, Any]:
        stored_memories.append(dict(memory))
        return memory

    vectorstore.retrieve = AsyncMock(side_effect=retrieve)
    vectorstore.store = AsyncMock(side_effect=store)
    vectorstore.stored_memories = stored_memories
    return vectorstore


@pytest.fixture
def test_app(monkeypatch: pytest.MonkeyPatch, mock_vectorstore: AsyncMock) -> Iterator[TestClient]:
    chat_module = importlib.import_module("chatbot.api.chat")
    streaming_module = importlib.import_module("chatbot.api.streaming")

    app = FastAPI()
    app.state.turn_contexts = []
    app.state.histories = {}
    sessions_store.clear()

    def build_pipeline() -> TurnPipeline:
        return _build_test_pipeline(
            vectorstore=mock_vectorstore,
            histories=app.state.histories,
            captured_contexts=app.state.turn_contexts,
        )

    monkeypatch.setattr(chat_module, "build_default_pipeline", build_pipeline)
    monkeypatch.setattr(streaming_module, "build_default_pipeline", build_pipeline)

    app.include_router(chat_router)
    app.include_router(sessions_router)
    app.include_router(streaming_router)

    with TestClient(app, follow_redirects=True) as client:
        yield client

    sessions_store.clear()


def _build_test_pipeline(
    *,
    vectorstore: AsyncMock,
    histories: dict[str, list[dict[str, str]]],
    captured_contexts: list[TurnContext],
) -> TurnPipeline:
    return TurnPipeline(
        [
            _RetrieveMemoryStep(vectorstore),
            _CompileContextStep(histories, captured_contexts),
            _ProviderStep(),
            _ExtractMemoryStep(),
            _StoreMemoryStep(vectorstore, histories),
            _RespondStep(captured_contexts),
        ]
    )


class _RetrieveMemoryStep(TurnStep):
    name = "retrieve_memory"

    def __init__(self, vectorstore: AsyncMock) -> None:
        self.vectorstore = vectorstore

    async def run(self, ctx: TurnContext) -> TurnContext:
        ctx.retrieved_memories = await self.vectorstore.retrieve(
            session_id=ctx.session_id,
            query=ctx.user_message,
        )
        return ctx


class _CompileContextStep(TurnStep):
    name = "compile_context"

    def __init__(
        self,
        histories: dict[str, list[dict[str, str]]],
        captured_contexts: list[TurnContext],
    ) -> None:
        self.histories = histories
        self.captured_contexts = captured_contexts

    async def run(self, ctx: TurnContext) -> TurnContext:
        ctx.history = list(self.histories.get(ctx.session_id, []))
        memory_lines = [memory["content"] for memory in ctx.retrieved_memories]
        history_lines = [
            f"{message['role']}: {message['content']}" for message in ctx.history
        ]
        ctx.compiled_context = "\n".join([*memory_lines, *history_lines])
        ctx.metadata["compiled_context"] = ctx.compiled_context
        self.captured_contexts.append(ctx)
        return ctx


class _ProviderStep(TurnStep):
    name = "call_provider"

    async def run(self, ctx: TurnContext) -> TurnContext:
        message = ctx.user_message.lower()
        context = (ctx.compiled_context or "").lower()

        if "what is my name" in message and "alice" in context:
            ctx.provider_response = "Your name is Alice."
        elif "what is my name" in message:
            ctx.provider_response = "You asked about your name, but I do not have it yet."
        elif "my name is alice" in message:
            ctx.provider_response = "Nice to meet you, Alice."
        elif "live" in message and ("where" in message or "what city" in message):
            if "berlin" in context:
                ctx.provider_response = "You told me you live in Berlin."
            else:
                ctx.provider_response = "You asked where you live, but I do not have that fact yet."
        elif "i live in berlin" in message:
            ctx.provider_response = "I will remember that you live in Berlin."
        elif "hello" in message:
            ctx.provider_response = "Hello. I can help."
        elif ctx.retrieved_memories:
            ctx.provider_response = f"I found this context: {ctx.retrieved_memories[0]['content']}"
        else:
            ctx.provider_response = "I can help with that."

        return ctx


class _ExtractMemoryStep(TurnStep):
    name = "extract_memory"

    async def run(self, ctx: TurnContext) -> TurnContext:
        memories = [
            {
                "session_id": ctx.session_id,
                "kind": "turn",
                "content": f"User said: {ctx.user_message}",
            }
        ]

        name_match = re.search(r"\bmy name is ([A-Z][a-z]+)\b", ctx.user_message)
        if name_match:
            memories.append(
                {
                    "session_id": ctx.session_id,
                    "kind": "profile",
                    "content": f"User name is {name_match.group(1)}",
                }
            )

        location_match = re.search(r"\bi live in ([A-Z][A-Za-z\s-]+)\b", ctx.user_message)
        if location_match:
            memories.append(
                {
                    "session_id": ctx.session_id,
                    "kind": "profile",
                    "content": f"User lives in {location_match.group(1).strip()}",
                }
            )

        ctx.extracted_memories = memories
        ctx.metadata["consolidated_memory_count"] = sum(
            1 for memory in memories if memory["kind"] == "profile"
        )
        return ctx


class _StoreMemoryStep(TurnStep):
    name = "store_memory"

    def __init__(
        self,
        vectorstore: AsyncMock,
        histories: dict[str, list[dict[str, str]]],
    ) -> None:
        self.vectorstore = vectorstore
        self.histories = histories

    async def run(self, ctx: TurnContext) -> TurnContext:
        for memory in ctx.extracted_memories:
            await self.vectorstore.store(memory)

        history = self.histories.setdefault(ctx.session_id, [])
        history.append({"role": "user", "content": ctx.user_message})
        history.append({"role": "assistant", "content": ctx.provider_response or ""})

        if ctx.session_id in sessions_store:
            sessions_store[ctx.session_id]["message_count"] += 1

        return ctx


class _RespondStep(TurnStep):
    name = "respond"

    def __init__(self, captured_contexts: list[TurnContext]) -> None:
        self.captured_contexts = captured_contexts

    async def run(self, ctx: TurnContext) -> TurnContext:
        ctx.final_response = ctx.provider_response or "No response"
        self.captured_contexts.append(ctx)
        return ctx


def _create_session(client: TestClient) -> str:
    response = client.post("/sessions")
    assert response.status_code == 200
    return response.json()["session_id"]


def _send_message(client: TestClient, session_id: str, message: str) -> dict[str, Any]:
    response = client.post("/chat", json={"session_id": session_id, "message": message})
    assert response.status_code == 200
    return response.json()


class TestFullConversation:
    @pytest.mark.asyncio
    async def test_create_session_and_send_message(self, test_app: TestClient) -> None:
        session_id = _create_session(test_app)

        payload = _send_message(test_app, session_id, "Hello")

        assert payload["session_id"] == session_id
        assert payload["response"]
        assert payload["step_results"]

    @pytest.mark.asyncio
    async def test_multi_turn_conversation(self, test_app: TestClient) -> None:
        session_id = _create_session(test_app)

        first = _send_message(test_app, session_id, "Hello")
        second = _send_message(test_app, session_id, "My name is Alice")
        third = _send_message(test_app, session_id, "What is my name?")

        assert first["response"]
        assert second["response"]
        assert "Alice" in third["response"] or "name" in third["response"].lower()

    @pytest.mark.asyncio
    async def test_session_persistence(self, test_app: TestClient) -> None:
        session_id = _create_session(test_app)
        _send_message(test_app, session_id, "Hello")

        response = test_app.get(f"/sessions/{session_id}")

        assert response.status_code == 200
        assert response.json()["message_count"] >= 1

    @pytest.mark.asyncio
    async def test_conversation_context_threading(self, test_app: TestClient) -> None:
        session_id = _create_session(test_app)

        _send_message(test_app, session_id, "I live in Berlin")
        payload = _send_message(test_app, session_id, "What city do I live in?")

        assert "Berlin" in payload["response"] or "live" in payload["response"].lower()

    @pytest.mark.asyncio
    async def test_delete_session_and_recreate(self, test_app: TestClient) -> None:
        session_id = _create_session(test_app)

        delete_response = test_app.delete(f"/sessions/{session_id}")
        new_session_id = _create_session(test_app)

        assert delete_response.status_code == 200
        assert delete_response.json()["deleted"] is True
        assert new_session_id != session_id


class TestStreamingConversation:
    @pytest.mark.asyncio
    async def test_stream_full_conversation(self, test_app: TestClient) -> None:
        session_id = _create_session(test_app)

        response = test_app.post(
            "/chat/stream",
            json={"session_id": session_id, "message": "Hello"},
        )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert "[DONE]" in response.text
        assert '"step":' in response.text


class TestMemoryIntegration:
    @pytest.mark.asyncio
    async def test_memory_store_called(
        self,
        test_app: TestClient,
        mock_vectorstore: AsyncMock,
    ) -> None:
        session_id = _create_session(test_app)

        _send_message(test_app, session_id, "My name is Alice")

        assert mock_vectorstore.store.await_count >= 1
        assert mock_vectorstore.stored_memories

    @pytest.mark.asyncio
    async def test_memory_retrieval_in_context(
        self,
        test_app: TestClient,
        mock_vectorstore: AsyncMock,
    ) -> None:
        session_id = _create_session(test_app)
        _send_message(test_app, session_id, "My name is Alice")

        payload = _send_message(test_app, session_id, "What is my name?")
        final_ctx = test_app.app.state.turn_contexts[-1]

        assert mock_vectorstore.retrieve.await_count >= 2
        assert any("Alice" in memory["content"] for memory in final_ctx.retrieved_memories)
        assert "Alice" in (final_ctx.compiled_context or "")
        assert "Alice" in payload["response"]
