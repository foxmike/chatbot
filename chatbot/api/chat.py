"""Chat endpoint for the chatbot engine."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from chatbot.core import TurnContext, build_default_pipeline


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    session_id: str
    response: str
    step_results: list[dict[str, Any]]


chat_router = APIRouter(prefix="/chat")


@chat_router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    ctx = TurnContext(
        session_id=request.session_id,
        user_message=request.message,
        history=[],
        persona={},
        retrieved_memories=[],
        compiled_context=None,
        provider_response=None,
        extracted_memories=[],
        final_response=None,
        metadata={},
    )

    ctx = await build_default_pipeline().run(ctx)
    response = ctx.final_response
    if response is None:
        response = str(ctx.metadata.get("pipeline_error", "No response"))

    return ChatResponse(
        session_id=ctx.session_id,
        response=response,
        step_results=_summarize_step_results(ctx.metadata.get("step_results", [])),
    )


def _summarize_step_results(step_results: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": getattr(step_result, "step_name", ""),
            "duration_ms": getattr(step_result, "duration_ms", 0.0),
            "success": getattr(step_result, "success", False),
        }
        for step_result in step_results
    ]
