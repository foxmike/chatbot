from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from chatbot.db import models
from chatbot.memory.pgvector_store import PgvectorStore
from chatbot.memory.retrieval import RetrievalFilter, _vector_store_filter


class _Session:
    async def execute(self, *args, **kwargs):
        raise AssertionError("query compilation must not execute SQL")


def test_retrieval_uses_persisted_session_as_owner_scope():
    filters = _vector_store_filter(RetrievalFilter(session_id="00000000-0000-0000-0000-000000000001"))
    assert filters == {"owner_session_id": "00000000-0000-0000-0000-000000000001"}
    store = PgvectorStore(_Session())
    stmt = store._apply_filter(sa.select(models.SemanticFact.id), filters)
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "semantic_facts.user_id = (SELECT sessions.user_id" in sql
    assert "FROM sessions" in sql


def test_source_provenance_excludes_other_companions_and_rooms_but_keeps_shared_facts():
    store = PgvectorStore(_Session())
    stmt = store._apply_filter(
        sa.select(models.SemanticFact.id),
        {"owner_session_id": "00000000-0000-0000-0000-000000000001"},
    )
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "coalesce(messages.companion_id, sessions.companion_id) IS NOT NULL" in sql
    assert "coalesce(messages.companion_id, sessions.companion_id) IS DISTINCT FROM" in sql
    assert "sessions.room_id IS NOT NULL" in sql
    assert "sessions.room_id IS DISTINCT FROM" in sql
