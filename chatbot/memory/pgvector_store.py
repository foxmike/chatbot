from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.db import models
from chatbot.memory.vector_store import SearchResult, VectorStore


class PgvectorStore(VectorStore):
    def __init__(
        self,
        session: AsyncSession,
        table: type[Any] | sa.Table | None = None,
    ) -> None:
        self.session = session
        self.model_or_table = table or models.SemanticFact
        self.table = self._resolve_table(self.model_or_table)
        self.id_column = self._require_column("id")
        self.embedding_column = self._require_column("embedding")

    async def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        metadata: list[dict] | None,
    ) -> None:
        """Insert vectors with optional metadata."""
        if len(ids) != len(embeddings):
            raise ValueError("ids and embeddings must have the same length")
        if metadata is not None and len(metadata) != len(ids):
            raise ValueError("metadata must be omitted or have the same length as ids")
        if not ids:
            return

        metadata_items = metadata or [{} for _ in ids]
        rows = [
            self._build_insert_row(id_, embedding, metadata_item)
            for id_, embedding, metadata_item in zip(ids, embeddings, metadata_items, strict=True)
        ]
        self._normalize_insert_rows(rows)
        await self.session.execute(sa.insert(self.table), rows)

    async def search(
        self,
        embedding: list[float],
        top_k: int,
        filter: dict | None,
    ) -> list[SearchResult]:
        """Return cosine-similarity search results."""
        if top_k <= 0:
            return []

        distance = self.embedding_column.op("<->")(
            sa.bindparam(
                "query_embedding",
                type_=self.embedding_column.type,
            )
        ).label("score")
        metadata_columns = self._search_metadata_columns()
        stmt = (
            sa.select(
                self.id_column.label("id"),
                distance,
                *metadata_columns,
            )
            .where(self.embedding_column.is_not(None))
            .order_by(distance)
            .limit(top_k)
        )
        stmt = self._apply_filter(stmt, filter)

        result = await self.session.execute(stmt, {"query_embedding": embedding})
        rows = result.mappings().all()
        if not rows:
            return []

        return [
            SearchResult(
                id=str(row["id"]),
                score=float(row["score"]),
                metadata=self._metadata_from_row(row),
            )
            for row in rows
        ]

    async def delete(self, ids: list[str]) -> int:
        """Delete vectors by id and return the number deleted."""
        if not ids:
            return 0

        coerced_ids = [self._coerce_column_value(self.id_column, id_) for id_ in ids]
        stmt = sa.delete(self.table).where(self.id_column.in_(coerced_ids))
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    async def count(self) -> int:
        """Return the total vector count."""
        stmt = sa.select(sa.func.count()).select_from(self.table)
        result = await self.session.execute(stmt)
        count = result.scalar_one()
        return int(count)

    @staticmethod
    def _resolve_table(table: type[Any] | sa.Table) -> sa.Table:
        if isinstance(table, sa.Table):
            return table

        resolved_table = getattr(table, "__table__", None)
        if isinstance(resolved_table, sa.Table):
            return resolved_table

        raise TypeError("table must be a SQLAlchemy Table or ORM model class")

    def _require_column(self, name: str) -> sa.Column[Any]:
        column = self.table.c.get(name)
        if column is None:
            raise ValueError(f"{self.table.name} must define a {name!r} column")
        return column

    def _build_insert_row(
        self,
        id_: str,
        embedding: list[float],
        metadata: dict,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            self.id_column.name: self._coerce_column_value(self.id_column, id_),
            self.embedding_column.name: embedding,
        }
        metadata = dict(metadata)

        for name, value in metadata.items():
            column = self.table.c.get(name)
            if column is not None and name not in row:
                row[name] = self._coerce_column_value(column, value)

        if self.table.name == models.SemanticFact.__tablename__:
            if "fact_text" not in row and "fact_text" in self.table.c:
                fact_text = metadata.get("text", metadata.get("content", ""))
                row["fact_text"] = "" if fact_text is None else str(fact_text)
            if "confidence" not in row and "confidence" in self.table.c:
                row["confidence"] = 1.0

        self._validate_required_insert_columns(row)
        return row

    def _normalize_insert_rows(self, rows: list[dict[str, Any]]) -> None:
        row_keys = set().union(*(row.keys() for row in rows))
        for row in rows:
            for name in row_keys - row.keys():
                column = self.table.c.get(name)
                if column is not None and column.nullable:
                    row[name] = None
                    continue
                raise ValueError(f"Missing required insert column: {name}")

    def _validate_required_insert_columns(self, row: dict[str, Any]) -> None:
        for column in self.table.c:
            if column.name in row:
                continue
            if column.nullable or column.default is not None or column.server_default is not None:
                continue
            raise ValueError(f"Missing required insert column: {column.name}")

    def _apply_filter(
        self,
        stmt: sa.sql.Select[Any],
        filter: dict | None,
    ) -> sa.sql.Select[Any]:
        if not filter:
            return stmt

        for name, value in filter.items():
            column = self.table.c.get(name)
            if column is None:
                raise ValueError(f"Unknown filter column: {name}")

            if self._is_iterable_filter_value(value):
                values = list(value)
                if not values:
                    return stmt.where(sa.false())
                stmt = stmt.where(
                    column.in_([self._coerce_column_value(column, item) for item in values])
                )
            elif value is None:
                stmt = stmt.where(column.is_(None))
            else:
                stmt = stmt.where(column == self._coerce_column_value(column, value))

        return stmt

    @staticmethod
    def _is_iterable_filter_value(value: object) -> bool:
        return isinstance(value, Iterable) and not isinstance(value, str | bytes | dict)

    def _search_metadata_columns(self) -> list[sa.Column[Any]]:
        return [
            column
            for column in self.table.c
            if column.name not in {self.id_column.name, self.embedding_column.name}
        ]

    def _metadata_from_row(self, row: sa.engine.RowMapping) -> dict | None:
        metadata = {
            column.name: self._serialize_metadata_value(row[column.name])
            for column in self._search_metadata_columns()
            if row[column.name] is not None
        }
        return metadata or None

    @staticmethod
    def _coerce_column_value(column: sa.Column[Any], value: Any) -> Any:
        if value is None:
            return None

        try:
            python_type = column.type.python_type
        except NotImplementedError:
            return value

        if python_type is uuid.UUID and isinstance(value, str):
            return uuid.UUID(value)

        return value

    @staticmethod
    def _serialize_metadata_value(value: Any) -> Any:
        if isinstance(value, uuid.UUID):
            return str(value)
        if isinstance(value, datetime | date):
            return value.isoformat()
        return value


__all__ = ["PgvectorStore"]
