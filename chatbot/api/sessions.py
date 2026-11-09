"""Session management endpoints for the chatbot engine."""

from __future__ import annotations

import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.db import get_db
from chatbot.db.models import (
    CompanionIdentityModel, Companion, CharacterProfileModel, IdentityStateModel, Message, Room, Session,
)

logger = logging.getLogger(__name__)


class SessionCreateRequest(BaseModel):
    companion_id: str | None = None
    room_id: str | None = None
    title: str | None = None


class SessionCreateResponse(BaseModel):
    session_id: str
    room_id: str | None = None
    title: str | None = None
    created_at: str | None = None


class SessionInfo(BaseModel):
    session_id: str
    room_id: str | None = None
    title: str | None = None
    created_at: str
    message_count: int


class SessionMessageInfo(BaseModel):
    turn_id: str | None = None
    message_id: str
    session_id: str
    role: str
    content: str
    created_at: str


class SessionDeleteResponse(BaseModel):
    session_id: str
    deleted: bool


sessions_router = APIRouter(prefix="/sessions")

# In-memory session cache (legacy compat, used by chat.py)
sessions_store: dict[str, dict] = {}


@sessions_router.post("/", response_model=SessionCreateResponse)
async def create_session(
    body: SessionCreateRequest = SessionCreateRequest(),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionCreateResponse:
    companion_id = body.companion_id
    companion_orm: Companion | None = None

    if body.room_id is not None:
        result = await db.execute(select(Room.room_id).where(
            Room.room_id == body.room_id, Room.user_id == current_user.user_id,
        ))
        if result.scalar_one_or_none() is None:
            raise HTTPException(404, "Room not found")

    if companion_id is not None:
        companion_orm = await _get_companion_orm(db, current_user.user_id, companion_id)
        if companion_orm is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Companion not found",
            )

    # Session.id is assigned by the Python-side default=uuid.uuid4 immediately,
    # so child rows can reference session.id before the INSERT is flushed.
    session = Session(
        user_id=uuid.UUID(current_user.user_id),
        companion_id=companion_id,
        room_id=body.room_id,
        title=body.title or "New Chat",
    )
    db.add(session)
    await db.flush()

    if companion_orm is not None:
        identity = await db.get(CompanionIdentityModel, companion_id)
        _seed_session_from_companion(db, session.id, companion_orm, identity)

    await db.commit()
    await db.refresh(session)
    return SessionCreateResponse(
        session_id=str(session.id),
        room_id=session.room_id,
        title=session.title,
        created_at=session.created_at.isoformat(),
    )


@sessions_router.get("/", response_model=list[SessionInfo])
async def list_sessions(
    companion_id: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SessionInfo]:
    stmt = (
        select(Session, func.count(Message.id).label("message_count"))
        .outerjoin(Message, Message.session_id == Session.id)
        .where(Session.user_id == uuid.UUID(current_user.user_id))
        .group_by(Session.id)
        .order_by(Session.updated_at.desc(), Session.created_at.desc())
    )
    if companion_id is not None:
        stmt = stmt.where(Session.companion_id == companion_id)
    if room_id is not None:
        stmt = stmt.where(Session.room_id == room_id)

    result = await db.execute(stmt)
    return [
        SessionInfo(
            session_id=str(s.id),
            room_id=s.room_id,
            title=s.title,
            created_at=s.created_at.isoformat(),
            message_count=int(message_count or 0),
        )
        for s, message_count in result.all()
    ]


@sessions_router.get("/{session_id}", response_model=SessionInfo)
async def get_session(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionInfo:
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    stmt = (
        select(Session, func.count(Message.id).label("message_count"))
        .outerjoin(Message, Message.session_id == Session.id)
        .where(Session.id == sid, Session.user_id == uuid.UUID(current_user.user_id))
        .group_by(Session.id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    session, message_count = row

    return SessionInfo(
        session_id=str(session.id),
        room_id=session.room_id,
        title=session.title,
        created_at=session.created_at.isoformat(),
        message_count=int(message_count or 0),
    )


@sessions_router.get("/{session_id}/messages", response_model=list[SessionMessageInfo])
async def list_session_messages(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SessionMessageInfo]:
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    session = await _get_owned_session(db, sid, uuid.UUID(current_user.user_id))
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    result = await db.execute(
        select(Message)
        .where(Message.session_id == sid)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    return [
        SessionMessageInfo(
            message_id=str(message.id),
            turn_id=getattr(message, "turn_id", None),
            session_id=str(message.session_id),
            role=message.role,
            content=message.content,
            created_at=message.created_at.isoformat(),
        )
        for message in result.scalars().all()
    ]


@sessions_router.delete("/{session_id}", response_model=SessionDeleteResponse)
async def delete_session(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionDeleteResponse:
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    stmt = select(Session).where(
        Session.id == sid,
        Session.user_id == uuid.UUID(current_user.user_id),
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    try:
        await db.delete(session)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Session deletion failed for %s", session_id)
        raise HTTPException(status_code=500, detail="Session deletion failed")
    return SessionDeleteResponse(session_id=session_id, deleted=True)


async def _get_owned_session(
    db: AsyncSession,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Session | None:
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def _get_companion_orm(
    db: AsyncSession,
    user_id: str,
    companion_id: str,
) -> Companion | None:
    result = await db.execute(
        select(Companion).where(
            Companion.companion_id == companion_id,
            Companion.user_id == uuid.UUID(user_id),
        )
    )
    return result.scalar_one_or_none()


def _seed_session_from_companion(
    db: AsyncSession,
    session_id: uuid.UUID,
    companion: Companion,
    identity: CompanionIdentityModel | None = None,
) -> None:
    """Seed CharacterProfileModel and IdentityStateModel from a companion's profile."""
    profile = companion.to_character_profile()

    db.add(
        CharacterProfileModel(
            session_id=session_id,
            name=profile.name,
            backstory=profile.backstory,
            personality_sliders=dict(profile.personality_sliders),
            humor_style=profile.humor_style,
            communication_style=profile.communication_style,
            favorite_topics=list(profile.favorite_topics),
        )
    )

    from chatbot.soul.persistent_identity import initial_session_identity
    state = initial_session_identity(companion, identity)
    db.add(IdentityStateModel(session_id=session_id, fixed=state.fixed, slow=state.slow, mood=state.mood))
