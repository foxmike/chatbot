"""First-party turn pipeline for the chatbot engine. No framework dependency."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import inspect
from time import perf_counter
from typing import Any

from chatbot.core.steps import StepResult, TurnContext, TurnStep
from chatbot.providers.base import ProviderConfig

try:
    from chatbot.db.models import BotMoodLog, Session, UserStateLog
except ImportError:
    BotMoodLog = None  # type: ignore[assignment]
    Session = None  # type: ignore[assignment]
    UserStateLog = None  # type: ignore[assignment]

try:
    from chatbot.memory.extraction import MemoryExtractor
except ImportError:
    MemoryExtractor = None  # type: ignore[assignment]

try:
    from chatbot.memory.working_memory import WorkingMemoryLoader
except ImportError:
    WorkingMemoryLoader = None  # type: ignore[assignment]

try:
    from chatbot.providers.router import ProviderRouter
except ImportError:
    ProviderRouter = None  # type: ignore[assignment]

try:
    from chatbot.soul.persona_prompt import PersonaPromptAssembler
except ImportError:
    PersonaPromptAssembler = None  # type: ignore[assignment]

try:
    from chatbot.soul.mood import MoodEngine, MoodState
except ImportError:
    MoodEngine = None  # type: ignore[assignment]
    MoodState = None  # type: ignore[assignment]

try:
    from chatbot.soul.user_state import UserStateEngine, UserState
except ImportError:
    UserStateEngine = None  # type: ignore[assignment]
    UserState = None  # type: ignore[assignment]

try:
    from chatbot.soul.modes import ModeSelector, ConversationMode
except ImportError:
    ModeSelector = None  # type: ignore[assignment]
    ConversationMode = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class ReceiveStep(TurnStep):
    name = "receive"

    async def run(self, ctx: TurnContext) -> TurnContext:
        ctx.user_message = ctx.user_message.strip()
        return ctx


class RetrieveStep(TurnStep):
    name = "retrieve"

    def __init__(self, loader_cls: Any | None = WorkingMemoryLoader) -> None:
        self._loader_cls = loader_cls

    async def run(self, ctx: TurnContext) -> TurnContext:
        if self._loader_cls is None:
            logger.warning("WorkingMemoryLoader is unavailable; using empty history")
            ctx.history = []
        else:
            store = _first_metadata_value(
                ctx,
                "working_memory_store",
                "working_memory",
                "memory_store",
                "database",
                "db",
            )
            if store is None:
                logger.warning("Working memory store is unavailable; using empty history")
                ctx.history = []
            else:
                try:
                    ctx.history = await self._loader_cls(store).load(ctx.session_id)
                except Exception:
                    logger.warning(
                        "Working memory load failed for session %s",
                        ctx.session_id,
                        exc_info=True,
                    )
                    ctx.history = []

        ctx.retrieved_memories = _coerce_memory_list(
            ctx.metadata.get("retrieved_memories")
            or ctx.metadata.get("retrieved_memory")
            or ctx.retrieved_memories
        )

        retrieval_engine = ctx.metadata.get("retrieval_engine")
        if retrieval_engine is None:
            _store_retrieved_memories(ctx)
            return ctx
        try:
            results = await retrieval_engine.retrieve(
                query=ctx.user_message,
                session_id=ctx.session_id,
            )
            ctx.retrieved_memories = _coerce_memory_list(results)
        except Exception:
            logger.warning(
                "Memory retrieval failed for session %s", ctx.session_id, exc_info=True
            )
            ctx.retrieved_memories = []
        _store_retrieved_memories(ctx)
        return ctx


class InferUserStateStep(TurnStep):
    name = "infer_user_state"

    def __init__(
        self,
        engine_cls: Any | None = UserStateEngine,
        mood_engine_cls: Any | None = MoodEngine,
    ) -> None:
        self._engine_cls = engine_cls
        self._mood_engine_cls = mood_engine_cls

    async def run(self, ctx: TurnContext) -> TurnContext:
        if not _emotional_intelligence_ready(ctx):
            return ctx
        if self._engine_cls is None or self._mood_engine_cls is None:
            return ctx

        mood_engine = _configured_component(
            ctx,
            ("mood_engine", "bot_mood_engine"),
            self._mood_engine_cls,
        )
        user_state_engine = _configured_component(
            ctx,
            ("user_state_engine",),
            self._engine_cls,
        )
        if mood_engine is None or user_state_engine is None:
            return ctx

        mood_state = _current_mood_state(ctx, mood_engine)
        if mood_state is None:
            return ctx

        prior_user_state = _current_user_state(ctx)
        messages = _recent_turn_messages(ctx)
        topics = _current_topics(ctx)

        try:
            user_state = user_state_engine.infer(
                messages=messages,
                topics=topics,
                mood_state=mood_state,
                prior_user_state=prior_user_state,
            )
        except Exception:
            logger.warning(
                "User state inference failed for session %s",
                ctx.session_id,
                exc_info=True,
            )
            return ctx

        ctx.metadata["mood_state"] = mood_state
        ctx.metadata["previous_user_state"] = prior_user_state
        ctx.metadata["user_state"] = user_state
        return ctx


class SelectConversationModeStep(TurnStep):
    name = "select_conversation_mode"

    def __init__(self, selector_cls: Any | None = ModeSelector) -> None:
        self._selector_cls = selector_cls

    async def run(self, ctx: TurnContext) -> TurnContext:
        if not _emotional_intelligence_ready(ctx) or self._selector_cls is None:
            return ctx

        selector = _configured_component(
            ctx,
            ("mode_selector", "conversation_mode_selector"),
            self._selector_cls,
        )
        if selector is None:
            return ctx

        mood_state = _current_mood_state(ctx)
        if mood_state is None:
            return ctx

        user_state = _current_user_state(ctx)
        messages = _recent_turn_messages(ctx)
        try:
            mode = selector.select(
                messages=messages,
                user_state=user_state,
                mood_state=mood_state,
            )
        except Exception:
            logger.warning(
                "Conversation mode selection failed for session %s",
                ctx.session_id,
                exc_info=True,
            )
            return ctx

        ctx.metadata["conversation_mode"] = mode
        response_params = getattr(selector, "get_response_params", None)
        if callable(response_params):
            try:
                ctx.metadata["conversation_mode_response_params"] = response_params(mode)
            except Exception:
                logger.warning("Conversation mode response params failed", exc_info=True)
        return ctx


class CompileContextStep(TurnStep):
    name = "compile_context"

    def __init__(self, assembler: Any | None = None) -> None:
        if assembler is not None:
            self._assembler = assembler
        elif PersonaPromptAssembler is not None:
            self._assembler = PersonaPromptAssembler()
        else:
            self._assembler = None

    async def run(self, ctx: TurnContext) -> TurnContext:
        mood_state = _current_mood_state(ctx) if _emotional_intelligence_ready(ctx) else None
        mode = _current_conversation_mode(ctx) if _emotional_intelligence_ready(ctx) else None
        persona_prompt = _assemble_persona_prompt(
            self._assembler,
            ctx.persona,
            mood=mood_state,
            mode=mode,
        )
        ctx.compiled_context = _compile_context_text(
            persona_prompt=persona_prompt,
            history=ctx.history,
            retrieved_memories=ctx.retrieved_memories,
        )
        ctx.metadata["persona_prompt"] = persona_prompt
        ctx.metadata["compiled_context"] = ctx.compiled_context
        return ctx


class CallProviderStep(TurnStep):
    name = "call_provider"

    async def run(self, ctx: TurnContext) -> TurnContext:
        provider = _select_provider(ctx)
        if provider is None:
            ctx.provider_response = _fallback_provider_response(ctx)
            return ctx

        config = _provider_config(ctx)
        history = _provider_history(ctx)
        try:
            result = await provider.complete(
                prompt=ctx.user_message,
                history=history,
                config=config,
            )
            ctx.provider_response = result.text
        except Exception as exc:
            logger.warning("Provider call failed: %s", exc, exc_info=True)
            ctx.provider_response = f"Provider error: {exc}"
        return ctx


class UpdateMoodStep(TurnStep):
    name = "update_mood"

    def __init__(self, engine_cls: Any | None = MoodEngine) -> None:
        self._engine_cls = engine_cls

    async def run(self, ctx: TurnContext) -> TurnContext:
        if not _emotional_intelligence_ready(ctx) or self._engine_cls is None:
            return ctx

        mood_engine = _configured_component(
            ctx,
            ("mood_engine", "bot_mood_engine"),
            self._engine_cls,
        )
        if mood_engine is None:
            return ctx

        current_state = _current_mood_state(ctx, mood_engine)
        if current_state is None:
            return ctx

        turn_result = {
            "session_id": ctx.session_id,
            "user_message": ctx.user_message,
            "provider_response": ctx.provider_response,
            "user_state": _json_safe(_current_user_state(ctx)),
            "conversation_mode": _json_safe(_current_conversation_mode(ctx)),
            "retrieved_memories": _json_safe(ctx.retrieved_memories),
            "extracted_memories": _json_safe(ctx.extracted_memories),
        }
        try:
            updated_state = mood_engine.update(turn_result, current_state)
        except Exception:
            logger.warning(
                "Mood update failed for session %s",
                ctx.session_id,
                exc_info=True,
            )
            return ctx

        ctx.metadata["previous_mood_state"] = current_state
        ctx.metadata["mood_state"] = updated_state
        return ctx


class PersistEmotionalStateStep(TurnStep):
    name = "persist_emotional_state"

    async def run(self, ctx: TurnContext) -> TurnContext:
        if not _emotional_intelligence_ready(ctx):
            return ctx

        mood_state = _current_mood_state(ctx)
        user_state = _current_user_state(ctx)
        mode = _current_conversation_mode(ctx)
        if mood_state is None and user_state is None and mode is None:
            return ctx

        _persist_session_mapping(ctx, mood_state, user_state, mode)
        await _persist_db_emotional_state(ctx, mood_state, user_state, mode)
        return ctx


class ExtractMemoryStep(TurnStep):
    name = "extract_memory"

    def __init__(self, extractor_cls: Any | None = MemoryExtractor) -> None:
        self._extractor_cls = extractor_cls

    async def run(self, ctx: TurnContext) -> TurnContext:
        if self._extractor_cls is None:
            logger.warning("MemoryExtractor is unavailable; no memories extracted")
            ctx.extracted_memories = []
            return ctx

        storage = ctx.metadata.get("memory_storage")
        database = _first_metadata_value(
            ctx,
            "memory_database",
            "database",
            "db",
            "episodic_store",
        )
        if storage is None and database is None:
            logger.warning("Memory extraction store is unavailable; no memories extracted")
            ctx.extracted_memories = []
            return ctx

        try:
            extractor = self._extractor_cls(database=database, storage=storage)
            ctx.extracted_memories = await extractor.extract(
                session_id=ctx.session_id,
                user_message=ctx.user_message,
                provider_response=ctx.provider_response or "",
            )
        except Exception:
            logger.warning(
                "Memory extraction failed for session %s",
                ctx.session_id,
                exc_info=True,
            )
            ctx.extracted_memories = []
        return ctx


class RespondStep(TurnStep):
    name = "respond"

    async def run(self, ctx: TurnContext) -> TurnContext:
        if ctx.provider_response is None:
            ctx.provider_response = "No response"
        ctx.final_response = ctx.provider_response
        return ctx


class TurnPipeline:
    def __init__(self, steps: list[TurnStep]) -> None:
        self._steps = list(steps)
        self._last_ctx: TurnContext | None = None

    async def run(self, ctx: TurnContext) -> TurnContext:
        step_results: list[StepResult] = []
        ctx.metadata["step_results"] = step_results
        self._last_ctx = ctx

        for step in self._steps:
            step_name = _step_name(step)
            started_at = perf_counter()

            try:
                ctx = await step.run(ctx)
            except Exception as exc:
                duration_ms = (perf_counter() - started_at) * 1000
                step_results.append(
                    StepResult(
                        step_name=step_name,
                        duration_ms=duration_ms,
                        success=False,
                        error=str(exc),
                    )
                )
                ctx.metadata["pipeline_error"] = str(exc)
                self._last_ctx = ctx
                return ctx

            duration_ms = (perf_counter() - started_at) * 1000
            step_results.append(
                StepResult(
                    step_name=step_name,
                    duration_ms=duration_ms,
                    success=True,
                )
            )
            ctx.metadata["step_results"] = step_results
            self._last_ctx = ctx

        return ctx

    @property
    def step_results(self) -> list[StepResult]:
        if self._last_ctx is None:
            return []

        results = self._last_ctx.metadata.get("step_results", [])
        return list(results)


class _IdentityTurnStep(TurnStep):
    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, ctx: TurnContext) -> TurnContext:
        return ctx


def build_default_pipeline() -> TurnPipeline:
    if WorkingMemoryLoader is None:
        logger.warning("WorkingMemoryLoader import failed; RetrieveStep will use fallback")
    if PersonaPromptAssembler is None:
        logger.warning("PersonaPromptAssembler import failed; CompileContextStep will use fallback")
    if MemoryExtractor is None:
        logger.warning("MemoryExtractor import failed; ExtractMemoryStep will use fallback")
    if not _emotional_intelligence_imports_available():
        logger.warning("Emotional intelligence imports unavailable; EI steps will no-op")

    return TurnPipeline(
        [
            ReceiveStep(),
            RetrieveStep(WorkingMemoryLoader),
            InferUserStateStep(UserStateEngine, MoodEngine),
            SelectConversationModeStep(ModeSelector),
            CompileContextStep(),
            CallProviderStep(),
            UpdateMoodStep(MoodEngine),
            PersistEmotionalStateStep(),
            ExtractMemoryStep(MemoryExtractor),
            RespondStep(),
        ]
    )


def _step_name(step: TurnStep) -> str:
    name = getattr(step, "name", None)
    if isinstance(name, str) and name:
        return name
    return step.__class__.__name__


def _first_metadata_value(ctx: TurnContext, *keys: str) -> Any | None:
    for key in keys:
        value = ctx.metadata.get(key)
        if value is not None:
            return value
    return None


def _store_retrieved_memories(ctx: TurnContext) -> None:
    memories = list(ctx.retrieved_memories)
    ctx.metadata["retrieved_memories"] = memories
    ctx.metadata["retrieved_memory"] = memories


def _assemble_persona_prompt(
    assembler: Any | None,
    persona: dict[str, Any],
    *,
    mood: Any | None = None,
    mode: Any | None = None,
) -> str:
    if assembler is None:
        logger.warning("Persona prompt assembler is unavailable; using fallback persona")
        return "You are a helpful, neutral assistant. Respond clearly and accurately."

    try:
        return str(assembler.assemble(persona, mood=mood, mode=mode))
    except TypeError:
        try:
            return str(assembler.assemble(persona))
        except Exception:
            logger.warning(
                "Persona prompt assembly failed; using fallback persona",
                exc_info=True,
            )
    except Exception:
        logger.warning(
            "Persona prompt assembly failed; using fallback persona",
            exc_info=True,
        )
        return "You are a helpful, neutral assistant. Respond clearly and accurately."
    return "You are a helpful, neutral assistant. Respond clearly and accurately."


def _compile_context_text(
    *,
    persona_prompt: str,
    history: Sequence[Mapping[str, Any]],
    retrieved_memories: Sequence[Mapping[str, Any]],
) -> str:
    sections: list[str] = []
    if persona_prompt.strip():
        sections.append(persona_prompt.strip())

    memory_lines = [_memory_line(memory) for memory in retrieved_memories]
    memory_lines = [line for line in memory_lines if line]
    if memory_lines:
        sections.append("Retrieved memories:\n" + "\n".join(memory_lines))

    history_lines = [_message_line(message) for message in history]
    history_lines = [line for line in history_lines if line]
    if history_lines:
        sections.append("Conversation history:\n" + "\n".join(history_lines))

    return "\n\n".join(sections)


def _message_line(message: Mapping[str, Any]) -> str:
    role = str(message.get("role") or "message").strip() or "message"
    content = str(message.get("content") or "").strip()
    if not content:
        return ""
    return f"{role}: {content}"


def _memory_line(memory: Mapping[str, Any]) -> str:
    content = memory.get("content")
    if content is None:
        content = memory.get("summary")
    if content is None:
        content = memory.get("fact_text")
    text = str(content or "").strip()
    if not text:
        return ""
    memory_type = str(memory.get("type") or memory.get("kind") or "memory").strip()
    return f"- {memory_type}: {text}" if memory_type else f"- {text}"


def _coerce_memory_list(value: Any) -> list[dict]:
    if value is None or isinstance(value, (str, bytes)):
        return []

    try:
        items = list(value)
    except TypeError:
        return []

    memories: list[dict] = []
    for item in items:
        if isinstance(item, Mapping):
            memories.append(dict(item))
            continue
        as_dict = getattr(item, "as_dict", None)
        if callable(as_dict):
            try:
                mapped = as_dict()
            except Exception:
                mapped = None
            if isinstance(mapped, Mapping):
                memories.append(dict(mapped))
                continue
        content = getattr(item, "content", None)
        if content is None:
            turn = getattr(item, "turn", None)
            content = getattr(turn, "content", None)
        if content is not None:
            memories.append({"content": str(content)})
    return memories


def _select_provider(ctx: TurnContext) -> Any | None:
    provider = ctx.metadata.get("provider")
    if provider is not None:
        return provider

    router = ctx.metadata.get("provider_router")
    config = _provider_config(ctx)
    if router is not None and hasattr(router, "select"):
        try:
            return router.select(ctx.user_message, config)
        except Exception:
            logger.warning("Provider router selection failed", exc_info=True)
            return None

    registry = ctx.metadata.get("provider_registry")
    if registry is None:
        logger.warning("Provider registry is unavailable; using local provider fallback")
        return None

    if ProviderRouter is not None:
        policy = ctx.metadata.get("provider_policy")
        try:
            return ProviderRouter(registry, policy).select(ctx.user_message, config)
        except Exception:
            logger.warning("Provider router selection failed", exc_info=True)

    provider_name = str(ctx.metadata.get("provider_name") or ctx.metadata.get("default_provider") or "openrouter")
    get_provider = getattr(registry, "get", None)
    if callable(get_provider):
        try:
            return get_provider(provider_name)
        except Exception:
            logger.warning("Provider lookup failed for %s", provider_name, exc_info=True)

    return None


def _provider_config(ctx: TurnContext) -> ProviderConfig:
    config = ctx.metadata.get("provider_config")
    if isinstance(config, ProviderConfig):
        return config

    if isinstance(config, Mapping):
        model = str(config.get("model") or "")
        extra = config.get("extra") if isinstance(config.get("extra"), Mapping) else {}
        return ProviderConfig(
            model=model,
            temperature=_optional_float(config.get("temperature")),
            max_tokens=_optional_int(config.get("max_tokens")),
            top_p=_optional_float(config.get("top_p")),
            stop=_optional_stop(config.get("stop")),
            timeout_seconds=_optional_float(config.get("timeout_seconds")),
            extra=dict(extra),
        )

    return ProviderConfig(
        model=str(ctx.metadata.get("model") or ctx.metadata.get("default_model") or ""),
        temperature=_optional_float(ctx.metadata.get("temperature")),
        max_tokens=_optional_int(ctx.metadata.get("max_tokens")),
        top_p=_optional_float(ctx.metadata.get("top_p")),
        stop=_optional_stop(ctx.metadata.get("stop")),
        timeout_seconds=_optional_float(ctx.metadata.get("timeout_seconds")),
    )


def _provider_history(ctx: TurnContext) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    if ctx.compiled_context:
        history.append({"role": "system", "content": ctx.compiled_context})

    for message in ctx.history:
        if not isinstance(message, Mapping):
            continue
        role = message.get("role")
        content = message.get("content")
        if role in {"system", "user", "assistant", "tool"} and content is not None:
            history.append({"role": str(role), "content": str(content)})

    return history


def _fallback_provider_response(ctx: TurnContext) -> str:
    message = ctx.user_message.strip()
    if not message:
        return "I received an empty message."
    return f"I received your message and no provider backend is configured: {message}"


def _emotional_intelligence_ready(ctx: TurnContext) -> bool:
    if not _emotional_intelligence_imports_available():
        return False
    if not _emotional_intelligence_enabled(ctx):
        return False
    for key in (
        "mood_engine",
        "bot_mood_engine",
        "user_state_engine",
        "mode_selector",
        "conversation_mode_selector",
    ):
        value = ctx.metadata.get(key)
        if key in ctx.metadata and (value is None or value is False):
            return False
    return True


def _emotional_intelligence_imports_available() -> bool:
    return (
        MoodEngine is not None
        and MoodState is not None
        and UserStateEngine is not None
        and UserState is not None
        and ModeSelector is not None
        and ConversationMode is not None
    )


def _emotional_intelligence_enabled(ctx: TurnContext) -> bool:
    for key in (
        "emotional_intelligence_enabled",
        "enable_emotional_intelligence",
        "soul_enabled",
    ):
        if key in ctx.metadata:
            return _coerce_bool(ctx.metadata[key], default=True)

    return _coerce_bool(
        os.getenv("CHATBOT_EMOTIONAL_INTELLIGENCE_ENABLED"),
        default=True,
    )


def _configured_component(
    ctx: TurnContext,
    keys: Sequence[str],
    default_cls: Any | None,
) -> Any | None:
    for key in keys:
        if key not in ctx.metadata:
            continue
        configured = ctx.metadata[key]
        if configured is None or configured is False:
            return None
        if isinstance(configured, type):
            try:
                return configured()
            except Exception:
                logger.warning("Configured component %s failed to initialize", key, exc_info=True)
                return None
        return configured

    if default_cls is None:
        return None
    try:
        return default_cls()
    except Exception:
        logger.warning("Default component failed to initialize", exc_info=True)
        return None


def _current_mood_state(ctx: TurnContext, mood_engine: Any | None = None) -> Any | None:
    state = _coerce_mood_state(ctx.metadata.get("mood_state"))
    if state is not None:
        return state

    state = _coerce_mood_state(_read_session_field(ctx, "mood_state"))
    if state is not None:
        ctx.metadata["mood_state"] = state
        return state

    if mood_engine is None and MoodEngine is not None:
        mood_engine = _configured_component(
            ctx,
            ("mood_engine", "bot_mood_engine"),
            MoodEngine,
        )
    default_state = getattr(mood_engine, "default_state", None)
    if callable(default_state):
        try:
            state = default_state()
        except Exception:
            logger.warning("Default mood state creation failed", exc_info=True)
            return None
        ctx.metadata["mood_state"] = state
        return state

    return None


def _current_user_state(ctx: TurnContext) -> Any | None:
    state = _coerce_user_state(ctx.metadata.get("user_state"))
    if state is not None:
        return state

    state = _coerce_user_state(_read_session_field(ctx, "user_state"))
    if state is not None:
        ctx.metadata["user_state"] = state
    return state


def _current_conversation_mode(ctx: TurnContext) -> Any | None:
    mode = _coerce_conversation_mode(ctx.metadata.get("conversation_mode"))
    if mode is not None:
        return mode

    mode = _coerce_conversation_mode(_read_session_field(ctx, "conversation_mode"))
    if mode is not None:
        ctx.metadata["conversation_mode"] = mode
    return mode


def _recent_turn_messages(ctx: TurnContext) -> list[dict]:
    messages: list[dict] = []
    for message in ctx.history:
        coerced = _coerce_message(message)
        if coerced is not None:
            messages.append(coerced)

    if ctx.user_message.strip():
        messages.append({"role": "user", "content": ctx.user_message.strip()})
    return messages[-12:]


def _coerce_message(message: Any) -> dict[str, str] | None:
    if isinstance(message, Mapping):
        role = message.get("role")
        content = message.get("content")
    else:
        role = getattr(message, "role", None)
        content = getattr(message, "content", None)

    if role is None or content is None:
        return None
    role_text = str(role).strip()
    content_text = str(content).strip()
    if not role_text or not content_text:
        return None
    return {"role": role_text, "content": content_text}


def _current_topics(ctx: TurnContext) -> list[str]:
    for key in ("topics", "current_topics", "topic"):
        topics = _coerce_text_list(ctx.metadata.get(key))
        if topics:
            return topics
    return []


def _read_session_field(ctx: TurnContext, field_name: str) -> Any | None:
    if field_name in ctx.metadata and ctx.metadata[field_name] is not None:
        return ctx.metadata[field_name]

    for key in ("session_record", "db_session_record", "session_model", "session"):
        session = ctx.metadata.get(key)
        value = _read_field(session, field_name)
        if value is not None:
            return value

    for key in ("session_store", "sessions_store"):
        session_store = ctx.metadata.get(key)
        if isinstance(session_store, Mapping):
            value = _read_field(session_store.get(ctx.session_id), field_name)
            if value is not None:
                return value
    return None


def _read_field(source: Any, field_name: str) -> Any | None:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(field_name)
    return getattr(source, field_name, None)


def _coerce_mood_state(value: Any) -> Any | None:
    if value is None or MoodState is None:
        return None
    if isinstance(value, MoodState):
        return value
    if not isinstance(value, Mapping):
        return None

    try:
        return MoodState(
            mood=str(value.get("mood") or "warm"),
            energy=_coerce_float(value.get("energy"), 0.5),
            closeness=_coerce_float(value.get("closeness"), 0.0),
            last_updated=_coerce_datetime(value.get("last_updated")),
            recent_emotional_context=_coerce_text_list(
                value.get("recent_emotional_context")
            ),
            turns_since_mood_shift=_coerce_int(
                value.get("turns_since_mood_shift"),
                0,
            ),
        )
    except Exception:
        logger.warning("Stored mood state could not be loaded", exc_info=True)
        return None


def _coerce_user_state(value: Any) -> Any | None:
    if value is None or UserState is None:
        return None
    if isinstance(value, UserState):
        return value
    if not isinstance(value, Mapping):
        return None

    primary_state = value.get("primary_state")
    if primary_state is not None:
        primary_state = str(primary_state).strip() or None

    try:
        return UserState(
            primary_state=primary_state,
            confidence=_coerce_float(value.get("confidence"), 0.0),
            secondary_states=_coerce_secondary_states(value.get("secondary_states")),
            evidence_summary=str(value.get("evidence_summary") or ""),
            last_inferred=_coerce_datetime(value.get("last_inferred")),
            inference_count=_coerce_int(value.get("inference_count"), 0),
            confidence_history=tuple(
                _coerce_float(item, 0.0)
                for item in _coerce_sequence(value.get("confidence_history"))
            ),
        )
    except Exception:
        logger.warning("Stored user state could not be loaded", exc_info=True)
        return None


def _coerce_conversation_mode(value: Any) -> Any | None:
    if value is None or ConversationMode is None:
        return None
    if isinstance(value, ConversationMode):
        return value
    if isinstance(value, Enum):
        value = value.value
    text = str(value).strip()
    if not text:
        return None
    try:
        return ConversationMode(text)
    except Exception:
        logger.warning("Stored conversation mode could not be loaded: %s", text)
        return None


def _coerce_secondary_states(value: Any) -> list[tuple[str, float]]:
    states: list[tuple[str, float]] = []
    for item in _coerce_sequence(value):
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes)):
            continue
        if len(item) < 2:
            continue
        state = str(item[0]).strip()
        if state:
            states.append((state, _coerce_float(item[1], 0.0)))
    return states


def _persist_session_mapping(
    ctx: TurnContext,
    mood_state: Any | None,
    user_state: Any | None,
    mode: Any | None,
) -> None:
    values = _emotional_state_values(mood_state, user_state, mode)

    session = ctx.metadata.get("session")
    if isinstance(session, dict):
        session.update(values)

    for key in ("session_store", "sessions_store"):
        session_store = ctx.metadata.get(key)
        if isinstance(session_store, dict):
            stored_session = session_store.get(ctx.session_id)
            if isinstance(stored_session, dict):
                stored_session.update(values)


async def _persist_db_emotional_state(
    ctx: TurnContext,
    mood_state: Any | None,
    user_state: Any | None,
    mode: Any | None,
) -> None:
    db_session = _db_session(ctx)
    if db_session is None or Session is None:
        return

    coerced_session_id = _coerce_uuid(ctx.session_id)
    if coerced_session_id is None:
        logger.warning(
            "Skipping emotional state DB persistence for non-UUID session %s",
            ctx.session_id,
        )
        return

    try:
        session_record = await _load_db_session_record(
            db_session,
            coerced_session_id,
            ctx,
        )
        if session_record is None:
            logger.warning(
                "Skipping emotional state DB persistence; session %s was not found",
                ctx.session_id,
            )
            return

        values = _emotional_state_values(mood_state, user_state, mode)
        if "mood_state" in values:
            session_record.mood_state = values["mood_state"]
        if "user_state" in values:
            session_record.user_state = values["user_state"]
        if "conversation_mode" in values:
            session_record.conversation_mode = values["conversation_mode"]

        if BotMoodLog is not None and mood_state is not None:
            db_session.add(
                BotMoodLog(
                    session_id=coerced_session_id,
                    mood=str(getattr(mood_state, "mood")),
                    energy=float(getattr(mood_state, "energy")),
                    closeness=float(getattr(mood_state, "closeness")),
                )
            )

        if UserStateLog is not None and user_state is not None:
            db_session.add(
                UserStateLog(
                    session_id=coerced_session_id,
                    primary_state=getattr(user_state, "primary_state", None),
                    confidence=float(getattr(user_state, "confidence", 0.0)),
                    evidence_summary=getattr(user_state, "evidence_summary", None),
                )
            )

        await _flush_or_commit(db_session)
    except Exception:
        logger.warning(
            "Emotional state persistence failed for session %s",
            ctx.session_id,
            exc_info=True,
        )


async def _load_db_session_record(
    db_session: Any,
    session_id: uuid.UUID,
    ctx: TurnContext,
) -> Any | None:
    for key in ("session_record", "db_session_record", "session_model", "session"):
        candidate = ctx.metadata.get(key)
        if Session is not None and isinstance(candidate, Session):
            return candidate

    get_method = getattr(db_session, "get", None)
    if callable(get_method) and Session is not None:
        try:
            record = await _maybe_await(get_method(Session, session_id))
            if record is not None:
                return record
        except Exception:
            logger.warning("Session.get failed for emotional state load", exc_info=True)

    try:
        import sqlalchemy as sa

        result = await _maybe_await(
            db_session.execute(sa.select(Session).where(Session.id == session_id))
        )
        scalar = getattr(result, "scalar_one_or_none", None)
        if callable(scalar):
            return scalar()
    except Exception:
        logger.warning("Session select failed for emotional state load", exc_info=True)
    return None


def _emotional_state_values(
    mood_state: Any | None,
    user_state: Any | None,
    mode: Any | None,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if mood_state is not None:
        values["mood_state"] = _json_safe(mood_state)
    if user_state is not None:
        values["user_state"] = _json_safe(user_state)
    if mode is not None:
        values["conversation_mode"] = _mode_value(mode)
    return values


def _db_session(ctx: TurnContext) -> Any | None:
    for key in (
        "db_session",
        "database_session",
        "database",
        "db",
        "memory_database",
        "working_memory_store",
    ):
        candidate = ctx.metadata.get(key)
        session = _extract_db_session(candidate)
        if session is not None:
            return session
    return None


def _extract_db_session(candidate: Any) -> Any | None:
    if candidate is None:
        return None
    if hasattr(candidate, "execute") and (hasattr(candidate, "add") or hasattr(candidate, "get")):
        return candidate

    session = getattr(candidate, "session", None)
    if session is not None and hasattr(session, "execute"):
        return session
    return None


async def _flush_or_commit(db_session: Any) -> None:
    in_transaction = getattr(db_session, "in_transaction", None)
    if callable(in_transaction) and in_transaction():
        flush = getattr(db_session, "flush", None)
        if callable(flush):
            await _maybe_await(flush())
        return

    commit = getattr(db_session, "commit", None)
    if callable(commit):
        await _maybe_await(commit())
        return

    flush = getattr(db_session, "flush", None)
    if callable(flush):
        await _maybe_await(flush())


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _mode_value(mode: Any | None) -> str | None:
    if mode is None:
        return None
    if isinstance(mode, Enum):
        return str(mode.value)
    return str(mode)


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    return default


def _coerce_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return [text for text in (str(item).strip() for item in _coerce_sequence(value)) if text]


def _coerce_sequence(value: Any) -> list[Any]:
    if value is None or isinstance(value, (str, bytes)):
        return []
    try:
        return list(value)
    except TypeError:
        return []


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            return _coerce_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_stop(value: Any) -> Sequence[str] | None:
    if value is None or isinstance(value, (str, bytes)):
        return None
    if not isinstance(value, Sequence):
        return None
    return [str(item) for item in value]


__all__ = ["TurnPipeline", "build_default_pipeline"]
