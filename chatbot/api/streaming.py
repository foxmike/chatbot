"""Server-sent event streaming endpoint for the chatbot engine."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterable, Mapping
from datetime import datetime, timedelta, timezone
import inspect
from time import perf_counter
from typing import Any
import uuid

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from chatbot.api.chat import _prepare_memory_workflow
from chatbot.avatar.coordinator import get_coordinator
from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.core import StepResult, TurnContext, TurnStep
from chatbot.core.turn import (
    UpdateIdentityStep,
    _generate_message_embeddings,
    _provider_config,
    _provider_history,
    _render_trace_prompt,
    _select_provider,
    _store_llm_trace_metadata,
    _start_turn_trace,
    _record_turn_trace_step,
    _complete_turn_trace,
    build_default_pipeline,
)
from chatbot.db import streaming_session_factory, get_db
from chatbot.companions.companion import CompanionManager
from chatbot.db.models import IdentityStateModel, Message, Session
from chatbot.providers.base import ProviderConfig, ProviderRegistry

logger = logging.getLogger(__name__)


class StreamRequest(BaseModel):
    session_id: str
    message: str
    stream_tokens: bool = True
    room_id: str | None = None
    model_override: str | None = None
    provider_override: str | None = None

    model_config = {"str_strip_whitespace": True, "str_min_length": 1}


streaming_router = APIRouter(prefix="/chat")
_STREAM_METHOD_NAMES = ("stream", "stream_complete", "complete_stream", "stream_tokens")


class ChatStreamingResponse(StreamingResponse):
    async def stream_response(self, send: Any) -> None:
        try:
            await super().stream_response(send)
        finally:
            # Cancellation can happen in ASGI send(), while the generator is
            # suspended at yield. Explicitly close it so its persistence cleanup
            # runs now rather than at async-generator garbage collection.
            with anyio.CancelScope(shield=True):
                close = getattr(self.body_iterator, "aclose", None)
                if close is not None:
                    await close()


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
    conditions = [Session.id == sid, Session.user_id == user_uuid]
    if request.room_id:
        conditions.append(Session.room_id == request.room_id)
    stmt = select(Session).where(*conditions)
    result = await db.execute(stmt)
    db_session = result.scalar_one_or_none()
    if db_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    character_profile = await _load_stream_companion_profile(
        db,
        db_session,
        current_user.user_id,
    )
    identity_state = await _load_stream_session_identity(db, sid)
    history = await _load_db_session_history(
        db,
        db_session.id,
        room_id=db_session.room_id,
    )
    session_snapshot = _session_snapshot(db_session)

    return ChatStreamingResponse(
        _stream_turn(
            request,
            http_request=http_request,
            current_user=current_user,
            session_id=db_session.id,
            session_title=db_session.title,
            session_companion_id=db_session.companion_id,
            session_room_id=db_session.room_id,
            session_snapshot=session_snapshot,
            character_profile=character_profile,
            identity_state=identity_state,
            history=history,
        ),
        media_type="text/event-stream",
    )


async def _stream_turn(
    request: StreamRequest,
    *,
    http_request: Request | None = None,
    current_user: CurrentUser,
    session_id: uuid.UUID,
    session_title: str | None,
    session_companion_id: str | None,
    session_room_id: str | None,
    session_snapshot: dict[str, Any],
    character_profile: dict[str, Any] | None,
    identity_state: Any,
    history: list[dict[str, Any]],
) -> AsyncIterator[str]:
    stream_db = streaming_session_factory()
    typing_active = False
    avatar = None
    avatar_turn = None
    coordinator = None
    ctx = None
    trace_id = None
    persisted = False
    try:
        if http_request is not None:
            coordinator = get_coordinator(http_request.app)
            avatar = coordinator.get(str(session_id), current_user.user_id)
            source = getattr(http_request.state, "avatar_source", "typed")
            if source == "spoken" and (
                avatar is None
                or avatar is not getattr(http_request.state, "avatar_binding", avatar)
                or avatar.companion_id != session_companion_id
                or character_profile is None
            ):
                raise HTTPException(409, "Avatar session or companion changed")
            if avatar and avatar.companion_id != session_companion_id:
                await coordinator.detach(avatar)
                avatar = None
            if avatar is not None:
                avatar_turn = await coordinator.begin(avatar, source=source)
                # The previous turn's interrupted persistence must finish BEFORE
                # loading context for its successor, including typed -> spoken.
                history = await _load_db_session_history(
                    stream_db, session_id, room_id=session_room_id,
                )
                identity_state = await _load_stream_session_identity(stream_db, session_id)
        metadata = _stream_metadata(http_request)
        if request.model_override:
            metadata["model"] = request.model_override
            metadata["default_model"] = request.model_override
        if request.provider_override:
            metadata["provider_name"] = request.provider_override
            metadata["default_provider"] = request.provider_override
        metadata["turn_id"] = avatar_turn.turn_id if avatar_turn else str(uuid.uuid4())
        metadata["session_id"] = request.session_id
        metadata["session_record"] = session_snapshot
        metadata["db_session"] = stream_db
        metadata["db"] = stream_db
        metadata["user_id"] = current_user.user_id
        metadata["companion_id"] = session_companion_id
        metadata["room_id"] = session_room_id or request.room_id

        _seed_stream_history_metadata(metadata, request.session_id, history)

        retrieval_engine = metadata.get("retrieval_engine") or metadata.get("memory_retriever")
        if retrieval_engine is not None and not callable(getattr(retrieval_engine, "retrieve", None)):
            retrieval_engine = None

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
            retrieval_engine=retrieval_engine,
        )
        if character_profile is not None:
            ctx.metadata["character_profile"] = character_profile
        if identity_state is not None:
            ctx.metadata["identity_state"] = identity_state

        if http_request is not None:
            await _prepare_memory_workflow(http_request, ctx, stream_db, session_id)

        pipeline = build_default_pipeline()
        ctx.metadata.setdefault("full_conversation_history", list(ctx.history))
        trace_id = await _start_turn_trace(ctx)

        yield _sse_data({"type": "typing", "status": "started"})
        typing_active = True

        provider_seen = False
        for step in getattr(pipeline, "_steps", []):
            step_name = _step_name(step)
            if step_name == "update_identity":
                continue

            if step_name == "call_provider":
                provider_seen = True
                yield _sse_data({"step": step_name, "status": "streaming"})

                started_at = perf_counter()
                stream_attempted = False
                token_emitted = False
                if request.stream_tokens:
                    async for token in _stream_provider_tokens(ctx):
                        if avatar_turn and avatar_turn.interrupted:
                            raise asyncio.CancelledError
                        stream_attempted = True
                        token_emitted = True
                        if token.startswith("Provider stream error:"):
                            yield _sse_data({"type": "error", "error": token})
                        else:
                            if avatar_turn:
                                coordinator.publish(avatar, avatar_turn, token)
                            yield _sse_data({"type": "token", "text": token})
                            if avatar_turn:
                                avatar_turn.text_delivered += token
                    stream_attempted = stream_attempted or bool(
                        ctx.metadata.pop("_stream_provider_attempted", False)
                    )

                if not stream_attempted:
                    ctx = await step.run(ctx)
                    for token in _provider_text_chunks(ctx):
                        if avatar_turn:
                            coordinator.publish(avatar, avatar_turn, token)
                        yield _sse_data({"type": "token", "text": token})
                        if avatar_turn:
                            avatar_turn.text_delivered += token
                elif not token_emitted:
                    for token in _provider_text_chunks(ctx):
                        if avatar_turn:
                            coordinator.publish(avatar, avatar_turn, token)
                        yield _sse_data({"type": "token", "text": token})
                        if avatar_turn:
                            avatar_turn.text_delivered += token

                duration_ms = (perf_counter() - started_at) * 1000
                ctx.metadata.setdefault("step_results", []).append(
                    StepResult(step_name=step_name, duration_ms=duration_ms, success=True)
                )
                await _record_turn_trace_step(ctx, trace_id, step_name, duration_ms, success=True)
                if avatar_turn:
                    coordinator.end(avatar, avatar_turn)
                    if avatar_turn.source == "spoken":
                        # Do not extract memories or persist unplayed generated text.
                        try:
                            await coordinator.wait_playback(avatar_turn)
                        except TimeoutError:
                            avatar_turn.interrupted = True
                            raise asyncio.CancelledError
                        # wait_playback returned: every declared segment finished playing, so the
                        # full response (markup intact, same as a typed turn) was heard.
                        ctx.metadata["generated_response"] = ctx.provider_response

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
            ctx.metadata.setdefault("step_results", []).append(
                StepResult(step_name=step_name, duration_ms=duration_ms, success=True)
            )
            await _record_turn_trace_step(ctx, trace_id, step_name, duration_ms, success=True)

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

        try:
            await stream_db.rollback()
        except Exception as exc:
            logger.warning(
                "Stream DB rollback before post-stream writes failed: %s",
                exc,
                exc_info=True,
            )

        persist_kwargs = dict(
            session_id=session_id,
            companion_id=session_companion_id,
            current_user=current_user,
            user_message=request.message,
            assistant_message=response,
            turn_id=ctx.metadata.get("turn_id"),
            strict=avatar_turn is not None,
        )
        if avatar_turn:
            # A commit can succeed even if its waiter is cancelled. Finish that
            # exact transaction before deciding whether partial persistence is
            # necessary, so an interruption cannot insert the exchange twice.
            persistence_task = asyncio.create_task(_persist_messages(**persist_kwargs))
            try:
                await asyncio.shield(persistence_task)
            except asyncio.CancelledError:
                with anyio.CancelScope(shield=True):
                    await asyncio.shield(persistence_task)
                persisted = True
                avatar_turn.persisted = True
                raise
        else:
            await _persist_messages(**persist_kwargs)
        persisted = True
        if avatar_turn:
            avatar_turn.persisted = True
        else:
            _maybe_schedule_title_generation(
                http_request=http_request,
                session_id=session_id,
                session_title=session_title,
                user_message=request.message,
                assistant_message=response,
            )

        await _run_post_stream_db_write(
            ctx,
            "message embedding generation",
            _generate_message_embeddings,
        )

        yield _sse_data({"type": "response", "text": response, "turn_id": ctx.metadata.get("turn_id")})

        await _run_post_stream_db_write(
            ctx,
            "identity update",
            lambda post_ctx: UpdateIdentityStep().run(post_ctx),
        )

        await _complete_turn_trace(ctx, trace_id)
        yield "data: [DONE]\n\n"
    except (asyncio.CancelledError, GeneratorExit):
        # Starlette cancels its streaming task on disconnect. Shield database
        # cleanup from that AnyIO scope; task.cancel() alone does not save a turn.
        with anyio.CancelScope(shield=True):
            if avatar_turn is not None and persisted and avatar_turn.source == "typed":
                # The browser closed the SSE connection after the full text was saved (the
                # text UI is done as soon as `response` arrives). That is not an interruption:
                # the speech keeps playing. Only the post-response bookkeeping is left.
                if ctx is not None:
                    try:
                        await _run_post_stream_db_write(
                            ctx,
                            "identity update",
                            lambda post_ctx: UpdateIdentityStep().run(post_ctx),
                        )
                        await _complete_turn_trace(ctx, trace_id)
                    except Exception:
                        logger.warning("Post-response work after disconnect failed", exc_info=True)
                raise
            if avatar_turn is not None:
                avatar_turn.interrupted = True
                await coordinator.stop_speech(avatar, avatar_turn)
            if avatar_turn is not None and not persisted:
                try:
                    await stream_db.rollback()
                    delivered = avatar_turn.delivered_text
                    await _persist_messages(
                        session_id=session_id,
                        companion_id=session_companion_id,
                        current_user=current_user,
                        user_message=request.message,
                        assistant_message=delivered,
                        turn_id=avatar_turn.turn_id,
                        interrupted=True,
                        strict=True,
                    )
                    persisted = True
                    avatar_turn.persisted = True
                except Exception:
                    avatar_turn.persistence_error = True
                    raise
                if ctx is not None:
                    ctx.metadata["interrupted"] = True
                    ctx.metadata["generated_response"] = ctx.provider_response
                    ctx.provider_response = delivered
                    ctx.final_response = delivered
                    await _complete_turn_trace(ctx, trace_id)
        raise
    except HTTPException:
        if typing_active:
            yield _sse_data({"type": "typing", "status": "stopped"})
        raise
    except Exception as exc:
        if avatar_turn is not None:
            avatar_turn.persistence_error = not persisted
            avatar_turn.interrupted = True
            await coordinator.stop_speech(avatar, avatar_turn)
        import logging

        logging.getLogger("chatbot.streaming").error(
            "Stream turn error: %s",
            exc,
            exc_info=True,
        )
        if typing_active:
            yield _sse_data({"type": "typing", "status": "stopped"})
        yield _sse_data({"type": "error", "error": "An internal error occurred"})
        yield "data: [DONE]\n\n"
    finally:
        if avatar_turn:
            avatar_turn.task = None
            avatar_turn.finished.set()
        with anyio.CancelScope(shield=True):
            try:
                await stream_db.close()
            except Exception:
                pass


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
    except (asyncio.CancelledError, GeneratorExit):
        ctx.provider_response = "".join(streamed_text_parts)
        _store_llm_trace_metadata(ctx, provider=provider, config=config, result=result_for_metadata)
        raise
    except Exception as exc:
        import logging

        logging.getLogger("chatbot.streaming").error(
            "Stream provider error: %s",
            exc,
            exc_info=True,
        )
        ctx.provider_response = f"Provider error: {exc}"
        _store_llm_trace_metadata(ctx, provider=provider, config=config, result=None)
        yield f"Provider stream error: {exc}"
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
    *,
    room_id: str | None = None,
) -> list[dict[str, Any]]:
    conditions = [Message.session_id == session_id]
    if room_id is not None:
        conditions.append(
            Message.session.has(Session.room_id == room_id)
        )
    result = await db.execute(
        select(Message)
        .where(*conditions)
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
    *,
    session_id: uuid.UUID,
    companion_id: str | None,
    current_user: CurrentUser,
    user_message: str,
    assistant_message: str,
    turn_id: str | None = None,
    interrupted: bool = False,
    strict: bool = False,
) -> None:
    user_uuid = uuid.UUID(current_user.user_id)
    user_msg_ts = datetime.now(timezone.utc)
    assistant_msg_ts = max(datetime.now(timezone.utc), user_msg_ts + timedelta(milliseconds=1))
    db = streaming_session_factory()
    try:
        db.add(
            Message(
                session_id=session_id,
                user_id=user_uuid,
                companion_id=companion_id,
                role="user",
                content=user_message,
                turn_id=turn_id,
                created_at=user_msg_ts,
            )
        )
        if assistant_message:
            db.add(
                Message(
                    session_id=session_id,
                    user_id=user_uuid,
                    companion_id=companion_id,
                    role="assistant",
                    content=assistant_message,
                    turn_id=turn_id,
                    created_at=assistant_msg_ts,
                )
            )
        if interrupted:
            db.add(
                Message(
                    session_id=session_id,
                    user_id=user_uuid,
                    companion_id=companion_id,
                    role="system",
                    content=("[Assistant turn interrupted. Only confirmed delivered text "
                             "is retained; a partially played speech segment may be omitted.]"),
                    turn_id=turn_id,
                    created_at=assistant_msg_ts + timedelta(milliseconds=1),
                )
            )
        await db.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(updated_at=datetime.now(timezone.utc))
        )
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.warning(
                "Message persistence rollback failed for session %s",
                session_id,
                exc_info=True,
            )
        logger.error(
            "Message persistence failed for session %s: %s",
            session_id,
            exc,
            exc_info=True,
        )
        if strict:
            raise
    finally:
        await db.close()


async def _run_post_stream_db_write(
    ctx: TurnContext,
    label: str,
    operation: Any,
) -> None:
    previous_db_session = ctx.metadata.get("db_session")
    previous_db = ctx.metadata.get("db")
    db = streaming_session_factory()
    try:
        ctx.metadata["db_session"] = db
        ctx.metadata["db"] = db
        await operation(ctx)
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.warning(
                "Post-stream %s rollback failed for session %s",
                label,
                ctx.session_id,
                exc_info=True,
            )
        logger.error(
            "Post-stream %s failed for session %s: %s",
            label,
            ctx.session_id,
            exc,
            exc_info=True,
        )
    finally:
        if previous_db_session is None:
            ctx.metadata.pop("db_session", None)
        else:
            ctx.metadata["db_session"] = previous_db_session
        if previous_db is None:
            ctx.metadata.pop("db", None)
        else:
            ctx.metadata["db"] = previous_db
        await db.close()


def _session_snapshot(db_session: Session) -> dict[str, Any]:
    """Return detached session fields used by streaming pipeline metadata."""
    return {
        "id": db_session.id,
        "user_id": db_session.user_id,
        "companion_id": db_session.companion_id,
        "room_id": db_session.room_id,
        "title": db_session.title,
        "mood_state": db_session.mood_state,
        "user_state": db_session.user_state,
        "conversation_mode": db_session.conversation_mode,
        "created_at": db_session.created_at,
        "updated_at": db_session.updated_at,
    }


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
) -> dict[str, Any] | None:
    """Return the most recent IdentityStateModel row for *session_id*, or None."""
    try:
        result = await db.execute(
            select(IdentityStateModel)
            .where(IdentityStateModel.session_id == session_id)
            .order_by(IdentityStateModel.last_updated.desc())
            .limit(1)
        )
        identity = result.scalar_one_or_none()
    except Exception:
        return None
    if identity is None:
        return None
    return {
        "id": identity.id,
        "session_id": identity.session_id,
        "fixed": identity.fixed,
        "slow": identity.slow,
        "mood": identity.mood,
        "last_updated": identity.last_updated,
    }


def _sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _maybe_schedule_title_generation(
    *,
    http_request: Request | None,
    session_id: uuid.UUID,
    session_title: str | None,
    user_message: str,
    assistant_message: str,
    turn_id: str | None = None,
) -> None:
    """Schedule background title generation if the session has no title yet."""
    if session_title is not None:
        return

    if http_request is None:
        return

    provider_registry = _first_state_value(
        http_request,
        "provider_registry",
    )
    if provider_registry is None or not isinstance(provider_registry, ProviderRegistry):
        logger.debug("No provider registry available; skipping title generation")
        return

    provider_name = _first_state_value(
        http_request,
        "provider_name",
        "default_provider",
    )
    model = _first_state_value(
        http_request,
        "model",
        "default_model",
    )
    if not provider_name and not model:
        provider_name = "openrouter"

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
    turn_id: str | None = None,
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
        db = streaming_session_factory()
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
