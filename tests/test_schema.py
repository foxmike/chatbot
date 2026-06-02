from __future__ import annotations

import ast
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_import_models() -> None:
    from chatbot.db.models import Entity, Episode, Message, SemanticFact, Session, User

    assert User.__tablename__ == "users"
    assert Session.__tablename__ == "sessions"
    assert Message.__tablename__ == "messages"
    assert Episode.__tablename__ == "episodes"
    assert SemanticFact.__tablename__ == "semantic_facts"
    assert Entity.__tablename__ == "entities"


def test_session_factory_creation() -> None:
    from chatbot.db import session as db_session

    assert isinstance(db_session.async_session_factory, async_sessionmaker)

    factory = async_sessionmaker(
        db_session.engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    assert isinstance(factory, async_sessionmaker)


def test_model_instantiation() -> None:
    from chatbot.db.models import Entity, Episode, Message, SemanticFact, Session, User

    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    objects = [
        User(external_id="user-1", display_name="Test User"),
        Session(user_id=user_id, title="Test Session"),
        Message(session_id=session_id, role="user", content="Hello"),
        Episode(session_id=session_id, title="Test Episode"),
        SemanticFact(user_id=user_id, fact_text="Likes tests"),
        Entity(name="pytest", entity_type="tool"),
    ]

    assert [type(obj) for obj in objects] == [
        User,
        Session,
        Message,
        Episode,
        SemanticFact,
        Entity,
    ]
    assert objects[0].external_id == "user-1"
    assert objects[2].role == "user"
    assert objects[3].title == "Test Episode"
    assert objects[5].metadata_json is None
    assert Session.__table__.c.is_active.default is not None
    assert Session.__table__.c.is_active.default.arg is True
    assert SemanticFact.__table__.c.confidence.default is not None
    assert SemanticFact.__table__.c.confidence.default.arg == 1.0

    for model in (User, Session, Message, Episode, SemanticFact, Entity):
        id_default = model.__table__.c.id.default
        assert id_default is not None
        generated_id = id_default.arg(None)
        assert isinstance(generated_id, uuid.UUID)

        created_at = model.__table__.c.created_at
        assert created_at.server_default is not None


def test_pgvector_column() -> None:
    from chatbot.db import models

    for column in (
        models.Message.__table__.c.embedding,
        models.SemanticFact.__table__.c.embedding,
    ):
        assert isinstance(column.type, models.Vector)


def test_vector_store_interface() -> None:
    from chatbot.memory.pgvector_store import PgvectorStore
    from chatbot.memory.vector_store import VectorStore

    assert issubclass(PgvectorStore, VectorStore)


def test_embeddings_interface() -> None:
    from chatbot.memory.embeddings import EmbeddingProvider

    with pytest.raises(TypeError):
        EmbeddingProvider()


def test_migration_reversible_smoke() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0001_core_schema.py"
    )
    tree = ast.parse(migration_path.read_text(encoding="utf-8"), filename=str(migration_path))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert isinstance(functions.get("upgrade"), ast.FunctionDef)
    assert isinstance(functions.get("downgrade"), ast.FunctionDef)

    for name in ("upgrade", "downgrade"):
        function: Any = functions[name]
        assert function.args.args == []
