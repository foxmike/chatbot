"""First-party turn pipeline for the chatbot engine. No framework dependency."""

from __future__ import annotations

import logging
from time import perf_counter

from chatbot.core.steps import StepResult, TurnContext, TurnStep
from chatbot.memory.context_compiler import compile_context
from chatbot.providers.base import ProviderConfig

logger = logging.getLogger(__name__)


class ReceiveStep(TurnStep):
    name = "receive"

    async def run(self, ctx: TurnContext) -> TurnContext:
        ctx.user_message = ctx.user_message.strip()
        return ctx


class RetrieveStep(TurnStep):
    name = "retrieve"

    async def run(self, ctx: TurnContext) -> TurnContext:
        retrieval_engine = ctx.metadata.get("retrieval_engine")
        if retrieval_engine is None:
            ctx.metadata["retrieved_memory"] = []
            return ctx
        try:
            results = await retrieval_engine.retrieve(
                query=ctx.user_message,
                session_id=ctx.session_id,
            )
            ctx.metadata["retrieved_memory"] = results
        except Exception:
            logger.warning(
                "Memory retrieval failed for session %s", ctx.session_id, exc_info=True
            )
            ctx.metadata["retrieved_memory"] = []
        return ctx


class CompileContextStep(TurnStep):
    name = "compile_context"
    _BUDGET = 32_000

    async def run(self, ctx: TurnContext) -> TurnContext:
        compiled = compile_context(
            budget=self._BUDGET,
            working_memory=ctx.metadata.get("working_memory"),
            retrieval_results=ctx.metadata.get("retrieved_memory") or [],
            persona_stubs=ctx.metadata.get("persona_stubs") or [],
        )
        ctx.metadata["compiled_context"] = compiled.as_messages()
        return ctx


class CallProviderStep(TurnStep):
    name = "call_provider"

    async def run(self, ctx: TurnContext) -> TurnContext:
        registry = ctx.metadata.get("provider_registry")
        if registry is None:
            ctx.provider_response = "Provider registry not available"
            return ctx
        try:
            provider = registry.get("openrouter")
            history = ctx.metadata.get("compiled_context") or []
            result = await provider.complete(
                prompt=ctx.user_message,
                history=history,
                config=ProviderConfig(model=""),
            )
            ctx.provider_response = result.text
        except Exception as exc:
            logger.warning("Provider call failed: %s", exc, exc_info=True)
            ctx.provider_response = f"Provider error: {exc}"
        return ctx


class ExtractMemoryStep(TurnStep):
    name = "extract_memory"

    async def run(self, ctx: TurnContext) -> TurnContext:
        episodic_store = ctx.metadata.get("episodic_store")
        if episodic_store is None:
            return ctx
        try:
            await episodic_store.save_turn(
                session_id=ctx.session_id,
                role="user",
                content=ctx.user_message,
            )
            if ctx.provider_response:
                await episodic_store.save_turn(
                    session_id=ctx.session_id,
                    role="assistant",
                    content=ctx.provider_response,
                )
        except Exception:
            logger.warning(
                "Memory persistence failed for session %s", ctx.session_id, exc_info=True
            )
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
    return TurnPipeline(
        [
            ReceiveStep(),
            RetrieveStep(),
            CompileContextStep(),
            CallProviderStep(),
            ExtractMemoryStep(),
            RespondStep(),
        ]
    )


def _step_name(step: TurnStep) -> str:
    name = getattr(step, "name", None)
    if isinstance(name, str) and name:
        return name
    return step.__class__.__name__


__all__ = ["TurnPipeline", "build_default_pipeline"]
