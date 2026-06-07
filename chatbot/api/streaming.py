"""Server-sent event streaming endpoint for the chatbot engine."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Mapping
from datetime import datetime, timedelta, timezone
import inspect
from time import perf_counter
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.core import TurnContext, TurnStep
from chatbot.core.turn import (
    _generate_message_embeddings,
    _provider_config,
    _provider_history,
    _render_trace_prompt,
    _select_provider,
    _store_llm_trace_metadata,
    build_default_pipeline,
)
from chatbot.db import get_db
from chatbot.companions.companion import CompanionManager
from chatbot.db.models import IdentityStateModel, Message, Session


class StreamRequest(BaseModel):
    session_id: str
    message: str
    stream_tokens: bool = True

    model_config = {"str_strip_whitespace": True, "str_min_length": 1}


streaming_router = APIRouter(prefix="/chat")
_STREAM_METHOD_NAMES = ("stream_complete", "complete_stream", "stream_tokens", "stream")


@streaming_router.post("/stream")
async def stream_chat(
    http_request: Request,
    request: StreamRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    try:
        sid = uuid.UUID(request.session_id)
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

    return StreamingResponse(
        _stream_turn(
            request,
            http_request=http_request,
            current_user=current_user,
            db=db,
            db_session=db_session,
        ),
        media_type="text/event-stream",
    )


async def _stream_turn(
    request: StreamRequest,
    *,
    http_request: Request | None = None,
    current_user: CurrentUser,
    db: AsyncSession,
    db_session: Session,
) -> AsyncIterator[str]:
    typing_active = False
    try:
        metadata = _stream_metadata(http_request)
        metadata["session_id"] = request.session_id
        metadata["session_record"] = db_session
        metadata["db_session"] = db
        metadata["user_id"] = current_user.user_id
        metadata["companion_id"] = db_session.companion_id

        character_profile = await _load_stream_companion_profile(
            db, db_session, current_user.user_id
        )
        identity_state = await _load_stream_session_identity(db, sid)

        history = await _load_db_session_history(db, db_session.id)
        _seed_stream_history_metadata(metadata, request.session_id, history)

        ctx = TurnContext(
            session_id=request.session_id,
            user_message=request.message,
            history=history,
            persona={},
            retrieved_memories=[],
            compiled_context=None,
            provider_response=None,
            extracted_memories=[],
            final_response=None,
            metadata=metadata,
        )
        if character_profile is not None:
            ctx.metadata["character_profile"] = character_profile
        if identity_state is not None:
            ctx.metadata["identity_state"] = identity_state

        pipeline = build_default_pipeline()

        yield _sse_data({"type": "typing", "status": "started"})
        typing_active = True

        provider_seen = False
        for step in getattr(pipeline, "_steps", []):
            step_name = _step_name(step)
            if step_name == "call_provider":
                provider_seen = True
                yield _sse_data({"step": step_name, "status": "streaming"})

                started_at = perf_counter()
                stream_attempted = False
                token_emitted = False
                if request.stream_tokens:
                    async for token in _stream_provider_tokens(ctx):
                        stream_attempted = True
                        token_emitted = True
                        yield _sse_data({"type": "token", "text": token})
                    stream_attempted = stream_attempted or bool(
                        ctx.metadata.pop("_stream_provider_attempted", False)
                    )

                if not stream_attempted:
                    ctx = await step.run(ctx)
                    for token in _provider_text_chunks(ctx):
                        yield _sse_data({"type": "token", "text": token})
                elif not token_emitted:
                    for token in _provider_text_chunks(ctx):
                        yield _sse_data({"type": "token", "text": token})

                duration_ms = (perf_counter() - started_at) * 1000

                yield _sse_data(
                    {
                        "step": step_name,
                        "status": "done",
                        "duration_ms": duration_ms,
                    }
                )
                yield _sse_data({"type": "typing", "status": "stopped"})
                typing_active = False
                continue

            yield _sse_data({"step": step_name, "status": "running"})

            started_at = perf_counter()
            ctx = await step.run(ctx)
            duration_ms = (perf_counter() - started_at) * 1000

            yield _sse_data(
                {
                    "step": step_name,
                    "status": "done",
                    "duration_ms": duration_ms,
                }
            )

            if step_name == "compile_context" and ctx.compiled_context:
                yield _sse_data(
                    {"type": "context", "text": ctx.compiled_context}
                )

        if not provider_seen and typing_active:
            yield _sse_data({"type": "typing", "status": "stopped"})
            typing_active = False

        response = ctx.final_response
        if response is None:
            response = str(ctx.metadata.get("pipeline_error", "No response"))

        await _persist_messages(
            db,
            db_session=db_session,
            current_user=current_user,
            user_message=request.message,
            assistant_message=response,
        )

        await _generate_message_embeddings(ctx)

        yield _sse_data({"type": "response", "text": response})
        yield "data: [DONE]\n\n"
    except HTTPException:
        if typing_active:
            yield _sse_data({"type": "typing", "status": "stopped"})
        raise
    except Exception:
        if typing_active:
            yield _sse_data({"type": "typing", "status": "stopped"})
        yield _sse_data({"type": "error", "error": "An internal error occurred"})
        yield "data: [DONE]\n\n"


async def _stream_provider_tokens(ctx: TurnContext) -> AsyncIterator[str]:
    provider = _select_provider(ctx)
    if provider is None:
        return

    stream_method = _provider_stream_method(provider)
    if stream_method is None:
        return
    ctx.metadata["_stream_provider_attempted"] = True

    config = _provider_config(ctx)
    history = _provider_history(ctx)
    ctx.metadata["llm_history"] = history
    ctx.metadata["llm_prompt"] = _render_trace_prompt(ctx.user_message, history)

    result_for_metadata: Any | None = None
    streamed_text_parts: list[str] = []
    try:
        stream_result = stream_method(
            prompt=ctx.user_message,
            history=history,
            config=config,
        )
        async for chunk in _iter_stream_result(stream_result):
            if _looks_like_provider_response(chunk):
                result_for_metadata = chunk
                text = getattr(chunk, "text", "")
            else:
                text = _chunk_text(chunk)
            if text:
                streamed_text_parts.append(text)
                yield text
    except Exception as exc:
        ctx.provider_response = f"Provider error: {exc}"
        _store_llm_trace_metadata(ctx, provider=provider, config=config, result=None)
        return

    streamed_text = "".join(streamed_text_parts)
    if not streamed_text and result_for_metadata is not None:
        streamed_text = str(getattr(result_for_metadata, "text", "") or "")
    ctx.provider_response = streamed_text
    _store_llm_trace_metadata(
        ctx,
        provider=provider,
        config=config,
        result=result_for_metadata,
    )


def _provider_stream_method(provider: Any) -> Any | None:
    for name in _STREAM_METHOD_NAMES:
        method = getattr(provider, name, None)
        if callable(method):
            return method
    return None


async def _iter_stream_result(stream_result: Any) -> AsyncIterator[Any]:
    if inspect.isawaitable(stream_result):
        stream_result = await stream_result

    if hasattr(stream_result, "__aiter__"):
        async for item in stream_result:
            yield item
        return

    if _is_sync_iterable(stream_result):
        for item in stream_result:
            yield item
        return

    if stream_result is not None:
        yield stream_result


def _is_sync_iterable(value: Any) -> bool:
    return isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping))


def _looks_like_provider_response(value: Any) -> bool:
    return hasattr(value, "text") and hasattr(value, "finish_reason")


def _chunk_text(chunk: Any) -> str:
    if chunk is None:
        return ""
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, bytes):
        return chunk.decode(errors="replace")
    if isinstance(chunk, Mapping):
        return _mapping_chunk_text(chunk)

    for attribute in ("text", "token", "content", "delta"):
        value = getattr(chunk, attribute, None)
        if isinstance(value, str):
            return value
        if isinstance(value, Mapping):
            text = _mapping_chunk_text(value)
            if text:
                return text
    return str(chunk)


def _mapping_chunk_text(chunk: Mapping[str, Any]) -> str:
    for key in ("text", "token", "content"):
        value = chunk.get(key)
        if isinstance(value, str):
            return value

    delta = chunk.get("delta")
    if isinstance(delta, str):
        return delta
    if isinstance(delta, Mapping):
        text = _mapping_chunk_text(delta)
        if text:
            return text

    choices = chunk.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, Mapping):
            text = _mapping_chunk_text(first_choice)
            if text:
                return text

    return ""


def _provider_text_chunks(ctx: TurnContext) -> list[str]:
    if ctx.provider_response is None:
        return []

    text = str(ctx.provider_response)
    return [text] if text else []


def _stream_metadata(http_request: Request | None) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if http_request is None:
        return metadata

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
        "working_memory_store": (
            "working_memory_store",
            "working_memory",
            "memory_store",
            "histories",
        ),
    }.items():
        value = _first_state_value(http_request, *state_names)
        if value is not None:
            metadata[metadata_key] = value

    return metadata


def _seed_stream_history_metadata(
    metadata: dict[str, Any],
    session_id: str,
    history: list[dict[str, Any]],
) -> None:
    if not history:
        return
    if any(
        metadata.get(key) is not None
        for key in ("working_memory_store", "working_memory", "memory_store")
    ):
        return

    working_memory_store = {session_id: list(reversed(history))}
    metadata["working_memory_store"] = working_memory_store
    metadata["working_memory"] = working_memory_store


def _first_state_value(http_request: Request, *names: str) -> Any | None:
    state = getattr(http_request.app, "state", None)
    if state is None:
        return None

    for name in names:
        value = getattr(state, name, None)
        if value is not None:
            return value
    return None


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


async def _persist_messages(
    db: AsyncSession,
    *,
    db_session: Session,
    current_user: CurrentUser,
    user_message: str,
    assistant_message: str,
) -> None:
    user_uuid = uuid.UUID(current_user.user_id)
    user_msg_ts = datetime.now(timezone.utc)
    assistant_msg_ts = max(datetime.now(timezone.utc), user_msg_ts + timedelta(milliseconds=1))
    db.add(
        Message(
            session_id=db_session.id,
            user_id=user_uuid,
            companion_id=db_session.companion_id,
            role="user",
            content=user_message,
            created_at=user_msg_ts,
        )
    )
    db.add(
        Message(
            session_id=db_session.id,
            user_id=user_uuid,
            companion_id=db_session.companion_id,
            role="assistant",
            content=assistant_message,
            created_at=assistant_msg_ts,
        )
    )
    db_session.updated_at = datetime.now(timezone.utc)
    await db.commit()


def _step_name(step: TurnStep) -> str:
    name = getattr(step, "name", None)
    if isinstance(name, str) and name:
        return name
    return step.__class__.__name__


async def _load_stream_companion_profile(
    db: AsyncSession,
    db_session: Session,
    user_id: str,
) -> dict[str, Any] | None:
    """Load companion character profile dict from the session-bound companion."""
    companion_id = db_session.companion_id
    if companion_id is None:
        return None
    try:
        companion_mgr = CompanionManager(db)
        companion = await companion_mgr.get(user_id, str(companion_id))
    except Exception:
        return None
    if companion is None:
        return None
    return _companion_to_profile_dict(companion)


def _companion_to_profile_dict(companion: Any) -> dict[str, Any]:
    return {
        "name": getattr(companion, "name", "Companion"),
        "backstory": getattr(companion, "backstory", None) or "",
        "personality_sliders": getattr(companion, "personality_sliders", None) or {},
        "humor_style": getattr(companion, "humor_style", None) or "witty",
        "communication_style": getattr(companion, "communication_style", None) or "concise",
        "favorite_topics": getattr(companion, "favorite_topics", None) or [],
    }


async def _load_stream_session_identity(
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


def _sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"
