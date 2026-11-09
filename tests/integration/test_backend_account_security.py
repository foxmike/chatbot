"""Real backend routes on an Alembic-built, disposable PostgreSQL/pgvector database.

Set MULTI_ACCOUNT_PG_URL to database ``multi_account_test`` on localhost at a
non-default port. The fixture creates and later drops only its own randomly
named database. No application lifespan or background worker is started.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import psycopg2
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.websockets import WebSocketDisconnect

from chatbot.api import auth, chat, continuity, rooms, streaming, visual
from chatbot.api.companions import companion_router
from chatbot.api.memory import memory_router
from chatbot.api.sessions import sessions_router
from chatbot.auth.security import create_access_token
from chatbot.avatar import router as avatar
from chatbot.continuity import narrative, rituals, timeline
from chatbot.core import TurnContext, TurnPipeline, TurnStep
from chatbot.db import get_db, models
from chatbot.rooms import room as room_service, scene as scene_service

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def database_url():
    raw = os.environ.get("MULTI_ACCOUNT_PG_URL")
    if not raw:
        pytest.skip("MULTI_ACCOUNT_PG_URL must name a disposable PostgreSQL/pgvector instance")
    url = sa.make_url(raw)
    if not (url.drivername.startswith("postgresql") and url.host in {"localhost", "127.0.0.1"}
            and url.port not in (None, 5432) and url.database == "multi_account_test"):
        raise RuntimeError("Refusing database creation outside the disposable multi_account_test instance")
    connection = psycopg2.connect(url.set(drivername="postgresql").render_as_string(hide_password=False))
    connection.autocommit = True
    name = f"backend_security_{uuid.uuid4().hex}"
    target = url.set(drivername="postgresql+asyncpg", database=name).render_as_string(hide_password=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{name}"')
        migration = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT,
            env={**os.environ, "DATABASE_URL": target}, capture_output=True, text=True, timeout=120,
        )
        assert migration.returncode == 0, migration.stderr
        yield target
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        connection.close()


class Reply(TurnStep):
    name = "call_provider"

    async def run(self, ctx: TurnContext) -> TurnContext:
        ctx.provider_response = "Local test reply"
        ctx.final_response = ctx.provider_response
        return ctx


@pytest.fixture
def api(database_url, monkeypatch):
    engine = create_async_engine(database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with factory() as session:
            yield session

    for module in (rooms, room_service, scene_service, visual, narrative, avatar):
        monkeypatch.setattr(module, "async_session_factory", factory)
    monkeypatch.setattr(streaming, "streaming_session_factory", factory)
    monkeypatch.setattr(timeline, "_default_timeline", timeline.RelationshipTimeline(factory))
    monkeypatch.setattr(rituals, "_default_rituals", rituals.Rituals(factory))
    monkeypatch.setattr(chat, "build_default_pipeline", lambda: TurnPipeline([Reply()]))
    monkeypatch.setattr(streaming, "build_default_pipeline", lambda: TurnPipeline([Reply()]))
    app = FastAPI()
    app.dependency_overrides[get_db] = db
    for router in (auth.auth_router, companion_router, sessions_router, rooms.rooms_router,
                   continuity.timeline_router, continuity.narrative_router, continuity.rituals_router,
                   visual.visual_router, chat.chat_router, streaming.streaming_router, memory_router,
                   avatar.avatar_router):
        app.include_router(router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, factory=factory, app=app)
    asyncio.run(engine.dispose())


def signup(api, label):
    name = label + uuid.uuid4().hex[:10]
    result = api.client.post("/auth/signup", json={
        "username": name, "email": name + "@example.com", "password": "safe-test-password",
    })
    assert result.status_code == 201, result.text
    payload = result.json()
    return SimpleNamespace(id=payload["user"]["user_id"], token=payload["access_token"],
                           email=payload["user"]["email"], username=name,
                           headers={"Authorization": "Bearer " + payload["access_token"]})


def create(api, path, user, body, status=201):
    result = api.client.post(path, headers=user.headers, json=body)
    assert result.status_code == status, result.text
    return result.json()


@pytest.fixture
def accounts(api):
    alice, bob = signup(api, "alice"), signup(api, "bob")
    ca = create(api, "/companions/", alice, {"name": "Alice companion"})["companion_id"]
    cb = create(api, "/companions/", bob, {"name": "Bob companion"})["companion_id"]
    ra = create(api, "/rooms/", alice, {"room_type": "roleplay_scene", "companion_id": ca})["room_id"]
    rb = create(api, "/rooms/", bob, {"room_type": "roleplay_scene", "companion_id": cb})["room_id"]
    sa_id = api.client.get(f"/rooms/{ra}/session", headers=alice.headers).json()["session_id"]
    sb_id = api.client.get(f"/rooms/{rb}/session", headers=bob.headers).json()["session_id"]
    return SimpleNamespace(alice=alice, bob=bob, ca=ca, cb=cb, ra=ra, rb=rb, sa=sa_id, sb=sb_id)


def test_room_types_and_created_rooms_are_independent(api, accounts):
    a = accounts
    # RoomType is a shared enum in the API schema, independent of the principal.
    schema = api.client.get("/openapi.json").json()
    types = schema["components"]["schemas"]["RoomType"]["enum"]
    assert len(types) == 5
    for kind in types:
        for user in (a.alice, a.bob):
            created = create(api, "/rooms/", user, {"room_type": kind, "user_id": a.bob.id})
            assert created["user_id"] == user.id
            contract = api.client.get(f"/rooms/{created['room_id']}/mode-contract", headers=user.headers)
            assert contract.status_code == 200
            assert contract.json()["room_type"] == kind
    left = api.client.get(f"/rooms/{a.alice.id}", headers=a.alice.headers).json()["rooms"]
    right = api.client.get(f"/rooms/{a.bob.id}", headers=a.bob.headers).json()["rooms"]
    assert len(left) == len(right) == 6
    assert {r["room_id"] for r in left}.isdisjoint({r["room_id"] for r in right})
    assert {r["room_type"] for r in left} == {r["room_type"] for r in right} == set(types)
    assert api.client.get(f"/rooms/{a.alice.id}", headers=a.bob.headers).status_code == 404


def test_all_room_and_scene_operations_enforce_owner(api, accounts):
    a = accounts
    scene = create(api, f"/rooms/{a.ra}/scenes", a.alice, {"location": "private room"})["scene_id"]
    operations = [
        ("GET", f"/rooms/detail/{a.ra}", None),
        ("PUT", f"/rooms/{a.ra}/memory-policy", {"persist_to_durable": False}),
        ("DELETE", f"/rooms/{a.ra}", None),
        ("POST", f"/rooms/{a.ra}/scenes", {}),
        ("GET", f"/rooms/{a.ra}/scenes/active", None),
        ("GET", f"/rooms/{a.ra}/session", None),
        ("POST", f"/rooms/{a.ra}/link-session", {"session_id": a.sb}),
        ("DELETE", f"/rooms/{a.ra}/session", None),
        ("GET", f"/rooms/{a.ra}/mode-contract", None),
        ("GET", f"/rooms/{a.ra}/prompt-template", None),
        ("POST", f"/rooms/{a.ra}/prompt-template", {}),
        ("GET", f"/rooms/scenes/{scene}", None),
        ("PATCH", f"/rooms/scenes/{scene}", {"location": "stolen"}),
        ("POST", f"/rooms/scenes/{scene}/pause", None),
        ("POST", f"/rooms/scenes/{scene}/resume", None),
        ("POST", f"/rooms/scenes/{scene}/end", None),
        ("POST", f"/rooms/scenes/{scene}/persona", {
            "name": "fake", "description": "", "traits": [], "voice_style": "",
        }),
        ("POST", f"/rooms/scenes/{scene}/classify", {"text": "secret"}),
    ]
    for method, path, body in operations:
        for headers, expected in (({}, 401), (a.bob.headers, 404)):
            response = api.client.request(method, path, headers=headers, json=body)
            assert response.status_code == expected, (method, path, response.text)
    assert api.client.post("/rooms/", json={"room_type": "project"}).status_code == 401
    assert api.client.get(f"/rooms/{a.alice.id}").status_code == 401
    assert api.client.get(f"/rooms/scenes/{scene}", headers=a.alice.headers).json()["location"] == "private room"


def test_owned_scene_lifecycle_and_room_session_links(api, accounts):
    a = accounts
    scene = create(api, f"/rooms/{a.ra}/scenes", a.alice, {"room_id": a.ra})["scene_id"]
    assert api.client.patch(f"/rooms/scenes/{scene}", headers=a.alice.headers,
                            json={"location": "private forest", "participants": ["user", "companion"]}).status_code == 200
    assert api.client.get(f"/rooms/{a.ra}/scenes/active", headers=a.alice.headers).json()["location"] == "private forest"
    assert api.client.post(f"/rooms/scenes/{scene}/persona", headers=a.alice.headers, json={
        "name": "Companion", "description": "", "traits": [], "voice_style": "gentle",
    }).status_code == 200
    assert api.client.post(f"/rooms/scenes/{scene}/classify", headers=a.alice.headers,
                           json={"text": "remember this forest"}).status_code == 200
    for action in ("pause", "resume", "end"):
        result = api.client.post(f"/rooms/scenes/{scene}/{action}", headers=a.alice.headers)
        assert result.status_code == 200, result.text
    assert api.client.post(f"/rooms/scenes/{scene}/resume", headers=a.alice.headers).status_code == 404
    assert api.client.put(f"/rooms/{a.ra}/memory-policy", headers=a.alice.headers,
                          json={"persist_to_durable": False}).status_code == 200
    assert api.client.post(f"/rooms/{a.ra}/scenes", headers=a.alice.headers,
                           json={"room_id": a.rb}).status_code == 400
    assert api.client.post(f"/rooms/{a.ra}/link-session", headers=a.alice.headers,
                           json={"session_id": a.sb}).status_code == 404
    assert api.client.post(f"/rooms/{a.ra}/link-session", headers=a.alice.headers,
                           json={"session_id": "invalid"}).status_code == 404
    assert api.client.post("/rooms/", headers=a.alice.headers,
                           json={"room_type": "project", "companion_id": a.cb}).status_code == 404
    assert api.client.post("/sessions/", headers=a.alice.headers,
                           json={"room_id": a.rb}).status_code == 404
    assert api.client.delete(f"/rooms/{a.ra}/session", headers=a.alice.headers).status_code == 204
    assert api.client.post(f"/rooms/{a.ra}/link-session", headers=a.alice.headers,
                           json={"session_id": a.sa}).status_code == 200
    assert api.client.delete(f"/rooms/{a.ra}", headers=a.alice.headers).status_code == 204


def test_corrupt_cross_user_room_links_fail_closed(api, accounts):
    a = accounts
    async def corrupt():
        async with api.factory() as db:
            await db.execute(sa.update(models.Room).where(models.Room.room_id == a.ra).values(session_id=uuid.UUID(a.sb)))
            await db.commit()
    asyncio.run(corrupt())
    for method, path in (("GET", f"/rooms/{a.ra}/session"), ("DELETE", f"/rooms/{a.ra}/session")):
        assert api.client.request(method, path, headers=a.alice.headers).status_code == 404


def test_continuity_reads_and_indirect_mutations_enforce_owner(api, accounts):
    a = accounts
    async def add_entry():
        return await timeline.add_timeline_entry(a.sa, "promise", "Private promise", "private", "private", str(uuid.uuid4()))
    entry = asyncio.run(add_entry())
    ritual = create(api, f"/rituals/{a.sa}", a.alice, {
        "name": "private", "ritual_type": "check_in", "description": "private", "schedule_description": "daily",
    }, status=200)["ritual_id"]
    operations = [
        ("GET", f"/timeline/{a.sa}", None),
        ("GET", f"/timeline/{a.sa}/projects/active", None),
        ("GET", f"/timeline/{a.sa}/promises/pending", None),
        ("POST", f"/timeline/{entry.entry_id}/resolve", {}),
        ("GET", f"/narrative/{a.sa}/threads", None),
        ("POST", f"/narrative/{a.sa}/callbacks", {"message": "private"}),
        ("GET", f"/rituals/{a.sa}", None),
        ("POST", f"/rituals/{a.sa}", {"name": "x", "ritual_type": "check_in", "description": "", "schedule_description": "daily"}),
        ("DELETE", f"/rituals/{ritual}", None),
    ]
    for method, path, body in operations:
        for headers, expected in (({}, 401), (a.bob.headers, 404)):
            response = api.client.request(method, path, headers=headers, json=body)
            assert response.status_code == expected, (method, path, response.text)
    assert api.client.get(f"/timeline/{a.sa}", headers=a.alice.headers).json()[0]["status"] == "active"
    assert api.client.get(f"/rituals/{a.sa}", headers=a.alice.headers).json()[0]["is_active"]
    assert api.client.get(f"/narrative/{a.sa}/threads", headers=a.alice.headers).status_code == 200
    assert api.client.post(f"/narrative/{a.sa}/callbacks", headers=a.alice.headers, json={"message": "private"}).status_code == 200
    assert api.client.post(f"/timeline/{entry.entry_id}/resolve", headers=a.alice.headers, json={}).status_code == 200
    assert api.client.delete(f"/rituals/{ritual}", headers=a.alice.headers).status_code == 204
    assert api.client.get(f"/rituals/{a.sa}", headers=a.alice.headers).json() == []


def test_private_image_gallery_retrieval_and_companion_ownership(api, accounts):
    a = accounts
    image = create(api, "/visual/upload", a.alice, {
        "image_base64": base64.b64encode(b"private-image-bytes").decode(), "companion_id": a.ca,
    }, status=200)
    image_id = image["image_id"]
    path = f"/visual/images/{image_id}"
    response = api.client.get(path, headers=a.alice.headers)
    assert response.status_code == 200 and response.content == b"private-image-bytes"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Authorization"
    assert "token" not in image["image_url"]
    for headers, expected in (({}, 401), (a.bob.headers, 404)):
        for method, suffix, body in (("GET", "", None), ("DELETE", "", None), ("PATCH", "/description", {"prompt_used": "stolen"})):
            assert api.client.request(method, path + suffix, headers=headers, json=body).status_code == expected
    assert api.client.get(path + "?token=" + a.alice.token).status_code == 401
    gallery = api.client.get(f"/visual/companion/{a.ca}/images", headers=a.alice.headers)
    assert gallery.status_code == 200 and gallery.json()["images"][0]["image_id"] == image_id
    assert api.client.get(f"/visual/companion/{a.ca}/images", headers=a.bob.headers).status_code == 404
    assert api.client.post("/visual/upload", headers=a.alice.headers, json={
        "companion_id": a.cb, "image_base64": "aW1hZ2U=",
    }).status_code == 404
    async def corrupt():
        async with api.factory() as db:
            await db.execute(sa.update(models.ImageMemory).where(models.ImageMemory.image_id == image_id).values(companion_id=a.cb))
            await db.commit()
    asyncio.run(corrupt())
    assert api.client.get(path, headers=a.alice.headers).status_code == 404


def test_appearance_is_explicit_per_companion_with_legacy_fallback(api, accounts):
    a = accounts
    second = create(api, "/companions/", a.alice, {"name": "Second"})["companion_id"]
    for companion_id, description in ((a.ca, "first appearance"), (second, "second appearance")):
        result = api.client.put(f"/visual/appearance?companion_id={companion_id}", headers=a.alice.headers,
                                json={"character_description": description, "character_seed": 42})
        assert result.status_code == 200, result.text
        assert result.json()["character_description"] == description
    for companion_id, description in ((a.ca, "first appearance"), (second, "second appearance")):
        assert api.client.get(f"/visual/appearance?companion_id={companion_id}", headers=a.alice.headers).json()["character_description"] == description
    assert api.client.get("/visual/appearance", headers=a.alice.headers).json()["character_description"] == "first appearance"
    for method in ("GET", "PUT"):
        response = api.client.request(method, f"/visual/appearance?companion_id={a.cb}", headers=a.alice.headers, json={})
        assert response.status_code == 404
    assert api.client.get("/visual/appearance").status_code == 401
    assert api.client.put("/visual/appearance", json={}).status_code == 401
    empty = signup(api, "empty")
    assert api.client.get("/visual/appearance", headers=empty.headers).status_code == 200


def test_session_revocation_expiration_and_independent_logins(api):
    user = signup(api, "auth")
    first = api.client.post("/auth/login", json={"email": user.email, "password": "safe-test-password"})
    second = api.client.post("/auth/login", json={"email": user.email, "password": "safe-test-password"})
    assert first.status_code == second.status_code == 200
    one, two = first.json()["access_token"], second.json()["access_token"]
    assert len({user.token, one, two}) == 3
    h1, h2 = {"Authorization": "Bearer " + one}, {"Authorization": "Bearer " + two}
    async def duplicate_legacy_session():
        async with api.factory() as db:
            db.add(models.UserSession(
                user_id=uuid.UUID(user.id), token_hash=hashlib.sha256(one.encode()).hexdigest(),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            ))
            await db.commit()
    asyncio.run(duplicate_legacy_session())
    assert api.client.get("/auth/me", headers=h1).status_code == 200
    assert api.client.post("/auth/logout", headers=h1).status_code == 200
    assert api.client.get("/auth/me", headers=h1).status_code == 401
    assert api.client.get("/auth/me", headers=h2).status_code == 200
    assert api.client.get("/auth/me", headers=user.headers).status_code == 200
    async def expire_session():
        async with api.factory() as db:
            await db.execute(sa.update(models.UserSession).where(models.UserSession.token_hash == hashlib.sha256(two.encode()).hexdigest()).values(expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
            await db.commit()
    asyncio.run(expire_session())
    assert api.client.get("/auth/me", headers=h2).status_code == 401
    for token in (
        create_access_token({"sub": user.id, "username": user.username, "email": user.email}),
        create_access_token({"sub": user.id, "username": user.username, "email": user.email}, expires_delta=timedelta(seconds=-1)),
        create_access_token({"sub": "not-a-uuid", "username": user.username, "email": user.email}),
        create_access_token({"sub": user.id}),
    ):
        response = api.client.get("/auth/me", headers={"Authorization": "Bearer " + token})
        assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"


def test_protected_chat_streaming_messages_and_avatar_auth(api, accounts):
    a = accounts
    for endpoint in ("/chat/", "/chat/stream"):
        for user, expected in ((a.alice, 200), (a.bob, 404)):
            result = api.client.post(endpoint, headers=user.headers, json={"session_id": a.sa, "message": "private"})
            assert result.status_code == expected, result.text
        assert api.client.post(endpoint, json={"session_id": a.sa, "message": "private"}).status_code == 401
    assert api.client.get(f"/sessions/{a.sa}/messages", headers=a.alice.headers).status_code == 200
    assert api.client.get(f"/sessions/{a.sa}/messages", headers=a.bob.headers).status_code == 404
    assert api.client.get(f"/companions/{a.ca}", headers=a.bob.headers).status_code == 404
    assert api.client.post("/auth/logout", headers=a.alice.headers).status_code == 200
    for endpoint in ("/chat/", "/chat/stream"):
        assert api.client.post(endpoint, headers=a.alice.headers, json={"session_id": a.sa, "message": "private"}).status_code == 401
    for token in (a.alice.token, a.bob.token):
        with api.client.websocket_connect("/api/avatar/ws") as ws:
            ws.send_json({"type": "auth", "token": token, "session_id": a.sa, "companion_id": a.ca})
            with pytest.raises(WebSocketDisconnect) as error:
                ws.receive_json()
            assert error.value.code == 1008


def test_generation_stays_owned_and_preserves_portrait(api, accounts, monkeypatch):
    from chatbot.visual.base import ImageGenerationProvider, ImageGenerationResult

    class LocalImages(ImageGenerationProvider):
        provider_name = "local-test"
        calls = 0

        async def generate(self, prompt, **options):
            type(self).calls += 1
            return ImageGenerationResult(b"generated-private-image", "image/png", 7, prompt,
                                         "local-test/image", 64, 64, 1.0)

    monkeypatch.setitem(visual.VISUAL_PROVIDER_REGISTRY, "local-test", LocalImages)
    a = accounts
    created = create(api, "/visual/generate", a.alice, {
        "model": "local-test/image", "prompt": "test portrait", "companion_id": a.ca,
    }, status=200)
    path = f"/visual/images/{created['image_id']}"
    assert api.client.get(path, headers=a.alice.headers).content == b"generated-private-image"
    assert api.client.get(path, headers=a.bob.headers).status_code == 404
    assert api.client.get(path).status_code == 401
    denied = api.client.post("/visual/generate", headers=a.alice.headers, json={
        "model": "local-test/image", "prompt": "test portrait", "companion_id": a.cb,
    })
    assert denied.status_code == 404 and LocalImages.calls == 1
    # Appearance edits do not replace the selected portrait or stored image.
    assert api.client.put(f"/companions/{a.ca}", headers=a.alice.headers,
                          json={"name": "Alice companion", "avatar_url": created["image_url"]}).status_code == 200
    assert api.client.put(f"/visual/appearance?companion_id={a.ca}", headers=a.alice.headers,
                          json={"character_description": "updated"}).status_code == 200
    assert api.client.get(f"/companions/{a.ca}", headers=a.alice.headers).json()["avatar_url"] == created["image_url"]
    assert api.client.get(path, headers=a.alice.headers).content == b"generated-private-image"
    # Rows without ownership are not implicitly treated as public system images.
    async def legacy_image():
        async with api.factory() as db:
            row = models.ImageMemory(source_type="generated", mime_type="image/png", image_data=b"legacy")
            db.add(row)
            await db.commit()
            return row.image_id
    unknown_owner = asyncio.run(legacy_image())
    assert api.client.get(f"/visual/images/{unknown_owner}", headers=a.alice.headers).status_code == 404


def test_memories_and_relationship_configuration_keep_ownership(api, accounts):
    from chatbot.api.config import config_router
    api.app.include_router(config_router, prefix="/config")
    a = accounts
    async def seed():
        async with api.factory() as db:
            own = models.SemanticFact(user_id=uuid.UUID(a.alice.id), fact_text="Alice private fact", source_type="explicit")
            other = models.SemanticFact(user_id=uuid.UUID(a.bob.id), fact_text="Bob private fact", source_type="explicit")
            db.add_all([own, other])
            await db.commit()
            return str(own.id)
    memory_id = asyncio.run(seed())
    for user, visible, hidden in ((a.alice, "Alice private fact", "Bob private fact"),
                                  (a.bob, "Bob private fact", "Alice private fact")):
        for path in ("/memories", "/memories/search", "/memories/export"):
            result = api.client.get(path, headers=user.headers)
            assert result.status_code == 200, result.text
            assert visible in result.text and hidden not in result.text
    for path in (f"/memories/{memory_id}/provenance", f"/memories/search?session_id={a.sa}"):
        assert api.client.get(path, headers=a.bob.headers).status_code == 404
    assert api.client.delete(f"/memories/{memory_id}", headers=a.bob.headers).status_code == 404
    assert api.client.put(f"/config/relationship/{a.sa}", headers=a.alice.headers,
                          json={"warmth": 0.85}).status_code == 200
    assert api.client.get(f"/config/relationship/{a.sa}", headers=a.bob.headers).status_code == 404
    assert api.client.put(f"/config/relationship/{a.sa}", headers=a.bob.headers,
                          json={"warmth": 0.1}).status_code == 404
    assert api.client.get(f"/config/relationship/{a.sa}", headers=a.alice.headers).json()["warmth"] == 0.85


def test_existing_avatar_connection_rechecks_revocation_before_spoken_turn(api, accounts):
    a = accounts

    class Runtime:
        def __init__(self, binding, emit):
            self.emit = emit

        async def start(self):
            pass

        async def aclose(self):
            pass

        async def microphone(self, pcm):
            await self.emit({"type": "transcript", "text": "test spoken words"})

    api.app.state.avatar_runtime_factory = Runtime
    with api.client.websocket_connect("/api/avatar/ws") as ws:
        ws.send_json({"type": "auth", "token": a.alice.token, "session_id": a.sa, "companion_id": a.ca})
        assert ws.receive_json()["type"] == "ready"
        assert api.client.post("/auth/logout", headers=a.alice.headers).status_code == 200
        ws.send_bytes(b"M" + b"\0" * 4)
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_json()
        assert error.value.code == 1008
