"""Restart persistence integration tests.

Verify that core chatbot data (sessions, messages, companions, semantic facts)
survives application restart by writing through one database session and
reading back through a newly-created session.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

SQLITE_DATABASE_URL = "sqlite+aiosqlite:///file::memory:?cache=shared&uri=true"

_engine = create_async_engine(SQLITE_DATABASE_URL, echo=False)
_session_factory = async_sessionmaker(
    _engine, class_=AsyncSession, expire_on_commit=False
)

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
    """CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id),
        user_id TEXT REFERENCES users(id),
        companion_id TEXT,
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
        content TEXT NOT NULL,
        token_count INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS semantic_facts (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        fact_text TEXT NOT NULL,
        source_message_id TEXT REFERENCES messages(id),
        source_type TEXT NOT NULL DEFAULT 'inferred'
            CHECK (source_type IN ('explicit', 'inferred')),
        source_turn_id TEXT,
        confidence REAL NOT NULL DEFAULT 1.0,
        privacy_level TEXT NOT NULL DEFAULT 'temporary'
            CHECK (privacy_level IN ('private', 'temporary', 'permanent')),
        pinned INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS rooms (
        room_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        companion_id TEXT,
        room_type TEXT NOT NULL,
        display_name TEXT NOT NULL,
        memory_persist_to_durable INTEGER NOT NULL DEFAULT 1,
        memory_scope TEXT NOT NULL DEFAULT 'room',
        memory_max_context_turns INTEGER NOT NULL DEFAULT 50,
        memory_include_embeddings INTEGER NOT NULL DEFAULT 1,
        scene_id TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        extra_metadata TEXT
    )""",
]

_TABLE_NAMES = [
    "semantic_facts",
    "messages",
    "sessions",
    "companions",
    "rooms",
    "users",
]

# ── helpers ──────────────────────────────────────────────────────────────────

_NOW = datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


def _user_row(
    *,
    user_id: str,
    username: str | None = None,
    email: str | None = None,
) -> dict[str, Any]:
    ident = username or f"user_{user_id[:8]}"
    return {
        "id": user_id,
        "external_id": f"ext_{user_id[:8]}",
        "display_name": ident.capitalize(),
        "username": ident.lower(),
        "email": email or f"{ident.lower()}@test.example",
        "password_hash": f"hash_{user_id[:8]}",
    }


def _fact_row(
    *,
    user_id: str,
    fact_text: str,
    source_type: str = "explicit",
    privacy_level: str = "permanent",
    confidence: float = 1.0,
    pinned: bool = False,
) -> dict[str, Any]:
    return {
        "id": _uid(),
        "user_id": user_id,
        "fact_text": fact_text,
        "source_message_id": None,
        "source_type": source_type,
        "source_turn_id": None,
        "confidence": confidence,
        "privacy_level": privacy_level,
        "pinned": 1 if pinned else 0,
    }


async def _init_tables() -> None:
    async with _engine.begin() as conn:
        for stmt_text in _TABLE_DEFINITIONS:
            await conn.execute(text(stmt_text))


async def _clear_all_tables() -> None:
    async with _engine.begin() as conn:
        for table in reversed(_TABLE_NAMES):
            await conn.execute(text(f"DELETE FROM {table}"))


async def _new_session() -> AsyncSession:
    return _session_factory()


def _make_session_dict(
    *,
    session_id: str,
    user_id: str,
    title: str = "Test Chat",
    companion_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": session_id,
        "user_id": user_id,
        "companion_id": companion_id,
        "title": title,
        "is_active": 1,
        "mood_state": None,
        "user_state": None,
        "conversation_mode": None,
    }


def _make_message_dict(
    *,
    message_id: str,
    session_id: str,
    user_id: str | None,
    role: str,
    content: str,
    companion_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": message_id,
        "session_id": session_id,
        "user_id": user_id,
        "companion_id": companion_id,
        "role": role,
        "content": content,
        "token_count": None,
    }


def _companion_dict(
    *,
    companion_id: str,
    user_id: str,
    name: str,
    persona: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    return {
        "companion_id": companion_id,
        "user_id": user_id,
        "name": name,
        "persona": persona,
        "description": description,
        "avatar_url": None,
    }


async def _count_rows(db: AsyncSession, table: str) -> int:
    result = await db.execute(text(f"SELECT COUNT(*) FROM {table}"))
    row = result.scalar()
    return int(row) if row is not None else 0


async def _fetch_row(db: AsyncSession, table: str, id_col: str, id_val: str) -> dict | None:
    result = await db.execute(
        text(f"SELECT * FROM {table} WHERE {id_col} = :id_val"),
        {"id_val": id_val},
    )
    rows = result.mappings().all()
    return dict(rows[0]) if rows else None


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def _manage_tables() -> AsyncGenerator[None, None]:
    await _init_tables()
    yield
    await _clear_all_tables()


# ── Restart Persistence Tests ────────────────────────────────────────────────


class TestSessionPersistenceAcrossRestart:
    """Sessions written in one database session survive a simulated restart."""

    @pytest.mark.asyncio
    async def test_session_survives_restart(self) -> None:
        user_id = _uid()
        session_id = _uid()

        # -- write phase (first "application instance") --
        db1 = await _new_session()
        try:
            await db1.execute(
                text(
                    """INSERT INTO users (id, external_id, display_name, username, email, password_hash)
                       VALUES (:id, :external_id, :display_name, :username, :email, :password_hash)"""
                ),
                _user_row(user_id=user_id, username="persist_user"),
            )
            await db1.execute(
                text(
                    """INSERT INTO sessions (id, user_id, title)
                       VALUES (:id, :user_id, :title)"""
                ),
                _make_session_dict(session_id=session_id, user_id=user_id, title="Restart Test"),
            )
            await db1.commit()
        finally:
            await db1.close()

        # -- restart simulation: create a brand-new session --
        db2 = await _new_session()
        try:
            row = await _fetch_row(db2, "sessions", "id", session_id)
        finally:
            await db2.close()

        assert row is not None
        assert row["id"] == session_id
        assert row["user_id"] == user_id
        assert row["title"] == "Restart Test"


class TestMessagePersistenceAcrossRestart:
    """Full conversation history persists through restarts."""

    @pytest.mark.asyncio
    async def test_messages_survive_restart(self) -> None:
        user_id = _uid()
        session_id = _uid()
        msg_user_id = _uid()
        msg_assistant_id = _uid()

        db1 = await _new_session()
        try:
            await db1.execute(
                text(
                    """INSERT INTO users (id, external_id, display_name, username, email, password_hash)
                       VALUES (:id, :external_id, :display_name, :username, :email, :password_hash)"""
                ),
                _user_row(user_id=user_id, username="msg_user"),
            )
            await db1.execute(
                text(
                    """INSERT INTO sessions (id, user_id, title)
                       VALUES (:id, :user_id, :title)"""
                ),
                _make_session_dict(session_id=session_id, user_id=user_id),
            )
            await db1.execute(
                text(
                    """INSERT INTO messages (id, session_id, user_id, role, content)
                       VALUES (:id, :session_id, :user_id, :role, :content)"""
                ),
                _make_message_dict(
                    message_id=msg_user_id,
                    session_id=session_id,
                    user_id=user_id,
                    role="user",
                    content="Hello, are you there?",
                ),
            )
            await db1.execute(
                text(
                    """INSERT INTO messages (id, session_id, user_id, role, content)
                       VALUES (:id, :session_id, :user_id, :role, :content)"""
                ),
                _make_message_dict(
                    message_id=msg_assistant_id,
                    session_id=session_id,
                    user_id=user_id,
                    role="assistant",
                    content="Yes, I'm here!",
                ),
            )
            await db1.commit()
        finally:
            await db1.close()

        # restart
        db2 = await _new_session()
        try:
            count = await _count_rows(db2, "messages")
            row1 = await _fetch_row(db2, "messages", "id", msg_user_id)
            row2 = await _fetch_row(db2, "messages", "id", msg_assistant_id)
        finally:
            await db2.close()

        assert count == 2
        assert row1 is not None and row1["content"] == "Hello, are you there?"
        assert row1["role"] == "user"
        assert row2 is not None and row2["content"] == "Yes, I'm here!"
        assert row2["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_message_order_preserved_after_restart(self) -> None:
        user_id = _uid()
        session_id = _uid()

        db1 = await _new_session()
        try:
            await db1.execute(
                text(
                    """INSERT INTO users (id, external_id, display_name, username, email, password_hash)
                       VALUES (:id, :external_id, :display_name, :username, :email, :password_hash)"""
                ),
                _user_row(user_id=user_id),
            )
            await db1.execute(
                text(
                    """INSERT INTO sessions (id, user_id, title)
                       VALUES (:id, :user_id, :title)"""
                ),
                _make_session_dict(session_id=session_id, user_id=user_id),
            )
            # Use explicit sequential timestamps so ordering is deterministic.
            base_ts = datetime.now(timezone.utc)
            for idx, content in enumerate(["First", "Second", "Third", "Fourth"]):
                ts = (base_ts + timedelta(seconds=idx)).isoformat()
                msg_id = _uid()
                await db1.execute(
                    text(
                        """INSERT INTO messages (id, session_id, user_id, role, content, created_at)
                           VALUES (:id, :session_id, :user_id, :role, :content, :created_at)"""
                    ),
                    {
                        **_make_message_dict(
                            message_id=msg_id,
                            session_id=session_id,
                            user_id=user_id,
                            role="user" if idx % 2 == 0 else "assistant",
                            content=content,
                        ),
                        "created_at": ts,
                    },
                )
            await db1.commit()
        finally:
            await db1.close()

        # restart
        db2 = await _new_session()
        try:
            result = await db2.execute(
                text(
                    """SELECT content FROM messages
                       WHERE session_id = :sid
                       ORDER BY created_at ASC, id ASC"""
                ),
                {"sid": session_id},
            )
            contents = [row[0] for row in result.fetchall()]
        finally:
            await db2.close()

        assert contents == ["First", "Second", "Third", "Fourth"]


class TestCompanionPersistenceAcrossRestart:
    """Companion definitions survive restart."""

    @pytest.mark.asyncio
    async def test_companion_survives_restart(self) -> None:
        user_id = _uid()
        companion_id = _uid()

        db1 = await _new_session()
        try:
            await db1.execute(
                text(
                    """INSERT INTO users (id, external_id, display_name, username, email, password_hash)
                       VALUES (:id, :external_id, :display_name, :username, :email, :password_hash)"""
                ),
                _user_row(user_id=user_id),
            )
            await db1.execute(
                text(
                    """INSERT INTO companions (companion_id, user_id, name, persona, description)
                       VALUES (:companion_id, :user_id, :name, :persona, :description)"""
                ),
                _companion_dict(
                    companion_id=companion_id,
                    user_id=user_id,
                    name="RestartHelper",
                    persona="friendly and persistent",
                    description="A companion that survives restarts",
                ),
            )
            await db1.commit()
        finally:
            await db1.close()

        # restart
        db2 = await _new_session()
        try:
            row = await _fetch_row(db2, "companions", "companion_id", companion_id)
        finally:
            await db2.close()

        assert row is not None
        assert row["name"] == "RestartHelper"
        assert row["persona"] == "friendly and persistent"
        assert row["description"] == "A companion that survives restarts"


class TestSemanticFactPersistenceAcrossRestart:
    """Learned facts survive restart."""

    @pytest.mark.asyncio
    async def test_facts_survive_restart(self) -> None:
        user_id = _uid()
        fact_1 = _fact_row(user_id=user_id, fact_text="User lives in Berlin")
        fact_2 = _fact_row(user_id=user_id, fact_text="User's name is Alice")
        fact_3 = _fact_row(
            user_id=user_id,
            fact_text="Temporary note",
            privacy_level="temporary",
            pinned=True,
        )

        db1 = await _new_session()
        try:
            await db1.execute(
                text(
                    """INSERT INTO users (id, external_id, display_name, username, email, password_hash)
                       VALUES (:id, :external_id, :display_name, :username, :email, :password_hash)"""
                ),
                _user_row(user_id=user_id),
            )
            for fact in (fact_1, fact_2, fact_3):
                await db1.execute(
                    text(
                        """INSERT INTO semantic_facts
                           (id, user_id, fact_text, source_type, privacy_level, confidence, pinned)
                           VALUES (:id, :user_id, :fact_text, :source_type, :privacy_level, :confidence, :pinned)"""
                    ),
                    fact,
                )
            await db1.commit()
        finally:
            await db1.close()

        # restart
        db2 = await _new_session()
        try:
            count = await _count_rows(db2, "semantic_facts")
            result = await db2.execute(
                text(
                    """SELECT fact_text, privacy_level, pinned
                       FROM semantic_facts WHERE user_id = :uid
                       ORDER BY fact_text ASC"""
                ),
                {"uid": user_id},
            )
            rows = result.mappings().all()
        finally:
            await db2.close()

        assert count == 3
        assert len(rows) == 3
        facts = {(r["fact_text"], r["privacy_level"], bool(r["pinned"])) for r in rows}
        assert ("User lives in Berlin", "permanent", False) in facts
        assert ("User's name is Alice", "permanent", False) in facts
        assert ("Temporary note", "temporary", True) in facts


class TestCrossEntityPersistenceAcrossRestart:
    """Multiple related entities stay consistent across restart."""

    @pytest.mark.asyncio
    async def test_full_conversation_graph_survives_restart(self) -> None:
        user_id = _uid()
        companion_id = _uid()
        session_id = _uid()
        msg1_id = _uid()
        msg2_id = _uid()

        db1 = await _new_session()
        try:
            # user
            await db1.execute(
                text(
                    """INSERT INTO users (id, external_id, display_name, username, email, password_hash)
                       VALUES (:id, :external_id, :display_name, :username, :email, :password_hash)"""
                ),
                _user_row(user_id=user_id, username="cross_entity"),
            )
            # companion
            await db1.execute(
                text(
                    """INSERT INTO companions (companion_id, user_id, name, persona)
                       VALUES (:companion_id, :user_id, :name, :persona)"""
                ),
                _companion_dict(
                    companion_id=companion_id,
                    user_id=user_id,
                    name="CrossEntity",
                    persona="survives everything",
                ),
            )
            # session bound to companion
            await db1.execute(
                text(
                    """INSERT INTO sessions (id, user_id, companion_id, title)
                       VALUES (:id, :user_id, :companion_id, :title)"""
                ),
                _make_session_dict(
                    session_id=session_id,
                    user_id=user_id,
                    companion_id=companion_id,
                    title="Full graph test",
                ),
            )
            # messages
            for msg in (
                _make_message_dict(
                    message_id=msg1_id,
                    session_id=session_id,
                    user_id=user_id,
                    role="user",
                    content="Remember Berlin",
                    companion_id=companion_id,
                ),
                _make_message_dict(
                    message_id=msg2_id,
                    session_id=session_id,
                    user_id=user_id,
                    role="assistant",
                    content="Got it — Berlin.",
                    companion_id=companion_id,
                ),
            ):
                await db1.execute(
                    text(
                        """INSERT INTO messages (id, session_id, user_id, companion_id, role, content)
                           VALUES (:id, :session_id, :user_id, :companion_id, :role, :content)"""
                    ),
                    msg,
                )
            await db1.commit()
        finally:
            await db1.close()

        # restart
        db2 = await _new_session()
        try:
            user_row = await _fetch_row(db2, "users", "id", user_id)
            comp_row = await _fetch_row(db2, "companions", "companion_id", companion_id)
            sess_row = await _fetch_row(db2, "sessions", "id", session_id)

            result = await db2.execute(
                text(
                    """SELECT content, role FROM messages
                       WHERE session_id = :sid ORDER BY created_at ASC"""
                ),
                {"sid": session_id},
            )
            msgs = result.mappings().all()
        finally:
            await db2.close()

        assert user_row is not None
        assert comp_row is not None
        assert sess_row is not None
        assert sess_row["companion_id"] == companion_id
        assert len(msgs) == 2
        assert msgs[0]["content"] == "Remember Berlin"
        assert msgs[0]["role"] == "user"
        assert msgs[1]["content"] == "Got it — Berlin."
        assert msgs[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_multi_session_isolation_after_restart(self) -> None:
        """Sessions for different users remain isolated after restart."""
        alice_id = _uid()
        bob_id = _uid()
        alice_session = _uid()
        bob_session = _uid()

        db1 = await _new_session()
        try:
            for uid, username in [(alice_id, "alice_iso"), (bob_id, "bob_iso")]:
                await db1.execute(
                    text(
                        """INSERT INTO users (id, external_id, display_name, username, email, password_hash)
                           VALUES (:id, :external_id, :display_name, :username, :email, :password_hash)"""
                    ),
                    _user_row(user_id=uid, username=username),
                )
            await db1.execute(
                text(
                    """INSERT INTO sessions (id, user_id, title)
                       VALUES (:id, :user_id, :title)"""
                ),
                _make_session_dict(session_id=alice_session, user_id=alice_id, title="Alice Chat"),
            )
            await db1.execute(
                text(
                    """INSERT INTO sessions (id, user_id, title)
                       VALUES (:id, :user_id, :title)"""
                ),
                _make_session_dict(session_id=bob_session, user_id=bob_id, title="Bob Chat"),
            )
            for sid, uid, content in [
                (alice_session, alice_id, "Alice message"),
                (bob_session, bob_id, "Bob message"),
            ]:
                await db1.execute(
                    text(
                        """INSERT INTO messages (id, session_id, user_id, role, content)
                           VALUES (:id, :session_id, :user_id, :role, :content)"""
                    ),
                    _make_message_dict(message_id=_uid(), session_id=sid, user_id=uid, role="user", content=content),
                )
            await db1.commit()
        finally:
            await db1.close()

        # restart
        db2 = await _new_session()
        try:
            alice_sessions = await db2.execute(
                text("SELECT COUNT(*) FROM sessions WHERE user_id = :uid"),
                {"uid": alice_id},
            )
            bob_sessions = await db2.execute(
                text("SELECT COUNT(*) FROM sessions WHERE user_id = :uid"),
                {"uid": bob_id},
            )
            alice_messages = await db2.execute(
                text(
                    """SELECT content FROM messages
                       WHERE session_id = :sid"""
                ),
                {"sid": alice_session},
            )
            bob_messages = await db2.execute(
                text(
                    """SELECT content FROM messages
                       WHERE session_id = :sid"""
                ),
                {"sid": bob_session},
            )
        finally:
            await db2.close()

        assert alice_sessions.scalar() == 1
        assert bob_sessions.scalar() == 1
        assert alice_messages.scalar() == "Alice message"
        assert bob_messages.scalar() == "Bob message"
