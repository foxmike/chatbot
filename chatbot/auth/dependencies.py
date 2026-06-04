"""FastAPI dependencies for authentication — current_user extraction and validation."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel

from chatbot.auth.security import decode_access_token


class CurrentUser(BaseModel):
    user_id: str
    username: str
    email: str


async def get_current_user(
    authorization: str | None = Header(default=None),
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

    return CurrentUser(
        user_id=payload["sub"],
        username=payload["username"],
        email=payload["email"],
    )


async def get_current_user_id(
    current_user: CurrentUser = Depends(get_current_user),
) -> str:
    return current_user.user_id