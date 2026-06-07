"""Chat endpoint for the chatbot engine."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.api.sessions import sessions_store
from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.companions.companion import CompanionManager
from chatbot.core.steps import StepResult, TurnContext
from chatbot.db import get_db
from chatbot.db.models import IdentityStateModel, Message, Session

try:
    from chatbot.core.turn import build_default_pipeline as _core_build_default_pipeline
except ImportError:
    _core_build_default_pipeline = None

try:
    from chatbot.perf.cache import PersonaCache
    _perf_cache_available = True
except ImportError:
    PersonaCache = None  # type: ignore[assignment]
    _perf_cache_available = False

try:
    from chatbot.perf.background import BackgroundQueue
    _perf_background_available = True
except ImportError:
    BackgroundQueue = None  # type: ignore[assignment]
    _perf_background_available = False

_persona_cache: Any = None
if _perf_cache_available:
    _persona_cache = PersonaCache()

_background_queue: Any = None
if _perf_background_available:
    _background_queue = BackgroundQueue()


class ChatRequest(BaseModel):
    session_id: str
    message: str
    companion_id: str | None = None
    room_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    response: str
    step_results: list[dict[str, Any]]


chat_router = APIRouter(prefix="/chat")


@chat_router.post("/", response_model=ChatResponse)
async def chat(
    request: Request,
    body: ChatRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    try:
        sid = uuid.UUID(body.session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    user_uuid = uuid.UUID(current_user.user_id)
    stmt = select(Session).where(Session.id == sid, Session.user_id == user_uuid)
    result = await db.execute(stmt)
    db_session = result.scalar_one_or_none()
    if db_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    companion_id = body.companion_id
    companion = None
    if companion_id is not None:
        companion_mgr = CompanionManager(db)
        companion = await companion_mgr.get(current_user.user_id, companion_id)
        if companion is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Companion not found",
            )
        if db_session.companion_id is not None and db_session.companion_id != companion_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Companion mismatch: session is bound to a different companion",
            )

    # --- Load companion profile and identity from DB ---
    character_profile = await _build_companion_character_profile(
        companion,
        db_session,
        db,
        current_user.user_id,
        companion_id,
    )
    identity_state = await _load_session_identity_state(db, sid)

    session = sessions_store.get(body.session_id, {})
    history = await _load_db_session_history(db, sid)
    if not history:
        history = _load_session_history(request, body.session_id, session)
    persona = _load_persona(request, body.session_id, session)
    ctx = TurnContext(
        session_id=body.session_id,
        user_message=body.message,
        history=history,
        persona=persona,
        retrieved_memories=[],
        compiled_context=None,
        provider_response=None,
        extracted_memories=[],
        final_response=None,
        metadata=_build_metadata(request, body.session_id, session, history, persona),
    )
    ctx.metadata["db"] = db
    ctx.metadata["user_id"] = current_user.user_id
    ctx.metadata["companion_id"] = companion_id
    ctx.metadata["room_id"] = body.room_id
    if character_profile is not None:
        ctx.metadata["character_profile"] = character_profile
    if identity_state is not None:
        ctx.metadata["identity_state"] = identity_state

    ctx = await _run_pipeline(ctx)
    response = ctx.final_response
    if response is None:
        response = str(ctx.metadata.get("pipeline_error", "No response"))

    user_msg_ts = datetime.now(timezone.utc)
    assistant_msg_ts = max(datetime.now(timezone.utc), user_msg_ts + timedelta(milliseconds=1))
    db.add(
        Message(
            session_id=sid,
            user_id=user_uuid,
            companion_id=db_session.companion_id or companion_id,
            role="user",
            content=body.message,
            created_at=user_msg_ts,
        )
    )
    db.add(
        Message(
            session_id=sid,
            user_id=user_uuid,
            companion_id=db_session.companion_id or companion_id,
            role="assistant",
            content=response,
            created_at=assistant_msg_ts,
        )
    )
    db_session.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return ChatResponse(
        session_id=ctx.session_id,
        response=response,
        step_results=_summarize_step_results(ctx.metadata.get("step_results", [])),
    )


def _summarize_step_results(step_results: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": getattr(step_result, "step_name", ""),
            "duration_ms": getattr(step_result, "duration_ms", 0.0),
            "success": getattr(step_result, "success", False),
        }
        for step_result in step_results
    ]


def build_default_pipeline() -> Any:
    if _core_build_default_pipeline is None:
        return _FallbackPipeline()
    return _core_build_default_pipeline(
        persona_cache=_persona_cache,
        background_queue=_background_queue,
    )


async def _run_pipeline(ctx: TurnContext) -> TurnContext:
    try:
        pipeline = build_default_pipeline()
        return await pipeline.run(ctx)
    except ImportError as exc:
        ctx.metadata["pipeline_import_error"] = str(exc)
        return await _FallbackPipeline().run(ctx)


def _build_metadata(
    request: Request,
    session_id: str,
    session: Mapping[str, Any],
    history: list[dict],
    persona: dict[str, Any],
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "session_id": session_id,
        "created_at": session.get("created_at"),
        "message_count": session.get("message_count", len(history)),
        "session": dict(session),
        "session_store": sessions_store,
        "sessions_store": sessions_store,
        "persona": dict(persona),
    }

    for metadata_key, state_names in {
        "provider_registry": ("provider_registry",),
        "provider_router": ("provider_router",),
        "provider_config": ("provider_config",),
        "provider_policy": ("provider_policy",),
        "provider_name": ("provider_name", "default_provider"),
        "default_provider": ("default_provider",),
        "model": ("model", "default_model"),
        "default_model": ("default_model",),
        "retrieval_engine": ("retrieval_engine", "memory_retriever", "vectorstore"),
        "memory_storage": ("memory_storage", "memory_store", "vectorstore"),
        "memory_database": ("memory_database", "database", "db"),
        "database": ("database", "db"),
        "db": ("db",),
    }.items():
        value = _first_state_value(request, *state_names)
        if value is not None:
            metadata[metadata_key] = value

    working_memory_store = _first_state_value(
        request,
        "working_memory_store",
        "working_memory",
        "memory_store",
        "histories",
    )
    if working_memory_store is None and history:
        working_memory_store = {session_id: list(reversed(history))}
    if working_memory_store is not None:
        metadata["working_memory_store"] = working_memory_store
        metadata["working_memory"] = working_memory_store

    return metadata


def _load_session_history(
    request: Request,
    session_id: str,
    session: Mapping[str, Any],
) -> list[dict]:
    for key in ("history", "messages", "turns"):
        history = _coerce_history(session.get(key))
        if history:
            return history

    histories = _first_state_value(request, "histories", "session_histories")
    if isinstance(histories, Mapping):
        return _coerce_history(histories.get(session_id))

    return []


async def _load_db_session_history(
    db: AsyncSession,
    session_id: uuid.UUID,
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .where(Message.role.in_(("user", "assistant", "system", "tool")))
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    return [
        {
            "role": message.role,
            "content": message.content,
        }
        for message in result.scalars().all()
    ]


async def _build_companion_character_profile(
    companion: Any,
    db_session: Any,
    db: AsyncSession,
    user_id: str,
    companion_id: str | None,
) -> dict[str, Any] | None:
    """Build a character profile dict from the companion response or DB session.

    Uses the already-fetched *companion* first. Falls back to loading the
    companion from *db_session.companion_id* so that the turn pipeline always
    receives a character profile when one is bound.
    """
    if companion is not None:
        return _companion_to_profile_dict(companion)

    effective_id = (
        companion_id
        or (getattr(db_session, "companion_id", None) if db_session is not None else None)
    )
    if effective_id is None:
        return None

    try:
        companion_mgr = CompanionManager(db)
        loaded = await companion_mgr.get(user_id, str(effective_id))
    except Exception:
        return None
    if loaded is None:
        return None
    return _companion_to_profile_dict(loaded)


def _companion_to_profile_dict(companion: Any) -> dict[str, Any]:
    return {
        "name": getattr(companion, "name", "Companion"),
        "backstory": getattr(companion, "backstory", None) or "",
        "personality_sliders": getattr(companion, "personality_sliders", None) or {},
        "humor_style": getattr(companion, "humor_style", None) or "witty",
        "communication_style": getattr(companion, "communication_style", None) or "concise",
        "favorite_topics": getattr(companion, "favorite_topics", None) or [],
    }


async def _load_session_identity_state(
    db: AsyncSession,
    session_id: uuid.UUID,
) -> Any:
    """Return the most recent IdentityStateModel row for *session_id*, or None."""
    try:
        result = await db.execute(
            select(IdentityStateModel)
            .where(IdentityStateModel.session_id == session_id)
            .order_by(IdentityStateModel.last_updated.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
    except Exception:
        return None


def _load_persona(
    request: Request,
    session_id: str,
    session: Mapping[str, Any],
) -> dict[str, Any]:
    persona = _first_persona(
        session.get("persona"),
        session.get("persona_config"),
        session.get("personality"),
        _persona_from_state(request, session_id),
    )
    if persona:
        return persona
    return _default_persona()


def _persona_from_state(request: Request, session_id: str) -> Any:
    personas = _first_state_value(request, "personas", "session_personas")
    if isinstance(personas, Mapping):
        persona = personas.get(session_id)
        if persona is not None:
            return persona

    return _first_state_value(
        request,
        "persona",
        "persona_config",
        "default_persona",
        "personality",
    )


def _first_persona(*values: Any) -> dict[str, Any]:
    for value in values:
        persona = _coerce_persona(value)
        if persona:
            return persona
    return {}


def _default_persona() -> dict[str, Any]:
    try:
        from chatbot.soul.personality import DEFAULT_PERSONALITY
    except Exception:
        return {
            "name": "Companion",
            "traits": ["helpful", "clear", "neutral"],
            "tone": "neutral",
        }

    persona = _coerce_persona(DEFAULT_PERSONALITY)
    if persona:
        return persona
    return {
        "name": "Companion",
        "traits": ["helpful", "clear", "neutral"],
        "tone": "neutral",
    }


def _coerce_persona(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            mapped = to_dict()
        except Exception:
            mapped = None
        if isinstance(mapped, Mapping):
            return dict(mapped)

    if is_dataclass(value):
        mapped = asdict(value)
        if isinstance(mapped, Mapping):
            return dict(mapped)

    return {}


def _coerce_history(value: Any) -> list[dict]:
    if value is None or isinstance(value, (str, bytes, Mapping)):
        return []

    try:
        items = list(value)
    except TypeError:
        return []

    history: list[dict] = []
    for item in items:
        message = _coerce_message(item)
        if message is not None:
            history.append(message)
    return history


def _coerce_message(value: Any) -> dict[str, Any] | None:
    as_message = getattr(value, "as_message", None)
    if callable(as_message):
        try:
            value = as_message()
        except Exception:
            return None

    if isinstance(value, Mapping):
        role = value.get("role")
        content = value.get("content")
        metadata = value.get("metadata")
    else:
        role = getattr(value, "role", None)
        content = getattr(value, "content", None)
        metadata = getattr(value, "metadata", None)

    if role not in {"system", "user", "assistant", "tool"} or content is None:
        return None

    message: dict[str, Any] = {"role": str(role), "content": str(content)}
    if isinstance(metadata, Mapping):
        message["metadata"] = dict(metadata)
    return message


def _first_state_value(request: Request, *names: str) -> Any:
    state = getattr(request.app, "state", None)
    if state is None:
        return None

    for name in names:
        value = getattr(state, name, None)
        if value is not None:
            return value
    return None


class _FallbackPipeline:
    async def run(self, ctx: TurnContext) -> TurnContext:
        started_at = perf_counter()
        if ctx.final_response is None:
            ctx.final_response = _fallback_response(ctx.user_message)
        ctx.provider_response = ctx.final_response
        ctx.metadata["step_results"] = [
            StepResult(
                step_name="fallback",
                duration_ms=(perf_counter() - started_at) * 1000,
                success=True,
            )
        ]
        return ctx


def _fallback_response(message: str) -> str:
    message = message.strip()
    if not message:
        return "I received an empty message."
    return f"I received your message and no provider backend is configured: {message}"
