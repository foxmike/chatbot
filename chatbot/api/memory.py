from __future__ import annotations

from datetime import datetime, timezone
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.db import get_db
from chatbot.db import models
from chatbot.memory import controls as memory_controls
from chatbot.memory import export as memory_export
from chatbot.memory import search as memory_search


memory_router = APIRouter(prefix="/memories", tags=["memory"])


class PinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pinned: bool


class EditMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    metadata: dict[str, Any] | None = None


class PrivacyLevelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str


class CorrectMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correction: str


class ProvenanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    origin: str
    confidence: float
    source_message_id: str | None = None
    source_turn_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


def _to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


@memory_router.get("")
async def list_memories(
    privacy: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    page = await memory_search.search(
        db,
        domain="semantic_facts",
        offset=offset,
        limit=limit,
    )
    results: list[dict[str, Any]] = []
    for r in page.results:
        item: dict[str, Any] = {
            "id": r.id,
            "content": r.content,
            "score": r.score,
            "created_at": _to_iso(r.created_at),
            "metadata": r.metadata,
        }
        if r.provenance:
            item["provenance"] = r.provenance
        results.append(item)

    return {
        "memories": results,
        "total": page.total,
        "offset": page.offset,
        "limit": page.limit,
    }


@memory_router.get("/search")
async def search_memories(
    query: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    topics: list[str] = Query(default_factory=list),
    tags: list[str] = Query(default_factory=list),
    session_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    domain: memory_search.SearchDomain | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    start_dt = _parse_iso(start)
    end_dt = _parse_iso(end)
    page = await memory_search.search(
        db,
        query=query,
        start=start_dt,
        end=end_dt,
        topics=tuple(topics) if topics else None,
        tags=tuple(tags) if tags else None,
        session_id=session_id,
        user_id=user_id,
        domain=domain,
        offset=offset,
        limit=limit,
    )
    return {
        "results": [
            {
                "domain": r.domain,
                "id": r.id,
                "content": r.content,
                "score": r.score,
                "created_at": _to_iso(r.created_at),
                "metadata": r.metadata,
                "provenance": r.provenance,
            }
            for r in page.results
        ],
        "total": page.total,
        "offset": page.offset,
        "limit": page.limit,
    }


@memory_router.get("/export")
async def export_memories(
    fmt: str = Query(default="json", alias="format"),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    topics: list[str] = Query(default_factory=list),
    privacy_level: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    include_turns: bool = Query(default=True),
    include_memories: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
) -> Any:
    start_dt = _parse_iso(start)
    end_dt = _parse_iso(end)
    bundle = await memory_export.export_memories(
        db,
        start=start_dt,
        end=end_dt,
        topics=tuple(topics) if topics else None,
        privacy_level=privacy_level,
        session_id=session_id,
        user_id=user_id,
        include_turns=include_turns,
        include_memories=include_memories,
    )
    if fmt == "text":
        return PlainTextResponse(content=memory_export.export_to_text(bundle))
    return bundle.as_dict()


@memory_router.get("/{memory_id}/provenance", response_model=ProvenanceResponse)
async def get_provenance(
    memory_id: str,
    db: AsyncSession = Depends(get_db),
) -> ProvenanceResponse:
    try:
        fact_id = uuid.UUID(memory_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Memory {memory_id} not found",
        )
    result = await db.execute(
        sa.select(models.SemanticFact).where(models.SemanticFact.id == fact_id)
    )
    fact = result.scalar_one_or_none()
    if fact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Memory {memory_id} not found",
        )
    return ProvenanceResponse(
        memory_id=str(fact.id),
        origin=fact.source_type,
        confidence=fact.confidence,
        source_message_id=str(fact.source_message_id) if fact.source_message_id else None,
        source_turn_id=str(fact.source_turn_id) if fact.source_turn_id else None,
        created_at=_to_iso(fact.created_at),
        updated_at=_to_iso(fact.updated_at),
    )


@memory_router.patch("/{memory_id}/pin")
async def pin_toggle(
    memory_id: str,
    body: PinRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if body.pinned:
        return await memory_controls.pin_memory(db, memory_id)
    return await memory_controls.unpin_memory(db, memory_id)


@memory_router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    hard: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await memory_controls.delete_memory(db, memory_id, hard=hard)


@memory_router.patch("/{memory_id}")
async def edit_memory(
    memory_id: str,
    body: EditMemoryRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await memory_controls.edit_memory(
        db, memory_id, content=body.content, metadata=body.metadata
    )


@memory_router.patch("/{memory_id}/privacy")
async def set_privacy(
    memory_id: str,
    body: PrivacyLevelRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await memory_controls.set_privacy_level(db, memory_id, body.level)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@memory_router.post("/{memory_id}/do-not-remember")
async def do_not_remember(
    memory_id: str,
    session_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await memory_controls.do_not_remember(db, memory_id, session_id=session_id)


@memory_router.post("/{memory_id}/remember-forever")
async def remember_forever(
    memory_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await memory_controls.remember_forever(db, memory_id)


@memory_router.patch("/{memory_id}/correct")
async def correct_misunderstanding(
    memory_id: str,
    body: CorrectMemoryRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await memory_controls.correct_misunderstanding(
            db, memory_id, body.correction
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))