"""Durable memory extraction handler for the durable job worker (Phase 2 design, Task 2.2).

Registered with the ``DurableJobWorker`` under ``task_type="memory_extract"``
during final activation (Task 2.2c.2); it must not run before the completion
hook started scheduling jobs (Task 2.2c.1). The handler loads only the messages
belonging to the job's persisted turn using the job's trusted session, user,
companion and turn identifiers, reuses the existing memory extraction
implementation (:class:`~chatbot.memory.extraction.MemoryExtractor`), and
persists extracted memories idempotently through :class:`TurnMemoryStorage`.

Idempotency mechanism: persisted effects are keyed by their provenance — facts
by ``(source_message_id, fact_text)``, episodes by
``(start_message_id, end_message_id)`` — so a retried job that runs after an
earlier attempt already wrote its results (crash between write and success)
finds those keys and completes without duplicate memories.

The deep conversation path still performs inline memory extraction; this
handler adds no second pass there because nothing invokes it yet.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import sqlalchemy as sa

from chatbot.db.models import Episode, Message, Session, SemanticFact
from chatbot.memory.extraction import MemoryExtractor

logger = logging.getLogger(__name__)

EPISODE_TITLE_LIMIT = 160


class _SessionHolder(Protocol):
    """Columns of ``Session``/``Message`` rows this module relies on."""

    user_id: Any
    companion_id: str | None
    id: Any
    role: str
    content: str
    created_at: Any


def _join_messages(messages: list[Any], role: str) -> str:
    return " ".join(
        message.content for message in messages if message.role == role and message.content.strip()
    )


def _trim(text: str | None, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    return cleaned[:limit]


def _source_message_id(source: Any, user_msg: Any, assistant_msg: Any) -> Any | None:
    """Map the extractor's ``metadata.source`` to that source message's row id."""
    normalized = str(source or "")
    if normalized == "user_message":
        return getattr(user_msg, "id", None)
    if normalized == "provider_response":
        return getattr(assistant_msg, "id", None)
    return None


class TurnMemoryStorage:
    """Idempotent provenance-aware persistence for one turn's extracted memories.

    A closure over the turn's resolved session (ownership already checked), the
    job's trusted identifiers, and the turn's source-message rows. Implements
    :class:`~chatbot.memory.extraction.MemoryStorageAdapter`, so it plugs into
    the existing extraction implementation unchanged.
    """

    def __init__(
        self,
        db: Any,
        *,
        session_row: Any,
        session_id: Any,
        turn_id: str,
        user_msg: Any,
        assistant_msg: Any,
    ) -> None:
        self.db = db
        self.session_row = session_row
        self.session_id = session_id
        self.turn_id = turn_id
        self.user_msg = user_msg
        self.assistant_msg = assistant_msg
        self.persistence_error: Exception | None = None

    async def persist(self, session_id: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            return await self._persist(session_id, entries)
        except Exception as exc:
            # The legacy extractor catches storage exceptions. Retain the error
            # so the job handler can fail and roll back instead of acknowledging
            # partially written effects as a successful extraction.
            self.persistence_error = exc
            raise

    async def _persist(self, session_id: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        fact_rows: list[dict[str, Any]] = []
        episode_rows: list[dict[str, Any]] = []

        for entry in entries:
            content = str(entry.get("content") or "").strip()
            metadata = entry.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            if not content:
                continue
            kind = str(entry.get("type") or "")

            if kind == "fact":
                source_message_id = _source_message_id(
                    metadata.get("source"), self.user_msg, self.assistant_msg
                )
                # Provenance is required: without a known source message the
                # write has no uniqueness key and is skipped rather than written.
                if source_message_id is None:
                    logger.warning(
                        "memory_extract: fact %r from session %s has no source message; skipped",
                        content[:80],
                        self.session_id,
                    )
                    continue
                already_persisted = await self.db.scalar(
                    sa.select(SemanticFact.id).where(
                        SemanticFact.source_message_id == source_message_id,
                        SemanticFact.fact_text == content,
                    )
                )
                if already_persisted is not None:
                    continue  # retried work whose earlier attempt already wrote this fact
                fact_rows.append(
                    {
                        "user_id": self.session_row.user_id,
                        "fact_text": content,
                        "source_message_id": source_message_id,
                        "confidence": float(metadata.get("confidence", 1.0)),
                        "source_type": metadata.get("source_type", "inferred"),
                    }
                )

            elif kind == "episode":
                start_message_id = getattr(self.user_msg, "id", None)
                end_message_id = getattr(self.assistant_msg, "id", None)
                if start_message_id is None or end_message_id is None:
                    continue
                already_persisted = await self.db.scalar(
                    sa.select(Episode.id).where(
                        Episode.start_message_id == start_message_id,
                        Episode.end_message_id == end_message_id,
                    )
                )
                if already_persisted is not None:
                    continue  # retried work whose earlier attempt already wrote this episode
                episode_rows.append(
                    {
                        "session_id": self.session_id,
                        "title": _trim(str(metadata.get("title") or "Conversation turn"), EPISODE_TITLE_LIMIT),
                        "summary": content,
                        "start_message_id": start_message_id,
                        "end_message_id": end_message_id,
                    }
                )

        written: list[dict[str, Any]] = []
        if fact_rows:
            await self.db.execute(sa.insert(SemanticFact), fact_rows)
            written.extend(e for e in entries if str(e.get("type")) == "fact")
        if episode_rows:
            await self.db.execute(sa.insert(Episode), episode_rows)
            written.extend(e for e in entries if str(e.get("type")) == "episode")
        return written


def make_durable_memory_extract_handler(
    session_factory: Any,
) -> Any:
    """Build the durable ``memory_extract`` handler bound to a session factory.

    Final activation (Task 2.2c.2) registers the app's session factory with the
    durable worker; tests bind the factory they control.
    """

    async def handler(job: Any) -> None:
        await _process_memory_extract_job(session_factory, job)

    return handler


async def durable_memory_extract_handler(job: Any) -> None:
    """Module-level handler bound to the app's session factory.

    Not activated in the application yet; ``make_durable_memory_extract_handler``
    is the registration entry point.
    """
    from chatbot.db.session import async_session_factory

    await _process_memory_extract_job(async_session_factory, job)


async def _process_memory_extract_job(session_factory: Any, job: Any) -> None:
    """Resolve the persisted turn from the job's trusted identifiers (the job
    row's session/user/companion and the row's turn id), enforce ownership
    against the live session row, load only that turn's messages, reuse
    :class:`MemoryExtractor` with :class:`TurnMemoryStorage`, and persist
    idempotently — a crash before success leaves nothing half-committed beyond
    what a retry dedupes.
    """

    session_id = job.session_id
    user_id = job.user_id
    companion_id = (job.companion_id or "").strip() or None
    turn_id = str(getattr(job, "turn_id", "") or "").strip()
    if not all((session_id, user_id, turn_id)):
        raise ValueError(
            "memory_extract job is missing required identifiers "
            f"(session_id={bool(session_id)} user_id={bool(user_id)} turn_id={bool(turn_id)})"
        )

    async with session_factory() as db:
        # Ownership: the live session row must match the job's trusted user and
        # companion. Wrong-user or wrong-companion access is rejected.
        session_row = await db.get(Session, session_id)
        if session_row is None:
            raise ValueError(f"memory_extract: session {session_id} does not exist")
        if session_row.user_id != user_id:
            raise PermissionError(
                f"memory_extract: session {session_id} does not belong to the job's user"
            )
        if session_row.companion_id != companion_id:
            raise PermissionError(
                f"memory_extract: session {session_id} does not belong to the job's companion"
            )

        result = await db.execute(
            sa.select(Message)
            .where(Message.session_id == session_id, Message.turn_id == turn_id)
            .order_by(Message.created_at)
        )
        messages = list(result.scalars().all())
        user_message = _join_messages(messages, "user")
        assistant_message = _join_messages(messages, "assistant")
        if not user_message and not assistant_message:
            logger.warning(
                "memory_extract: no persisted messages for turn %s in session %s; nothing to extract",
                turn_id,
                session_id,
            )
            return

        user_msg = next((m for m in messages if m.role == "user"), None)
        assistant_msg = next((m for m in messages if m.role == "assistant"), None)

        storage = TurnMemoryStorage(
            db,
            session_row=session_row,
            session_id=session_id,
            turn_id=turn_id,
            user_msg=user_msg,
            assistant_msg=assistant_msg,
        )
        extractor = MemoryExtractor(storage=storage)
        # Extraction runs outside the claim transaction (the worker claims with
        # its own session); this handler owns this persistence transaction.
        written = await extractor.extract(
            session_id=str(session_id),
            user_message=user_message,
            provider_response=assistant_message,
        )
        if storage.persistence_error is not None:
            raise storage.persistence_error
        await db.commit()
        logger.info(
            "memory_extract: %d memories persisted for turn %s (session %s)",
            len(written),
            turn_id,
            session_id,
        )


__all__ = ["TurnMemoryStorage", "durable_memory_extract_handler", "make_durable_memory_extract_handler"]
