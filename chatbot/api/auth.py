"""Authentication API endpoints — signup, login, logout."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.auth.models import TokenResponse, UserCreateRequest, UserLoginRequest, UserResponse
from chatbot.auth.security import ACCESS_TOKEN_EXPIRE_MINUTES, PasswordHasher, create_access_token
from chatbot.db import get_db
from chatbot.db.models import User, UserSession

auth_router = APIRouter(prefix="/auth", tags=["auth"])
password_hasher = PasswordHasher()


def _user_to_response(user: User) -> UserResponse:
    return UserResponse(
        user_id=str(user.id),
        username=user.username,
        email=user.email,
        adaptive_verbosity=user.adaptive_verbosity,
        created_at=user.created_at.isoformat(),
    )


@auth_router.post(
    "/signup",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def signup(
    body: UserCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    existing = await db.execute(sa.select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    existing = await db.execute(sa.select(User).where(User.username == body.username))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Username already taken")

    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        external_id=str(user_id),
        display_name=body.username,
        username=body.username,
        email=body.email,
        password_hash=password_hasher.hash_password(body.password),
    )
    db.add(user)

    token = create_access_token(
        {"sub": str(user_id), "username": body.username, "email": body.email}
    )
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    session = UserSession(
        user_id=user_id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=expires_at,
    )
    db.add(session)
    await db.commit()
    await db.refresh(user)

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=_user_to_response(user),
    )


@auth_router.post("/login", response_model=TokenResponse)
async def login(
    body: UserLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    result = await db.execute(sa.select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not password_hasher.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(
        {"sub": str(user.id), "username": user.username, "email": user.email}
    )
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    session = UserSession(
        user_id=user.id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=expires_at,
    )
    db.add(session)
    await db.commit()

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=_user_to_response(user),
    )


@auth_router.post("/logout")
async def logout(
    current_user: CurrentUser = Depends(get_current_user),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    if authorization is not None and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        result = await db.execute(
            sa.select(UserSession).where(
                UserSession.token_hash == token_hash,
                UserSession.revoked == False,
            )
        )
        session = result.scalar_one_or_none()
        if session is not None:
            session.revoked = True
            await db.commit()

    return {"detail": "Logged out"}


@auth_router.get("/me", response_model=UserResponse)
async def me(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    result = await db.execute(
        sa.select(User).where(User.id == uuid.UUID(current_user.user_id))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_to_response(user)
