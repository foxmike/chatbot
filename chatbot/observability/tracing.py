"""Per-turn trace collection — prompt payload, retrieval decisions, model routing records, and latency instrumentation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import inspect
import logging
import math
import sqlalchemy as sa
import time
from types import TracebackType
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


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
    raw_response: str = ""
    routing_decision: dict[str, Any] = field(default_factory=dict)
    retrieval_snippets: list[str] = field(default_factory=list)
    steps: list[TraceStep] = field(default_factory=list)
    total_latency_ms: float = 0
    started_at: str = ""
    completed_at: str = ""


class TraceCollector:
    def __init__(
        self,
        store: str = "memory",
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        if store not in {"memory", "sqlite", "postgres"}:
            raise ValueError("store must be one of: memory, sqlite, postgres")

        self.store = store
        self._session_factory = session_factory
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
        await self._persist_trace(trace)
        return trace

    async def get_trace(self, trace_id: str) -> TraceRecord | None:
        trace = self._traces_by_id.get(trace_id)
        if trace is not None:
            return trace
        return await self._get_trace_from_db(trace_id)

    async def list_traces(
        self,
        session_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TraceRecord]:
        db_traces = await self._list_traces_from_db(session_id, limit, offset)
        if db_traces is not None:
            return db_traces

        traces = self._traces
        if session_id is not None:
            traces = [trace for trace in traces if trace.session_id == session_id]

        start = max(0, int(offset))
        stop = start + max(0, int(limit))
        return list(traces[start:stop])

    async def flush(self) -> None:
        if not self._uses_database:
            return None
        for trace in list(self._traces):
            await self._persist_trace(trace)
        return None

    @property
    def _uses_database(self) -> bool:
        return self.store in {"sqlite", "postgres"}

    def _get_session_factory(self) -> Callable[[], Any]:
        if self._session_factory is not None:
            return self._session_factory

        from chatbot.db.session import async_session_factory

        return async_session_factory

    async def _persist_trace(self, trace: TraceRecord) -> None:
        if not self._uses_database:
            return

        try:
            session_factory = self._get_session_factory()
            from chatbot.db.models import TraceRecord as TraceRecordModel

            async with session_factory() as session:
                result = await session.execute(
                    sa.select(TraceRecordModel).where(
                        TraceRecordModel.trace_id == trace.trace_id
                    )
                )
                model = result.scalar_one_or_none()
                if model is None:
                    model = TraceRecordModel(trace_id=trace.trace_id)
                    session.add(model)

                _apply_trace_model(model, trace)
                await _commit_or_flush(session)
        except Exception:
            logger.warning(
                "Observability trace DB persistence failed for trace_id=%s",
                trace.trace_id,
                exc_info=True,
            )

    async def _get_trace_from_db(self, trace_id: str) -> TraceRecord | None:
        if not self._uses_database:
            return None

        try:
            session_factory = self._get_session_factory()
            from chatbot.db.models import TraceRecord as TraceRecordModel

            async with session_factory() as session:
                result = await session.execute(
                    sa.select(TraceRecordModel).where(
                        TraceRecordModel.trace_id == trace_id
                    )
                )
                model = result.scalar_one_or_none()
                if model is None:
                    return None
                trace = _trace_from_model(model)
                self._remember_trace(trace)
                return trace
        except Exception:
            logger.warning(
                "Observability trace DB read failed for trace_id=%s",
                trace_id,
                exc_info=True,
            )
            return None

    async def _list_traces_from_db(
        self,
        session_id: str | None,
        limit: int,
        offset: int,
    ) -> list[TraceRecord] | None:
        if not self._uses_database:
            return None

        try:
            session_factory = self._get_session_factory()
            from chatbot.db.models import TraceRecord as TraceRecordModel

            statement = sa.select(TraceRecordModel)
            if session_id is not None:
                statement = statement.where(TraceRecordModel.session_id == session_id)
            statement = (
                statement.order_by(TraceRecordModel.id.desc())
                .offset(max(0, int(offset)))
                .limit(max(0, int(limit)))
            )

            async with session_factory() as session:
                result = await session.execute(statement)
                traces = [_trace_from_model(model) for model in result.scalars().all()]
                for trace in traces:
                    self._remember_trace(trace)
                return traces
        except Exception:
            logger.warning("Observability trace DB list failed", exc_info=True)
            return None

    def _remember_trace(self, trace: TraceRecord) -> None:
        existing = self._traces_by_id.get(trace.trace_id)
        if existing is None:
            self._traces.append(trace)
        else:
            try:
                index = self._traces.index(existing)
                self._traces[index] = trace
            except ValueError:
                self._traces.append(trace)
        self._traces_by_id[trace.trace_id] = trace


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


def _apply_trace_model(model: Any, trace: TraceRecord) -> None:
    model.trace_id = trace.trace_id
    model.turn_id = _truncate(trace.turn_id, 36)
    model.session_id = _truncate(trace.session_id, 36)
    model.user_id = _truncate(trace.user_id, 36) if trace.user_id else None
    model.model_provider = _truncate(trace.model_provider, 64) if trace.model_provider else None
    model.model_id = _truncate(trace.model_id, 128) if trace.model_id else None
    model.task_class = _truncate(trace.task_class, 32) if trace.task_class else None
    model.prompt_tokens = max(0, int(trace.prompt_tokens))
    model.completion_tokens = max(0, int(trace.completion_tokens))
    model.cost_usd_micros = max(0, int(trace.cost_usd_micros))
    model.total_latency_ms = _finite_float(trace.total_latency_ms)
    model.retrieve_latency_ms = _finite_float(trace.retrieve_latency_ms)
    model.prompt = str(trace.prompt or "")
    model.routing_decision = _json_safe_dict(trace.routing_decision)
    model.retrieval_snippets = [str(snippet) for snippet in trace.retrieval_snippets or []]
    model.steps = [_step_to_payload(step) for step in trace.steps]
    if trace.started_at:
        started_at = _parse_datetime(trace.started_at)
        if started_at is not None:
            model.created_at = started_at


def _trace_from_model(model: Any) -> TraceRecord:
    created_at = _datetime_to_iso(getattr(model, "created_at", None))
    return TraceRecord(
        trace_id=str(getattr(model, "trace_id", "")),
        turn_id=str(getattr(model, "turn_id", "")),
        session_id=str(getattr(model, "session_id", "")),
        user_id=getattr(model, "user_id", None),
        model_provider=getattr(model, "model_provider", None),
        model_id=getattr(model, "model_id", None),
        task_class=getattr(model, "task_class", None),
        prompt_tokens=int(getattr(model, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(model, "completion_tokens", 0) or 0),
        cost_usd_micros=int(getattr(model, "cost_usd_micros", 0) or 0),
        retrieve_latency_ms=_finite_float(getattr(model, "retrieve_latency_ms", 0)),
        prompt=str(getattr(model, "prompt", "") or ""),
        routing_decision=dict(getattr(model, "routing_decision", {}) or {}),
        retrieval_snippets=[
            str(snippet) for snippet in getattr(model, "retrieval_snippets", []) or []
        ],
        steps=[
            _step_from_payload(step)
            for step in getattr(model, "steps", []) or []
            if isinstance(step, dict)
        ],
        total_latency_ms=_finite_float(getattr(model, "total_latency_ms", 0)),
        started_at=created_at,
        completed_at=created_at,
    )


def _step_to_payload(step: TraceStep) -> dict[str, Any]:
    return {
        "step_name": str(step.step_name),
        "started_at": _finite_float(step.started_at),
        "ended_at": _finite_float(step.ended_at) if step.ended_at is not None else None,
        "duration_ms": _finite_float(step.duration_ms),
        "metadata": _json_safe_dict(step.metadata),
    }


def _step_from_payload(payload: dict[str, Any]) -> TraceStep:
    return TraceStep(
        step_name=str(payload.get("step_name", "")),
        started_at=_finite_float(payload.get("started_at", 0)),
        ended_at=(
            _finite_float(payload.get("ended_at"))
            if payload.get("ended_at") is not None
            else None
        ),
        duration_ms=_finite_float(payload.get("duration_ms", 0)),
        metadata=_json_safe_dict(payload.get("metadata", {})),
    )


def _json_safe_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _json_safe(value) for key, value in value.items()}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, dict):
        return _json_safe_dict(value)
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _finite_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return number


def _truncate(value: str | None, max_length: int) -> str:
    return str(value or "")[:max_length]


def _parse_datetime(value: str) -> datetime | None:
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _datetime_to_iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return ""


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


trace_collector = TraceCollector(store="postgres")


__all__ = ["TraceCollector", "TraceRecord", "TraceStep", "trace_collector"]
