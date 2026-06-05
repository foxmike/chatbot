"""Background task handlers for the chatbot performance layer.

Provides ready-to-use handler functions for common background task types.
Handlers match the :data:`~chatbot.perf.worker.TaskHandler` signature so they can be
registered directly with :class:`~chatbot.perf.worker.BackgroundWorker`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from chatbot.memory.extraction import MemoryExtractor
from chatbot.perf.background import BackgroundTask

logger = logging.getLogger(__name__)


async def memory_extract_handler(task: BackgroundTask) -> None:
    """Handle a ``memory_extract`` background task via the memory extraction pipeline.

    Extracts the *session_id*, *user_message*, and *provider_response* from the
    task payload and delegates to
    :class:`~chatbot.memory.extraction.MemoryExtractor`.

    The payload is expected to match the shape produced by
    :meth:`BackgroundQueue.enqueue_memory_extraction()
    <chatbot.perf.background.BackgroundQueue.enqueue_memory_extraction>`::

        {
            "user_id": str,
            "session_id": str,
            "turn_context": {
                "user_message": str,
                "provider_response": str,
            },
        }

    If an optional ``"database"`` key is present in *turn_context* or the payload,
    it is forwarded to :class:`MemoryExtractor` so extracted facts and episodes
    can be persisted.

    Raises :class:`ValueError` when required fields are missing.
    Re-raises any exception from the extractor after logging.
    """

    payload = task.payload
    turn_context = payload.get("turn_context")
    if not isinstance(turn_context, Mapping):
        turn_context = {}

    session_id = _payload_text(payload, "session_id") or _payload_text(
        turn_context, "session_id"
    )
    if not session_id:
        raise ValueError(
            "memory_extract payload missing required field: session_id"
        )

    user_message = _payload_text(payload, "user_message") or _payload_text(
        turn_context, "user_message"
    )
    if not user_message:
        raise ValueError(
            "memory_extract payload missing required field: user_message"
        )

    provider_response = payload.get("provider_response")
    if provider_response is None:
        provider_response = turn_context.get("provider_response")
    provider_response = str(provider_response or "")

    database = payload.get("database") or turn_context.get("database")

    extractor = MemoryExtractor(database=database)
    try:
        await extractor.extract(
            session_id=session_id,
            user_message=user_message,
            provider_response=provider_response,
        )
    except Exception:
        logger.exception(
            "Memory extraction failed for session_id=%s",
            session_id,
        )
        raise


def _payload_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = ["memory_extract_handler"]
