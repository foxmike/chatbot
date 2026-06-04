"""Integration tests for authentication, user scoping, and companion management."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pytest
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, status
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

TEST_SECRET_KEY = "test-secret-key-for-integration-tests"
TEST_ALGORITHM = "HS256"
SQLITE_DATABASE_URL = "sqlite+aiosqlite:///file::memory:?cache=shared&uri=true"

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_engine = create_async_engine(SQLITE_DATABASE_URL, echo=False)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

_TABLE_DEFINITIONS = [
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        external_id TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        username TEXT NOT NULL UNIQUE,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS user_sessions (
        session_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        token_hash TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        revoked INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS companions (
        companion_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        name TEXT NOT NULL,
        persona TEXT,
        description TEXT,
        avatar_url TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        companion_id TEXT,
        title TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        mood_state TEXT,
        user_state TEXT,
        conversation_mode TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
]

_TABLE_NAMES = ["sessions", "user_sessions", "companions", "users"]

sessions_store: dict[str, dict[str, Any]] = {}


async def _init_tables() -> None:
    async with _engine.begin() as conn:
        for stmt_text in _TABLE_DEFINITIONS:
            await conn.execute(text(stmt_text))


async def _clear_all_tables() -> None:
    async with _engine.begin() as conn:
        for table in _TABLE_NAMES:
            await conn.execute(text(f"DELETE FROM {table}"))


async def _get_test_db():
    async with _session_factory() as session:
        yield session


class PasswordHasher:
    @staticmethod
    def hash_password(password: str) -> str:
        return _pwd_context.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        return _pwd_context.verify(plain_password, hashed_password)


password_hasher = PasswordHasher()


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta is not None else timedelta(minutes=60)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, TEST_SECRET_KEY, algorithm=TEST_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, TEST_SECRET_KEY, algorithms=[TEST_ALGORITHM])
    except jwt.PyJWTError:
        return None


class CurrentUser(BaseModel):
    user_id: str
    username: str
    email: str


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
    created_at: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class CompanionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    persona: str | None = None
    description: str | None = None
    avatar_url: str | None = None


class CompanionPatchRequest(BaseModel):
    name: str | None = None
    persona: str | None = None
    description: str | None = None
    avatar_url: str | None = None


class CompanionResponse(BaseModel):
    companion_id: str
    user_id: str
    name: str
    persona: str | None
    description: str | None
    avatar_url: str | None
    created_at: str
    updated_at: str | None


class CompanionListResponse(BaseModel):
    companions: list[CompanionResponse]


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


class ChatRequest(BaseModel):
    session_id: str
    message: str
    companion_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    response: str
    step_results: list[dict[str, Any]]


async def get_current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(_get_test_db),
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

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    result = await db.execute(
        text("SELECT revoked FROM user_sessions WHERE token_hash = :token_hash"),
        {"token_hash": token_hash},
    )
    row = result.fetchone()
    if row is not None and row._mapping["revoked"] == 1:  # noqa: SLF001
        raise HTTPException(
            status_code=401,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return CurrentUser(
        user_id=payload["sub"],
        username=payload["username"],
        email=payload["email"],
    )


_auth_router = APIRouter(prefix="/auth", tags=["auth"])


@_auth_router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    body: UserCreateRequest,
    db: AsyncSession = Depends(_get_test_db),
) -> TokenResponse:
    existing = await db.execute(
        text("SELECT id FROM users WHERE email = :email"), {"email": body.email}
    )
    if existing.fetchone() is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    existing = await db.execute(
        text("SELECT id FROM users WHERE username = :username"), {"username": body.username}
    )
    if existing.fetchone() is not None:
        raise HTTPException(status_code=409, detail="Username already taken")

    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        text(
            """INSERT INTO users (id, external_id, display_name, username, email, password_hash, created_at, updated_at)
               VALUES (:id, :ext, :dn, :un, :em, :ph, :ca, :ua)"""
        ),
        {
            "id": user_id, "ext": user_id, "dn": body.username,
            "un": body.username, "em": body.email,
            "ph": password_hasher.hash_password(body.password),
            "ca": now, "ua": now,
        },
    )

    token = create_access_token(
        {"sub": user_id, "username": body.username, "email": body.email}
    )
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=60)).isoformat()
    session_id = str(uuid.uuid4())
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    await db.execute(
        text(
            """INSERT INTO user_sessions (session_id, user_id, token_hash, expires_at, created_at, revoked)
               VALUES (:si, :ui, :th, :ea, :ca, 0)"""
        ),
        {"si": session_id, "ui": user_id, "th": token_hash, "ea": expires_at, "ca": now},
    )
    await db.commit()

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse(user_id=user_id, username=body.username, email=body.email, created_at=now),
    )


@_auth_router.post("/login", response_model=TokenResponse)
async def login(
    body: UserLoginRequest,
    db: AsyncSession = Depends(_get_test_db),
) -> TokenResponse:
    result = await db.execute(
        text("SELECT id, username, email, password_hash, created_at FROM users WHERE email = :email"),
        {"email": body.email},
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    mapping = row._mapping  # noqa: SLF001
    if not password_hasher.verify_password(body.password, mapping["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user_id = mapping["id"]
    token = create_access_token(
        {"sub": user_id, "username": mapping["username"], "email": mapping["email"]}
    )
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=60)).isoformat()
    session_id = str(uuid.uuid4())
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    await db.execute(
        text(
            """INSERT INTO user_sessions (session_id, user_id, token_hash, expires_at, created_at, revoked)
               VALUES (:si, :ui, :th, :ea, :ca, 0)"""
        ),
        {"si": session_id, "ui": user_id, "th": token_hash, "ea": expires_at, "ca": datetime.now(timezone.utc).isoformat()},
    )
    await db.commit()

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse(
            user_id=user_id,
            username=mapping["username"],
            email=mapping["email"],
            created_at=mapping["created_at"],
        ),
    )


@_auth_router.post("/logout")
async def logout(
    current_user: CurrentUser = Depends(get_current_user),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(_get_test_db),
) -> dict[str, str]:
    if authorization is not None and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        await db.execute(
            text("UPDATE user_sessions SET revoked = 1 WHERE token_hash = :th AND revoked = 0"),
            {"th": token_hash},
        )
        await db.commit()
    return {"detail": "Logged out"}


@_auth_router.get("/me", response_model=UserResponse)
async def me(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(_get_test_db),
) -> UserResponse:
    result = await db.execute(
        text("SELECT id, username, email, created_at FROM users WHERE id = :uid"),
        {"uid": current_user.user_id},
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    mapping = row._mapping  # noqa: SLF001
    return UserResponse(
        user_id=mapping["id"],
        username=mapping["username"],
        email=mapping["email"],
        created_at=mapping["created_at"],
    )


_companions_router = APIRouter(prefix="/companions")


def _row_to_companion(mapping: Any) -> CompanionResponse:
    return CompanionResponse(
        companion_id=str(mapping["companion_id"]),
        user_id=str(mapping["user_id"]),
        name=str(mapping["name"]),
        persona=mapping["persona"],
        description=mapping["description"],
        avatar_url=mapping["avatar_url"],
        created_at=str(mapping["created_at"]),
        updated_at=str(mapping["updated_at"]) if mapping["updated_at"] is not None else None,
    )


@_companions_router.post("/", response_model=CompanionResponse, status_code=status.HTTP_201_CREATED)
async def create_companion(
    body: CompanionCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(_get_test_db),
) -> CompanionResponse:
    companion_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        text(
            """INSERT INTO companions (companion_id, user_id, name, persona, description, avatar_url, created_at, updated_at)
               VALUES (:cid, :uid, :nm, :pr, :ds, :av, :ca, :ua)"""
        ),
        {"cid": companion_id, "uid": current_user.user_id, "nm": body.name,
         "pr": body.persona, "ds": body.description, "av": body.avatar_url,
         "ca": now, "ua": now},
    )
    await db.commit()
    return CompanionResponse(
        companion_id=companion_id, user_id=current_user.user_id,
        name=body.name, persona=body.persona, description=body.description,
        avatar_url=body.avatar_url, created_at=now, updated_at=now,
    )


@_companions_router.get("/", response_model=CompanionListResponse)
async def list_companions(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(_get_test_db),
) -> CompanionListResponse:
    result = await db.execute(
        text("SELECT * FROM companions WHERE user_id = :uid"),
        {"uid": current_user.user_id},
    )
    rows = result.fetchall()
    return CompanionListResponse(
        companions=[_row_to_companion(r._mapping) for r in rows]  # noqa: SLF001
    )


@_companions_router.get("/{companion_id}", response_model=CompanionResponse)
async def get_companion(
    companion_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(_get_test_db),
) -> CompanionResponse:
    result = await db.execute(
        text("SELECT * FROM companions WHERE companion_id = :cid"),
        {"cid": companion_id},
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Companion not found")
    mapping = row._mapping  # noqa: SLF001
    if str(mapping["user_id"]) != current_user.user_id:
        raise HTTPException(status_code=404, detail="Companion not found")
    return _row_to_companion(mapping)


@_companions_router.patch("/{companion_id}", response_model=CompanionResponse)
async def patch_companion(
    companion_id: str,
    body: CompanionPatchRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(_get_test_db),
) -> CompanionResponse:
    result = await db.execute(
        text("SELECT * FROM companions WHERE companion_id = :cid"),
        {"cid": companion_id},
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Companion not found")
    mapping = row._mapping  # noqa: SLF001
    if str(mapping["user_id"]) != current_user.user_id:
        raise HTTPException(status_code=404, detail="Companion not found")

    parts: list[str] = []
    params: dict[str, str] = {}
    if body.name is not None:
        parts.append("name = :nm")
        params["nm"] = body.name
    if body.persona is not None:
        parts.append("persona = :pr")
        params["pr"] = body.persona
    if body.description is not None:
        parts.append("description = :ds")
        params["ds"] = body.description
    if body.avatar_url is not None:
        parts.append("avatar_url = :av")
        params["av"] = body.avatar_url

    if parts:
        parts.append("updated_at = :ua")
        params["ua"] = datetime.now(timezone.utc).isoformat()
        params["cid"] = companion_id
        await db.execute(
            text(f"UPDATE companions SET {', '.join(parts)} WHERE companion_id = :cid"),
            params,
        )
        await db.commit()

    result = await db.execute(
        text("SELECT * FROM companions WHERE companion_id = :cid"),
        {"cid": companion_id},
    )
    return _row_to_companion(result.fetchone()._mapping)  # noqa: SLF001


@_companions_router.delete("/{companion_id}")
async def delete_companion(
    companion_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(_get_test_db),
) -> dict[str, str]:
    result = await db.execute(
        text("SELECT * FROM companions WHERE companion_id = :cid"),
        {"cid": companion_id},
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Companion not found")
    mapping = row._mapping  # noqa: SLF001
    if str(mapping["user_id"]) != current_user.user_id:
        raise HTTPException(status_code=404, detail="Companion not found")

    await db.execute(text("DELETE FROM companions WHERE companion_id = :cid"), {"cid": companion_id})
    await db.commit()
    return {"detail": "Companion deleted"}


_sessions_router = APIRouter(prefix="/sessions")


@_sessions_router.post("/", response_model=SessionCreateResponse)
async def create_session(
    body: SessionCreateRequest = SessionCreateRequest(),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(_get_test_db),
) -> SessionCreateResponse:
    companion_id = body.companion_id
    if companion_id is not None:
        result = await db.execute(
            text("SELECT user_id FROM companions WHERE companion_id = :cid"),
            {"cid": companion_id},
        )
        comp_row = result.fetchone()
        if comp_row is None or str(comp_row._mapping["user_id"]) != current_user.user_id:  # noqa: SLF001
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")

    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        text(
            """INSERT INTO sessions (id, user_id, companion_id, created_at, updated_at, is_active)
               VALUES (:id, :uid, :cid, :ca, :ua, 1)"""
        ),
        {"id": session_id, "uid": current_user.user_id, "cid": companion_id, "ca": now, "ua": now},
    )
    await db.commit()
    return SessionCreateResponse(session_id=session_id)


@_sessions_router.get("/", response_model=list[SessionInfo])
async def list_sessions(
    companion_id: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(_get_test_db),
) -> list[SessionInfo]:
    if companion_id is not None:
        result = await db.execute(
            text(
                """SELECT s.*, COUNT(1) as message_count
                   FROM sessions s WHERE s.user_id = :uid AND s.companion_id = :cid
                   GROUP BY s.id ORDER BY s.created_at DESC"""
            ),
            {"uid": current_user.user_id, "cid": companion_id},
        )
    else:
        result = await db.execute(
            text(
                """SELECT s.*, COUNT(1) as message_count FROM sessions s
                   WHERE s.user_id = :uid GROUP BY s.id ORDER BY s.created_at DESC"""
            ),
            {"uid": current_user.user_id},
        )
    rows = result.fetchall()
    return [
        SessionInfo(
            session_id=str(r._mapping["id"]),  # noqa: SLF001
            created_at=str(r._mapping["created_at"]),  # noqa: SLF001
            message_count=r._mapping["message_count"] or 0,  # noqa: SLF001
        )
        for r in rows
    ]


@_sessions_router.get("/{session_id}", response_model=SessionInfo)
async def get_session(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(_get_test_db),
) -> SessionInfo:
    try:
        uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    result = await db.execute(
        text(
            """SELECT s.*, COUNT(1) as message_count FROM sessions s
               WHERE s.id = :sid AND s.user_id = :uid GROUP BY s.id"""
        ),
        {"sid": session_id, "uid": current_user.user_id},
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    mapping = row._mapping  # noqa: SLF001
    return SessionInfo(
        session_id=str(mapping["id"]),
        created_at=str(mapping["created_at"]),
        message_count=mapping["message_count"] or 0,
    )


@_sessions_router.delete("/{session_id}", response_model=SessionDeleteResponse)
async def delete_session(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(_get_test_db),
) -> SessionDeleteResponse:
    try:
        uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    result = await db.execute(
        text("SELECT id FROM sessions WHERE id = :sid AND user_id = :uid"),
        {"sid": session_id, "uid": current_user.user_id},
    )
    if result.fetchone() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    await db.execute(text("DELETE FROM sessions WHERE id = :sid"), {"sid": session_id})
    await db.commit()
    return SessionDeleteResponse(session_id=session_id, deleted=True)


_chat_router = APIRouter(prefix="/chat")


@_chat_router.post("/", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(_get_test_db),
) -> ChatResponse:
    try:
        uuid.UUID(body.session_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    result = await db.execute(
        text(
            "SELECT id, user_id, companion_id FROM sessions WHERE id = :sid AND user_id = :uid"
        ),
        {"sid": body.session_id, "uid": current_user.user_id},
    )
    session_row = result.fetchone()
    if session_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    companion_id = body.companion_id
    if companion_id is not None:
        comp_result = await db.execute(
            text("SELECT user_id FROM companions WHERE companion_id = :cid"),
            {"cid": companion_id},
        )
        comp_row = comp_result.fetchone()
        if comp_row is None or str(comp_row._mapping["user_id"]) != current_user.user_id:  # noqa: SLF001
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Companion not found")

    response_text = f"Response to: {body.message}"
    return ChatResponse(
        session_id=body.session_id,
        response=response_text,
        step_results=[{"name": "test_step", "duration_ms": 1.0, "success": True}],
    )


def _build_test_app() -> FastAPI:
    app = FastAPI()
    sessions_store.clear()

    app.include_router(_auth_router)
    app.include_router(_companions_router)
    app.include_router(_sessions_router)
    app.include_router(_chat_router)

    return app


@pytest.fixture(scope="session", autouse=True)
def _initialize_test_db():
    import asyncio
    asyncio.get_event_loop().run_until_complete(_init_tables())


@pytest.fixture(autouse=True)
async def _clean_db():
    yield
    await _clear_all_tables()


@pytest.fixture
def test_app() -> FastAPI:
    return _build_test_app()


@pytest.fixture
def test_client(test_app: FastAPI) -> TestClient:
    with TestClient(test_app, follow_redirects=True) as client:
        yield client


def _register_and_login(
    client: TestClient,
    username: str = "testuser",
    email: str = "test@example.com",
    password: str = "securepass123",
) -> tuple[dict, str]:
    signup_resp = client.post(
        "/auth/signup",
        json={"username": username, "email": email, "password": password},
    )
    assert signup_resp.status_code == 201, f"Signup failed: {signup_resp.text}"
    data = signup_resp.json()
    return data, data["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestAuthSignup:
    def test_success(self, test_client: TestClient) -> None:
        response = test_client.post(
            "/auth/signup",
            json={
                "username": "alice",
                "email": "alice@example.com",
                "password": "securepass123",
            },
        )
        assert response.status_code == 201
        payload = response.json()
        assert "access_token" in payload
        assert payload["token_type"] == "bearer"
        assert payload["user"]["username"] == "alice"
        assert payload["user"]["email"] == "alice@example.com"

    def test_duplicate_email(self, test_client: TestClient) -> None:
        test_client.post(
            "/auth/signup",
            json={
                "username": "bob1",
                "email": "bob@example.com",
                "password": "securepass123",
            },
        )
        response = test_client.post(
            "/auth/signup",
            json={
                "username": "bob2",
                "email": "bob@example.com",
                "password": "securepass123",
            },
        )
        assert response.status_code == 409

    def test_duplicate_username(self, test_client: TestClient) -> None:
        test_client.post(
            "/auth/signup",
            json={
                "username": "carol",
                "email": "carol1@example.com",
                "password": "securepass123",
            },
        )
        response = test_client.post(
            "/auth/signup",
            json={
                "username": "carol",
                "email": "carol2@example.com",
                "password": "securepass123",
            },
        )
        assert response.status_code == 409

    def test_short_password(self, test_client: TestClient) -> None:
        response = test_client.post(
            "/auth/signup",
            json={
                "username": "dave",
                "email": "dave@example.com",
                "password": "short",
            },
        )
        assert response.status_code == 422

    def test_short_username(self, test_client: TestClient) -> None:
        response = test_client.post(
            "/auth/signup",
            json={
                "username": "ab",
                "email": "eve@example.com",
                "password": "securepass123",
            },
        )
        assert response.status_code == 422

    def test_invalid_email(self, test_client: TestClient) -> None:
        response = test_client.post(
            "/auth/signup",
            json={
                "username": "frank",
                "email": "not-an-email",
                "password": "securepass123",
            },
        )
        assert response.status_code == 422


class TestAuthLogin:
    def test_success(self, test_client: TestClient) -> None:
        test_client.post(
            "/auth/signup",
            json={
                "username": "logintest",
                "email": "logintest@example.com",
                "password": "securepass123",
            },
        )
        response = test_client.post(
            "/auth/login",
            json={"email": "logintest@example.com", "password": "securepass123"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert "access_token" in payload
        assert payload["token_type"] == "bearer"
        assert payload["user"]["username"] == "logintest"

    def test_wrong_password(self, test_client: TestClient) -> None:
        test_client.post(
            "/auth/signup",
            json={
                "username": "wrongpw",
                "email": "wrongpw@example.com",
                "password": "securepass123",
            },
        )
        response = test_client.post(
            "/auth/login",
            json={"email": "wrongpw@example.com", "password": "wrongpassword"},
        )
        assert response.status_code == 401

    def test_nonexistent_email(self, test_client: TestClient) -> None:
        response = test_client.post(
            "/auth/login",
            json={"email": "noone@example.com", "password": "somepass"},
        )
        assert response.status_code == 401

    def test_expired_token(self, test_client: TestClient) -> None:
        _, token = _register_and_login(test_client, "expiredtest", "expired@example.com")
        expired_token = create_access_token(
            {"sub": "fake-id", "username": "expiredtest", "email": "expired@example.com"},
            expires_delta=timedelta(seconds=-1),
        )
        response = test_client.get("/auth/me", headers=_auth_headers(expired_token))
        assert response.status_code == 401


class TestAuthLogout:
    def test_success(self, test_client: TestClient) -> None:
        _, token = _register_and_login(test_client, "logoutuser", "logout@example.com")
        response = test_client.post("/auth/logout", headers=_auth_headers(token))
        assert response.status_code == 200
        assert response.json()["detail"] == "Logged out"

    def test_revoked_token_rejected(self, test_client: TestClient) -> None:
        _, token = _register_and_login(test_client, "revokeduser", "revoked@example.com")
        logout_resp = test_client.post("/auth/logout", headers=_auth_headers(token))
        assert logout_resp.status_code == 200
        response = test_client.get("/auth/me", headers=_auth_headers(token))
        assert response.status_code == 401

    def test_unauthenticated(self, test_client: TestClient) -> None:
        response = test_client.post("/auth/logout")
        assert response.status_code == 401


class TestAuthMe:
    def test_authenticated(self, test_client: TestClient) -> None:
        _, token = _register_and_login(test_client, "meuser", "me@example.com")
        response = test_client.get("/auth/me", headers=_auth_headers(token))
        assert response.status_code == 200
        payload = response.json()
        assert payload["username"] == "meuser"
        assert payload["email"] == "me@example.com"
        assert "user_id" in payload
        assert "created_at" in payload

    def test_unauthenticated(self, test_client: TestClient) -> None:
        response = test_client.get("/auth/me")
        assert response.status_code == 401


class TestGetCurrentUser:
    def test_valid_token(self, test_client: TestClient) -> None:
        _, token = _register_and_login(test_client, "cuuser", "cu@example.com")
        response = test_client.get("/auth/me", headers=_auth_headers(token))
        assert response.status_code == 200

    def test_missing_header(self, test_client: TestClient) -> None:
        response = test_client.get("/auth/me")
        assert response.status_code == 401

    def test_malformed_header(self, test_client: TestClient) -> None:
        response = test_client.get(
            "/auth/me", headers={"Authorization": "InvalidFormat"}
        )
        assert response.status_code == 401

    def test_invalid_token(self, test_client: TestClient) -> None:
        response = test_client.get(
            "/auth/me", headers=_auth_headers("garbage-token-value")
        )
        assert response.status_code == 401


class TestCompanionCRUD:
    @staticmethod
    def _create(c: TestClient, t: str, name: str, persona: str | None = None) -> dict:
        resp = c.post(
            "/companions/",
            json={"name": name, "persona": persona},
            headers=_auth_headers(t),
        )
        assert resp.status_code == 201
        return resp.json()

    def test_create(self, test_client: TestClient) -> None:
        _, token = _register_and_login(test_client, "compcreate", "compcreate@example.com")
        resp = test_client.post(
            "/companions/",
            json={"name": "My Companion", "persona": "Friendly helper"},
            headers=_auth_headers(token),
        )
        assert resp.status_code == 201
        payload = resp.json()
        assert payload["name"] == "My Companion"
        assert payload["persona"] == "Friendly helper"
        assert "companion_id" in payload

    def test_list(self, test_client: TestClient) -> None:
        _, token = _register_and_login(test_client, "complist", "complist@example.com")
        self._create(test_client, token, "Comp A")
        self._create(test_client, token, "Comp B")
        resp = test_client.get("/companions/", headers=_auth_headers(token))
        assert resp.status_code == 200
        assert len(resp.json()["companions"]) == 2

    def test_get(self, test_client: TestClient) -> None:
        _, token = _register_and_login(test_client, "compget", "compget@example.com")
        created = self._create(test_client, token, "Target")
        resp = test_client.get(
            f"/companions/{created['companion_id']}", headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Target"

    def test_get_wrong_user(self, test_client: TestClient) -> None:
        _, token_a = _register_and_login(test_client, "compwra", "compwra@example.com")
        _, token_b = _register_and_login(test_client, "compwrb", "compwrb@example.com")
        created = self._create(test_client, token_a, "A's Comp")
        resp = test_client.get(
            f"/companions/{created['companion_id']}", headers=_auth_headers(token_b),
        )
        assert resp.status_code == 404

    def test_update(self, test_client: TestClient) -> None:
        _, token = _register_and_login(test_client, "compupd", "compupd@example.com")
        created = self._create(test_client, token, "Original", "old persona")
        resp = test_client.patch(
            f"/companions/{created['companion_id']}",
            json={"name": "Updated", "persona": "new persona"},
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"
        assert resp.json()["persona"] == "new persona"

    def test_update_wrong_user(self, test_client: TestClient) -> None:
        _, token_a = _register_and_login(test_client, "compupwa", "compupwa@example.com")
        _, token_b = _register_and_login(test_client, "compupwb", "compupwb@example.com")
        created = self._create(test_client, token_a, "A's Comp")
        resp = test_client.patch(
            f"/companions/{created['companion_id']}",
            json={"name": "Hijacked"},
            headers=_auth_headers(token_b),
        )
        assert resp.status_code == 404

    def test_delete(self, test_client: TestClient) -> None:
        _, token = _register_and_login(test_client, "compdel", "compdel@example.com")
        created = self._create(test_client, token, "To Delete")
        resp = test_client.delete(
            f"/companions/{created['companion_id']}", headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        assert resp.json()["detail"] == "Companion deleted"
        get_resp = test_client.get(
            f"/companions/{created['companion_id']}", headers=_auth_headers(token),
        )
        assert get_resp.status_code == 404

    def test_delete_wrong_user(self, test_client: TestClient) -> None:
        _, token_a = _register_and_login(test_client, "compdelwa", "compdelwa@example.com")
        _, token_b = _register_and_login(test_client, "compdelwb", "compdelwb@example.com")
        created = self._create(test_client, token_a, "A's Delete Target")
        resp = test_client.delete(
            f"/companions/{created['companion_id']}", headers=_auth_headers(token_b),
        )
        assert resp.status_code == 404


class TestSessionScoping:
    @staticmethod
    def _create(client: TestClient, token: str, companion_id: str | None = None) -> str:
        body: dict[str, str | None] = {}
        if companion_id is not None:
            body["companion_id"] = companion_id
        resp = client.post("/sessions/", json=body, headers=_auth_headers(token))
        assert resp.status_code == 200
        return resp.json()["session_id"]

    def test_create(self, test_client: TestClient) -> None:
        _, token = _register_and_login(test_client, "sesscreate", "sesscreate@example.com")
        resp = test_client.post("/sessions/", json={}, headers=_auth_headers(token))
        assert resp.status_code == 200
        assert "session_id" in resp.json()

    def test_list_user_only(self, test_client: TestClient) -> None:
        _, token_a = _register_and_login(test_client, "sesslista", "sesslista@example.com")
        _, token_b = _register_and_login(test_client, "sesslistb", "sesslistb@example.com")
        self._create(test_client, token_a)
        self._create(test_client, token_b)

        resp = test_client.get("/sessions/", headers=_auth_headers(token_a))
        assert resp.status_code == 200
        assert len(resp.json()) == 1

        resp = test_client.get("/sessions/", headers=_auth_headers(token_b))
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_get_own(self, test_client: TestClient) -> None:
        _, token = _register_and_login(test_client, "sessget", "sessget@example.com")
        session_id = self._create(test_client, token)
        resp = test_client.get(
            f"/sessions/{session_id}", headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        assert resp.json()["session_id"] == session_id

    def test_get_other_user(self, test_client: TestClient) -> None:
        _, token_a = _register_and_login(test_client, "sessotha", "sessotha@example.com")
        _, token_b = _register_and_login(test_client, "sessothb", "sessothb@example.com")
        session_id = self._create(test_client, token_a)
        resp = test_client.get(
            f"/sessions/{session_id}", headers=_auth_headers(token_b),
        )
        assert resp.status_code == 404

    def test_delete_own(self, test_client: TestClient) -> None:
        _, token = _register_and_login(test_client, "sessdelown", "sessdelown@example.com")
        session_id = self._create(test_client, token)
        resp = test_client.delete(
            f"/sessions/{session_id}", headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_other_user(self, test_client: TestClient) -> None:
        _, token_a = _register_and_login(test_client, "sessdela", "sessdela@example.com")
        _, token_b = _register_and_login(test_client, "sessdelb", "sessdelb@example.com")
        session_id = self._create(test_client, token_a)
        resp = test_client.delete(
            f"/sessions/{session_id}", headers=_auth_headers(token_b),
        )
        assert resp.status_code == 404

    def test_create_with_companion(self, test_client: TestClient) -> None:
        _, token = _register_and_login(test_client, "sesscomp", "sesscomp@example.com")
        comp_resp = test_client.post(
            "/companions/",
            json={"name": "Session Buddy", "persona": "Friendly"},
            headers=_auth_headers(token),
        )
        assert comp_resp.status_code == 201
        companion_id = comp_resp.json()["companion_id"]

        resp = test_client.post(
            "/sessions/",
            json={"companion_id": companion_id},
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        session_id = resp.json()["session_id"]

        get_resp = test_client.get(
            f"/sessions/{session_id}", headers=_auth_headers(token),
        )
        assert get_resp.status_code == 200


class TestChatScoping:
    @staticmethod
    def _session(client: TestClient, token: str, comp_id: str | None = None) -> str:
        body: dict[str, str | None] = {}
        if comp_id is not None:
            body["companion_id"] = comp_id
        resp = client.post("/sessions/", json=body, headers=_auth_headers(token))
        assert resp.status_code == 200
        return resp.json()["session_id"]

    @staticmethod
    def _companion(client: TestClient, token: str, name: str) -> str:
        resp = client.post(
            "/companions/", json={"name": name}, headers=_auth_headers(token),
        )
        assert resp.status_code == 201
        return resp.json()["companion_id"]

    def test_chat_own_session(self, test_client: TestClient) -> None:
        _, token = _register_and_login(test_client, "chatown", "chatown@example.com")
        session_id = self._session(test_client, token)
        resp = test_client.post(
            "/chat/",
            json={"session_id": session_id, "message": "Hello"},
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        assert resp.json()["session_id"] == session_id

    def test_chat_other_user_session(self, test_client: TestClient) -> None:
        _, token_a = _register_and_login(test_client, "chatotha", "chatotha@example.com")
        _, token_b = _register_and_login(test_client, "chatothb", "chatothb@example.com")
        session_id = self._session(test_client, token_a)
        resp = test_client.post(
            "/chat/",
            json={"session_id": session_id, "message": "Hello"},
            headers=_auth_headers(token_b),
        )
        assert resp.status_code == 404

    def test_chat_with_valid_companion(self, test_client: TestClient) -> None:
        _, token = _register_and_login(test_client, "chatvalidc", "chatvalidc@example.com")
        companion_id = self._companion(test_client, token, "Chat Buddy")
        session_id = self._session(test_client, token)
        resp = test_client.post(
            "/chat/",
            json={
                "session_id": session_id,
                "message": "Hi",
                "companion_id": companion_id,
            },
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200

    def test_chat_with_invalid_companion(self, test_client: TestClient) -> None:
        _, token = _register_and_login(test_client, "chatinvcomp", "chatinvcomp@example.com")
        session_id = self._session(test_client, token)
        resp = test_client.post(
            "/chat/",
            json={
                "session_id": session_id,
                "message": "Hi",
                "companion_id": "nonexistent-id",
            },
            headers=_auth_headers(token),
        )
        assert resp.status_code == 404

    def test_chat_with_other_user_companion(self, test_client: TestClient) -> None:
        _, token_a = _register_and_login(test_client, "chatcompa", "chatcompa@example.com")
        _, token_b = _register_and_login(test_client, "chatcompb", "chatcompb@example.com")
        companion_id = self._companion(test_client, token_a, "A's Companion")
        session_id = self._session(test_client, token_b)
        resp = test_client.post(
            "/chat/",
            json={
                "session_id": session_id,
                "message": "Hi",
                "companion_id": companion_id,
            },
            headers=_auth_headers(token_b),
        )
        assert resp.status_code == 404


class TestDataIsolation:
    @staticmethod
    def _companion(client: TestClient, token: str, name: str) -> str:
        resp = client.post(
            "/companions/", json={"name": name}, headers=_auth_headers(token),
        )
        assert resp.status_code == 201
        return resp.json()["companion_id"]

    @staticmethod
    def _session(client: TestClient, token: str) -> str:
        resp = client.post("/sessions/", json={}, headers=_auth_headers(token))
        assert resp.status_code == 200
        return resp.json()["session_id"]

    def _setup_user(
        self, client: TestClient, username: str, email: str,
    ) -> tuple[str, list[str], list[str]]:
        _, token = _register_and_login(client, username, email)
        c1 = self._companion(client, token, f"{username}-comp-1")
        c2 = self._companion(client, token, f"{username}-comp-2")
        s1 = self._session(client, token)
        s2 = self._session(client, token)
        return token, [c1, c2], [s1, s2]

    def test_users_cannot_see_each_others_data(self, test_client: TestClient) -> None:
        token_a, comps_a, sess_a = self._setup_user(test_client, "isola", "isola@example.com")
        token_b, comps_b, sess_b = self._setup_user(test_client, "isolb", "isolb@example.com")

        for comp_id in comps_a:
            resp = test_client.get(
                f"/companions/{comp_id}", headers=_auth_headers(token_b),
            )
            assert resp.status_code == 404

        for comp_id in comps_b:
            resp = test_client.get(
                f"/companions/{comp_id}", headers=_auth_headers(token_a),
            )
            assert resp.status_code == 404

        for s_id in sess_a:
            resp = test_client.get(
                f"/sessions/{s_id}", headers=_auth_headers(token_b),
            )
            assert resp.status_code == 404

        for s_id in sess_b:
            resp = test_client.get(
                f"/sessions/{s_id}", headers=_auth_headers(token_a),
            )
            assert resp.status_code == 404

        resp_a = test_client.get("/sessions/", headers=_auth_headers(token_a))
        assert resp_a.status_code == 200
        assert len(resp_a.json()) == 2

        resp_b = test_client.get("/sessions/", headers=_auth_headers(token_b))
        assert resp_b.status_code == 200
        assert len(resp_b.json()) == 2

        comps_a_resp = test_client.get("/companions/", headers=_auth_headers(token_a))
        assert len(comps_a_resp.json()["companions"]) == 2

        comps_b_resp = test_client.get("/companions/", headers=_auth_headers(token_b))
        assert len(comps_b_resp.json()["companions"]) == 2

        for comp_id in comps_a:
            resp = test_client.patch(
                f"/companions/{comp_id}",
                json={"name": "hacked"},
                headers=_auth_headers(token_b),
            )
            assert resp.status_code == 404

        for comp_id in comps_b:
            resp = test_client.delete(
                f"/companions/{comp_id}", headers=_auth_headers(token_a),
            )
            assert resp.status_code == 404

    def test_companion_isolation(self, test_client: TestClient) -> None:
        _, token_a = _register_and_login(test_client, "compiso_a", "compiso_a@example.com")
        _, token_b = _register_and_login(test_client, "compiso_b", "compiso_b@example.com")
        comp_a = self._companion(test_client, token_a, "Alpha Companion")
        comp_b = self._companion(test_client, token_b, "Beta Companion")

        resp = test_client.get(
            f"/companions/{comp_a}", headers=_auth_headers(token_a),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Alpha Companion"

        resp = test_client.get(
            f"/companions/{comp_b}", headers=_auth_headers(token_b),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Beta Companion"

        resp = test_client.get(
            f"/companions/{comp_a}", headers=_auth_headers(token_b),
        )
        assert resp.status_code == 404

        resp = test_client.get(
            f"/companions/{comp_b}", headers=_auth_headers(token_a),
        )
        assert resp.status_code == 404

        list_a = test_client.get("/companions/", headers=_auth_headers(token_a))
        assert len(list_a.json()["companions"]) == 1

        list_b = test_client.get("/companions/", headers=_auth_headers(token_b))
        assert len(list_b.json()["companions"]) == 1