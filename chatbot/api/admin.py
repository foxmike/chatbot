"""Admin dashboard API endpoints — trace inspection, metrics summaries, structured log queries, and usage reports."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.auth.security import decode_access_token
from chatbot.observability import metrics_collector, structured_logger
from chatbot.observability.logs import LogEvent
from chatbot.observability.metrics import ModelMetrics
from chatbot.observability.tracing import TraceRecord, trace_collector



_ADMIN_ERROR = "Internal admin API error"

admin_router = APIRouter(prefix="/admin", tags=["admin"])


from pydantic.alias_generators import to_camel

class _CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, by_alias=True)

class TraceSummary(_CamelModel):
    trace_id: str
    session_id: str
    turn_id: str
    model_id: str | None
    task_class: str | None
    total_latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    cost_usd_micros: int
    created_at: str


class TraceDetail(TraceSummary):
    user_id: str | None
    model_provider: str | None
    prompt: str
    raw_response: str = ""
    routing_decision: dict[str, Any]
    retrieval_snippets: list[str]
    steps: list[dict[str, Any]]
    retrieve_latency_ms: float
    started_at: str
    completed_at: str


class TraceListResponse(_CamelModel):
    traces: list[TraceSummary]
    total: int
    page: int
    page_size: int


class MetricsGlobal(_CamelModel):
    total_requests: int
    total_tokens: int
    total_cost_usd_micros: int
    avg_latency_ms: float
    models_used: int


class ModelMetricSummary(_CamelModel):
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


class ModelMetricsListResponse(_CamelModel):
    models: list[ModelMetricSummary]


class LogEventResponse(_CamelModel):
    event_id: str
    event_type: str
    timestamp: str
    session_id: str | None
    turn_id: str | None
    details: dict[str, Any]
    severity: str


class LogEventsListResponse(_CamelModel):
    events: list[LogEventResponse]
    total: int


class UsageReportResponse(_CamelModel):
    total_sessions: int
    total_turns: int
    total_tokens: int
    total_cost_usd_micros: int
    per_model: list[ModelMetricSummary]


async def require_admin(
    current_user: CurrentUser = Depends(get_current_user),
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    """Dependency that gates routes to admin users only.

    Checks for admin access via, in order:
      1. JWT token ``role`` claim equal to ``"admin"``  (role-based)
      2. ``is_admin`` claim set to ``True``              (flag-based)
      3. Hardcoded admin emails                          (legacy / dev fallback)
    """
    # ── role-based admin check ──────────────────────────────────────
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
        payload = decode_access_token(token)
        if payload:
            if payload.get("role") == "admin":
                return current_user
            if payload.get("is_admin") is True:
                return current_user

    # ── legacy / dev fallback ───────────────────────────────────────
    if current_user.email in ("admin@localhost", "admin@example.com", "foxmike@gmail.com"):
        return current_user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin access required",
    )


@admin_router.get("/traces", response_model=TraceListResponse)
async def list_traces(
    session_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: CurrentUser = Depends(require_admin),
) -> TraceListResponse:
    try:
        offset = (page - 1) * page_size
        traces = await trace_collector.list_traces(
            session_id=session_id,
            limit=page_size,
            offset=offset,
        )
        total = _count_traces(session_id)
        return TraceListResponse(
            traces=[_trace_summary(trace) for trace in traces],
            total=total,
            page=page,
            page_size=page_size,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_ADMIN_ERROR,
        ) from exc


@admin_router.get("/traces/{trace_id}", response_model=TraceDetail)
async def get_trace(trace_id: str, current_user: CurrentUser = Depends(require_admin)) -> TraceDetail:
    try:
        trace = await trace_collector.get_trace(trace_id)
        if trace is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Trace not found",
            )
        return _trace_detail(trace)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_ADMIN_ERROR,
        ) from exc


@admin_router.get("/traces/by-turn/{turn_id}", response_model=TraceDetail)
async def get_trace_by_turn(turn_id: str, current_user: CurrentUser = Depends(require_admin)) -> TraceDetail:
    try:
        for trace in trace_collector._traces:
            if trace.turn_id == turn_id:
                return _trace_detail(trace)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trace not found",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_ADMIN_ERROR,
        ) from exc


@admin_router.get("/metrics/global", response_model=MetricsGlobal)
async def get_global_metrics(current_user: CurrentUser = Depends(require_admin)) -> MetricsGlobal:
    try:
        summary = await metrics_collector.get_global_summary()
        return MetricsGlobal(
            total_requests=int(summary.get("total_requests", 0)),
            total_tokens=int(summary.get("total_tokens", 0)),
            total_cost_usd_micros=int(summary.get("total_cost_usd_micros", 0)),
            avg_latency_ms=float(summary.get("avg_latency_ms", 0.0)),
            models_used=int(summary.get("models_used", 0)),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_ADMIN_ERROR,
        ) from exc


@admin_router.get("/metrics/models", response_model=ModelMetricsListResponse)
async def get_model_metrics(current_user: CurrentUser = Depends(require_admin)) -> ModelMetricsListResponse:
    try:
        models = await metrics_collector.get_model_metrics_all()
        return ModelMetricsListResponse(
            models=[_model_metric_summary(model) for model in models]
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_ADMIN_ERROR,
        ) from exc


@admin_router.get("/logs", response_model=LogEventsListResponse)
async def list_log_events(
    event_type: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    since: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1),
    offset: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(require_admin),
) -> LogEventsListResponse:
    try:
        events = await structured_logger.query_events(
            event_type=event_type,  # type: ignore[arg-type]
            session_id=session_id,
            since=since,
            limit=limit,
            offset=offset,
        )
        total = len(_filter_log_events(event_type, session_id, since))
        return LogEventsListResponse(
            events=[_log_event_response(event) for event in events],
            total=total,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_ADMIN_ERROR,
        ) from exc


@admin_router.get("/usage", response_model=UsageReportResponse)
async def get_usage_report(current_user: CurrentUser = Depends(require_admin)) -> UsageReportResponse:
    try:
        summary = await metrics_collector.get_global_summary()
        models = await metrics_collector.get_model_metrics_all()
        return UsageReportResponse(
            total_sessions=int(summary.get("total_sessions", 0)),
            total_turns=int(summary.get("total_requests", 0)),
            total_tokens=int(summary.get("total_tokens", 0)),
            total_cost_usd_micros=int(summary.get("total_cost_usd_micros", 0)),
            per_model=[_model_metric_summary(model) for model in models],
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_ADMIN_ERROR,
        ) from exc


@admin_router.get("/health")
async def admin_health(current_user: CurrentUser = Depends(require_admin)) -> dict[str, int | str]:
    try:
        return {
            "status": "ok",
            "traces_stored": _count_traces(),
            "events_stored": _count_log_events(),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_ADMIN_ERROR,
        ) from exc


def _trace_summary(trace: TraceRecord) -> TraceSummary:
    return TraceSummary(
        trace_id=trace.trace_id,
        session_id=trace.session_id,
        turn_id=trace.turn_id,
        model_id=trace.model_id,
        task_class=trace.task_class,
        total_latency_ms=float(trace.total_latency_ms),
        prompt_tokens=int(trace.prompt_tokens),
        completion_tokens=int(trace.completion_tokens),
        cost_usd_micros=int(trace.cost_usd_micros),
        created_at=trace.started_at,
    )


def _trace_detail(trace: TraceRecord) -> TraceDetail:
    summary = _trace_summary(trace)
    return TraceDetail(
        **summary.model_dump(),
        prompt=trace.prompt,
        raw_response=trace.raw_response,
        user_id=trace.user_id,
        model_provider=trace.model_provider,
        routing_decision=dict(trace.routing_decision or {}),
        retrieval_snippets=list(trace.retrieval_snippets or []),
        steps=[_serialize_step(step) for step in trace.steps],
        retrieve_latency_ms=float(trace.retrieve_latency_ms),
        started_at=trace.started_at,
        completed_at=trace.completed_at,
    )


def _model_metric_summary(metric: ModelMetrics) -> ModelMetricSummary:
    return ModelMetricSummary(
        model_id=metric.model_id,
        provider=metric.provider,
        request_count=int(metric.request_count),
        total_prompt_tokens=int(metric.total_prompt_tokens),
        total_completion_tokens=int(metric.total_completion_tokens),
        total_cost_usd_micros=int(metric.total_cost_usd_micros),
        avg_latency_ms=float(metric.avg_latency_ms),
        p50_latency_ms=float(metric.p50_latency_ms),
        p95_latency_ms=float(metric.p95_latency_ms),
        p99_latency_ms=float(metric.p99_latency_ms),
    )


def _log_event_response(event: LogEvent) -> LogEventResponse:
    return LogEventResponse(
        event_id=event.event_id,
        event_type=str(event.event_type),
        timestamp=event.timestamp,
        session_id=event.session_id,
        turn_id=event.turn_id,
        details=dict(event.details or {}),
        severity=event.severity,
    )


def _serialize_step(step: Any) -> dict[str, Any]:
    if is_dataclass(step):
        return asdict(step)
    if isinstance(step, dict):
        return dict(step)
    return {
        "step_name": getattr(step, "step_name", ""),
        "started_at": getattr(step, "started_at", 0.0),
        "ended_at": getattr(step, "ended_at", None),
        "duration_ms": getattr(step, "duration_ms", 0.0),
        "metadata": dict(getattr(step, "metadata", {}) or {}),
    }


def _count_traces(session_id: str | None = None) -> int:
    traces = getattr(trace_collector, "_traces", [])
    if session_id is None:
        return len(traces)
    return sum(1 for trace in traces if trace.session_id == session_id)


def _count_log_events() -> int:
    return len(getattr(structured_logger, "_events", []))


def _filter_log_events(
    event_type: str | None,
    session_id: str | None,
    since: str | None,
) -> list[LogEvent]:
    since_at = _parse_timestamp(since)
    events = []
    for event in getattr(structured_logger, "_events", []):
        if event_type is not None and event.event_type != event_type:
            continue
        if session_id is not None and event.session_id != session_id:
            continue
        if since_at is not None:
            event_at = _parse_timestamp(event.timestamp)
            if event_at is not None and event_at < since_at:
                continue
        events.append(event)
    return events


def _parse_timestamp(timestamp: str | None) -> datetime | None:
    if timestamp is None:
        return None

    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
