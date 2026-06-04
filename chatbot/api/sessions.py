"""Session management endpoints for the chatbot engine."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.companions.companion import CompanionManager
from chatbot.db import get_db
from chatbot.db.models import Session


class SessionCreateRequest(BaseModel):
    companion_id: str | None = None


class SessionCreateResponse(BaseModel):
    session_id: str


class SessionInfo(BaseModel):
    session_id: str
    created_at: str
    message_count: int


class SessionDeleteResponse(BaseModel):
    session_id: str
    deleted: bool


sessions_router = APIRouter(prefix="/sessions")


@sessions_router.post("/", response_model=SessionCreateResponse)
async def create_session(
    body: SessionCreateRequest = SessionCreateRequest(),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionCreateResponse:
    companion_id = body.companion_id
    if companion_id is not None:
        companion_mgr = CompanionManager(db)
        companion = await companion_mgr.get(current_user.user_id, companion_id)
        if companion is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Companion not found",
            )

    session = Session(
        user_id=uuid.UUID(current_user.user_id),
        companion_id=companion_id,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return SessionCreateResponse(session_id=str(session.id))


@sessions_router.get("/", response_model=list[SessionInfo])
async def list_sessions(
    companion_id: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SessionInfo]:
    stmt = (
        select(Session)
        .where(Session.user_id == uuid.UUID(current_user.user_id))
        .options(selectinload(Session.messages))
        .order_by(Session.created_at.desc())
    )
    if companion_id is not None:
        stmt = stmt.where(Session.companion_id == companion_id)

    result = await db.execute(stmt)
    sessions = result.scalars().all()
    return [
        SessionInfo(
            session_id=str(s.id),
            created_at=s.created_at.isoformat(),
            message_count=len(s.messages),
        )
        for s in sessions
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
        select(Session)
        .where(Session.id == sid, Session.user_id == uuid.UUID(current_user.user_id))
        .options(selectinload(Session.messages))
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return SessionInfo(
        session_id=str(session.id),
        created_at=session.created_at.isoformat(),
        message_count=len(session.messages),
    )


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

    await db.delete(session)
    await db.commit()
    return SessionDeleteResponse(session_id=session_id, deleted=True)