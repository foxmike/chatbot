"""Session management endpoints for the chatbot engine."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.db import get_db
from chatbot.db.models import Companion, CharacterProfileModel, IdentityStateModel, Message, Session
from chatbot.soul.evolution import IdentityState


class SessionCreateRequest(BaseModel):
    companion_id: str | None = None
    title: str | None = None


class SessionCreateResponse(BaseModel):
    session_id: str
    title: str | None = None
    created_at: str | None = None


class SessionInfo(BaseModel):
    session_id: str
    title: str | None = None
    created_at: str
    message_count: int


class SessionMessageInfo(BaseModel):
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
        title=body.title or "New Chat",
    )
    db.add(session)

    if companion_orm is not None:
        _seed_session_from_companion(db, session.id, companion_orm)

    await db.commit()
    await db.refresh(session)
    return SessionCreateResponse(
        session_id=str(session.id),
        title=session.title,
        created_at=session.created_at.isoformat(),
    )


@sessions_router.get("/", response_model=list[SessionInfo])
async def list_sessions(
    companion_id: str | None = Query(default=None),
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

    result = await db.execute(stmt)
    return [
        SessionInfo(
            session_id=str(s.id),
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

    messages_result = await db.execute(select(Message).where(Message.session_id == sid))
    for message in messages_result.scalars().all():
        await db.delete(message)
    await db.delete(session)
    await db.commit()
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


# Communication-style weights mirror IdentityState evolution constants.
_COMM_STYLE_WEIGHTS: dict[str, float] = {
    "concise": 0.35,
    "verbose": 0.7,
    "storyteller": 0.65,
    "question_driven": 0.55,
    "aphoristic": 0.45,
}


def _seed_session_from_companion(
    db: AsyncSession,
    session_id: uuid.UUID,
    companion: Companion,
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

    base = IdentityState.defaults()
    fixed = {
        **base.fixed,
        "name": profile.name,
        "backstory": profile.backstory,
        "humor_style": profile.humor_style,
    }
    slow = {
        **base.slow,
        "communication_style_weight": _COMM_STYLE_WEIGHTS.get(
            profile.communication_style, 0.5
        ),
        "favorite_topics": list(profile.favorite_topics),
        **{k: float(v) for k, v in profile.personality_sliders.items()},
    }
    db.add(
        IdentityStateModel(
            session_id=session_id,
            fixed=fixed,
            slow=slow,
            mood={**base.mood},
        )
    )
