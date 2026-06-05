"""Continuity API endpoints — timeline, narrative threads, and rituals."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from chatbot.continuity.narrative import (
    NarrativeThread,
    ThreadCallback,
    build_narrative_threads,
    generate_thread_callback,
)
from chatbot.continuity.rituals import (
    Ritual,
    create_ritual,
    deactivate_ritual,
    get_rituals,
)
from chatbot.continuity.timeline import (
    TimelineEntry,
    TimelineFilter,
    get_active_projects,
    get_pending_promises,
    get_timeline,
    resolve_entry,
)

# ---------------------------------------------------------------------------
# Router instances — three prefixes matching the frontend client paths
# ---------------------------------------------------------------------------

timeline_router = APIRouter(prefix="/timeline", tags=["continuity"])
narrative_router = APIRouter(prefix="/narrative", tags=["continuity"])
rituals_router = APIRouter(prefix="/rituals", tags=["continuity"])


# ---------------------------------------------------------------------------
# Timeline Pydantic models
# ---------------------------------------------------------------------------


class TimelineEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    session_id: str
    entry_type: str
    title: str
    description: str
    content: str
    turn_id: str
    created_at: str
    updated_at: str
    status: str
    tags: list[str]
    metadata: dict[str, Any]


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: str | None = None


def _entry_to_response(entry: TimelineEntry) -> TimelineEntryResponse:
    return TimelineEntryResponse(
        entry_id=entry.entry_id,
        session_id=entry.session_id,
        entry_type=entry.entry_type,
        title=entry.title,
        description=entry.description,
        content=entry.content,
        turn_id=entry.turn_id,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        status=entry.status,
        tags=entry.tags,
        metadata=entry.metadata,
    )


# ---------------------------------------------------------------------------
# Timeline routes
# ---------------------------------------------------------------------------


@timeline_router.get("/{session_id}", response_model=list[TimelineEntryResponse])
async def fetch_timeline(
    session_id: str,
    entry_types: list[str] | None = Query(default=None),
    statuses: list[str] | None = Query(default=None),
    tags: list[str] | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
) -> list[TimelineEntryResponse]:
    """Return timeline entries for a session, optionally filtered."""
    timeline_filter = TimelineFilter(
        session_id=None,
        entry_types=entry_types,
        statuses=statuses,
        tags=tags,
        date_from=date_from,
        date_to=date_to,
    )
    entries = await get_timeline(session_id, timeline_filter)
    return [_entry_to_response(entry) for entry in entries]


@timeline_router.get(
    "/{session_id}/projects/active",
    response_model=list[TimelineEntryResponse],
)
async def fetch_active_projects(
    session_id: str,
) -> list[TimelineEntryResponse]:
    """Return active running projects for a session."""
    entries = await get_active_projects(session_id)
    return [_entry_to_response(entry) for entry in entries]


@timeline_router.get(
    "/{session_id}/promises/pending",
    response_model=list[TimelineEntryResponse],
)
async def fetch_pending_promises(
    session_id: str,
) -> list[TimelineEntryResponse]:
    """Return pending promises for a session."""
    entries = await get_pending_promises(session_id)
    return [_entry_to_response(entry) for entry in entries]


@timeline_router.post("/{entry_id}/resolve", response_model=TimelineEntryResponse)
async def resolve_timeline_entry(
    entry_id: str,
    body: ResolveRequest = ResolveRequest(),
) -> TimelineEntryResponse:
    """Resolve a timeline entry (milestone, promise, etc.)."""
    try:
        entry = await resolve_entry(entry_id, body.resolution)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    return _entry_to_response(entry)


# ---------------------------------------------------------------------------
# Narrative Pydantic models
# ---------------------------------------------------------------------------


class NarrativeThreadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    session_id: str
    label: str
    turns: list[str]
    created_at: str
    last_referenced_at: str
    relevance_score: float
    summary: str


class ThreadCallbackResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    label: str
    callback_text: str
    turn_id: str
    confidence: float
    thread_summary: str


class CallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str


def _thread_to_response(thread: NarrativeThread) -> NarrativeThreadResponse:
    return NarrativeThreadResponse(
        thread_id=thread.thread_id,
        session_id=thread.session_id,
        label=thread.label,
        turns=thread.turns,
        created_at=thread.created_at,
        last_referenced_at=thread.last_referenced_at,
        relevance_score=thread.relevance_score,
        summary=thread.summary,
    )


def _callback_to_response(callback: ThreadCallback) -> ThreadCallbackResponse:
    return ThreadCallbackResponse(
        thread_id=callback.thread_id,
        label=callback.label,
        callback_text=callback.callback_text,
        turn_id=callback.turn_id,
        confidence=callback.confidence,
        thread_summary=callback.thread_summary,
    )


# ---------------------------------------------------------------------------
# Narrative routes
# ---------------------------------------------------------------------------


@narrative_router.get(
    "/{session_id}/threads",
    response_model=list[NarrativeThreadResponse],
)
async def fetch_narrative_threads(
    session_id: str,
) -> list[NarrativeThreadResponse]:
    """Build and return narrative threads for a session."""
    threads = await build_narrative_threads(session_id)
    return [_thread_to_response(thread) for thread in threads]


@narrative_router.post(
    "/{session_id}/callbacks",
    response_model=list[ThreadCallbackResponse],
)
async def fetch_thread_callbacks(
    session_id: str,
    body: CallbackRequest,
) -> list[ThreadCallbackResponse]:
    """Generate thread callbacks relevant to the current message."""
    callbacks = await generate_thread_callback(session_id, body.message)
    return [_callback_to_response(callback) for callback in callbacks]


# ---------------------------------------------------------------------------
# Rituals Pydantic models
# ---------------------------------------------------------------------------


class RitualResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ritual_id: str
    session_id: str
    name: str
    ritual_type: str
    description: str
    schedule_description: str
    last_triggered_at: str | None
    next_trigger_at: str | None
    is_active: bool
    trigger_prompt: str | None
    created_at: str
    metadata: dict[str, Any]


class CreateRitualRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    ritual_type: str
    description: str
    schedule_description: str
    trigger_prompt: str | None = None


def _ritual_to_response(ritual: Ritual) -> RitualResponse:
    return RitualResponse(
        ritual_id=ritual.ritual_id,
        session_id=ritual.session_id,
        name=ritual.name,
        ritual_type=ritual.ritual_type,
        description=ritual.description,
        schedule_description=ritual.schedule_description,
        last_triggered_at=ritual.last_triggered_at,
        next_trigger_at=ritual.next_trigger_at,
        is_active=ritual.is_active,
        trigger_prompt=ritual.trigger_prompt,
        created_at=ritual.created_at,
        metadata=ritual.metadata,
    )


# ---------------------------------------------------------------------------
# Rituals routes
# ---------------------------------------------------------------------------


@rituals_router.get("/{session_id}", response_model=list[RitualResponse])
async def fetch_rituals(
    session_id: str,
    type: str | None = Query(default=None),
) -> list[RitualResponse]:
    """Return rituals for a session, optionally filtered by type."""
    rituals = await get_rituals(session_id, ritual_type=type)
    return [_ritual_to_response(ritual) for ritual in rituals]


@rituals_router.post("/{session_id}", response_model=RitualResponse)
async def create_ritual_endpoint(
    session_id: str,
    body: CreateRitualRequest,
) -> RitualResponse:
    """Create a new ritual for a session."""
    try:
        ritual = await create_ritual(
            session_id=session_id,
            name=body.name,
            ritual_type=body.ritual_type,
            description=body.description,
            schedule_description=body.schedule_description,
            trigger_prompt=body.trigger_prompt,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return _ritual_to_response(ritual)


@rituals_router.delete("/{ritual_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_ritual_endpoint(ritual_id: str) -> None:
    """Deactivate a ritual (soft delete)."""
    try:
        await deactivate_ritual(ritual_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
