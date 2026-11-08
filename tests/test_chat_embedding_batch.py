"""Recent message vectors are prepared in one ordered request before retrieval."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from chatbot.api import conversation
from chatbot.core.steps import TurnContext


def test_recent_message_embeddings_use_one_ordered_batch(monkeypatch):
    messages = [SimpleNamespace(role="user", content="one", embedding=None),
                SimpleNamespace(role="assistant", content="two", embedding=None)]
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = messages
    db.execute.return_value = result
    db.add = MagicMock()
    provider = SimpleNamespace(dimension=3, embed=AsyncMock(return_value=[[1, 2, 3], [4, 5, 6]]))
    monkeypatch.setattr("chatbot.memory.retrieval.OpenRouterEmbeddingProvider", lambda **_: provider)
    monkeypatch.setattr("chatbot.config.get_settings", lambda: SimpleNamespace(
        openrouter_api_key="test", openrouter_base_url="https://example.test/v1",
        embedding_base_url="https://example.test/v1", embedding_model="example"))
    ctx = TurnContext(session_id="test", user_message="hello", history=[], persona={},
                      retrieved_memories=[], compiled_context=None, provider_response=None,
                      extracted_memories=[], final_response=None)

    asyncio.run(conversation.generate_recent_message_embeddings(db, uuid.uuid4(), ctx))

    provider.embed.assert_awaited_once_with(["user: one", "assistant: two"])
    assert [m.embedding for m in messages] == [[1, 2, 3], [4, 5, 6]]
    assert db.add.call_count == 2
    db.flush.assert_awaited_once()
    assert ctx.metadata["message_embeddings_generated"] == 2
