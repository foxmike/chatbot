"""Per-turn trace collection — prompt payload, retrieval decisions, model routing records, and latency instrumentation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from types import TracebackType
from typing import Any
from uuid import uuid4


@dataclass
class TraceStep:
    step_name: str
    started_at: float = field(default_factory=time.monotonic)
    ended_at: float | None = None
    duration_ms: float = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __enter__(self) -> TraceStep:
        self.started_at = time.monotonic()
        self.ended_at = None
        self.duration_ms = 0
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.ended_at = time.monotonic()
        self.duration_ms = (self.ended_at - self.started_at) * 1000
        return False


@dataclass
class TraceRecord:
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    turn_id: str = ""
    session_id: str = ""
    user_id: str | None = None
    model_provider: str | None = None
    model_id: str | None = None
    task_class: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd_micros: int = 0
    retrieve_latency_ms: float = 0
    prompt: str = ""
    routing_decision: dict[str, Any] = field(default_factory=dict)
    retrieval_snippets: list[str] = field(default_factory=list)
    steps: list[TraceStep] = field(default_factory=list)
    total_latency_ms: float = 0
    started_at: str = ""
    completed_at: str = ""


class TraceCollector:
    def __init__(self, store: str = "memory") -> None:
        if store not in {"memory", "sqlite", "postgres"}:
            raise ValueError("store must be one of: memory, sqlite, postgres")

        self.store = store
        self._traces: list[TraceRecord] = []
        self._traces_by_id: dict[str, TraceRecord] = {}
        self._started_monotonic: dict[str, float] = {}

    async def start_trace(
        self,
        turn_id: str,
        session_id: str,
        user_id: str | None,
        task_class: str | None,
    ) -> str:
        trace = TraceRecord(
            turn_id=turn_id,
            session_id=session_id,
            user_id=user_id,
            task_class=task_class,
            started_at=_utc_now_iso(),
        )
        self._traces.append(trace)
        self._traces_by_id[trace.trace_id] = trace
        self._started_monotonic[trace.trace_id] = time.monotonic()
        return trace.trace_id

    async def record_step(
        self,
        trace_id: str,
        step_name: str,
        duration_ms: float,
        metadata: dict[str, Any],
    ) -> None:
        trace = self._traces_by_id.get(trace_id)
        if trace is None:
            return

        ended_at = time.monotonic()
        duration = float(duration_ms)
        trace.steps.append(
            TraceStep(
                step_name=step_name,
                started_at=ended_at - (duration / 1000),
                ended_at=ended_at,
                duration_ms=duration,
                metadata=dict(metadata or {}),
            )
        )

    async def complete_trace(
        self,
        trace_id: str,
        model_provider: str | None,
        model_id: str | None,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd_micros: int,
        prompt: str,
        routing_decision: dict[str, Any],
        retrieval_snippets: list[str],
        total_latency_ms: float,
    ) -> TraceRecord:
        trace = self._traces_by_id.get(trace_id)
        if trace is None:
            trace = TraceRecord(trace_id=trace_id, started_at=_utc_now_iso())
            self._traces.append(trace)
            self._traces_by_id[trace_id] = trace

        trace.model_provider = model_provider
        trace.model_id = model_id
        trace.prompt_tokens = int(prompt_tokens)
        trace.completion_tokens = int(completion_tokens)
        trace.cost_usd_micros = int(cost_usd_micros)
        trace.prompt = prompt
        trace.routing_decision = dict(routing_decision or {})
        trace.retrieval_snippets = [str(snippet) for snippet in retrieval_snippets or []]
        trace.retrieve_latency_ms = _retrieve_latency_ms(trace.steps)
        trace.total_latency_ms = _total_latency_ms(
            trace_id,
            self._started_monotonic,
            total_latency_ms,
        )
        trace.completed_at = _utc_now_iso()
        self._started_monotonic.pop(trace_id, None)
        return trace

    async def get_trace(self, trace_id: str) -> TraceRecord | None:
        return self._traces_by_id.get(trace_id)

    async def list_traces(
        self,
        session_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TraceRecord]:
        traces = self._traces
        if session_id is not None:
            traces = [trace for trace in traces if trace.session_id == session_id]

        start = max(0, int(offset))
        stop = start + max(0, int(limit))
        return list(traces[start:stop])

    async def flush(self) -> None:
        return None


def _retrieve_latency_ms(steps: list[TraceStep]) -> float:
    return sum(
        step.duration_ms
        for step in steps
        if "retrieve" in step.step_name.lower()
    )


def _total_latency_ms(
    trace_id: str,
    started_monotonic: dict[str, float],
    provided_latency_ms: float,
) -> float:
    if provided_latency_ms > 0:
        return float(provided_latency_ms)

    started_at = started_monotonic.get(trace_id)
    if started_at is None:
        return 0
    return (time.monotonic() - started_at) * 1000


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


trace_collector = TraceCollector()


__all__ = ["TraceCollector", "TraceRecord", "TraceStep", "trace_collector"]
