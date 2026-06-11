"""SQLAlchemy models for user accounts and sessions."""

from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, EmailStr, Field


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserLoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    user_id: str
    username: str
    email: str
    adaptive_verbosity: float | None = None
    created_at: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


AUTH_MODEL_DEFINITIONS: dict[str, dict[str, dict]] = {
    "users": {
        "user_id": {"type": "String(36)", "primary_key": True, "default": uuid4},
        "username": {"type": "String(64)", "unique": True, "nullable": False},
        "email": {"type": "String(255)", "unique": True, "nullable": False},
        "password_hash": {"type": "String(128)", "nullable": False},
        "adaptive_verbosity": {"type": "Float", "nullable": True},
        "created_at": {"type": "DateTime", "default": "func.now()"},
        "updated_at": {"type": "DateTime", "onupdate": "func.now()"},
    },
    "user_sessions": {
        "session_id": {"type": "String(36)", "primary_key": True, "default": uuid4},
        "user_id": {
            "type": "String(36)",
            "foreign_key": "users.user_id",
            "nullable": False,
        },
        "token_hash": {"type": "String(128)", "nullable": False},
        "expires_at": {"type": "DateTime", "nullable": False},
        "created_at": {"type": "DateTime", "default": "func.now()"},
        "revoked": {"type": "Boolean", "default": False},
    },
}


__all__ = [
    "AUTH_MODEL_DEFINITIONS",
    "TokenResponse",
    "UserCreateRequest",
    "UserLoginRequest",
    "UserResponse",
]
