"""Latency, token, and cost metering — per-conversation and per-model aggregate metrics."""

from __future__ import annotations

from bisect import bisect_left, insort
from collections import deque
from dataclasses import dataclass, field
from math import ceil
from typing import Any

from chatbot.observability.tracing import TraceRecord


_MAX_LATENCIES_PER_MODEL = 1000


@dataclass
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd_micros: int


@dataclass
class ModelMetrics:
    model_id: str
    provider: str
    request_count: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_cost_usd_micros: int
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float


@dataclass
class SessionMetrics:
    session_id: str
    turn_count: int
    total_tokens: int
    total_cost_usd_micros: int
    avg_latency_ms: float
    models_used: list[str]


@dataclass
class _SessionAggregate:
    turn_count: int = 0
    total_tokens: int = 0
    total_cost_usd_micros: int = 0
    total_latency_ms: float = 0
    models_used: set[str] = field(default_factory=set)


@dataclass
class _ModelAggregate:
    model_id: str
    provider: str
    request_count: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_usd_micros: int = 0
    total_latency_ms: float = 0
    latencies_ms: list[float] = field(default_factory=list)
    latency_order: deque[float] = field(default_factory=deque)


class MetricsCollector:
    def __init__(self) -> None:
        self._session_metrics: dict[str, _SessionAggregate] = {}
        self._model_metrics: dict[str, _ModelAggregate] = {}
        self._total_requests = 0
        self._total_tokens = 0
        self._total_cost_usd_micros = 0
        self._total_latency_ms = 0.0

    async def record_trace(self, trace: TraceRecord) -> None:
        prompt_tokens = max(0, int(trace.prompt_tokens))
        completion_tokens = max(0, int(trace.completion_tokens))
        total_tokens = prompt_tokens + completion_tokens
        cost_usd_micros = max(0, int(trace.cost_usd_micros))
        latency_ms = max(0.0, _trace_latency_ms(trace))
        session_id = trace.session_id or ""
        model_id = trace.model_id or "unknown"
        provider = trace.model_provider or "unknown"

        session = self._session_metrics.setdefault(session_id, _SessionAggregate())
        session.turn_count += 1
        session.total_tokens += total_tokens
        session.total_cost_usd_micros += cost_usd_micros
        session.total_latency_ms += latency_ms
        session.models_used.add(model_id)

        model = self._model_metrics.get(model_id)
        if model is None:
            model = _ModelAggregate(model_id=model_id, provider=provider)
            self._model_metrics[model_id] = model
        elif model.provider == "unknown" and provider != "unknown":
            model.provider = provider

        model.request_count += 1
        model.total_prompt_tokens += prompt_tokens
        model.total_completion_tokens += completion_tokens
        model.total_cost_usd_micros += cost_usd_micros
        model.total_latency_ms += latency_ms
        _record_latency(model, latency_ms)

        self._total_requests += 1
        self._total_tokens += total_tokens
        self._total_cost_usd_micros += cost_usd_micros
        self._total_latency_ms += latency_ms

    async def get_session_metrics(self, session_id: str) -> SessionMetrics | None:
        session = self._session_metrics.get(session_id)
        if session is None:
            return None

        return SessionMetrics(
            session_id=session_id,
            turn_count=session.turn_count,
            total_tokens=session.total_tokens,
            total_cost_usd_micros=session.total_cost_usd_micros,
            avg_latency_ms=_average(session.total_latency_ms, session.turn_count),
            models_used=sorted(session.models_used),
        )

    async def get_model_metrics(self, model_id: str) -> ModelMetrics | None:
        model = self._model_metrics.get(model_id)
        if model is None:
            return None
        return _to_model_metrics(model)

    async def get_model_metrics_all(self) -> list[ModelMetrics]:
        metrics = [_to_model_metrics(model) for model in self._model_metrics.values()]
        return sorted(
            metrics,
            key=lambda model: model.total_cost_usd_micros,
            reverse=True,
        )

    async def get_global_summary(self) -> dict[str, Any]:
        return {
            "total_requests": self._total_requests,
            "total_tokens": self._total_tokens,
            "total_cost_usd_micros": self._total_cost_usd_micros,
            "avg_latency_ms": _average(self._total_latency_ms, self._total_requests),
            "models_used": len(self._model_metrics),
            "total_sessions": len(self._session_metrics),
        }

    async def reset(self) -> None:
        self._session_metrics.clear()
        self._model_metrics.clear()
        self._total_requests = 0
        self._total_tokens = 0
        self._total_cost_usd_micros = 0
        self._total_latency_ms = 0.0


def _trace_latency_ms(trace: TraceRecord) -> float:
    if trace.total_latency_ms > 0:
        return float(trace.total_latency_ms)
    return float(sum(step.duration_ms for step in trace.steps))


def _record_latency(model: _ModelAggregate, latency_ms: float) -> None:
    insort(model.latencies_ms, latency_ms)
    model.latency_order.append(latency_ms)

    if len(model.latency_order) <= _MAX_LATENCIES_PER_MODEL:
        return

    expired_latency = model.latency_order.popleft()
    index = bisect_left(model.latencies_ms, expired_latency)
    if index < len(model.latencies_ms):
        model.latencies_ms.pop(index)


def _to_model_metrics(model: _ModelAggregate) -> ModelMetrics:
    return ModelMetrics(
        model_id=model.model_id,
        provider=model.provider,
        request_count=model.request_count,
        total_prompt_tokens=model.total_prompt_tokens,
        total_completion_tokens=model.total_completion_tokens,
        total_cost_usd_micros=model.total_cost_usd_micros,
        avg_latency_ms=_average(model.total_latency_ms, model.request_count),
        p50_latency_ms=_percentile(model.latencies_ms, 50),
        p95_latency_ms=_percentile(model.latencies_ms, 95),
        p99_latency_ms=_percentile(model.latencies_ms, 99),
    )


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0

    index = max(0, min(len(values) - 1, ceil((percentile / 100) * len(values)) - 1))
    return float(values[index])


def _average(total: float, count: int) -> float:
    if count <= 0:
        return 0.0
    return float(total / count)


metrics_collector = MetricsCollector()


__all__ = [
    "MetricsCollector",
    "ModelMetrics",
    "SessionMetrics",
    "TokenUsage",
    "metrics_collector",
]
