"""Chat endpoint for the chatbot engine."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from time import perf_counter
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.api import conversation
from chatbot.api.sessions import sessions_store
from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.core.steps import StepResult, TurnContext
from chatbot.db import async_session_factory, get_db
from chatbot.db.models import Room as RoomModel, Session
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
    thinking: bool = False


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

    # Companion, personality and identity: shared with streaming/spoken chat.
    companion_id = body.companion_id
    durable_state = {}
    character_profile, companion_persona = await conversation.load_companion_inputs(
        db,
        db_session,
        current_user.user_id,
        companion_id,
        durable_state=durable_state,
    )
    identity_state = await conversation.load_session_identity(db, sid)

    session = sessions_store.get(body.session_id, {})
    chat_room = await _load_chat_room(
        db,
        db_session,
        current_user.user_id,
        body.room_id,
    )
    effective_room_id = chat_room.room_id if chat_room is not None else None

    history = await conversation.load_db_session_history(db, sid, effective_room_id)
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
    ctx = conversation.build_turn_context(
        request,
        session_id=body.session_id,
        user_message=body.message,
        history=history,
        persona=conversation.resolve_persona(
            companion_persona,
            conversation.standard_persona_fallback(request, body.session_id),
        ),
        db=db,
        user_id=current_user.user_id,
        companion_id=companion_id,
        room_id=effective_room_id,
        room_type=chat_room.room_type if chat_room is not None else None,
        room_system_prompt=room_system_prompt,
        character_profile=character_profile,
        identity_state=identity_state,
        durable_state=durable_state,
        provider_override=body.provider_override,
        model_override=body.model_override,
        thinking=body.thinking,
    )

    await conversation.prepare_memory_workflow(request, ctx, db, sid)
    ctx = await _run_pipeline(ctx)
    response = ctx.final_response
    if response is None:
        response = str(ctx.metadata.get("pipeline_error", "No response"))

    # Standard chat commits the turn's messages and every in-turn effect
    # (extracted memories, emotional state, timeline) as one request transaction.
    await conversation.add_turn_messages(
        db,
        session_id=sid,
        user_id=user_uuid,
        companion_id=db_session.companion_id or companion_id,
        user_message=body.message,
        assistant_message=response,
        turn_id=ctx.metadata.get("turn_id"),
    )
    if (
        not ctx.metadata.get("pipeline_error")
        and not str(ctx.provider_response or "").startswith("Provider error:")
    ):
        await conversation.record_completed_interaction(
            db, db_session.companion_id or companion_id, user_uuid
        )
        # The durable memory-extraction job is inserted in the same transaction
        # that persists the completed turn's messages and effects: a rollback
        # leaves neither completed turn nor job.
        await conversation.schedule_memory_job(
            db,
            session_id=sid,
            user_id=user_uuid,
            companion_id=db_session.companion_id or companion_id,
            turn_id=ctx.metadata.get("turn_id"),
        )
    await db.commit()

    conversation.schedule_title_generation(
        request,
        session_id=db_session.id,
        session_title=db_session.title,
        user_message=body.message,
        assistant_message=response,
        session_factory=async_session_factory,
        provider_override=body.provider_override,
        model_override=body.model_override,
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

    histories = conversation.first_state_value(request, "histories", "session_histories")
    if isinstance(histories, Mapping):
        return _filter_history_by_room_id(
            _coerce_history(histories.get(session_id)),
            room_id,
        )

    return []


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
