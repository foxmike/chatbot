from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.db import models
from chatbot.memory.provenance import ProvenanceAnnotation, ProvenanceTracker

_VALID_PRIVACY_LEVELS = frozenset({"private", "temporary", "permanent"})
_PROVENANCE_KEY = ProvenanceTracker.PROVENANCE_KEY
_DEFAULT_EXPORT_LIMIT = 10_000


def _to_uuid(value: str | uuid.UUID) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(value)


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _dt_iso(value: datetime | None) -> str | None:
    normalized = _normalize_datetime(value)
    if normalized is None:
        return None
    return normalized.isoformat()


def _safe_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _normalize_datetime(value)
    return None


def _has_content(text: str | None) -> bool:
    return bool(text and text.strip())


def _escape_like(value: str) -> str:
    return value.replace("%", "\\%").replace("_", "\\_")


def _unique_sorted(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(item).strip() for item in items if str(item).strip()}))


def _derive_privacy_level(fact: models.SemanticFact) -> str:
    if fact.confidence <= 0.0:
        return "private"
    if fact.confidence >= 0.9:
        return "permanent"
    return "temporary"


@dataclass(frozen=True, slots=True)
class ExportFilter:
    start: datetime | None = None
    end: datetime | None = None
    topics: tuple[str, ...] = ()
    privacy_level: str | None = None
    session_id: str | uuid.UUID | None = None
    user_id: str | uuid.UUID | None = None
    include_turns: bool = True
    include_memories: bool = True

    def __post_init__(self) -> None:
        if self.privacy_level is not None and self.privacy_level not in _VALID_PRIVACY_LEVELS:
            raise ValueError(
                f"Invalid privacy level: {self.privacy_level!r}. Must be one of "
                f"{sorted(_VALID_PRIVACY_LEVELS)}"
            )
        normalized_start = _normalize_datetime(self.start)
        normalized_end = _normalize_datetime(self.end)
        if (
            normalized_start is not None
            and normalized_end is not None
            and normalized_start > normalized_end
        ):
            raise ValueError("start must be before or equal to end")


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    id: str
    user_id: str | None
    fact_text: str
    confidence: float
    source_message_id: str | None
    created_at: datetime | None
    updated_at: datetime | None
    provenance: dict[str, Any] | None
    privacy_level: str
    topics: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "fact_text": self.fact_text,
            "confidence": self.confidence,
            "privacy_level": self.privacy_level,
        }
        if self.user_id is not None:
            result["user_id"] = self.user_id
        if self.source_message_id is not None:
            result["source_message_id"] = self.source_message_id
        if self.created_at is not None:
            result["created_at"] = _dt_iso(self.created_at)
        if self.updated_at is not None:
            result["updated_at"] = _dt_iso(self.updated_at)
        if self.provenance is not None:
            result["provenance"] = dict(self.provenance)
        if self.topics:
            result["topics"] = list(self.topics)
        return result


@dataclass(frozen=True, slots=True)
class TurnEntry:
    id: str
    session_id: str
    role: str
    content: str
    token_count: int | None
    created_at: datetime | None
    provenance: dict[str, Any] | None
    topics: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
        }
        if self.token_count is not None:
            result["token_count"] = self.token_count
        if self.created_at is not None:
            result["created_at"] = _dt_iso(self.created_at)
        if self.provenance is not None:
            result["provenance"] = dict(self.provenance)
        if self.topics:
            result["topics"] = list(self.topics)
        return result


@dataclass(frozen=True, slots=True)
class ExportBundle:
    export_id: str
    created_at: datetime
    filter: ExportFilter
    memories: list[MemoryEntry]
    turns: list[TurnEntry]
    summary: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "export_id": self.export_id,
            "created_at": _dt_iso(self.created_at),
            "filter": _export_filter_as_dict(self.filter),
            "summary": dict(self.summary),
            "memories": [entry.as_dict() for entry in self.memories],
            "turns": [entry.as_dict() for entry in self.turns],
        }
        return result


def _export_filter_as_dict(ef: ExportFilter) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if ef.start is not None:
        result["start"] = _dt_iso(ef.start)
    if ef.end is not None:
        result["end"] = _dt_iso(ef.end)
    if ef.topics:
        result["topics"] = list(ef.topics)
    if ef.privacy_level is not None:
        result["privacy_level"] = ef.privacy_level
    if ef.session_id is not None:
        result["session_id"] = str(ef.session_id)
    if ef.user_id is not None:
        result["user_id"] = str(ef.user_id)
    result["include_turns"] = ef.include_turns
    result["include_memories"] = ef.include_memories
    return result


async def export_memories(
    db: AsyncSession,
    *,
    filters: ExportFilter | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    topics: tuple[str, ...] | None = None,
    privacy_level: str | None = None,
    session_id: str | uuid.UUID | None = None,
    user_id: str | uuid.UUID | None = None,
    include_turns: bool | None = None,
    include_memories: bool | None = None,
    limit: int | None = None,
) -> ExportBundle:
    export_filter = _merge_filters(
        filters,
        start=start,
        end=end,
        topics=topics,
        privacy_level=privacy_level,
        session_id=session_id,
        user_id=user_id,
        include_turns=include_turns,
        include_memories=include_memories,
    )
    export_filter.__post_init__()

    resolved_limit = limit if limit is not None else _DEFAULT_EXPORT_LIMIT
    if resolved_limit <= 0:
        raise ValueError("limit must be greater than zero")

    memory_entries: list[MemoryEntry] = []
    turn_entries: list[TurnEntry] = []

    if export_filter.include_memories:
        memory_entries = await _query_memories(db, export_filter, resolved_limit)
    if export_filter.include_turns:
        turn_entries = await _query_turns(db, export_filter, resolved_limit)

    export_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)

    summary: dict[str, Any] = {
        "total_memories": len(memory_entries),
        "total_turns": len(turn_entries),
        "total_entries": len(memory_entries) + len(turn_entries),
        "generated_at": _dt_iso(created_at),
    }

    return ExportBundle(
        export_id=export_id,
        created_at=created_at,
        filter=export_filter,
        memories=memory_entries,
        turns=turn_entries,
        summary=summary,
    )


def export_to_json(bundle: ExportBundle, *, indent: int | None = 2) -> str:
    return json.dumps(bundle.as_dict(), indent=indent, ensure_ascii=False)


def export_to_text(bundle: ExportBundle) -> str:
    lines: list[str] = []
    lines.append(f"Export: {bundle.export_id}")
    lines.append(f"Generated: {_dt_iso(bundle.created_at)}")
    lines.append("")

    ef = bundle.filter
    lines.append("Filters:")
    if ef.start is not None:
        lines.append(f"  Start: {_dt_iso(ef.start)}")
    if ef.end is not None:
        lines.append(f"  End: {_dt_iso(ef.end)}")
    if ef.topics:
        lines.append(f"  Topics: {', '.join(ef.topics)}")
    if ef.privacy_level is not None:
        lines.append(f"  Privacy Level: {ef.privacy_level}")
    if ef.session_id is not None:
        lines.append(f"  Session: {ef.session_id}")
    if ef.user_id is not None:
        lines.append(f"  User: {ef.user_id}")
    lines.append(f"  Include Turns: {ef.include_turns}")
    lines.append(f"  Include Memories: {ef.include_memories}")
    lines.append("")

    lines.append("Summary:")
    lines.append(f"  Total Memories: {bundle.summary.get('total_memories', 0)}")
    lines.append(f"  Total Turns: {bundle.summary.get('total_turns', 0)}")
    lines.append(f"  Total Entries: {bundle.summary.get('total_entries', 0)}")
    lines.append("")

    if bundle.memories:
        lines.append("=" * 72)
        lines.append("MEMORIES")
        lines.append("=" * 72)
        lines.append("")
        for idx, entry in enumerate(bundle.memories, start=1):
            lines.append(f"--- Memory {idx} ---")
            lines.append(f"ID: {entry.id}")
            if entry.user_id:
                lines.append(f"User: {entry.user_id}")
            lines.append(f"Confidence: {entry.confidence}")
            lines.append(f"Privacy: {entry.privacy_level}")
            if entry.source_message_id:
                lines.append(f"Source Message: {entry.source_message_id}")
            if entry.created_at:
                lines.append(f"Created: {_dt_iso(entry.created_at)}")
            if entry.updated_at:
                lines.append(f"Updated: {_dt_iso(entry.updated_at)}")
            if entry.provenance:
                lines.append(f"Provenance: {json.dumps(entry.provenance, ensure_ascii=False)}")
            if entry.topics:
                lines.append(f"Topics: {', '.join(entry.topics)}")
            lines.append("")
            lines.append(entry.fact_text)
            lines.append("")

    if bundle.turns:
        lines.append("=" * 72)
        lines.append("CONVERSATION TURNS")
        lines.append("=" * 72)
        lines.append("")
        for idx, entry in enumerate(bundle.turns, start=1):
            lines.append(f"--- Turn {idx} ---")
            lines.append(f"ID: {entry.id}")
            lines.append(f"Session: {entry.session_id}")
            lines.append(f"Role: {entry.role}")
            if entry.token_count is not None:
                lines.append(f"Tokens: {entry.token_count}")
            if entry.created_at:
                lines.append(f"Created: {_dt_iso(entry.created_at)}")
            if entry.provenance:
                lines.append(f"Provenance: {json.dumps(entry.provenance, ensure_ascii=False)}")
            if entry.topics:
                lines.append(f"Topics: {', '.join(entry.topics)}")
            lines.append("")
            lines.append(entry.content)
            lines.append("")

    return "\n".join(lines)


def _merge_filters(
    filters: ExportFilter | None,
    *,
    start: datetime | None,
    end: datetime | None,
    topics: tuple[str, ...] | None,
    privacy_level: str | None,
    session_id: str | uuid.UUID | None,
    user_id: str | uuid.UUID | None,
    include_turns: bool | None,
    include_memories: bool | None,
) -> ExportFilter:
    resolved_start = (
        start if start is not None else (filters.start if filters is not None else None)
    )
    resolved_end = (
        end if end is not None else (filters.end if filters is not None else None)
    )
    resolved_topics = _unique_sorted(topics or ())
    if not resolved_topics and filters is not None:
        resolved_topics = _unique_sorted(filters.topics)
    resolved_privacy = (
        privacy_level
        if privacy_level is not None
        else (filters.privacy_level if filters is not None else None)
    )
    resolved_session = (
        session_id
        if session_id is not None
        else (filters.session_id if filters is not None else None)
    )
    resolved_user = (
        user_id if user_id is not None else (filters.user_id if filters is not None else None)
    )
    resolved_include_turns = (
        include_turns
        if include_turns is not None
        else (filters.include_turns if filters is not None else True)
    )
    resolved_include_memories = (
        include_memories
        if include_memories is not None
        else (filters.include_memories if filters is not None else True)
    )

    return ExportFilter(
        start=resolved_start,
        end=resolved_end,
        topics=resolved_topics,
        privacy_level=resolved_privacy,
        session_id=resolved_session,
        user_id=resolved_user,
        include_turns=resolved_include_turns,
        include_memories=resolved_include_memories,
    )


async def _query_memories(
    db: AsyncSession,
    filters: ExportFilter,
    limit: int,
) -> list[MemoryEntry]:
    stmt = sa.select(models.SemanticFact)

    if filters.session_id is not None:
        stmt = stmt.where(sa.false())

    if filters.user_id is not None:
        stmt = stmt.where(models.SemanticFact.user_id == _to_uuid(filters.user_id))

    normalized_start = _normalize_datetime(filters.start)
    normalized_end = _normalize_datetime(filters.end)
    if normalized_start is not None:
        stmt = stmt.where(models.SemanticFact.created_at >= normalized_start)
    if normalized_end is not None:
        stmt = stmt.where(models.SemanticFact.created_at <= normalized_end)

    if filters.topics:
        for topic in filters.topics:
            if topic:
                stmt = stmt.where(
                    models.SemanticFact.fact_text.ilike(f"%{_escape_like(topic)}%")
                )

    stmt = stmt.order_by(models.SemanticFact.created_at.desc()).limit(limit)

    result = await db.execute(stmt)
    rows = result.scalars().all()

    entries: list[MemoryEntry] = []
    for fact in rows:
        privacy_level = _derive_privacy_level(fact)

        if filters.privacy_level is not None and privacy_level != filters.privacy_level:
            continue

        provenance = ProvenanceAnnotation(
            origin="inferred",
            source_message_id=(
                str(fact.source_message_id) if fact.source_message_id else None
            ),
            confidence=fact.confidence,
            created_at=_normalize_datetime(fact.created_at),
        )
        matched_topics = tuple(
            topic
            for topic in filters.topics
            if topic and topic.lower() in fact.fact_text.lower()
        )

        entries.append(
            MemoryEntry(
                id=str(fact.id),
                user_id=str(fact.user_id),
                fact_text=fact.fact_text,
                confidence=fact.confidence,
                source_message_id=(
                    str(fact.source_message_id) if fact.source_message_id else None
                ),
                created_at=_normalize_datetime(fact.created_at),
                updated_at=_normalize_datetime(fact.updated_at),
                provenance=provenance.as_dict(),
                privacy_level=privacy_level,
                topics=matched_topics or filters.topics,
            )
        )

    return entries


async def _query_turns(
    db: AsyncSession,
    filters: ExportFilter,
    limit: int,
) -> list[TurnEntry]:
    stmt = sa.select(models.Message)

    if filters.session_id is not None:
        stmt = stmt.where(models.Message.session_id == _to_uuid(filters.session_id))

    if filters.user_id is not None:
        stmt = stmt.where(sa.false())

    normalized_start = _normalize_datetime(filters.start)
    normalized_end = _normalize_datetime(filters.end)
    if normalized_start is not None:
        stmt = stmt.where(models.Message.created_at >= normalized_start)
    if normalized_end is not None:
        stmt = stmt.where(models.Message.created_at <= normalized_end)

    if filters.topics:
        for topic in filters.topics:
            if topic:
                stmt = stmt.where(
                    models.Message.content.ilike(f"%{_escape_like(topic)}%")
                )

    stmt = stmt.order_by(models.Message.created_at.desc()).limit(limit)

    result = await db.execute(stmt)
    rows = result.scalars().all()

    entries: list[TurnEntry] = []
    for message in rows:
        provenance = ProvenanceAnnotation(
            origin="explicit",
            source_message_id=str(message.id),
            confidence=1.0,
            created_at=_normalize_datetime(message.created_at),
        )
        matched_topics = tuple(
            topic
            for topic in filters.topics
            if topic and topic.lower() in message.content.lower()
        )

        entries.append(
            TurnEntry(
                id=str(message.id),
                session_id=str(message.session_id),
                role=message.role,
                content=message.content,
                token_count=message.token_count,
                created_at=_normalize_datetime(message.created_at),
                provenance=provenance.as_dict(),
                topics=matched_topics or filters.topics,
            )
        )

    return entries


__all__ = [
    "ExportBundle",
    "ExportFilter",
    "MemoryEntry",
    "TurnEntry",
    "export_memories",
    "export_to_json",
    "export_to_text",
]