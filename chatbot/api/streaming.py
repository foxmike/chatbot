"""Server-sent event streaming endpoint for the chatbot engine."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterable, Mapping
import inspect
import time
from time import perf_counter
from typing import Any
import uuid

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from chatbot.api import conversation
from chatbot.avatar.coordinator import get_coordinator
from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.core import StepResult, TurnContext, TurnStep
from chatbot.core.turn import (
    UpdateIdentityStep,
    _generate_message_embeddings,
    _store_llm_trace_metadata,
    prepare_provider_call,
    _start_turn_trace,
    _record_turn_trace_step,
    _complete_turn_trace,
    build_default_pipeline,
)
from chatbot.db import streaming_session_factory, get_db
from chatbot.db.models import Session

logger = logging.getLogger(__name__)


class StreamRequest(BaseModel):
    session_id: str
    message: str
    stream_tokens: bool = True
    room_id: str | None = None
    model_override: str | None = None
    provider_override: str | None = None
    thinking: bool = False

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

    # Companion, personality and identity: shared with standard chat.
    durable_state = {}
    character_profile, companion_persona = await conversation.load_companion_inputs(
        db,
        db_session,
        current_user.user_id,
        durable_state=durable_state,
    )
    history = None
    identity_state = None
    if get_coordinator(http_request.app).get(str(db_session.id), current_user.user_id) is None:
        identity_state = await conversation.load_session_identity(db, sid)
        history = await conversation.load_db_session_history(
            db,
            db_session.id,
            room_id=db_session.room_id,
        )
    # else: an avatar-bound turn loads both after it owns the turn
    # (coordinator.begin), so loading them here would only be discarded.

    return ChatStreamingResponse(
        _stream_turn(
            request,
            http_request=http_request,
            current_user=current_user,
            session_id=db_session.id,
            session_title=db_session.title,
            session_companion_id=db_session.companion_id,
            session_room_id=db_session.room_id,
            session_snapshot=_session_snapshot(db_session),
            character_profile=character_profile,
            durable_state=durable_state,
            identity_state=identity_state,
            history=history,
            # Streaming's established fallback without a companion is an empty persona.
            persona=conversation.resolve_persona(companion_persona, {}),
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
    durable_state: dict[str, Any] | None = None,
    identity_state: Any,
    history: list[dict[str, Any]] | None,
    persona: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    context_at = time.monotonic()
    stream_db = streaming_session_factory()
    typing_active = False
    avatar = None
    avatar_turn = None
    coordinator = None
    ctx = None
    trace_id = None
    persisted = False
    transaction_released = False

    async def release_preparation_transaction() -> None:
        """End the preparation transaction once generation is under way.

        No database transaction stays open through the rest of generation or
        spoken playback. Runs after retrieval, so it changes nothing for this
        turn, and after the first token, so it costs no first-text latency.
        """
        nonlocal transaction_released
        if transaction_released:
            return
        transaction_released = True
        try:
            await conversation.end_preparation_transaction(stream_db)
        except Exception:
            logger.warning("Ending the preparation transaction failed", exc_info=True)
            try:
                await stream_db.rollback()
            except Exception:
                pass

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
                if avatar_turn.source == "spoken":
                    avatar_turn.latency_times["begin_complete"] = time.monotonic()
                # The previous turn's interrupted persistence must finish BEFORE
                # loading context for its successor, including typed -> spoken.
                history = await conversation.load_db_session_history(
                    stream_db, session_id, room_id=session_room_id,
                )
                identity_state = await conversation.load_session_identity(stream_db, session_id)
                if avatar_turn.source == "spoken":
                    avatar_turn.latency_times["history_ready"] = time.monotonic()
        if history is None:
            # The endpoint expected an avatar-bound turn and skipped these loads,
            # but no avatar owns the turn after all.
            history = await conversation.load_db_session_history(
                stream_db, session_id, room_id=session_room_id,
            )
            identity_state = await conversation.load_session_identity(stream_db, session_id)
        if persona is None:
            persona = {}
        ctx = conversation.build_turn_context(
            http_request,
            session_id=request.session_id,
            user_message=request.message,
            history=history,
            persona=persona,
            db=stream_db,
            user_id=current_user.user_id,
            companion_id=session_companion_id,
            room_id=session_room_id or request.room_id,
            character_profile=character_profile,
            identity_state=identity_state,
            durable_state=durable_state,
            session_record=session_snapshot,
            provider_override=request.provider_override,
            model_override=request.model_override,
            thinking=request.thinking,
            turn_id=avatar_turn.turn_id if avatar_turn else str(uuid.uuid4()),
        )
        ctx.metadata["db_session"] = stream_db
        if avatar_turn and avatar_turn.source == "spoken":
            avatar_turn.latency_times["context_start"] = context_at
            ctx.metadata["avatar_latency_times"] = avatar_turn.latency_times
            logger.info("avatar_latency turn_id=%s stage=context_start t=%.6f", avatar_turn.turn_id, context_at)

        if http_request is not None:
            if avatar_turn and avatar_turn.source == "spoken":
                avatar_turn.latency_times["memory_prepare_start"] = time.monotonic()
            await conversation.prepare_memory_workflow(http_request, ctx, stream_db, session_id)
            if avatar_turn and avatar_turn.source == "spoken":
                avatar_turn.latency_times["memory_prepare_complete"] = time.monotonic()

        pipeline = build_default_pipeline()
        ctx.metadata.setdefault("full_conversation_history", list(ctx.history))
        trace_id = await _start_turn_trace(ctx)
        if avatar_turn and avatar_turn.source == "spoken":
            avatar_turn.latency_times["trace_started"] = time.monotonic()

        yield _sse_data({"type": "typing", "status": "started"})
        typing_active = True

        provider_seen = False
        for step in getattr(pipeline, "_steps", []):
            step_name = _step_name(step)
            if step_name == "update_identity":
                continue

            if step_name == "call_provider":
                if avatar_turn and avatar_turn.source == "spoken":
                    avatar_turn.latency_times["context_complete"] = time.monotonic()
                    logger.info("avatar_latency turn_id=%s stage=context_complete t=%.6f", avatar_turn.turn_id, time.monotonic())
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
                            yield _sse_data({"type": "token", "text": token, "turn_id": avatar_turn.turn_id if avatar_turn else None})
                            if avatar_turn:
                                avatar_turn.text_delivered += token
                        await release_preparation_transaction()
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
                await release_preparation_transaction()
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

        # Reached without a provider step (no generation to wait for).
        await release_preparation_transaction()

        # A completed turn's messages and in-turn effects (extracted memories,
        # emotional state, timeline) are committed as one transaction on the
        # generation session, exactly as standard chat commits its request
        # session. A turn whose generation failed is still saved (the error text
        # is the stored reply) but without its effects.
        keep_effects = not _generation_failed(ctx)
        if not keep_effects:
            await conversation.discard_effects_transaction(stream_db)

        persist_kwargs = dict(
            session_id=session_id,
            companion_id=session_companion_id,
            current_user=current_user,
            user_message=request.message,
            assistant_message=response,
            turn_id=ctx.metadata.get("turn_id"),
            strict=avatar_turn is not None,
            completed=keep_effects,
            db=stream_db if keep_effects else None,
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
            conversation.schedule_title_generation(
                http_request,
                session_id=session_id,
                session_title=session_title,
                user_message=request.message,
                assistant_message=response,
                session_factory=streaming_session_factory,
                provider_override=request.provider_override,
                model_override=request.model_override,
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
    inputs = prepare_provider_call(ctx)
    if inputs.error is not None:
        # Same outcome as the non-streaming CallProviderStep: report, never substitute.
        ctx.metadata["_stream_provider_attempted"] = True
        ctx.provider_response = f"Provider error: {inputs.error}"
        _store_llm_trace_metadata(
            ctx, provider=None, config=inputs.config, result=None, streaming=True
        )
        yield f"Provider stream error: {inputs.error}"
        return
    provider = inputs.provider
    if provider is None:
        return

    stream_method = _provider_stream_method(provider)
    if stream_method is None:
        return
    ctx.metadata["_stream_provider_attempted"] = True

    config = inputs.config

    result_for_metadata: Any | None = None
    streamed_text_parts: list[str] = []
    try:
        stream_result = stream_method(
            prompt=inputs.prompt,
            history=inputs.history,
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
        _store_llm_trace_metadata(ctx, provider=provider, config=config, result=result_for_metadata, streaming=True)
        raise
    except Exception as exc:
        import logging

        logging.getLogger("chatbot.streaming").error(
            "Stream provider error: %s",
            exc,
            exc_info=True,
        )
        ctx.provider_response = f"Provider error: {exc}"
        _store_llm_trace_metadata(ctx, provider=provider, config=config, result=None, streaming=True)
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
        streaming=True,
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
    completed: bool = True,
    db: Any | None = None,
) -> None:
    """Commit a turn's messages, in a session of its own unless the caller passes *db*.

    A separate session keeps the commit independent of the generation session
    and of cancellation of the SSE task (used for interrupted turns, whose
    generation session cannot be trusted). A caller-owned *db* is committed or
    rolled back here, so whatever else it holds (the turn's effects) shares the
    outcome, and is left open for the caller to close.
    """
    owned = db is None
    if owned:
        db = streaming_session_factory()
    try:
        await conversation.add_turn_messages(
            db,
            session_id=session_id,
            user_id=uuid.UUID(current_user.user_id),
            companion_id=companion_id,
            user_message=user_message,
            assistant_message=assistant_message,
            turn_id=turn_id,
            interrupted=interrupted,
            omit_empty_assistant=True,
        )
        if completed and not interrupted:
            await conversation.record_completed_interaction(db, companion_id, current_user.user_id)
            # The durable memory-extraction job is inserted in the same
            # transaction that persists the completed turn's messages and
            # effects: a rollback leaves neither completed turn nor job.
            # Interrupted and failed turns schedule no job.
            await conversation.schedule_memory_job(
                db,
                session_id=session_id,
                user_id=uuid.UUID(current_user.user_id),
                companion_id=companion_id,
                turn_id=turn_id,
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
        if owned:
            await db.close()


def _generation_failed(ctx: TurnContext) -> bool:
    """Whether generation produced a provider error instead of a reply.

    Such a turn is still saved (the error text is the stored reply, as in
    standard chat) but its in-turn effects are not committed.
    """
    return str(ctx.provider_response or "").startswith("Provider error:")


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


def _sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"
