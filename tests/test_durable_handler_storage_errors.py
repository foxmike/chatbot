"""Focused adapter checks; no database or provider requests."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from chatbot.perf.durable_handlers import TurnMemoryStorage


def storage(db):
    return TurnMemoryStorage(
        db, session_row=SimpleNamespace(user_id=uuid.uuid4()),
        session_id=uuid.uuid4(), turn_id="turn",
        user_msg=SimpleNamespace(id=uuid.uuid4()),
        assistant_msg=SimpleNamespace(id=uuid.uuid4()),
    )


@pytest.mark.asyncio
async def test_storage_preserves_explicit_source_type():
    db = SimpleNamespace(scalar=AsyncMock(return_value=None), execute=AsyncMock())
    adapter = storage(db)
    await adapter.persist(str(adapter.session_id), [{
        "type": "fact", "content": "I live in Seattle",
        "metadata": {"source": "user_message", "source_type": "explicit"},
    }])
    assert db.execute.call_args.args[1][0]["source_type"] == "explicit"


@pytest.mark.asyncio
async def test_storage_retains_error_for_legacy_extractor_handler():
    error = RuntimeError("write failed")
    adapter = storage(SimpleNamespace(scalar=AsyncMock(side_effect=error)))
    with pytest.raises(RuntimeError, match="write failed"):
        await adapter.persist(str(adapter.session_id), [{
            "type": "fact", "content": "I live in Seattle",
            "metadata": {"source": "user_message"},
        }])
    assert adapter.persistence_error is error
