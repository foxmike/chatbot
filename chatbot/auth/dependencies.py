"""FastAPI dependencies for authentication — current_user extraction and validation."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.auth.security import decode_access_token
from chatbot.db import get_db
from chatbot.db.models import UserSession


class CurrentUser(BaseModel):
    user_id: str
    username: str
    email: str


async def get_current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.removeprefix("Bearer ")
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = uuid.UUID(payload["sub"])
        user = CurrentUser(
            user_id=str(user_id), username=payload["username"], email=payload["email"]
        )
    except (KeyError, ValueError, TypeError, AttributeError):
        raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})

    result = await db.execute(
        sa.select(UserSession.session_id).where(
            UserSession.user_id == user_id,
            UserSession.token_hash == hashlib.sha256(token.encode()).hexdigest(),
            UserSession.revoked.is_(False),
            UserSession.expires_at > datetime.now(timezone.utc),
        ).limit(1)
    )
    active = result.scalar_one_or_none()
    if active is None:
        raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    return user


async def get_current_user_id(
    current_user: CurrentUser = Depends(get_current_user),
) -> str:
    return current_user.user_id
