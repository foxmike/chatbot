"""End-to-end multi-user isolation tests for authenticated API resources."""

from __future__ import annotations

import sys
import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chatbot.api import auth as auth_module
from chatbot.api import chat as chat_module
from chatbot.api import streaming as streaming_module
from chatbot.api.auth import auth_router
from chatbot.api.chat import chat_router
from chatbot.api.companions import companion_router
from chatbot.api.config import config_router
from chatbot.api.sessions import sessions_router
from chatbot.api.streaming import streaming_router
from chatbot.core import TurnContext, TurnPipeline, TurnStep
from chatbot.db import get_db
from chatbot.db.models import Base


@dataclass(frozen=True)
class UserSession:
    username: str
    email: str
    token: str
    user_id: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


class _ProviderStep(TurnStep):
    name = "call_provider"

    async def run(self, ctx: TurnContext) -> TurnContext:
        user_id = ctx.metadata.get("user_id", "anonymous")
        ctx.provider_response = f"deterministic response for {user_id}: {ctx.user_message}"
        return ctx


class _RespondStep(TurnStep):
    name = "respond"

    async def run(self, ctx: TurnContext) -> TurnContext:
        ctx.final_response = ctx.provider_response or "deterministic response"
        return ctx


def _build_test_pipeline() -> TurnPipeline:
    return TurnPipeline([_ProviderStep(), _RespondStep()])


class _FastPasswordHasher:
    @staticmethod
    def hash_password(password: str) -> str:
        return f"test-hash:{password}"

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        return hashed_password == f"test-hash:{plain_password}"


class _AsyncSessionAdapter:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return self._session.execute(*args, **kwargs)

    def add(self, *args: Any, **kwargs: Any) -> None:
        self._session.add(*args, **kwargs)

    async def commit(self) -> None:
        self._session.commit()

    async def refresh(self, *args: Any, **kwargs: Any) -> None:
        self._session.refresh(*args, **kwargs)

    async def delete(self, *args: Any, **kwargs: Any) -> None:
        self._session.delete(*args, **kwargs)

    async def close(self) -> None:
        self._session.close()


class _ASGIClient:
    def __init__(self, app: FastAPI) -> None:
        self._app = app

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        async def _send() -> httpx.Response:
            transport = httpx.ASGITransport(app=self._app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                follow_redirects=True,
            ) as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(_send())


@pytest.fixture
def test_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[_ASGIClient]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection: Any, _connection_record: Any) -> None:
        dbapi_connection.create_function(
            "now",
            0,
            lambda: datetime.now(timezone.utc).isoformat(),
        )

    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    async def _override_get_db() -> AsyncIterator[_AsyncSessionAdapter]:
        session = session_factory()
        try:
            yield _AsyncSessionAdapter(session)
        finally:
            session.close()

    Base.metadata.create_all(engine)

    monkeypatch.setattr(auth_module, "password_hasher", _FastPasswordHasher())
    monkeypatch.setattr(chat_module, "build_default_pipeline", _build_test_pipeline)
    monkeypatch.setattr(streaming_module, "build_default_pipeline", _build_test_pipeline)

    app = FastAPI()
    app.dependency_overrides[get_db] = _override_get_db
    app.include_router(auth_router)
    app.include_router(companion_router)
    app.include_router(sessions_router)
    app.include_router(chat_router)
    app.include_router(streaming_router)
    app.include_router(config_router, prefix="/config", tags=["config"])

    try:
        yield _ASGIClient(app)
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def alice(test_client: _ASGIClient) -> UserSession:
    return _signup(test_client, "alice", "alice@example.com")


@pytest.fixture
def bob(test_client: _ASGIClient) -> UserSession:
    return _signup(test_client, "bob", "bob@example.com")


def _signup(client: _ASGIClient, username: str, email: str) -> UserSession:
    response = client.post(
        "/auth/signup",
        json={
            "username": username,
            "email": email,
            "password": "securepass123",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return UserSession(
        username=username,
        email=email,
        token=payload["access_token"],
        user_id=payload["user"]["user_id"],
    )


def _create_companion(
    client: _ASGIClient,
    user: UserSession,
    name: str,
) -> dict[str, Any]:
    response = client.post(
        "/companions/",
        headers=user.headers,
        json={
            "name": name,
            "persona": f"{name} persona",
            "description": f"{name} private description",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_session(
    client: _ASGIClient,
    user: UserSession,
    *,
    title: str,
    companion_id: str | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/sessions/",
        headers=user.headers,
        json={"title": title, "companion_id": companion_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _send_chat(
    client: _ASGIClient,
    user: UserSession,
    session_id: str,
    message: str,
    *,
    companion_id: str | None = None,
) -> dict[str, Any]:
    body = {"session_id": session_id, "message": message}
    if companion_id is not None:
        body["companion_id"] = companion_id
    response = client.post("/chat/", headers=user.headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _ids(items: list[dict[str, Any]], key: str) -> set[str]:
    return {item[key] for item in items}


def test_sessions_companions_and_messages_are_user_scoped(
    test_client: _ASGIClient,
    alice: UserSession,
    bob: UserSession,
) -> None:
    alice_companion = _create_companion(test_client, alice, "Alice Companion")
    bob_companion = _create_companion(test_client, bob, "Bob Companion")
    alice_session = _create_session(
        test_client,
        alice,
        title="Alice private chat",
        companion_id=alice_companion["companion_id"],
    )
    bob_session = _create_session(
        test_client,
        bob,
        title="Bob private chat",
        companion_id=bob_companion["companion_id"],
    )

    _send_chat(
        test_client,
        alice,
        alice_session["session_id"],
        "Alice secret message",
        companion_id=alice_companion["companion_id"],
    )
    _send_chat(
        test_client,
        bob,
        bob_session["session_id"],
        "Bob secret message",
        companion_id=bob_companion["companion_id"],
    )

    alice_sessions = test_client.get("/sessions/", headers=alice.headers)
    bob_sessions = test_client.get("/sessions/", headers=bob.headers)
    alice_companions = test_client.get("/companions/", headers=alice.headers)
    bob_companions = test_client.get("/companions/", headers=bob.headers)
    alice_messages = test_client.get(
        f"/sessions/{alice_session['session_id']}/messages",
        headers=alice.headers,
    )
    bob_messages = test_client.get(
        f"/sessions/{bob_session['session_id']}/messages",
        headers=bob.headers,
    )

    assert alice_sessions.status_code == 200
    assert bob_sessions.status_code == 200
    assert _ids(alice_sessions.json(), "session_id") == {alice_session["session_id"]}
    assert _ids(bob_sessions.json(), "session_id") == {bob_session["session_id"]}

    assert alice_companions.status_code == 200
    assert bob_companions.status_code == 200
    assert _ids(alice_companions.json()["companions"], "companion_id") == {
        alice_companion["companion_id"]
    }
    assert _ids(bob_companions.json()["companions"], "companion_id") == {
        bob_companion["companion_id"]
    }

    assert alice_messages.status_code == 200
    assert bob_messages.status_code == 200
    alice_message_text = " ".join(message["content"] for message in alice_messages.json())
    bob_message_text = " ".join(message["content"] for message in bob_messages.json())
    assert "Alice secret message" in alice_message_text
    assert "Bob secret message" not in alice_message_text
    assert "Bob secret message" in bob_message_text
    assert "Alice secret message" not in bob_message_text


def test_cross_user_resource_access_returns_not_found(
    test_client: _ASGIClient,
    alice: UserSession,
    bob: UserSession,
) -> None:
    alice_companion = _create_companion(test_client, alice, "Alice Companion")
    bob_companion = _create_companion(test_client, bob, "Bob Companion")
    alice_session = _create_session(
        test_client,
        alice,
        title="Alice private chat",
        companion_id=alice_companion["companion_id"],
    )
    bob_session = _create_session(
        test_client,
        bob,
        title="Bob private chat",
        companion_id=bob_companion["companion_id"],
    )

    assert (
        test_client.get(
            f"/sessions/{alice_session['session_id']}",
            headers=bob.headers,
        ).status_code
        == 404
    )
    assert (
        test_client.get(
            f"/sessions/{alice_session['session_id']}/messages",
            headers=bob.headers,
        ).status_code
        == 404
    )
    assert (
        test_client.get(
            f"/companions/{alice_companion['companion_id']}",
            headers=bob.headers,
        ).status_code
        == 404
    )
    assert (
        test_client.put(
            f"/companions/{alice_companion['companion_id']}",
            headers=bob.headers,
            json={"name": "Stolen", "persona": "wrong"},
        ).status_code
        == 404
    )
    assert (
        test_client.delete(
            f"/companions/{alice_companion['companion_id']}",
            headers=bob.headers,
        ).status_code
        == 404
    )
    assert (
        test_client.post(
            "/sessions/",
            headers=alice.headers,
            json={"title": "bad companion", "companion_id": bob_companion["companion_id"]},
        ).status_code
        == 404
    )
    assert (
        test_client.post(
            "/chat/",
            headers=alice.headers,
            json={"session_id": bob_session["session_id"], "message": "cross user"},
        ).status_code
        == 404
    )
    assert (
        test_client.post(
            "/chat/",
            headers=alice.headers,
            json={
                "session_id": alice_session["session_id"],
                "message": "cross companion",
                "companion_id": bob_companion["companion_id"],
            },
        ).status_code
        == 404
    )

    alice_companion_after = test_client.get(
        f"/companions/{alice_companion['companion_id']}",
        headers=alice.headers,
    )
    assert alice_companion_after.status_code == 200
    assert alice_companion_after.json()["name"] == "Alice Companion"


def test_session_bound_companion_mismatch_is_rejected_for_same_user(
    test_client: _ASGIClient,
    alice: UserSession,
) -> None:
    first_companion = _create_companion(test_client, alice, "First Companion")
    second_companion = _create_companion(test_client, alice, "Second Companion")
    session = _create_session(
        test_client,
        alice,
        title="Bound to first companion",
        companion_id=first_companion["companion_id"],
    )

    response = test_client.post(
        "/chat/",
        headers=alice.headers,
        json={
            "session_id": session["session_id"],
            "message": "use the wrong companion",
            "companion_id": second_companion["companion_id"],
        },
    )

    assert response.status_code == 400
    assert "different companion" in response.json()["detail"]


def test_config_endpoints_require_owned_session(
    test_client: _ASGIClient,
    alice: UserSession,
    bob: UserSession,
) -> None:
    alice_session = _create_session(test_client, alice, title="Alice config")

    own_get = test_client.get(
        f"/config/relationship/{alice_session['session_id']}",
        headers=alice.headers,
    )
    own_put = test_client.put(
        f"/config/relationship/{alice_session['session_id']}",
        headers=alice.headers,
        json={"warmth": 0.85, "directness": 0.65},
    )
    cross_get = test_client.get(
        f"/config/relationship/{alice_session['session_id']}",
        headers=bob.headers,
    )
    cross_character_put = test_client.put(
        f"/config/character/{alice_session['session_id']}",
        headers=bob.headers,
        json={"name": "Leaked Character"},
    )
    cross_identity_post = test_client.post(
        f"/config/identity/{alice_session['session_id']}/evolve",
        headers=bob.headers,
        json={"turn_context": {"mood": "curious"}},
    )

    assert own_get.status_code == 200
    assert own_put.status_code == 200
    assert own_put.json()["warmth"] == 0.85
    assert own_put.json()["directness"] == 0.65
    assert cross_get.status_code == 404
    assert cross_character_put.status_code == 404
    assert cross_identity_post.status_code == 404


def test_streaming_chat_is_authenticated_owned_and_persistent(
    test_client: _ASGIClient,
    alice: UserSession,
    bob: UserSession,
) -> None:
    alice_session = _create_session(test_client, alice, title="Alice stream")

    unauthenticated = test_client.post(
        "/chat/stream",
        json={"session_id": alice_session["session_id"], "message": "no token"},
    )
    bob_cross_user = test_client.post(
        "/chat/stream",
        headers=bob.headers,
        json={"session_id": alice_session["session_id"], "message": "wrong user"},
    )
    alice_stream = test_client.post(
        "/chat/stream",
        headers=alice.headers,
        json={"session_id": alice_session["session_id"], "message": "stream me"},
    )
    messages = test_client.get(
        f"/sessions/{alice_session['session_id']}/messages",
        headers=alice.headers,
    )

    assert unauthenticated.status_code == 401
    assert bob_cross_user.status_code == 404
    assert alice_stream.status_code == 200
    assert "text/event-stream" in alice_stream.headers["content-type"]
    assert "stream me" in alice_stream.text
    assert "[DONE]" in alice_stream.text

    assert messages.status_code == 200
    contents = [message["content"] for message in messages.json()]
    assert "stream me" in contents
    assert any("deterministic response" in content for content in contents)
