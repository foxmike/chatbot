"""First-party turn pipeline for the chatbot engine. No framework dependency."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from time import perf_counter
from typing import Any

from chatbot.core.steps import StepResult, TurnContext, TurnStep
from chatbot.providers.base import ProviderConfig

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
        persona_prompt = _assemble_persona_prompt(self._assembler, ctx.persona)
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

    return TurnPipeline(
        [
            ReceiveStep(),
            RetrieveStep(WorkingMemoryLoader),
            CompileContextStep(),
            CallProviderStep(),
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


def _assemble_persona_prompt(assembler: Any | None, persona: dict[str, Any]) -> str:
    if assembler is None:
        logger.warning("Persona prompt assembler is unavailable; using fallback persona")
        return "You are a helpful, neutral assistant. Respond clearly and accurately."

    try:
        return str(assembler.assemble(persona))
    except Exception:
        logger.warning("Persona prompt assembly failed; using fallback persona", exc_info=True)
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
