"""Chat endpoint for the chatbot engine."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from chatbot.api.sessions import sessions_store
from chatbot.core.steps import StepResult, TurnContext

try:
    from chatbot.core.turn import build_default_pipeline as _core_build_default_pipeline
except ImportError:
    _core_build_default_pipeline = None


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    session_id: str
    response: str
    step_results: list[dict[str, Any]]


chat_router = APIRouter(prefix="/chat")


@chat_router.post("/", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    session = sessions_store.get(body.session_id, {})
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

    ctx = await _run_pipeline(ctx)
    response = ctx.final_response
    if response is None:
        response = str(ctx.metadata.get("pipeline_error", "No response"))

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
    return _core_build_default_pipeline()


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
