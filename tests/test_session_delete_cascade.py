"""Session deletion owns conversation rows, never reusable resources."""

import uuid

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from chatbot.api.auth import auth_router
from chatbot.api.companions import companion_router
from chatbot.api.sessions import sessions_router
from chatbot.auth.dependencies import get_current_user
from chatbot.config import get_settings
from chatbot.db import get_db
from chatbot.db.models import (BotMoodLog, CharacterProfileModel, Companion, Episode,
                               Message, RelationshipConfigModel, Room, SemanticFact, Session,
                               User, UserStateLog, Voice)


@pytest.mark.asyncio
async def test_session_delete_cascades_only_owned_rows():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        outer = await connection.begin()
        factory = async_sessionmaker(connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        app = FastAPI()
        app.include_router(auth_router)
        app.include_router(companion_router)
        app.include_router(sessions_router)
        fail_delete = False

        async def test_db(request: Request):
            async with factory() as db:
                if fail_delete and request.method == "DELETE":
                    async def fail_commit():
                        raise RuntimeError("simulated commit failure")
                    db.commit = fail_commit
                yield db

        app.dependency_overrides[get_db] = test_db
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                async def signup():
                    tag = uuid.uuid4().hex
                    response = await client.post("/auth/signup", json={"username": f"probe{tag[:10]}", "email": f"{tag}@example.com", "password": "test-password-123"})
                    assert response.status_code == 201, response.text
                    data = response.json()
                    return {"Authorization": f"Bearer {data['access_token']}"}, data["user"]["user_id"]

                owner, owner_id = await signup()
                other, other_id = await signup()
                companion_response = await client.post("/companions", headers=owner, json={"name": "Delete probe"})
                assert companion_response.status_code == 201, companion_response.text
                companion_id = companion_response.json()["companion_id"]

                async def make_session(companion=False):
                    response = await client.post("/sessions/", headers=owner, json={"companion_id": companion_id if companion else None})
                    assert response.status_code == 200, response.text
                    return response.json()["session_id"]

                empty_id = await make_session()
                owned_id = await make_session(True)
                untouched_id = await make_session(True)
                other_response = await client.post("/sessions/", headers=other, json={})
                assert other_response.status_code == 200
                other_session_id = other_response.json()["session_id"]
                async with factory() as db:
                    profile_count = await db.scalar(select(func.count()).select_from(CharacterProfileModel).where(CharacterProfileModel.session_id == uuid.UUID(owned_id)))
                    assert profile_count == 1
                    message = Message(session_id=uuid.UUID(owned_id), user_id=uuid.UUID(owner_id), role="user", content="test message")
                    db.add(message)
                    await db.flush()
                    fact = SemanticFact(user_id=uuid.UUID(owner_id), fact_text="durable fact", source_message_id=message.id)
                    db.add(fact)
                    await db.flush()
                    fact_id = fact.id
                    db.add(Episode(session_id=uuid.UUID(owned_id), title="test episode"))
                    db.add(RelationshipConfigModel(session_id=uuid.UUID(owned_id), type="friend", tone="warm",
                                                   directness=0.5, warmth=0.5, initiative_level=0.5, content_level="normal"))
                    db.add(BotMoodLog(session_id=uuid.UUID(owned_id), mood="calm", energy=0.5, closeness=0.5))
                    db.add(UserStateLog(session_id=uuid.UUID(owned_id), confidence=0.5))
                    room_id = str(uuid.uuid4())
                    db.add(Room(room_id=room_id, user_id=owner_id, companion_id=companion_id,
                                room_type="conversation", display_name="test room", session_id=uuid.UUID(owned_id)))
                    voice_id = await db.scalar(select(Voice.voice_id).where(Voice.is_builtin.is_(True)).limit(1))
                    assert voice_id is not None
                    owned = await db.get(Session, uuid.UUID(owned_id))
                    owned.voice_id = voice_id
                    await db.commit()

                forbidden = await client.delete(f"/sessions/{owned_id}", headers=other)
                assert forbidden.status_code == 404
                assert (await client.delete(f"/sessions/{owned_id}")).status_code == 401
                fail_delete = True
                failed = await client.delete(f"/sessions/{owned_id}", headers=owner)
                assert failed.status_code == 500
                fail_delete = False
                async with factory() as db:
                    assert await db.get(Session, uuid.UUID(owned_id)) is not None
                    assert await db.scalar(select(func.count()).select_from(CharacterProfileModel).where(CharacterProfileModel.session_id == uuid.UUID(owned_id))) == 1
                assert (await client.delete(f"/sessions/{empty_id}", headers=owner)).status_code == 200
                assert (await client.delete(f"/sessions/{owned_id}", headers=owner)).status_code == 200
                async with factory() as db:
                    assert await db.get(Session, uuid.UUID(owned_id)) is None
                    assert await db.get(Session, uuid.UUID(untouched_id)) is not None
                    assert await db.get(Session, uuid.UUID(other_session_id)) is not None
                    assert await db.get(Companion, companion_id) is not None
                    assert await db.get(Voice, voice_id) is not None
                    saved_fact = await db.get(SemanticFact, fact_id)
                    assert saved_fact is not None and saved_fact.source_message_id is None
                    assert (await db.get(Room, room_id)).session_id is None
                    assert await db.scalar(select(func.count()).select_from(CharacterProfileModel).where(CharacterProfileModel.session_id == uuid.UUID(owned_id))) == 0
                    assert await db.scalar(select(func.count()).select_from(Message).where(Message.session_id == uuid.UUID(owned_id))) == 0
                    for model in (Episode, RelationshipConfigModel, BotMoodLog, UserStateLog):
                        assert await db.scalar(select(func.count()).select_from(model).where(model.session_id == uuid.UUID(owned_id))) == 0
                    assert await db.get(User, uuid.UUID(other_id)) is not None
        finally:
            await outer.rollback()
    await engine.dispose()
