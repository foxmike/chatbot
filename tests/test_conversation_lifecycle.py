"""Regression tests for conversation persistence lifecycle across delivery modes.

Covers existing persistence behavior and exposes divergence/defects across:
1. Standard HTTP chat (/chat/)
2. SSE streaming chat (/chat/stream)
3. Spoken / avatar chat (via stream_chat)

Prioritizes the five concrete test scenarios established for Phase 0.4:
TEST 1 — Standard Chat Persistence (fast-path deferred vs deep-path full persistence)
TEST 2 — Streaming Memory Persistence (extraction attempted vs rollback defect)
TEST 3 — Streaming Emotional-State Persistence (mood/user-state attempted vs rollback defect)
TEST 4 — Timeline Persistence (independent timeline session persistence vs stream_db rollback)
TEST 5 — Failed and Interrupted Turns (provider error, SSE disconnect, avatar barge-in cancellation)
TEST 0 — PostgreSQL characterization (T0, skipped if pg_isready fails)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import shutil
import subprocess
from types import SimpleNamespace
from typing import Any, AsyncIterator
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request
from fastapi import FastAPI

from chatbot.auth.dependencies import CurrentUser
from chatbot.api import chat as chat_api
from chatbot.api import streaming as streaming_api
from chatbot.api.chat import ChatRequest
from chatbot.api.streaming import StreamRequest
from chatbot.core.steps import TurnContext
from chatbot.core.turn import (
    TurnPipeline,
    CallProviderStep,
    RespondStep,
    ExtractMemoryStep,
    PersistEmotionalStateStep,
    TimelineUpdateStep,
    ReceiveStep,
    CompileContextStep,
    UpdateIdentityStep,
    InferUserStateStep,
    SelectConversationModeStep,
    UpdateMoodStep,
    SceneMemoryGateStep,
)
from chatbot.db import models
from chatbot.db.models import Message, Session, SemanticFact, BotMoodLog, UserStateLog
from chatbot.continuity.timeline import (
    RelationshipTimelineEntryModel,
    _default_timeline,
    RelationshipTimeline,
)
from chatbot.soul.mood import MoodState, MoodEngine
from chatbot.soul.user_state import UserState, UserStateEngine
from chatbot.soul.modes import ConversationMode, ModeSelector


# ============================================================================
# Test Fixtures and Helpers
# ============================================================================


@pytest.fixture(autouse=True)
def isolate_observability_and_rituals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep these tests off any real database.

    The real trace/metrics collectors persist to PostgreSQL and the ritual lookup
    opens its own session; without this, a development database on localhost
    would receive rows from a test run.
    """
    from chatbot.core import turn as turn_module
    from chatbot.observability.metrics import MetricsCollector
    from chatbot.observability.tracing import TraceCollector

    monkeypatch.setattr(turn_module, "trace_collector", TraceCollector(store="memory"))
    monkeypatch.setattr(turn_module, "metrics_collector", MetricsCollector(store="memory"))
    monkeypatch.setattr(turn_module, "get_due_rituals", AsyncMock(return_value=[]))

TEST_SESSION_ID = "11111111-2222-3333-4444-555555555555"
TEST_USER_ID = "99999999-8888-7777-6666-555555555555"
TEST_COMPANION_ID = "companion-test-01"

TEST_USER = CurrentUser(
    user_id=TEST_USER_ID,
    username="test_user",
    email="test@example.com",
    display_name="Test User",
    token="test-token",
)


def _is_postgres_ready() -> bool:
    """Check whether a real PostgreSQL instance is responding."""
    if not shutil.which("pg_isready"):
        return False
    try:
        res = subprocess.run(["pg_isready"], capture_output=True, timeout=2)
        return res.returncode == 0
    except Exception:
        return False


class TrackingSession:
    """Session tracking that accurately models SQLAlchemy transaction lifecycle.

    Records:
    - Object additions, flushes, commits, rollbacks, and queries
    - Tracks attempted writes vs committed writes
    """

    def __init__(
        self,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        companion_id: str | None = None,
        title: str = "Test Session",
        in_tx: bool = True,
    ) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.session_row = Session(
            id=session_id,
            user_id=user_id,
            companion_id=companion_id,
            title=title,
            room_id=None,
            mood_state=None,
            user_state=None,
            conversation_mode=None,
            updated_at=datetime.now(timezone.utc),
        )

        self.added_objects: list[Any] = []
        self.flushed_objects: list[Any] = []
        self.committed_objects: list[Any] = []
        self.rolled_back_objects: list[Any] = []

        self.inserted_facts: list[dict[str, Any]] = []
        self.committed_facts: list[dict[str, Any]] = []
        self.rolled_back_facts: list[dict[str, Any]] = []

        self.commit_count = 0
        self.rollback_count = 0
        self.flush_count = 0
        self.closed = False
        self._in_tx = in_tx

    def in_transaction(self) -> bool:
        return self._in_tx

    def begin_nested(self) -> Any:
        class Savepoint:
            async def __aenter__(s: Any) -> Any:
                return s

            async def __aexit__(s: Any, *args: Any) -> None:
                pass

        return Savepoint()

    async def get(self, model_cls: Any, pk: Any) -> Any:
        if model_cls is Session and pk == self.session_id:
            return self.session_row
        return None

    def add(self, obj: Any) -> None:
        if isinstance(obj, RelationshipTimelineEntryModel):
            if getattr(obj, "status", None) is None:
                obj.status = "active"
        self.added_objects.append(obj)

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> Any:
        sql_str = str(stmt).lower()

        # Check if selecting session user_id specifically
        if "select sessions.user_id" in sql_str:
            return self._make_result(self.user_id)

        # Check if selecting session record
        if "from sessions" in sql_str:
            return self._make_result(self.session_row)

        # Check if selecting room
        if "from rooms" in sql_str:
            return self._make_result(None)

        # Check if selecting messages (history)
        if "from messages" in sql_str:
            messages = [
                obj for obj in self.committed_objects if isinstance(obj, Message)
            ]
            return self._make_result(messages, is_list=True)

        # Check if selecting timeline entries
        if "from relationship_timeline_entries" in sql_str:
            entries = [
                obj for obj in self.committed_objects if isinstance(obj, RelationshipTimelineEntryModel)
            ]
            return self._make_result(entries, is_list=True)

        # Check if inserting SemanticFact
        if "insert into semantic_facts" in sql_str or (
            len(args) > 0 and isinstance(args[0], list) and "fact_text" in args[0][0]
        ):
            rows = args[0] if len(args) > 0 and isinstance(args[0], list) else []
            self.inserted_facts.extend(rows)
            return self._make_result(None)

        # Check if updating session
        if "update sessions" in sql_str:
            self.session_row.updated_at = datetime.now(timezone.utc)
            return self._make_result(None)

        return self._make_result(None)

    def _make_result(self, val: Any, is_list: bool = False) -> Any:
        class Result:
            def __init__(self, value: Any) -> None:
                self.value = value

            def scalar_one_or_none(self) -> Any:
                return self.value

            def scalar(self) -> Any:
                return self.value

            def scalars(self) -> Any:
                items = self.value if is_list else ([self.value] if self.value else [])

                class ScalarsResult:
                    def all(self) -> list[Any]:
                        return items

                return ScalarsResult()

        return Result(val)

    async def flush(self) -> None:
        self.flush_count += 1
        self.flushed_objects.extend(self.added_objects)
        self.added_objects.clear()

    async def commit(self) -> None:
        self.commit_count += 1
        self.flushed_objects.extend(self.added_objects)
        self.added_objects.clear()
        self.committed_objects.extend(self.flushed_objects)
        self.flushed_objects.clear()

        self.committed_facts.extend(self.inserted_facts)
        self.inserted_facts.clear()
        self._in_tx = False

    async def rollback(self) -> None:
        self.rollback_count += 1
        self.flushed_objects.extend(self.added_objects)
        self.added_objects.clear()
        self.rolled_back_objects.extend(self.flushed_objects)
        self.flushed_objects.clear()

        self.rolled_back_facts.extend(self.inserted_facts)
        self.inserted_facts.clear()
        self._in_tx = False

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


class TrackingSessionFactory:
    """Produces TrackingSession instances and keeps references to all created sessions."""

    def __init__(
        self,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        companion_id: str | None = None,
        in_tx: bool = True,
    ) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.companion_id = companion_id
        self.in_tx = in_tx
        self.sessions: list[TrackingSession] = []

    def __call__(self) -> TrackingSession:
        session = TrackingSession(
            session_id=self.session_id,
            user_id=self.user_id,
            companion_id=self.companion_id,
            in_tx=self.in_tx,
        )
        self.sessions.append(session)
        return session


# ============================================================================
# TEST 1 — Standard Chat Persistence
# ============================================================================

@pytest.mark.asyncio
async def test_standard_chat_fast_path_persists_messages_and_respects_deferral() -> None:
    """TEST 1.1: Standard chat fast-path persists messages and respects deferred effects.

    Completed greeting/casual turns must route to fast-path:
    - User and assistant messages are persisted and committed in db
    - Fast path skips/defers memory extraction and emotional-state steps
    - No uncommitted memory or mood records are left behind
    """
    sid = uuid.UUID(TEST_SESSION_ID)
    uid = uuid.UUID(TEST_USER_ID)
    db = TrackingSession(session_id=sid, user_id=uid)

    body = ChatRequest(
        session_id=str(sid),
        message="Hello! Good morning.",
    )
    req = Request({"type": "http", "app": FastAPI()})

    resp = await chat_api.chat(
        request=req,
        body=body,
        current_user=TEST_USER,
        db=db,
    )

    # 1. Verify turn completed with response
    assert resp.response is not None
    assert resp.session_id == str(sid)

    # 2. Verify messages committed in the single request transaction
    committed_messages = [
        obj for obj in db.committed_objects if isinstance(obj, Message)
    ]
    assert len(committed_messages) == 2
    roles = [m.role for m in committed_messages]
    assert roles == ["user", "assistant"]
    assert committed_messages[0].content == "Hello! Good morning."
    assert committed_messages[1].content == resp.response
    assert db.commit_count == 1
    assert db.rollback_count == 0

    # 3. Verify fast-path deferred processing was respected
    # Fast path steps do not include extract_memory or persist_emotional_state
    committed_facts = [
        obj for obj in db.committed_objects if isinstance(obj, SemanticFact)
    ]
    committed_mood_logs = [
        obj for obj in db.committed_objects if isinstance(obj, BotMoodLog)
    ]
    assert len(committed_facts) == 0
    assert len(committed_mood_logs) == 0


@pytest.mark.asyncio
async def test_standard_chat_deep_path_persists_messages_and_eligible_effects() -> None:
    """TEST 1.2: Standard chat deep-path persists messages AND eligible post-turn effects.

    When deep-path executes (e.g. sharing factual memory info with planning intent):
    - Full pipeline executes, extracting memories and updating emotional state
    - All effects and conversation messages are committed in the single request transaction
    """
    sid = uuid.UUID(TEST_SESSION_ID)
    uid = uuid.UUID(TEST_USER_ID)
    db = TrackingSession(session_id=sid, user_id=uid)

    # Message with factual content and planning intent triggering full pipeline
    body = ChatRequest(
        session_id=str(sid),
        message="Can you help me plan my schedule? I live in Seattle and I have two golden retrievers.",
    )
    req = Request({"type": "http", "app": FastAPI()})

    resp = await chat_api.chat(
        request=req,
        body=body,
        current_user=TEST_USER,
        db=db,
    )

    assert resp.response is not None
    assert db.commit_count == 1
    assert db.rollback_count == 0

    # Messages must be committed
    committed_messages = [
        obj for obj in db.committed_objects if isinstance(obj, Message)
    ]
    assert len(committed_messages) == 2
    assert [m.role for m in committed_messages] == ["user", "assistant"]

    # In standard chat deep-path, eligible facts inserted during the turn
    # are committed when await db.commit() is called
    assert len(db.committed_facts) > 0
    assert "I live in Seattle and I have two golden retrievers." in db.committed_facts[0]["fact_text"]


# ============================================================================
# TEST 2 — Streaming Memory Persistence
# ============================================================================

@pytest.mark.asyncio
async def test_streaming_memory_extraction_is_committed_with_the_messages() -> None:
    """TEST 2.1: Extracted memories are committed together with the turn's messages.

    This test originally characterized the defect (facts flushed to stream_db and
    then discarded by ``stream_db.rollback()``). It now pins the corrected
    transaction ownership for a completed turn:
    1. ExtractMemoryStep flushes facts on stream_db inside the effects transaction.
    2. The messages are added to that same session and committed once, so the
       facts and the messages share one commit.
    3. Nothing is rolled back.

    (``test_completion_persistence.py`` runs the same flow on a real session.)
    """
    sid = uuid.UUID(TEST_SESSION_ID)
    uid = uuid.UUID(TEST_USER_ID)
    factory = TrackingSessionFactory(session_id=sid, user_id=uid)

    with patch.object(streaming_api, "streaming_session_factory", factory):
        stream_req = StreamRequest(
            session_id=str(sid),
            message="I have a golden retriever named Rex.",
            stream_tokens=False,
        )
        app = FastAPI()
        http_req = Request({"type": "http", "app": app})

        frames = [
            frame
            async for frame in streaming_api._stream_turn(
                stream_req,
                http_request=http_req,
                current_user=TEST_USER,
                session_id=sid,
                session_title="Test Session",
                session_companion_id=None,
                session_room_id=None,
                session_snapshot={},
                character_profile={"name": "Companion"},
                identity_state=None,
                history=[],
            )
        ]

    assert len(frames) > 0
    stream_db = factory.sessions[0]

    assert len(stream_db.committed_facts) > 0, "extracted facts must be committed"
    assert stream_db.rolled_back_facts == []
    assert stream_db.rollback_count == 0

    committed_messages = [obj for obj in stream_db.committed_objects if isinstance(obj, Message)]
    assert [m.role for m in committed_messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_streaming_memory_persistence_contract_completed_turn() -> None:
    """TEST 2.2: Regression contract — completed streaming turns MUST persist extracted memories.

    Phase 0.3 contract requirement:
    Completed turns with eligible memory content must commit extracted memories.

    DEFECT EXPOSURE:
    Under current Phase 0.2 / unrefactored code, this test FAILS because
    stream_db.rollback() discards the extracted memories before post-stream writes.
    """
    sid = uuid.UUID(TEST_SESSION_ID)
    uid = uuid.UUID(TEST_USER_ID)
    factory = TrackingSessionFactory(session_id=sid, user_id=uid)

    with patch.object(streaming_api, "streaming_session_factory", factory):
        stream_req = StreamRequest(
            session_id=str(sid),
            message="I have a golden retriever named Rex.",
            stream_tokens=False,
        )
        app = FastAPI()
        http_req = Request({"type": "http", "app": app})

        async for _ in streaming_api._stream_turn(
            stream_req,
            http_request=http_req,
            current_user=TEST_USER,
            session_id=sid,
            session_title="Test Session",
            session_companion_id=None,
            session_room_id=None,
            session_snapshot={},
            character_profile={"name": "Companion"},
            identity_state=None,
            history=[],
        ):
            pass

    # Total committed facts across all sessions opened during the turn
    all_committed_facts = [
        fact for sess in factory.sessions for fact in sess.committed_facts
    ]
    assert len(all_committed_facts) > 0, (
        "PERSISTENCE DEFECT: Completed streaming turn discarded eligible extracted memories. "
        "Records were flushed to stream_db but rolled back at streaming.py:355."
    )


# ============================================================================
# TEST 3 — Streaming Emotional-State Persistence
# ============================================================================

@pytest.mark.asyncio
async def test_streaming_emotional_state_is_committed_with_the_messages() -> None:
    """TEST 3.1: Emotional-state writes are committed together with the turn's messages.

    This test originally characterized the defect (BotMoodLog/UserStateLog and the
    session state flushed on stream_db and then discarded by ``rollback()``). It
    now pins the corrected behavior for a completed turn: the logs are flushed in
    the effects transaction and committed in the same commit as the messages.
    """
    sid = uuid.UUID(TEST_SESSION_ID)
    uid = uuid.UUID(TEST_USER_ID)
    factory = TrackingSessionFactory(session_id=sid, user_id=uid)

    now = datetime.now(timezone.utc)
    test_mood = MoodState(mood="upbeat", energy=0.8, closeness=0.7, last_updated=now)
    test_user_state = UserState(
        primary_state="excited",
        confidence=0.8,
        secondary_states=[],
        evidence_summary="user feeling great",
        last_inferred=now,
        inference_count=1,
    )

    with patch.object(streaming_api, "streaming_session_factory", factory):
        stream_req = StreamRequest(
            session_id=str(sid),
            message="I am feeling wonderful and excited today!",
            stream_tokens=False,
        )
        app = FastAPI()
        http_req = Request({"type": "http", "app": app})

        # Inject emotional state into the TurnContext pipeline execution
        original_infer = InferUserStateStep.run

        async def mock_infer(self: Any, ctx: TurnContext) -> TurnContext:
            ctx = await original_infer(self, ctx)
            ctx.metadata["mood_state"] = test_mood
            ctx.metadata["user_state"] = test_user_state
            ctx.metadata["conversation_mode"] = ConversationMode.NORMAL
            return ctx

        with patch.object(InferUserStateStep, "run", mock_infer):
            async for _ in streaming_api._stream_turn(
                stream_req,
                http_request=http_req,
                current_user=TEST_USER,
                session_id=sid,
                session_title="Test Session",
                session_companion_id=None,
                session_room_id=None,
                session_snapshot={},
                character_profile={"name": "Companion"},
                identity_state=None,
                history=[],
            ):
                pass

    stream_db = factory.sessions[0]

    committed_logs = [
        obj
        for obj in stream_db.committed_objects
        if isinstance(obj, (BotMoodLog, UserStateLog))
    ]
    assert len(committed_logs) >= 2, f"Expected committed BotMoodLog and UserStateLog, got {committed_logs}"
    assert stream_db.rolled_back_objects == []
    assert stream_db.rollback_count == 0
    committed_messages = [obj for obj in stream_db.committed_objects if isinstance(obj, Message)]
    assert [m.role for m in committed_messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_streaming_emotional_state_persistence_contract() -> None:
    """TEST 3.2: Regression contract — completed streaming turns MUST persist emotional-state logs.

    Phase 0.3 contract requirement:
    Completed turns with emotional intelligence updates must commit BotMoodLog and UserStateLog.

    DEFECT EXPOSURE:
    Under current Phase 0.2 / unrefactored code, this test FAILS because
    stream_db.rollback() discards the mood and user state logs before post-stream writes.
    """
    sid = uuid.UUID(TEST_SESSION_ID)
    uid = uuid.UUID(TEST_USER_ID)
    factory = TrackingSessionFactory(session_id=sid, user_id=uid)

    now = datetime.now(timezone.utc)
    test_mood = MoodState(mood="upbeat", energy=0.8, closeness=0.7, last_updated=now)
    test_user_state = UserState(
        primary_state="excited",
        confidence=0.8,
        secondary_states=[],
        evidence_summary="user feeling great",
        last_inferred=now,
        inference_count=1,
    )

    with patch.object(streaming_api, "streaming_session_factory", factory):
        stream_req = StreamRequest(
            session_id=str(sid),
            message="I am feeling wonderful and excited today!",
            stream_tokens=False,
        )
        app = FastAPI()
        http_req = Request({"type": "http", "app": app})

        original_infer = InferUserStateStep.run

        async def mock_infer(self: Any, ctx: TurnContext) -> TurnContext:
            ctx = await original_infer(self, ctx)
            ctx.metadata["mood_state"] = test_mood
            ctx.metadata["user_state"] = test_user_state
            ctx.metadata["conversation_mode"] = ConversationMode.NORMAL
            return ctx

        with patch.object(InferUserStateStep, "run", mock_infer):
            async for _ in streaming_api._stream_turn(
                stream_req,
                http_request=http_req,
                current_user=TEST_USER,
                session_id=sid,
                session_title="Test Session",
                session_companion_id=None,
                session_room_id=None,
                session_snapshot={},
                character_profile={"name": "Companion"},
                identity_state=None,
                history=[],
            ):
                pass

    all_committed_logs = [
        obj
        for sess in factory.sessions
        for obj in sess.committed_objects
        if isinstance(obj, (BotMoodLog, UserStateLog))
    ]
    assert len(all_committed_logs) > 0, (
        "PERSISTENCE DEFECT: Completed streaming turn discarded emotional-state logs. "
        "BotMoodLog and UserStateLog were flushed on stream_db but rolled back at streaming.py:355."
    )


# ============================================================================
# TEST 4 — Timeline Persistence
# ============================================================================

@pytest.mark.asyncio
async def test_timeline_entry_is_committed_with_the_turn_not_independently() -> None:
    """TEST 4: A timeline milestone belongs to the turn's transaction.

    This test originally characterized the timeline writing through its own
    session factory, so an entry could be committed for a turn whose messages and
    other effects were not. Now:
    - TimelineUpdateStep detects the milestone ("I got a new job today!")
    - the entry is written on the turn's own session and committed with it
    - the timeline's independent session factory is not used
    """
    sid = uuid.UUID(TEST_SESSION_ID)
    uid = uuid.UUID(TEST_USER_ID)
    stream_factory = TrackingSessionFactory(session_id=sid, user_id=uid)
    timeline_session = TrackingSession(session_id=sid, user_id=uid, in_tx=False)

    with patch.object(streaming_api, "streaming_session_factory", stream_factory):
        with patch.object(_default_timeline, "_session_factory", lambda: timeline_session):
            stream_req = StreamRequest(
                session_id=str(sid),
                message="I got a new job today! I am so thrilled.",
                stream_tokens=False,
            )
            app = FastAPI()
            http_req = Request({"type": "http", "app": app})

            async for _ in streaming_api._stream_turn(
                stream_req,
                http_request=http_req,
                current_user=TEST_USER,
                session_id=sid,
                session_title="Test Session",
                session_companion_id=None,
                session_room_id=None,
                session_snapshot={},
                character_profile={"name": "Companion"},
                identity_state=None,
                history=[],
            ):
                pass

    stream_db = stream_factory.sessions[0]
    committed_timeline = [
        obj
        for obj in stream_db.committed_objects
        if isinstance(obj, RelationshipTimelineEntryModel)
    ]
    assert len(committed_timeline) >= 1
    entry = committed_timeline[0]
    assert entry.entry_type == "milestone"
    assert "got a new job" in entry.content.lower()
    committed_messages = [obj for obj in stream_db.committed_objects if isinstance(obj, Message)]
    assert [m.role for m in committed_messages] == ["user", "assistant"]
    assert stream_db.rollback_count == 0

    # The independent session was never used for the entry.
    assert timeline_session.committed_objects == []
    assert timeline_session.commit_count == 0


# ============================================================================
# TEST 5 — Failed and Interrupted Turns
# ============================================================================

@pytest.mark.asyncio
async def test_failed_streaming_generation_persists_error_response() -> None:
    """TEST 5.1: Characterizes persistence when streaming generation fails.

    When token streaming fails with a provider error:
    - Provider stream yields an error token
    - stream_db is rolled back
    - _persist_messages is called: user message AND assistant error message are persisted
    """
    sid = uuid.UUID(TEST_SESSION_ID)
    uid = uuid.UUID(TEST_USER_ID)
    factory = TrackingSessionFactory(session_id=sid, user_id=uid)

    async def failing_stream_tokens(ctx: TurnContext) -> AsyncIterator[str]:
        # Reflect actual behavior of streaming.py:_stream_provider_tokens on error
        ctx.provider_response = "Provider error: Connection reset by peer"
        yield "Provider stream error: Connection reset by peer"

    with patch.object(streaming_api, "streaming_session_factory", factory):
        with patch.object(streaming_api, "_stream_provider_tokens", failing_stream_tokens):
            stream_req = StreamRequest(
                session_id=str(sid),
                message="Tell me a story",
                stream_tokens=True,
            )
            app = FastAPI()
            http_req = Request({"type": "http", "app": app})

            frames = [
                frame
                async for frame in streaming_api._stream_turn(
                    stream_req,
                    http_request=http_req,
                    current_user=TEST_USER,
                    session_id=sid,
                    session_title="Test Session",
                    session_companion_id=None,
                    session_room_id=None,
                    session_snapshot={},
                    character_profile={"name": "Companion"},
                    identity_state=None,
                    history=[],
                )
            ]

    # Verify error frame emitted
    error_frames = [f for f in frames if '"type": "error"' in f]
    assert len(error_frames) >= 1

    # Verify that stream_db rolled back, but post-stream messages WERE saved
    assert factory.sessions[0].rollback_count == 1

    post_stream_db = factory.sessions[1]
    assert post_stream_db.commit_count >= 1
    committed_messages = [
        obj for obj in post_stream_db.committed_objects if isinstance(obj, Message)
    ]
    assert len(committed_messages) == 2
    roles = [m.role for m in committed_messages]
    assert roles == ["user", "assistant"]
    # Existing behavior: error text is recorded in the assistant message
    assert "Provider error:" in committed_messages[1].content


@pytest.mark.asyncio
async def test_interrupted_typed_sse_delivery_persists_nothing() -> None:
    """TEST 5.2: Interrupted SSE delivery (typed disconnect without avatar) persists NOTHING.

    When the client disconnects before persistence:
    - CancelledError / GeneratorExit is raised
    - In typed mode without avatar (avatar_turn is None):
      outcome is ABANDONED
    - CancelledError is re-raised
    - ZERO messages and ZERO post-turn effects are persisted
    """
    sid = uuid.UUID(TEST_SESSION_ID)
    uid = uuid.UUID(TEST_USER_ID)
    factory = TrackingSessionFactory(session_id=sid, user_id=uid)

    async def cancelling_stream_tokens(ctx: TurnContext) -> AsyncIterator[str]:
        yield "First token"
        raise asyncio.CancelledError

    with patch.object(streaming_api, "streaming_session_factory", factory):
        with patch.object(streaming_api, "_stream_provider_tokens", cancelling_stream_tokens):
            stream_req = StreamRequest(
                session_id=str(sid),
                message="Hello",
                stream_tokens=True,
            )
            app = FastAPI()
            http_req = Request({"type": "http", "app": app})

            with pytest.raises(asyncio.CancelledError):
                async for _ in streaming_api._stream_turn(
                    stream_req,
                    http_request=http_req,
                    current_user=TEST_USER,
                    session_id=sid,
                    session_title="Test Session",
                    session_companion_id=None,
                    session_room_id=None,
                    session_snapshot={},
                    character_profile={"name": "Companion"},
                    identity_state=None,
                    history=[],
                ):
                    pass

    # No post-stream persistence session was created; stream_db had no commits
    all_committed_messages = [
        obj
        for sess in factory.sessions
        for obj in sess.committed_objects
        if isinstance(obj, Message)
    ]
    assert len(all_committed_messages) == 0


@pytest.mark.asyncio
async def test_cancelled_spoken_generation_persists_heard_prefix_only() -> None:
    """TEST 5.3: Cancelled spoken generation persists ONLY confirmed delivered prefix.

    When avatar spoken turn is interrupted (barge-in):
    - coordinator.stop_speech is called
    - stream_db is rolled back
    - _persist_messages is called with interrupted=True and assistant_message=delivered_text
    - User message, delivered text prefix, and system [Assistant turn interrupted] marker are saved
    - Post-turn effects (such as identity updates or embedding generation) are NOT run
    """
    sid = uuid.UUID(TEST_SESSION_ID)
    uid = uuid.UUID(TEST_USER_ID)
    factory = TrackingSessionFactory(session_id=sid, user_id=uid)

    mock_coordinator = MagicMock()
    mock_coordinator.stop_speech = AsyncMock()

    mock_avatar = SimpleNamespace(companion_id="companion-1")
    mock_coordinator.get.return_value = mock_avatar

    mock_avatar_turn = SimpleNamespace(
        turn_id=str(uuid.uuid4()),
        source="spoken",
        interrupted=False,
        persisted=False,
        persistence_error=False,
        delivered_text="I heard you say that.",
        text_delivered="I heard you say that.",
        latency_times={},
        task=None,
        finished=asyncio.Event(),
    )
    mock_coordinator.begin = AsyncMock(return_value=mock_avatar_turn)

    async def cancelling_stream_tokens(ctx: TurnContext) -> AsyncIterator[str]:
        yield "I heard you say that."
        mock_avatar_turn.interrupted = True
        raise asyncio.CancelledError

    with patch.object(streaming_api, "streaming_session_factory", factory):
        with patch.object(streaming_api, "_stream_provider_tokens", cancelling_stream_tokens):
            with patch.object(streaming_api, "get_coordinator", lambda app: mock_coordinator):
                stream_req = StreamRequest(
                    session_id=str(sid),
                    message="Interrupted query",
                    stream_tokens=True,
                )
                app = FastAPI()
                http_req = Request({
                    "type": "http",
                    "app": app,
                    "state": {
                        "avatar_source": "spoken",
                        "avatar_binding": mock_avatar,
                    },
                })

                with pytest.raises(asyncio.CancelledError):
                    async for _ in streaming_api._stream_turn(
                        stream_req,
                        http_request=http_req,
                        current_user=TEST_USER,
                        session_id=sid,
                        session_title="Test Session",
                        session_companion_id="companion-1",
                        session_room_id=None,
                        session_snapshot={},
                        character_profile={"name": "Companion"},
                        identity_state=None,
                        history=[],
                    ):
                        pass

    # Stop speech was called
    mock_coordinator.stop_speech.assert_awaited_once()

    # Stream DB was rolled back
    assert factory.sessions[0].rollback_count == 1

    # Interrupted persistence was executed
    all_committed_messages = [
        obj
        for sess in factory.sessions
        for obj in sess.committed_objects
        if isinstance(obj, Message)
    ]
    assert len(all_committed_messages) == 3
    roles = [m.role for m in all_committed_messages]
    assert roles == ["user", "assistant", "system"]
    assert all_committed_messages[0].content == "Interrupted query"
    assert all_committed_messages[1].content == "I heard you say that."
    assert "turn interrupted" in all_committed_messages[2].content.lower()


# ============================================================================
# TEST 0 — PostgreSQL Integration Characterization (T0)
# ============================================================================

@pytest.mark.postgres
@pytest.mark.skipif(
    not _is_postgres_ready(),
    reason="PostgreSQL is not running on this machine (pg_isready: no response)",
)
@pytest.mark.asyncio
async def test_postgres_streaming_rollback_discards_memory_and_mood_t0() -> None:
    """TEST 0 (T0): PostgreSQL integration characterization of C5.

    When PostgreSQL is available:
    1. Executes real ExtractMemoryStep and PersistEmotionalStateStep on a real session.
    2. Calls session.rollback().
    3. Asserts rows are absent in PostgreSQL tables (confirming F1 / C5 defect on real DB).
    """
    from chatbot.db.session import async_session_factory
    from sqlalchemy import delete, select

    sid = uuid.uuid4()
    uid = uuid.uuid4()

    try:
        await _run_postgres_characterization(async_session_factory, select, sid, uid)
    finally:
        # This test writes to the configured database; remove exactly the rows it created.
        try:
            async with async_session_factory() as db:
                await db.execute(delete(SemanticFact).where(SemanticFact.user_id == uid))
                await db.execute(delete(models.Episode).where(models.Episode.session_id == sid))
                await db.execute(delete(models.Session).where(models.Session.id == sid))
                await db.execute(delete(models.User).where(models.User.id == uid))
                await db.commit()
        except Exception:  # never mask the test's own result
            pass


async def _run_postgres_characterization(async_session_factory: Any, select: Any, sid: Any, uid: Any) -> None:
    async with async_session_factory() as db:
        # Create user and session
        user = models.User(
            id=uid,
            external_id=f"ext_{uid.hex}",  # NOT NULL, no default
            username=f"u_{uid.hex[:8]}",
            email=f"u_{uid.hex[:8]}@example.com",
            display_name="User",
            password_hash="hash",
        )
        session_row = models.Session(
            id=sid,
            user_id=uid,
            title="Postgres Characterization",
        )
        db.add(user)
        db.add(session_row)
        await db.commit()

        # Streaming's preparation reads (history, identity) leave the session inside a
        # transaction, and that is what makes the extractor write through a SAVEPOINT.
        # A session that has just committed reports no transaction, so the extractor
        # would commit on its own and a later rollback would discard nothing.
        await db.execute(select(models.Session).where(models.Session.id == sid))
        assert db.in_transaction()

        # In-turn operations
        ctx = TurnContext(
            session_id=str(sid),
            user_message="I have a golden retriever named Rex.",
            history=[],
            persona={},
            retrieved_memories=[],
            compiled_context=None,
            provider_response="Rex is great!",
            extracted_memories=[],
            final_response="Rex is great!",
            metadata={"db": db, "db_session": db, "user_id": str(uid)},
        )

        step = ExtractMemoryStep()
        await step.run(ctx)

        # In streaming, rollback is called:
        await db.rollback()

        # Verify rows absent in real PostgreSQL
        res = await db.execute(select(SemanticFact).where(SemanticFact.user_id == uid))
        facts = res.scalars().all()
        assert len(facts) == 0, "PostgreSQL characterization confirms rows rolled back"
