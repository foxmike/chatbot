"""Authentication security primitives — password hashing, JWT token issue and verify."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext
from jwt import PyJWTError

SECRET_KEY: str = os.environ.get("CHATBOT_SECRET_KEY", secrets.token_hex(32))
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.environ.get("CHATBOT_TOKEN_EXPIRE_MINUTES", 60))
ALGORITHM: str = "HS256"

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class PasswordHasher:

    @staticmethod
    def hash_password(password: str) -> str:
        return _pwd_context.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        return _pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta is not None else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except PyJWTError:
        return None