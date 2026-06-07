"""Background task handlers for the chatbot performance layer.

Provides ready-to-use handler functions for common background task types.
Handlers match the :data:`~chatbot.perf.worker.TaskHandler` signature so they can be
registered directly with :class:`~chatbot.perf.worker.BackgroundWorker`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from chatbot.consolidation.worker import ConsolidationWorker
from chatbot.memory.extraction import MemoryExtractor
from chatbot.perf.background import BackgroundTask
from chatbot.perf.worker import TaskHandler
from chatbot.providers import Provider

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


async def consolidate_handler(task: BackgroundTask) -> None:
    """Handle a ``consolidate`` background task via the consolidation pipeline.

    Validates the payload and delegates to
    :class:`~chatbot.consolidation.worker.ConsolidationWorker`.

    The payload is expected to match the shape produced by
    :meth:`BackgroundQueue.enqueue_consolidation()
    <chatbot.perf.background.BackgroundQueue.enqueue_consolidation>`::

        {
            "user_id": str,
        }

    An optional ``"session_id"`` key scopes consolidation to a single session.

    Consolidation runs the full pipeline: summarization, contradiction resolution,
    and memory decay pruning.

    Raises :class:`ValueError` when required fields are missing.
    Re-raises any exception from the consolidation worker after logging.

    .. note::

        This handler requires a :class:`~chatbot.providers.Provider` and a
        ``memory_store``.  Use :func:`make_consolidate_handler` when these
        dependencies are not available at module level.
    """

    payload = task.payload

    user_id = _payload_text(payload, "user_id")
    if not user_id:
        raise ValueError(
            "consolidate payload missing required field: user_id"
        )

    session_id = _payload_text(payload, "session_id")

    logger.warning(
        "consolidate_handler called without a configured provider "
        "and memory_store; use make_consolidate_handler() to create a "
        "fully-wired handler.  Skipping consolidation for user_id=%s "
        "session_id=%s",
        user_id,
        session_id,
    )


def make_consolidate_handler(
    memory_store: Any,
    *,
    provider: Provider | None = None,
    coalescing_window_seconds: float = 60.0,
    recent_entry_limit: int = 50,
) -> TaskHandler:
    """Return a fully-configured ``consolidate`` task handler.

    The returned handler is an :term:`awaitable` callable matching the
    :data:`~chatbot.perf.worker.TaskHandler` signature and can be registered
    directly with :class:`~chatbot.perf.worker.BackgroundWorker`.

    Parameters
    ----------
    memory_store:
        Backing store for memory entries and consolidation metadata.
    provider:
        Completion provider used for summarization and contradiction resolution.
        When *None* (default) the default provider from application settings
        is used.
    coalescing_window_seconds:
        Minimum interval between successive consolidation runs for the same
        session (default ``60.0``).
    recent_entry_limit:
        Maximum recent entries to consider during contradiction resolution
        (default ``50``).
    """

    if provider is None:
        provider = _default_consolidation_provider()

    worker = ConsolidationWorker(
        provider=provider,
        memory_store=memory_store,
        coalescing_window_seconds=coalescing_window_seconds,
        recent_entry_limit=recent_entry_limit,
    )

    async def handler(task: BackgroundTask) -> None:
        payload = task.payload

        user_id = _payload_text(payload, "user_id")
        if not user_id:
            raise ValueError(
                "consolidate payload missing required field: user_id"
            )

        session_id = _payload_text(payload, "session_id")
        session_ids: list[str] = [session_id] if session_id else [user_id]

        for sid in session_ids:
            try:
                await worker.run_consolidation(sid)
            except Exception as exc:
                logger.warning(
                    "Consolidation failed for session_id=%s: %s",
                    sid,
                    exc,
                )

    return handler


def _default_consolidation_provider() -> Provider:
    """Return the default LLM provider from application settings."""

    try:
        from chatbot.config import get_settings
    except ImportError:
        raise RuntimeError(
            "Unable to import chatbot.config; ensure the settings module is available"
        )

    try:
        from chatbot.providers.bootstrap import build_default_registry
    except ImportError:
        raise RuntimeError(
            "Unable to import chatbot.providers.bootstrap; ensure the provider bootstrap is available"
        )

    settings = get_settings()
    registry = build_default_registry(settings)
    return registry.get(settings.default_provider)


__all__ = ["consolidate_handler", "make_consolidate_handler", "memory_extract_handler"]
