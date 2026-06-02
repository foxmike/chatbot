"""Session management endpoints for the chatbot engine."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel


class SessionCreateResponse(BaseModel):
    session_id: str


class SessionInfo(BaseModel):
    session_id: str
    created_at: str
    message_count: int


class SessionDeleteResponse(BaseModel):
    session_id: str
    deleted: bool


sessions_store: dict[str, dict] = {}

sessions_router = APIRouter(prefix="/sessions")


@sessions_router.post("/", response_model=SessionCreateResponse)
async def create_session() -> SessionCreateResponse:
    session_id = str(uuid4())
    sessions_store[session_id] = {
        "session_id": session_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "message_count": 0,
    }

    return SessionCreateResponse(session_id=session_id)


@sessions_router.get("/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str) -> SessionInfo:
    session = sessions_store.get(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return SessionInfo(**session)


@sessions_router.delete("/{session_id}", response_model=SessionDeleteResponse)
async def delete_session(session_id: str) -> SessionDeleteResponse:
    if session_id not in sessions_store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    del sessions_store[session_id]
    return SessionDeleteResponse(session_id=session_id, deleted=True)
