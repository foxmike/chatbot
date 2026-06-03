from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.db import models
from chatbot.memory.provenance import ProvenanceTracker

SearchDomain = Literal["messages", "semantic_facts"]

_PROVENANCE_KEY = ProvenanceTracker.PROVENANCE_KEY


def _to_uuid(value: str | uuid.UUID) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(value)


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class SearchFilter:
    query: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    topics: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    session_id: str | uuid.UUID | None = None
    user_id: str | uuid.UUID | None = None
    domain: SearchDomain | None = None
    semantic: bool = False


@dataclass(frozen=True, slots=True)
class SearchResult:
    domain: SearchDomain
    id: str
    content: str
    score: float
    created_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SearchPage:
    results: list[SearchResult]
    total: int
    offset: int
    limit: int


async def search(
    db: AsyncSession,
    *,
    filters: SearchFilter | None = None,
    query: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    topics: tuple[str, ...] | None = None,
    tags: tuple[str, ...] | None = None,
    session_id: str | uuid.UUID | None = None,
    user_id: str | uuid.UUID | None = None,
    domain: SearchDomain | None = None,
    offset: int = 0,
    limit: int = 20,
) -> SearchPage:
    _validate_pagination(offset, limit)

    search_filter = _merge_filters(
        filters,
        query=query,
        start=start,
        end=end,
        topics=topics,
        tags=tags,
        session_id=session_id,
        user_id=user_id,
        domain=domain,
    )
    _validate_time_range(search_filter.start, search_filter.end)

    queries: list[sa.sql.Select[tuple[Any, ...]]] = []
    if search_filter.domain is None or search_filter.domain == "messages":
        queries.append(_build_message_query(search_filter))
    if search_filter.domain is None or search_filter.domain == "semantic_facts":
        queries.append(_build_fact_query(search_filter))

    count_cte = _build_count_cte(queries)
    rows_cte = _build_rows_cte(queries, search_filter, offset, limit)

    count_stmt, rows_stmt = _build_paginated_stmts(count_cte, rows_cte, queries)
    count_result = await db.execute(count_stmt)
    total = count_result.scalar_one_or_none() or 0

    rows_result = await db.execute(rows_stmt)
    db_rows = rows_result.mappings().all()

    results = [
        SearchResult(
            domain=row["source"],
            id=str(row["id"]),
            content=row.get("content") or "",
            score=float(row.get("relevance", 0.0)),
            created_at=_safe_datetime(row.get("created_at")),
            metadata=_extract_metadata(row, search_filter),
            provenance=_build_provenance(row),
        )
        for row in db_rows
    ]

    return SearchPage(results=results, total=total, offset=offset, limit=limit)


def _merge_filters(
    filters: SearchFilter | None,
    *,
    query: str | None,
    start: datetime | None,
    end: datetime | None,
    topics: tuple[str, ...] | None,
    tags: tuple[str, ...] | None,
    session_id: str | uuid.UUID | None,
    user_id: str | uuid.UUID | None,
    domain: SearchDomain | None,
) -> SearchFilter:
    resolved_query = query if query is not None else (filters.query if filters is not None else None)
    resolved_start = start if start is not None else (filters.start if filters is not None else None)
    resolved_end = end if end is not None else (filters.end if filters is not None else None)
    resolved_topics = _unique_sorted(topics or ())
    if not resolved_topics and filters is not None:
        resolved_topics = _unique_sorted(filters.topics)
    resolved_tags = _unique_sorted(tags or ())
    if not resolved_tags and filters is not None:
        resolved_tags = _unique_sorted(filters.tags)
    resolved_session = session_id if session_id is not None else (filters.session_id if filters is not None else None)
    resolved_user = user_id if user_id is not None else (filters.user_id if filters is not None else None)
    resolved_domain = domain if domain is not None else (filters.domain if filters is not None else None)
    resolved_semantic = filters.semantic if filters is not None else False

    return SearchFilter(
        query=resolved_query,
        start=resolved_start,
        end=resolved_end,
        topics=resolved_topics,
        tags=resolved_tags,
        session_id=resolved_session,
        user_id=resolved_user,
        domain=resolved_domain,
        semantic=resolved_semantic,
    )


def _build_message_query(filters: SearchFilter) -> sa.sql.Select[tuple[Any, ...]]:
    columns = [
        sa.literal("messages").label("source"),
        models.Message.id.label("id"),
        models.Message.content.label("content"),
        models.Message.role.label("role"),
        models.Message.token_count.label("token_count"),
        models.Message.created_at.label("created_at"),
        models.Message.session_id.label("session_id"),
        sa.cast(sa.null(), sa.Float).label("confidence"),
    ]
    stmt = sa.select(*columns)

    stmt = _apply_common_filters(stmt, models.Message, filters)

    if filters.query and _has_content(filters.query):
        stmt = _apply_text_match(stmt, models.Message.content, filters.query)

    if filters.topics:
        stmt = _apply_topic_filter(stmt, models.Message.content, filters.topics)

    if filters.tags:
        pass

    return stmt


def _build_fact_query(filters: SearchFilter) -> sa.sql.Select[tuple[Any, ...]]:
    columns = [
        sa.literal("semantic_facts").label("source"),
        models.SemanticFact.id.label("id"),
        models.SemanticFact.fact_text.label("content"),
        sa.cast(sa.null(), sa.String).label("role"),
        sa.cast(sa.null(), sa.Integer).label("token_count"),
        models.SemanticFact.created_at.label("created_at"),
        sa.cast(sa.null(), sa.dialects.postgresql.UUID).label("session_id"),
        models.SemanticFact.confidence.label("confidence"),
    ]
    stmt = sa.select(*columns)

    stmt = _apply_common_filters(stmt, models.SemanticFact, filters)

    if filters.user_id is not None:
        stmt = stmt.where(models.SemanticFact.user_id == _to_uuid(filters.user_id))

    if filters.query and _has_content(filters.query):
        stmt = _apply_text_match(stmt, models.SemanticFact.fact_text, filters.query)

    if filters.topics:
        stmt = _apply_topic_filter(stmt, models.SemanticFact.fact_text, filters.topics)

    if filters.tags:

        stmt = stmt.where(sa.false())

    stmt = stmt.where(models.SemanticFact.confidence > 0.0)

    return stmt


def _apply_common_filters(
    stmt: sa.sql.Select[tuple[Any, ...]],
    model: type,
    filters: SearchFilter,
) -> sa.sql.Select[tuple[Any, ...]]:
    if filters.session_id is not None:
        session_col = getattr(model, "session_id", None)
        if session_col is not None:
            stmt = stmt.where(session_col == _to_uuid(filters.session_id))
        else:
            stmt = stmt.where(sa.false())
    normalized_start = _normalize_datetime(filters.start)
    normalized_end = _normalize_datetime(filters.end)
    if normalized_start is not None:
        stmt = stmt.where(model.created_at >= normalized_start)
    if normalized_end is not None:
        stmt = stmt.where(model.created_at <= normalized_end)
    return stmt


def _apply_text_match(
    stmt: sa.sql.Select[tuple[Any, ...]],
    column: sa.Column[str],
    query_text: str,
) -> sa.sql.Select[tuple[Any, ...]]:
    pattern = f"%{_escape_like(query_text)}%"
    return stmt.where(column.ilike(pattern))


def _apply_topic_filter(
    stmt: sa.sql.Select[tuple[Any, ...]],
    column: sa.Column[str],
    topics: tuple[str, ...],
) -> sa.sql.Select[tuple[Any, ...]]:
    for topic in topics:
        if topic:
            stmt = stmt.where(column.ilike(f"%{_escape_like(topic)}%"))
    return stmt


def _build_count_cte(
    queries: list[sa.sql.Select[tuple[Any, ...]]],
) -> sa.sql.CTE | None:
    combined_count = None
    for q in queries:
        count_q = sa.select(sa.func.count().label("cnt")).select_from(q.subquery())
        combined_count = (
            sa.union_all(combined_count, count_q) if combined_count is not None else count_q
        )
    if combined_count is None:
        return None
    return combined_count.cte("counts")


def _build_rows_cte(
    queries: list[sa.sql.Select[tuple[Any, ...]]],
    filters: SearchFilter,
    offset: int,
    limit: int,
) -> sa.sql.CTE | None:
    scored_queries = [
        _add_relevance_score(q, filters) for q in queries
    ]
    combined_rows = None
    for q in scored_queries:
        combined_rows = (
            sa.union_all(combined_rows, q) if combined_rows is not None else q
        )
    if combined_rows is None:
        return None
    return combined_rows.cte("unified")


def _add_relevance_score(
    stmt: sa.sql.Select[tuple[Any, ...]],
    filters: SearchFilter,
) -> sa.sql.Select[tuple[Any, ...]]:
    if filters.query and _has_content(filters.query):
        content_rank = sa.cast(
            sa.func.length(sa.column("content")),
            sa.Float,
        )
        relevance = sa.cast(1.0 / sa.func.greatest(content_rank, sa.cast(1.0, sa.Float)), sa.Float)
    else:
        relevance = sa.cast(1.0, sa.Float)
    return stmt.add_columns(relevance.label("relevance"))


def _build_paginated_stmts(
    count_cte: sa.sql.CTE | None,
    rows_cte: sa.sql.CTE | None,
    queries: list[sa.sql.Select[tuple[Any, ...]]],
) -> tuple[sa.sql.Select[tuple[Any, ...]], sa.sql.Select[tuple[Any, ...]]]:
    if count_cte is not None:
        count_stmt = sa.select(sa.func.coalesce(sa.func.sum(sa.column("cnt")), 0)).select_from(count_cte)
    else:
        count_stmt = sa.select(sa.cast(0, sa.Integer))

    if rows_cte is not None:
        rows_stmt = (
            sa.select(rows_cte)
            .order_by(sa.column("relevance").desc(), sa.column("created_at").desc())
            .offset(sa.bindparam("offset", value=0))
            .limit(sa.bindparam("limit", value=20))
        )
    else:
        rows_stmt = sa.select(sa.literal("sentinel")).where(sa.false())

    return count_stmt, rows_stmt


def _extract_metadata(
    row: sa.engine.RowMapping,
    filters: SearchFilter,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if row.get("role") is not None:
        metadata["role"] = row["role"]
    if row.get("token_count") is not None:
        metadata["token_count"] = row["token_count"]
    if row.get("confidence") is not None:
        metadata["confidence"] = row["confidence"]
    if row.get("session_id") is not None:
        metadata["session_id"] = str(row["session_id"])
    if filters.tags:
        metadata["tags"] = list(filters.tags)
    return metadata


def _build_provenance(row: sa.engine.RowMapping) -> dict[str, Any] | None:
    source = row.get("source")
    if source is None:
        return None
    origin = "explicit" if source == "messages" else "inferred"
    provenance: dict[str, Any] = {
        "origin": origin,
        "confidence": float(row.get("confidence", 1.0)),
    }
    if row.get("created_at") is not None:
        provenance["created_at"] = _normalize_datetime(
            _safe_datetime(row.get("created_at"))
        ).isoformat() if _safe_datetime(row.get("created_at")) else None
    return provenance


def _safe_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _normalize_datetime(value)
    return None


def _has_content(query: str) -> bool:
    return bool(query and query.strip())


def _escape_like(value: str) -> str:
    return value.replace("%", "\\%").replace("_", "\\_")


def _unique_sorted(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(item).strip() for item in items if str(item).strip()}))


def _validate_pagination(offset: int, limit: int) -> None:
    if offset < 0:
        raise ValueError("offset must be non-negative")
    if limit <= 0:
        raise ValueError("limit must be greater than zero")


def _validate_time_range(start: datetime | None, end: datetime | None) -> None:
    normalized_start = _normalize_datetime(start)
    normalized_end = _normalize_datetime(end)
    if (
        normalized_start is not None
        and normalized_end is not None
        and normalized_start > normalized_end
    ):
        raise ValueError("start must be before or equal to end")


__all__ = [
    "SearchDomain",
    "SearchFilter",
    "SearchPage",
    "SearchResult",
    "search",
]