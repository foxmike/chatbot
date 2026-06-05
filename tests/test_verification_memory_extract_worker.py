"""Verification: memory_extract handler processes extraction via background worker.

Tests the full pipeline:
  BackgroundWorker._dispatch → memory_extract_handler → MemoryExtractor.extract
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from chatbot.perf.background import BackgroundQueue, BackgroundTask
from chatbot.perf.handlers import memory_extract_handler
from chatbot.perf.worker import BackgroundWorker


def test_handler_parses_enqueue_memory_extraction_payload():
    """Verify memory_extract_handler correctly parses a payload from enqueue_memory_extraction."""

    async def run():
        queue = BackgroundQueue()
        await queue.enqueue_memory_extraction(
            user_id="user-1",
            session_id="session-test",
            turn_context={
                "user_message": "Hi, my name is Alice and I live in Berlin.",
                "provider_response": "I'll remember that your name is Alice.",
            },
        )
        task = await queue.dequeue()
        assert task is not None
        assert task.task_type == "memory_extract"
        assert task.payload["session_id"] == "session-test"
        assert task.payload["turn_context"]["user_message"] == "Hi, my name is Alice and I live in Berlin."
        assert task.payload["turn_context"]["provider_response"] == "I'll remember that your name is Alice."

        # Verify handler can parse payload without error (no database → returns [])
        result = await memory_extract_handler(task)
        assert result is None  # Returns None, not []; extract returns []

    asyncio.run(run())


def test_handler_raises_on_missing_session_id():
    """memory_extract_handler raises ValueError when session_id is missing."""

    async def run():
        task = BackgroundTask(
            task_id="t1",
            task_type="memory_extract",
            payload={"turn_context": {}},
            created_at="2026-01-01T00:00:00Z",
        )
        with pytest.raises(ValueError, match="session_id"):
            await memory_extract_handler(task)

    asyncio.run(run())


def test_handler_raises_on_missing_user_message():
    """memory_extract_handler raises ValueError when user_message is missing."""

    async def run():
        task = BackgroundTask(
            task_id="t1",
            task_type="memory_extract",
            payload={
                "session_id": "s1",
                "turn_context": {},
            },
            created_at="2026-01-01T00:00:00Z",
        )
        with pytest.raises(ValueError, match="user_message"):
            await memory_extract_handler(task)

    asyncio.run(run())


def test_handler_reads_session_id_from_turn_context_fallback():
    """session_id can be inside turn_context (fallback path)."""

    async def run():
        task = BackgroundTask(
            task_id="t1",
            task_type="memory_extract",
            payload={
                "turn_context": {
                    "session_id": "ctx-session",
                    "user_message": "Hello",
                },
            },
            created_at="2026-01-01T00:00:00Z",
        )
        # No database → returns None (not [])
        result = await memory_extract_handler(task)
        assert result is None

    asyncio.run(run())


def test_handler_calls_memory_extractor_with_correct_args():
    """Verify handler passes session_id, user_message, provider_response to MemoryExtractor.extract."""

    async def run():
        with patch("chatbot.perf.handlers.MemoryExtractor") as mock_extractor_cls:
            mock_instance = AsyncMock()
            mock_instance.extract = AsyncMock(return_value=[])
            mock_extractor_cls.return_value = mock_instance

            task = BackgroundTask(
                task_id="t2",
                task_type="memory_extract",
                payload={
                    "session_id": "s-extract",
                    "user_message": "I work at Acme Corp.",
                    "provider_response": "Got it, you work at Acme Corp.",
                    "turn_context": {},
                },
                created_at="2026-01-01T00:00:00Z",
            )

            await memory_extract_handler(task)

            mock_extractor_cls.assert_called_once()
            mock_instance.extract.assert_called_once_with(
                session_id="s-extract",
                user_message="I work at Acme Corp.",
                provider_response="Got it, you work at Acme Corp.",
            )

    asyncio.run(run())


def test_handler_forwards_database_to_memory_extractor():
    """The database key from turn_context or payload is forwarded to MemoryExtractor."""

    async def run():
        fake_db = object()

        with patch("chatbot.perf.handlers.MemoryExtractor") as mock_extractor_cls:
            mock_instance = AsyncMock()
            mock_instance.extract = AsyncMock(return_value=[])
            mock_extractor_cls.return_value = mock_instance

            task = BackgroundTask(
                task_id="t3",
                task_type="memory_extract",
                payload={
                    "session_id": "s-db",
                    "user_message": "I like pizza.",
                    "provider_response": "Pizza noted.",
                    "turn_context": {"database": fake_db},
                },
                created_at="2026-01-01T00:00:00Z",
            )

            await memory_extract_handler(task)

            mock_extractor_cls.assert_called_once_with(database=fake_db)

    asyncio.run(run())


def test_background_worker_dispatches_memory_extract_to_handler():
    """Full integration: BackgroundWorker dequeues a memory_extract task and calls the handler."""

    async def run():
        queue = BackgroundQueue()
        stop_event = asyncio.Event()

        handler_called = []

        async def tracking_handler(task: BackgroundTask) -> None:
            handler_called.append(task)

        worker = BackgroundWorker(
            queue,
            stop_event=stop_event,
            handlers={"memory_extract": tracking_handler},
        )

        await queue.enqueue_memory_extraction(
            user_id="u1",
            session_id="s-worker",
            turn_context={
                "user_message": "Test message.",
                "provider_response": "Test response.",
            },
        )

        # Start worker, let it process one task, then stop
        worker_task = worker.start()

        # Wait for the task to be processed
        await asyncio.sleep(0.2)

        await worker.shutdown(timeout_seconds=2.0)

        assert len(handler_called) == 1
        task = handler_called[0]
        assert task.task_type == "memory_extract"
        assert task.payload["session_id"] == "s-worker"
        assert task.payload["turn_context"]["user_message"] == "Test message."
        assert worker.tasks_processed == 1

    asyncio.run(run())


def test_heuristic_extraction_produces_expected_facts():
    """Verify the heuristic extraction functions produce correct output shapes."""
    from chatbot.memory.extraction import _extract_entries

    entries = _extract_entries(
        session_id="s-heur",
        user_message="Hi, my name is Bob and I work at Google. I live in Mountain View.",
        provider_response="I'll remember that your name is Bob and you work at Google.",
    )

    assert isinstance(entries, list)
    # Should have facts from user_message + an episode
    facts = [e for e in entries if e["type"] == "fact"]
    episodes = [e for e in entries if e["type"] == "episode"]

    assert len(facts) >= 1, "Expected at least one fact from user_message"
    assert len(episodes) == 1, "Expected exactly one episode per turn"

    # Verify fact shapes
    for fact in facts:
        assert "content" in fact
        assert "metadata" in fact
        assert fact["metadata"]["source"] in ("user_message", "provider_response")
        assert fact["metadata"]["extractor"] == "heuristic-v1"

    # Verify episode shape
    episode = episodes[0]
    assert "content" in episode
    assert episode["metadata"]["source"] == "turn"
    assert episode["metadata"]["extractor"] == "heuristic-v1"

    # Verify user message "I work at Google" yields a fact
    work_fact = [f for f in facts if "Google" in f["content"]]
    assert len(work_fact) >= 1, f"Expected fact about Google, got facts: {facts}"

    # Verify "my name is" yields a fact
    name_fact = [f for f in facts if "name is Bob" in f["content"] or "my name is Bob" in f["content"].lower()]
    assert len(name_fact) >= 1, f"Expected fact about name, got facts: {facts}"


def test_handler_re_raises_extraction_exception():
    """memory_extract_handler re-raises exceptions from MemoryExtractor.extract."""

    async def run():
        with patch("chatbot.perf.handlers.MemoryExtractor") as mock_extractor_cls:
            mock_instance = AsyncMock()
            mock_instance.extract = AsyncMock(
                side_effect=RuntimeError("Extraction failed")
            )
            mock_extractor_cls.return_value = mock_instance

            task = BackgroundTask(
                task_id="t-err",
                task_type="memory_extract",
                payload={
                    "session_id": "s-err",
                    "user_message": "Boom.",
                    "provider_response": "Okay.",
                    "turn_context": {},
                },
                created_at="2026-01-01T00:00:00Z",
            )

            with pytest.raises(RuntimeError, match="Extraction failed"):
                await memory_extract_handler(task)

    asyncio.run(run())
