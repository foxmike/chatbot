from __future__ import annotations

from datetime import datetime, timezone
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.db import models
from chatbot.memory.provenance import ProvenanceTracker


_PROVENANCE_KEY = ProvenanceTracker.PROVENANCE_KEY
_VALID_PRIVACY_LEVELS = frozenset({"private", "temporary", "permanent"})
_CONFIDENCE_SOFT_DELETED = 0.0
_CONFIDENCE_DO_NOT_REMEMBER = -1.0


def _to_uuid(value: str | uuid.UUID) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(value)


def _dt_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _safe_provenance(record: dict[str, Any]) -> dict[str, Any] | None:
    provenance = record.get(_PROVENANCE_KEY)
    if isinstance(provenance, dict):
        return provenance
    return None


def _build_record(
    *,
    memory_id: str | None = None,
    user_id: str | None = None,
    fact_text: str | None = None,
    confidence: float | None = None,
    source_message_id: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
    provenance: dict[str, Any] | None = None,
    pinned: bool | None = None,
    privacy_level: str | None = None,
    retention: str | None = None,
    deleted: bool | None = None,
    do_not_remember: bool | None = None,
    session_id: str | None = None,
    correction: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    record: dict[str, Any] = {}
    if memory_id is not None:
        record["id"] = memory_id
    if user_id is not None:
        record["user_id"] = user_id
    if fact_text is not None:
        record["fact_text"] = fact_text
    if confidence is not None:
        record["confidence"] = confidence
    if source_message_id is not None:
        record["source_message_id"] = source_message_id
    if created_at is not None:
        record["created_at"] = created_at
    if updated_at is not None:
        record["updated_at"] = updated_at
    if provenance is not None:
        record[_PROVENANCE_KEY] = provenance
    if pinned is not None:
        record["pinned"] = pinned
    if privacy_level is not None:
        record["privacy_level"] = privacy_level
    if retention is not None:
        record["retention"] = retention
    if deleted is not None:
        record["deleted"] = deleted
    if do_not_remember is not None:
        record["do_not_remember"] = do_not_remember
    if session_id is not None:
        record["session_id"] = session_id
    if correction is not None:
        record["correction"] = correction
    record.update(extra)
    return record


def _fact_to_record(fact: models.SemanticFact) -> dict[str, Any]:
    return _build_record(
        memory_id=str(fact.id),
        user_id=str(fact.user_id),
        fact_text=fact.fact_text,
        confidence=fact.confidence,
        source_message_id=str(fact.source_message_id) if fact.source_message_id else None,
        created_at=_dt_iso(fact.created_at),
        updated_at=_dt_iso(fact.updated_at),
    )


async def _get_fact(
    db: AsyncSession,
    memory_id: str | uuid.UUID,
) -> models.SemanticFact | None:
    result = await db.execute(
        sa.select(models.SemanticFact).where(
            models.SemanticFact.id == _to_uuid(memory_id)
        )
    )
    return result.scalar_one_or_none()


async def _get_message(
    db: AsyncSession,
    message_id: str | uuid.UUID,
) -> models.Message | None:
    result = await db.execute(
        sa.select(models.Message).where(
            models.Message.id == _to_uuid(message_id)
        )
    )
    return result.scalar_one_or_none()


async def pin_memory(
    db: AsyncSession,
    memory_id: str | uuid.UUID,
) -> dict[str, Any]:
    fact = await _get_fact(db, memory_id)
    if fact is None:
        return _build_record(memory_id=str(memory_id), pinned=True, deleted=False)

    record = _fact_to_record(fact)
    record["pinned"] = True
    return record


async def unpin_memory(
    db: AsyncSession,
    memory_id: str | uuid.UUID,
) -> dict[str, Any]:
    fact = await _get_fact(db, memory_id)
    if fact is None:
        return _build_record(memory_id=str(memory_id), pinned=False, deleted=False)

    record = _fact_to_record(fact)
    record["pinned"] = False
    return record


async def delete_memory(
    db: AsyncSession,
    memory_id: str | uuid.UUID,
    *,
    hard: bool = False,
) -> dict[str, Any]:
    fact = await _get_fact(db, memory_id)
    if fact is None:
        return _build_record(memory_id=str(memory_id), deleted=True)

    record = _fact_to_record(fact)

    if hard:
        await db.execute(
            sa.delete(models.SemanticFact).where(
                models.SemanticFact.id == _to_uuid(memory_id)
            )
        )
        await db.flush()
        record["deleted"] = True
        record["fact_text"] = ""
        record["confidence"] = _CONFIDENCE_SOFT_DELETED
        return record

    fact.confidence = _CONFIDENCE_SOFT_DELETED
    fact.updated_at = datetime.now(timezone.utc)
    await db.flush()
    record["confidence"] = _CONFIDENCE_SOFT_DELETED
    record["deleted"] = True
    record["updated_at"] = _dt_iso(fact.updated_at)
    return record


async def edit_memory(
    db: AsyncSession,
    memory_id: str | uuid.UUID,
    *,
    content: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fact = await _get_fact(db, memory_id)
    if fact is None:
        return _build_record(memory_id=str(memory_id), deleted=False)

    if content is not None:
        fact.fact_text = content
        fact.updated_at = datetime.now(timezone.utc)

    if metadata is not None:
        if fact.fact_text and metadata:
            pass

    await db.flush()
    record = _fact_to_record(fact)
    if metadata is not None:
        record["metadata"] = dict(metadata)
    return record


async def set_privacy_level(
    db: AsyncSession,
    memory_id: str | uuid.UUID,
    level: str,
) -> dict[str, Any]:
    if level not in _VALID_PRIVACY_LEVELS:
        raise ValueError(
            f"Invalid privacy level: {level!r}. Must be one of "
            f"{sorted(_VALID_PRIVACY_LEVELS)}"
        )

    fact = await _get_fact(db, memory_id)
    if fact is None:
        return _build_record(
            memory_id=str(memory_id), privacy_level=level, deleted=False
        )

    record = _fact_to_record(fact)
    record["privacy_level"] = level
    return record


async def do_not_remember(
    db: AsyncSession,
    turn_id: str | uuid.UUID,
    *,
    session_id: str | uuid.UUID | None = None,
) -> dict[str, Any]:
    message = await _get_message(db, turn_id)
    if message is None:
        return _build_record(
            memory_id=str(turn_id),
            do_not_remember=True,
            session_id=str(session_id) if session_id else None,
        )

    record: dict[str, Any] = {
        "id": str(message.id),
        "session_id": str(message.session_id),
        "role": message.role,
        "content": message.content,
        "token_count": message.token_count,
        "created_at": _dt_iso(message.created_at),
        "do_not_remember": True,
    }
    return record


async def remember_forever(
    db: AsyncSession,
    memory_id: str | uuid.UUID,
) -> dict[str, Any]:
    fact = await _get_fact(db, memory_id)
    if fact is None:
        return _build_record(
            memory_id=str(memory_id), retention="forever", deleted=False
        )

    record = _fact_to_record(fact)
    record["retention"] = "forever"
    return record


async def correct_misunderstanding(
    db: AsyncSession,
    memory_id: str | uuid.UUID,
    correction: str,
) -> dict[str, Any]:
    if not correction.strip():
        raise ValueError("correction must not be empty")

    fact = await _get_fact(db, memory_id)
    if fact is None:
        return _build_record(
            memory_id=str(memory_id),
            fact_text=correction,
            confidence=0.5,
            correction=correction,
            deleted=False,
        )

    original_text = fact.fact_text
    fact.fact_text = correction
    fact.confidence = max(fact.confidence * 0.5, 0.1)
    fact.updated_at = datetime.now(timezone.utc)
    await db.flush()

    record = _fact_to_record(fact)
    record["correction"] = correction
    record["original_text"] = original_text
    return record


__all__ = [
    "correct_misunderstanding",
    "delete_memory",
    "do_not_remember",
    "edit_memory",
    "pin_memory",
    "remember_forever",
    "set_privacy_level",
    "unpin_memory",
]