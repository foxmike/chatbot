"""First-party turn pipeline for the chatbot engine. No framework dependency."""

from __future__ import annotations

from time import perf_counter

from chatbot.core.steps import StepResult, TurnContext, TurnStep


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
            _IdentityTurnStep("receive"),
            _IdentityTurnStep("retrieve"),
            _IdentityTurnStep("compile_context"),
            _IdentityTurnStep("call_provider"),
            _IdentityTurnStep("extract_memory"),
            _IdentityTurnStep("respond"),
        ]
    )


def _step_name(step: TurnStep) -> str:
    name = getattr(step, "name", None)
    if isinstance(name, str) and name:
        return name
    return step.__class__.__name__


__all__ = ["TurnPipeline", "build_default_pipeline"]
