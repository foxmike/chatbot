from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock
from chatbot.core.steps import TurnContext
from chatbot.core.turn import RetrieveStep
from chatbot.api.chat import _prepare_memory_workflow

def test_turn_context_retrieval_engine_field():
    ctx = TurnContext(
        session_id="test-session",
        user_message="hello",
        history=[],
        persona={},
        retrieved_memories=[],
        compiled_context=None,
        provider_response=None,
        extracted_memories=[],
        final_response=None,
    )
    assert ctx.retrieval_engine is None

@pytest.mark.asyncio
async def test_retrieve_step_uses_context_retrieval_engine():
    mock_engine = MagicMock()
    mock_engine.retrieve = AsyncMock(return_value=[{"content": "retrieved memory"}])
    
    ctx = TurnContext(
        session_id="test-session",
        user_message="hello",
        history=[],
        persona={},
        retrieved_memories=[],
        compiled_context=None,
        provider_response=None,
        extracted_memories=[],
        final_response=None,
        retrieval_engine=mock_engine,
    )
    
    step = RetrieveStep(loader_cls=None)
    res_ctx = await step.run(ctx)
    
    mock_engine.retrieve.assert_called_once_with(
        query="hello",
        session_id="test-session",
    )
    assert len(res_ctx.retrieved_memories) == 1
    assert res_ctx.retrieved_memories[0]["content"] == "retrieved memory"

@pytest.mark.asyncio
async def test_prepare_memory_workflow_sets_retrieval_engine():
    mock_request = MagicMock()
    # Mock first_state_value for settings/providers lookup
    mock_request.app.state.retrieval_engine = None
    mock_request.app.state.memory_vector_store = None
    mock_request.app.state.embedding_provider = None
    mock_request.app.state.provider_registry = None
    
    ctx1 = TurnContext(
        session_id="test-session",
        user_message="hello",
        history=[],
        persona={},
        retrieved_memories=[],
        compiled_context=None,
        provider_response=None,
        extracted_memories=[],
        final_response=None,
    )
    
    # Mock db session
    mock_db = AsyncMock()
    # Mock select query in _generate_recent_message_embeddings
    mock_db.execute = AsyncMock()
    
    import uuid
    session_id = uuid.uuid4()
    
    # Run _prepare_memory_workflow with no mock_engine in state
    await _prepare_memory_workflow(
        request=mock_request,
        ctx=ctx1,
        db=mock_db,
        session_id=session_id
    )
    
    # Test case 2: mock retrieval engine provided in metadata
    mock_engine = MagicMock()
    mock_engine.retrieve = AsyncMock()
    
    ctx2 = TurnContext(
        session_id="test-session",
        user_message="hello",
        history=[],
        persona={},
        retrieved_memories=[],
        compiled_context=None,
        provider_response=None,
        extracted_memories=[],
        final_response=None,
        metadata={"retrieval_engine": mock_engine},
    )
    
    await _prepare_memory_workflow(
        request=mock_request,
        ctx=ctx2,
        db=mock_db,
        session_id=session_id
    )
    
    assert ctx2.retrieval_engine is mock_engine
    assert ctx2.metadata["retrieval_engine"] is mock_engine

    # Test case 3: mock retrieval engine provided directly in retrieval_engine field
    mock_engine3 = MagicMock()
    mock_engine3.retrieve = AsyncMock()
    
    ctx3 = TurnContext(
        session_id="test-session",
        user_message="hello",
        history=[],
        persona={},
        retrieved_memories=[],
        compiled_context=None,
        provider_response=None,
        extracted_memories=[],
        final_response=None,
        retrieval_engine=mock_engine3,
    )
    
    await _prepare_memory_workflow(
        request=mock_request,
        ctx=ctx3,
        db=mock_db,
        session_id=session_id
    )
    
    assert ctx3.retrieval_engine is mock_engine3
    assert ctx3.metadata["retrieval_engine"] is mock_engine3

@pytest.mark.asyncio
async def test_stream_turn_wires_retrieval_engine(monkeypatch):
    from chatbot.api.streaming import _stream_turn, StreamRequest
    from chatbot.core.steps import TurnStep
    from chatbot.core.turn import TurnPipeline
    import uuid
    
    mock_engine = MagicMock()
    mock_engine.retrieve = AsyncMock(return_value=[])
    
    mock_request = MagicMock()
    mock_request.app.state.retrieval_engine = mock_engine
    mock_request.app.state.memory_vector_store = None
    mock_request.app.state.embedding_provider = None
    mock_request.app.state.provider_registry = None
    
    captured_ctx = []
    
    class CaptureStep(TurnStep):
        name = "capture"
        async def run(self, ctx: TurnContext) -> TurnContext:
            captured_ctx.append(ctx)
            return ctx
            
    monkeypatch.setattr(
        "chatbot.api.streaming.build_default_pipeline",
        lambda: TurnPipeline([CaptureStep()])
    )
    
    mock_db = AsyncMock()
    monkeypatch.setattr("chatbot.api.streaming.streaming_session_factory", lambda: mock_db)
    monkeypatch.setattr("chatbot.api.streaming._persist_messages", AsyncMock())
    monkeypatch.setattr("chatbot.api.streaming._run_post_stream_db_write", AsyncMock())
    monkeypatch.setattr("chatbot.api.streaming._maybe_schedule_title_generation", MagicMock())
    
    session_id = uuid.uuid4()
    
    stream_req = StreamRequest(session_id=str(session_id), message="test message")
    current_user = MagicMock()
    current_user.user_id = str(uuid.uuid4())
    
    async for _ in _stream_turn(
        request=stream_req,
        http_request=mock_request,
        current_user=current_user,
        session_id=session_id,
        session_title="Test title",
        session_companion_id=None,
        session_room_id=None,
        session_snapshot={},
        character_profile=None,
        identity_state=None,
        history=[]
    ):
        pass
        
    assert len(captured_ctx) == 1
    assert captured_ctx[0].retrieval_engine is mock_engine
