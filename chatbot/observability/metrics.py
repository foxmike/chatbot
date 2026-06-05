"""Latency, token, and cost metering — per-conversation and per-model aggregate metrics."""

from __future__ import annotations

from bisect import bisect_left, insort
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
import inspect
import logging
from math import ceil
from typing import Any

import sqlalchemy as sa

from chatbot.observability.tracing import TraceRecord


_MAX_LATENCIES_PER_MODEL = 1000
logger = logging.getLogger(__name__)


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
    def __init__(
        self,
        store: str = "memory",
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        if store not in {"memory", "sqlite", "postgres"}:
            raise ValueError("store must be one of: memory, sqlite, postgres")

        self.store = store
        self._session_factory = session_factory
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
        await self._persist_usage(
            session_id=session_id,
            provider=provider,
            model_id=model_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd_micros=cost_usd_micros,
            latency_ms=latency_ms,
        )

    async def get_session_metrics(self, session_id: str) -> SessionMetrics | None:
        db_metrics = await self._get_session_metrics_from_db(session_id)
        if db_metrics is not None:
            return db_metrics

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
        db_metrics = await self._get_model_metrics_from_db(model_id)
        if db_metrics is not None:
            return db_metrics

        model = self._model_metrics.get(model_id)
        if model is None:
            return None
        return _to_model_metrics(model)

    async def get_model_metrics_all(self) -> list[ModelMetrics]:
        db_metrics = await self._get_model_metrics_all_from_db()
        if db_metrics is not None:
            return db_metrics

        metrics = [_to_model_metrics(model) for model in self._model_metrics.values()]
        return sorted(
            metrics,
            key=lambda model: model.total_cost_usd_micros,
            reverse=True,
        )

    async def get_global_summary(self) -> dict[str, Any]:
        db_summary = await self._get_global_summary_from_db()
        if db_summary is not None:
            return db_summary

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

    @property
    def _uses_database(self) -> bool:
        return self.store in {"sqlite", "postgres"}

    def _get_session_factory(self) -> Callable[[], Any]:
        if self._session_factory is not None:
            return self._session_factory

        from chatbot.db.session import async_session_factory

        return async_session_factory

    async def _persist_usage(
        self,
        *,
        session_id: str,
        provider: str,
        model_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd_micros: int,
        latency_ms: float,
    ) -> None:
        if not self._uses_database:
            return

        try:
            session_factory = self._get_session_factory()
            from chatbot.db.models import UsageRecord as UsageRecordModel

            async with session_factory() as session:
                result = await session.execute(
                    sa.select(UsageRecordModel).where(
                        UsageRecordModel.session_id == _truncate(session_id, 36),
                        UsageRecordModel.model_provider == _truncate(provider, 64),
                        UsageRecordModel.model_id == _truncate(model_id, 128),
                    )
                )
                usage = result.scalar_one_or_none()
                if usage is None:
                    usage = UsageRecordModel(
                        session_id=_truncate(session_id, 36),
                        model_provider=_truncate(provider, 64),
                        model_id=_truncate(model_id, 128),
                        turn_count=0,
                        prompt_tokens=0,
                        completion_tokens=0,
                        cost_usd_micros=0,
                        avg_latency_ms=0.0,
                    )
                    session.add(usage)

                previous_turns = max(0, int(usage.turn_count or 0))
                next_turns = previous_turns + 1
                usage.turn_count = next_turns
                usage.prompt_tokens = max(0, int(usage.prompt_tokens or 0)) + prompt_tokens
                usage.completion_tokens = (
                    max(0, int(usage.completion_tokens or 0)) + completion_tokens
                )
                usage.cost_usd_micros = (
                    max(0, int(usage.cost_usd_micros or 0)) + cost_usd_micros
                )
                usage.avg_latency_ms = _average(
                    (float(usage.avg_latency_ms or 0.0) * previous_turns)
                    + latency_ms,
                    next_turns,
                )
                await _commit_or_flush(session)
        except Exception:
            logger.warning(
                "Observability usage DB persistence failed for session_id=%s model_id=%s",
                session_id,
                model_id,
                exc_info=True,
            )

    async def _get_session_metrics_from_db(
        self,
        session_id: str,
    ) -> SessionMetrics | None:
        if not self._uses_database:
            return None

        try:
            session_factory = self._get_session_factory()
            from chatbot.db.models import UsageRecord as UsageRecordModel

            async with session_factory() as session:
                result = await session.execute(
                    sa.select(UsageRecordModel).where(
                        UsageRecordModel.session_id == _truncate(session_id, 36)
                    )
                )
                records = list(result.scalars().all())
        except Exception:
            logger.warning(
                "Observability session metrics DB read failed for session_id=%s",
                session_id,
                exc_info=True,
            )
            return None

        if not records:
            return None

        turn_count = sum(max(0, int(record.turn_count or 0)) for record in records)
        total_tokens = sum(
            max(0, int(record.prompt_tokens or 0))
            + max(0, int(record.completion_tokens or 0))
            for record in records
        )
        total_cost_usd_micros = sum(
            max(0, int(record.cost_usd_micros or 0)) for record in records
        )
        total_latency_ms = sum(
            float(record.avg_latency_ms or 0.0) * max(0, int(record.turn_count or 0))
            for record in records
        )
        return SessionMetrics(
            session_id=session_id,
            turn_count=turn_count,
            total_tokens=total_tokens,
            total_cost_usd_micros=total_cost_usd_micros,
            avg_latency_ms=_average(total_latency_ms, turn_count),
            models_used=sorted({str(record.model_id) for record in records}),
        )

    async def _get_model_metrics_from_db(self, model_id: str) -> ModelMetrics | None:
        if not self._uses_database:
            return None

        usage_records = await self._usage_records_for_model(model_id)
        if usage_records is None:
            return None
        if not usage_records:
            return None

        latencies = await self._trace_latencies_for_model(model_id)
        return _usage_records_to_model_metrics(model_id, usage_records, latencies or [])

    async def _get_model_metrics_all_from_db(self) -> list[ModelMetrics] | None:
        if not self._uses_database:
            return None

        try:
            session_factory = self._get_session_factory()
            from chatbot.db.models import UsageRecord as UsageRecordModel

            async with session_factory() as session:
                result = await session.execute(sa.select(UsageRecordModel))
                records = list(result.scalars().all())
        except Exception:
            logger.warning("Observability model metrics DB read failed", exc_info=True)
            return None

        grouped: dict[str, list[Any]] = {}
        for record in records:
            grouped.setdefault(str(record.model_id), []).append(record)

        metrics: list[ModelMetrics] = []
        for model_id, group in grouped.items():
            latencies = await self._trace_latencies_for_model(model_id)
            metrics.append(
                _usage_records_to_model_metrics(model_id, group, latencies or [])
            )
        return sorted(
            metrics,
            key=lambda model: model.total_cost_usd_micros,
            reverse=True,
        )

    async def _get_global_summary_from_db(self) -> dict[str, Any] | None:
        if not self._uses_database:
            return None

        try:
            session_factory = self._get_session_factory()
            from chatbot.db.models import UsageRecord as UsageRecordModel

            async with session_factory() as session:
                result = await session.execute(sa.select(UsageRecordModel))
                records = list(result.scalars().all())
        except Exception:
            logger.warning("Observability global metrics DB read failed", exc_info=True)
            return None

        total_requests = sum(max(0, int(record.turn_count or 0)) for record in records)
        total_tokens = sum(
            max(0, int(record.prompt_tokens or 0))
            + max(0, int(record.completion_tokens or 0))
            for record in records
        )
        total_cost_usd_micros = sum(
            max(0, int(record.cost_usd_micros or 0)) for record in records
        )
        total_latency_ms = sum(
            float(record.avg_latency_ms or 0.0) * max(0, int(record.turn_count or 0))
            for record in records
        )
        return {
            "total_requests": total_requests,
            "total_tokens": total_tokens,
            "total_cost_usd_micros": total_cost_usd_micros,
            "avg_latency_ms": _average(total_latency_ms, total_requests),
            "models_used": len({str(record.model_id) for record in records}),
            "total_sessions": len({str(record.session_id) for record in records}),
        }

    async def _usage_records_for_model(self, model_id: str) -> list[Any] | None:
        try:
            session_factory = self._get_session_factory()
            from chatbot.db.models import UsageRecord as UsageRecordModel

            async with session_factory() as session:
                result = await session.execute(
                    sa.select(UsageRecordModel).where(
                        UsageRecordModel.model_id == _truncate(model_id, 128)
                    )
                )
                return list(result.scalars().all())
        except Exception:
            logger.warning(
                "Observability usage DB read failed for model_id=%s",
                model_id,
                exc_info=True,
            )
            return None

    async def _trace_latencies_for_model(self, model_id: str) -> list[float] | None:
        try:
            session_factory = self._get_session_factory()
            from chatbot.db.models import TraceRecord as TraceRecordModel

            async with session_factory() as session:
                result = await session.execute(
                    sa.select(TraceRecordModel.total_latency_ms).where(
                        TraceRecordModel.model_id == _truncate(model_id, 128)
                    )
                )
                latencies = [
                    max(0.0, float(value or 0.0))
                    for value in result.scalars().all()
                ]
                return sorted(latencies)
        except Exception:
            logger.warning(
                "Observability trace latency DB read failed for model_id=%s",
                model_id,
                exc_info=True,
            )
            return None


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


def _usage_records_to_model_metrics(
    model_id: str,
    records: list[Any],
    latencies_ms: list[float],
) -> ModelMetrics:
    request_count = sum(max(0, int(record.turn_count or 0)) for record in records)
    total_prompt_tokens = sum(
        max(0, int(record.prompt_tokens or 0)) for record in records
    )
    total_completion_tokens = sum(
        max(0, int(record.completion_tokens or 0)) for record in records
    )
    total_cost_usd_micros = sum(
        max(0, int(record.cost_usd_micros or 0)) for record in records
    )
    total_latency_ms = sum(
        float(record.avg_latency_ms or 0.0) * max(0, int(record.turn_count or 0))
        for record in records
    )
    providers = [str(record.model_provider) for record in records if record.model_provider]
    provider = providers[0] if providers else "unknown"
    return ModelMetrics(
        model_id=model_id,
        provider=provider,
        request_count=request_count,
        total_prompt_tokens=total_prompt_tokens,
        total_completion_tokens=total_completion_tokens,
        total_cost_usd_micros=total_cost_usd_micros,
        avg_latency_ms=_average(total_latency_ms, request_count),
        p50_latency_ms=_percentile(latencies_ms, 50),
        p95_latency_ms=_percentile(latencies_ms, 95),
        p99_latency_ms=_percentile(latencies_ms, 99),
    )


def _truncate(value: str | None, max_length: int) -> str:
    return str(value or "")[:max_length]


async def _commit_or_flush(session: Any) -> None:
    commit = getattr(session, "commit", None)
    if callable(commit):
        await _maybe_await(commit())
        return

    flush = getattr(session, "flush", None)
    if callable(flush):
        await _maybe_await(flush())


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


metrics_collector = MetricsCollector(store="postgres")


__all__ = [
    "MetricsCollector",
    "ModelMetrics",
    "SessionMetrics",
    "TokenUsage",
    "metrics_collector",
]
