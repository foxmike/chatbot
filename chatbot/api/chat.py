"""Chat endpoint for the chatbot engine."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import logging
from time import perf_counter
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import asyncio

from chatbot.api.sessions import sessions_store
from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.companions.companion import CompanionManager
from chatbot.core.steps import StepResult, TurnContext
from chatbot.db import async_session_factory, get_db
from chatbot.db.models import IdentityStateModel, Message, Room as RoomModel, Session
from chatbot.providers.base import ProviderConfig, ProviderRegistry
from chatbot.prompts.templates import render_room_prompt
from chatbot.rooms.room import RoomType

logger = logging.getLogger(__name__)

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
    model_override: str | None = None
    provider_override: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    response: str
    step_results: list[dict[str, Any]]
    turn_id: str | None = None


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
    chat_room = await _load_chat_room(
        db,
        db_session,
        current_user.user_id,
        body.room_id,
    )
    effective_room_id = chat_room.room_id if chat_room is not None else None

    history = await _load_db_session_history(db, sid, effective_room_id)
    if not history:
        history = _load_session_history(
            request,
            body.session_id,
            session,
            effective_room_id,
        )
    room_system_prompt = _build_room_system_prompt(
        current_user,
        db_session,
        chat_room,
        character_profile,
    )
    history = _inject_room_system_prompt(history, room_system_prompt, effective_room_id)
    persona = _load_persona(request, body.session_id, session)
    retrieval_engine = _first_state_value(
        request,
        "retrieval_engine",
        "memory_retriever",
    )
    if retrieval_engine is not None and not callable(getattr(retrieval_engine, "retrieve", None)):
        retrieval_engine = None

    metadata = _build_metadata(request, body.session_id, session, history, persona)
    if body.provider_override:
        metadata["provider_name"] = body.provider_override
        metadata["default_provider"] = body.provider_override
    if body.model_override:
        metadata["model"] = body.model_override
        metadata["default_model"] = body.model_override

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
        metadata=metadata,
        retrieval_engine=retrieval_engine,
    )
    ctx.metadata["db"] = db
    ctx.metadata["user_id"] = current_user.user_id
    ctx.metadata["companion_id"] = companion_id
    ctx.metadata["room_id"] = effective_room_id
    if chat_room is not None:
        ctx.metadata["room_type"] = chat_room.room_type
    if room_system_prompt is not None:
        ctx.metadata["room_system_prompt"] = room_system_prompt
    if character_profile is not None:
        ctx.metadata["character_profile"] = character_profile
    if identity_state is not None:
        ctx.metadata["identity_state"] = identity_state

    await _prepare_memory_workflow(request, ctx, db, sid)
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
            turn_id=ctx.metadata.get("turn_id"),
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
            turn_id=ctx.metadata.get("turn_id"),
            created_at=assistant_msg_ts,
        )
    )
    db_session.updated_at = datetime.now(timezone.utc)
    await db.commit()

    _maybe_schedule_title_generation(
        request=request,
        db_session=db_session,
        user_message=body.message,
        assistant_message=response,
    )

    return ChatResponse(
        session_id=ctx.session_id,
        response=response,
        step_results=_summarize_step_results(ctx.metadata.get("step_results", [])),
        turn_id=ctx.metadata.get("turn_id"),
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
        return await pipeline.execute_turn(ctx, update_identity=True)
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


async def _prepare_memory_workflow(
    request: Request,
    ctx: TurnContext,
    db: AsyncSession,
    session_id: uuid.UUID,
) -> None:
    """Wire embeddings and retrieval before the turn pipeline reaches the LLM."""
    await _generate_recent_message_embeddings(db, session_id, ctx)

    retrieval_engine = ctx.retrieval_engine or _metadata_retrieval_engine(ctx.metadata)
    if retrieval_engine is None:
        retrieval_engine = _build_chat_retrieval_engine(request, db)

    if retrieval_engine is not None:
        ctx.metadata["retrieval_engine"] = retrieval_engine
        ctx.metadata["memory_retriever"] = retrieval_engine
        ctx.retrieval_engine = retrieval_engine


async def _generate_recent_message_embeddings(
    db: AsyncSession,
    session_id: uuid.UUID,
    ctx: TurnContext,
    limit: int = 20,
) -> None:
    try:
        from chatbot.memory.embedding import generate_embedding
    except ImportError:
        return

    try:
        result = await db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .where(Message.role.in_(("user", "assistant")))
            .where(Message.embedding.is_(None))
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
    except Exception:
        logger.warning(
            "Message embedding target load failed for session %s",
            session_id,
            exc_info=True,
        )
        return

    generated = 0
    for message in messages:
        try:
            embedding = await generate_embedding(db, message)
        except Exception:
            logger.warning(
                "Message embedding generation failed for message %s",
                getattr(message, "id", ""),
                exc_info=True,
            )
            continue
        if embedding:
            generated += 1

    if messages:
        ctx.metadata["message_embeddings_checked"] = len(messages)
    if generated:
        ctx.metadata["message_embeddings_generated"] = generated


def _metadata_retrieval_engine(metadata: Mapping[str, Any]) -> Any | None:
    for key in ("retrieval_engine", "memory_retriever"):
        candidate = metadata.get(key)
        if callable(getattr(candidate, "retrieve", None)):
            return candidate
    return None


def _build_chat_retrieval_engine(request: Request, db: AsyncSession) -> Any | None:
    try:
        from chatbot.config import get_settings
        from chatbot.memory.pgvector_store import PgvectorStore
        from chatbot.memory.retrieval import (
            DisabledEmbeddingProvider,
            OpenRouterEmbeddingProvider,
            RetrievalEngine,
        )
    except ImportError:
        return None

    vector_store = _first_state_value(
        request,
        "memory_vector_store",
        "vector_store",
        "vectorstore",
    )
    if vector_store is None or not callable(getattr(vector_store, "search", None)):
        vector_store = PgvectorStore(db, Message)

    embeddings = _first_state_value(
        request,
        "embedding_provider",
        "embeddings",
        "memory_embeddings",
    )
    if embeddings is None or not callable(getattr(embeddings, "embed", None)):
        settings = get_settings()
        if settings.openrouter_api_key:
            embeddings = OpenRouterEmbeddingProvider(
                api_key=settings.openrouter_api_key,
                base_url=getattr(
                    settings,
                    "embedding_base_url",
                    settings.openrouter_base_url,
                ),
                model=getattr(
                    settings,
                    "embedding_model",
                    "openai/text-embedding-3-small",
                ),
            )
        else:
            embeddings = DisabledEmbeddingProvider(
                "Memory retrieval skipped because no embedding provider API key is configured"
            )

    return RetrievalEngine(vector_store, embeddings)


def _load_session_history(
    request: Request,
    session_id: str,
    session: Mapping[str, Any],
    room_id: str | None = None,
) -> list[dict]:
    for key in ("history", "messages", "turns"):
        history = _coerce_history(session.get(key))
        if history:
            return _filter_history_by_room_id(history, room_id)

    histories = _first_state_value(request, "histories", "session_histories")
    if isinstance(histories, Mapping):
        return _filter_history_by_room_id(
            _coerce_history(histories.get(session_id)),
            room_id,
        )

    return []


async def _load_db_session_history(
    db: AsyncSession,
    session_id: uuid.UUID,
    room_id: str | None = None,
) -> list[dict[str, Any]]:
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .where(Message.role.in_(("user", "assistant", "system", "tool")))
    )
    if room_id is not None:
        room_session_ids = select(RoomModel.session_id).where(
            RoomModel.room_id == room_id,
            RoomModel.session_id.is_not(None),
        )
        stmt = (
            stmt.join(Session, Message.session_id == Session.id)
            .where(
                or_(
                    Session.room_id == room_id,
                    Session.id.in_(room_session_ids),
                )
            )
        )

    result = await db.execute(
        stmt.order_by(Message.created_at.asc(), Message.id.asc())
    )
    return [
        {
            "role": message.role,
            "content": message.content,
        }
        for message in result.scalars().all()
    ]


async def _load_chat_room(
    db: AsyncSession,
    db_session: Session,
    user_id: str,
    requested_room_id: str | None,
) -> RoomModel | None:
    room_id = _normalize_room_id(requested_room_id)
    if room_id is not None:
        room = await _load_user_room(db, user_id, room_id)
        if room is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Room not found",
            )
        _validate_room_session(room, db_session)
        return room

    session_room_id = _normalize_room_id(db_session.room_id)
    if session_room_id is not None:
        return await _load_user_room(db, user_id, session_room_id)

    result = await db.execute(
        select(RoomModel)
        .where(RoomModel.user_id == user_id)
        .where(RoomModel.session_id == db_session.id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _load_user_room(
    db: AsyncSession,
    user_id: str,
    room_id: str,
) -> RoomModel | None:
    result = await db.execute(
        select(RoomModel)
        .where(RoomModel.room_id == room_id)
        .where(RoomModel.user_id == user_id)
    )
    return result.scalar_one_or_none()


def _validate_room_session(room: RoomModel, db_session: Session) -> None:
    session_room_id = _normalize_room_id(db_session.room_id)
    if session_room_id is not None and session_room_id != room.room_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Room mismatch: session is bound to a different room",
        )

    room_session_id = getattr(room, "session_id", None)
    if room_session_id is not None and room_session_id != db_session.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Room mismatch: room is linked to a different session",
        )


def _build_room_system_prompt(
    current_user: CurrentUser,
    db_session: Session,
    room: RoomModel | None,
    character_profile: Mapping[str, Any] | None,
) -> str | None:
    if room is None:
        return None

    try:
        room_type = RoomType(room.room_type)
    except ValueError:
        logger.warning(
            "Unknown room type for room %s: %s",
            room.room_id,
            room.room_type,
        )
        return None

    kwargs: dict[str, str] = {
        "user_name": current_user.username,
        "display_name": room.display_name,
    }
    if db_session.conversation_mode:
        kwargs["conversation_mode"] = db_session.conversation_mode

    if character_profile:
        for prompt_key, profile_key in {
            "companion_name": "name",
            "backstory": "backstory",
            "personality_sliders": "personality_sliders",
            "humor_style": "humor_style",
            "communication_style": "communication_style",
            "favorite_topics": "favorite_topics",
        }.items():
            value = _stringify_prompt_value(character_profile.get(profile_key))
            if value:
                kwargs[prompt_key] = value

    prompt = render_room_prompt(room_type, **kwargs).strip()
    return prompt or None


def _inject_room_system_prompt(
    history: list[dict[str, Any]],
    room_system_prompt: str | None,
    room_id: str | None,
) -> list[dict[str, Any]]:
    if room_system_prompt is None:
        return history

    for message in history:
        if (
            message.get("role") == "system"
            and str(message.get("content", "")).strip() == room_system_prompt
        ):
            return history

    metadata: dict[str, Any] = {"room_type_prompt": True}
    if room_id is not None:
        metadata["room_id"] = room_id
    return [
        {
            "role": "system",
            "content": room_system_prompt,
            "metadata": metadata,
        },
        *history,
    ]


def _filter_history_by_room_id(
    history: list[dict[str, Any]],
    room_id: str | None,
) -> list[dict[str, Any]]:
    normalized_room_id = _normalize_room_id(room_id)
    if normalized_room_id is None:
        return history

    filtered: list[dict[str, Any]] = []
    for message in history:
        message_room_id = _normalize_room_id(_message_room_id(message))
        if message_room_id is None or message_room_id == normalized_room_id:
            filtered.append(message)
    return filtered


def _message_room_id(message: Mapping[str, Any]) -> Any:
    metadata = message.get("metadata")
    if isinstance(metadata, Mapping):
        room_id = metadata.get("room_id")
        if room_id is not None:
            return room_id
    return message.get("room_id")


def _normalize_room_id(room_id: Any) -> str | None:
    if room_id is None:
        return None
    normalized = str(room_id).strip()
    return normalized or None


def _stringify_prompt_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return ", ".join(
            f"{key}: {item}"
            for key, item in value.items()
            if item is not None and str(item).strip()
        )
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value if str(item).strip())
    return str(value).strip()


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


def _maybe_schedule_title_generation(
    request: Request,
    db_session: Session,
    user_message: str,
    assistant_message: str,
) -> None:
    """Schedule background title generation if the session has no title yet."""
    if db_session.title is not None:
        return

    provider_registry = _first_state_value(
        request,
        "provider_registry",
    )
    if provider_registry is None or not isinstance(provider_registry, ProviderRegistry):
        logger.debug("No provider registry available; skipping title generation")
        return

    provider_name = _first_state_value(
        request,
        "provider_name",
        "default_provider",
    )
    model = _first_state_value(
        request,
        "model",
        "default_model",
    )
    if not provider_name and not model:
        provider_name = "openrouter"

    session_id = db_session.id

    asyncio.create_task(
        _generate_conversation_title(
            provider_registry=provider_registry,
            provider_name=str(provider_name) if provider_name else "openrouter",
            model=str(model) if model else None,
            session_id=session_id,
            user_message=user_message,
            assistant_message=assistant_message,
        )
    )


async def _generate_conversation_title(
    *,
    provider_registry: ProviderRegistry,
    provider_name: str,
    model: str | None,
    session_id: uuid.UUID,
    user_message: str,
    assistant_message: str,
) -> None:
    """Generate a short conversation title using the LLM (runs in background)."""
    try:
        provider = provider_registry.get(provider_name)
    except KeyError:
        logger.debug(
            "Provider %r not found in registry; skipping title generation",
            provider_name,
        )
        return

    if model is None:
        model = ""

    title_prompt = (
        "Generate a short, concise title (2-5 words) for a conversation "
        "that starts with the following exchange. Return ONLY the title, "
        "with no quotes, explanations, or extra text.\n\n"
        f"User: {user_message}\n"
        f"Assistant: {assistant_message}\n"
    )

    config = ProviderConfig(
        model=model,
        temperature=0.3,
        max_tokens=16,
    )

    try:
        llm_response = await provider.complete(
            prompt=title_prompt,
            history=[],
            config=config,
        )
    except Exception:
        logger.warning(
            "Title generation LLM call failed for session %s",
            session_id,
            exc_info=True,
        )
        return

    raw_title = (llm_response.text or "").strip().strip('"').strip("'")
    if not raw_title:
        return

    # Enforce 2-5 word range via truncation
    words = raw_title.split()
    if len(words) > 5:
        raw_title = " ".join(words[:5])
    elif len(words) < 2:
        # Too short; keep as-is (at least something)
        pass

    # Persist the title in a fresh DB session
    try:
        db = async_session_factory()
        try:
            from sqlalchemy import update as sa_update

            await db.execute(
                sa_update(Session)
                .where(Session.id == session_id, Session.title.is_(None))
                .values(title=raw_title)
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()
    except Exception:
        logger.warning(
            "Failed to persist title for session %s",
            session_id,
            exc_info=True,
        )
        return

    logger.info("Auto-generated title for session %s: %r", session_id, raw_title)


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
