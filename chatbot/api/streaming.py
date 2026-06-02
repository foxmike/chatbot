"""Server-sent event streaming endpoint for the chatbot engine."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from time import perf_counter

from fastapi import APIRouter
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from chatbot.core import TurnContext, TurnStep, build_default_pipeline


class StreamRequest(BaseModel):
    session_id: str
    message: str


streaming_router = APIRouter(prefix="/chat")


@streaming_router.post("/stream")
async def stream_chat(request: StreamRequest) -> StreamingResponse:
    return StreamingResponse(
        _stream_turn(request),
        media_type="text/event-stream",
    )


async def _stream_turn(request: StreamRequest) -> AsyncIterator[str]:
    try:
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
        pipeline = build_default_pipeline()

        for step in getattr(pipeline, "_steps", []):
            step_name = _step_name(step)
            yield _sse_data({"step": step_name, "status": "running"})

            started_at = perf_counter()
            ctx = await step.run(ctx)
            duration_ms = (perf_counter() - started_at) * 1000

            yield _sse_data(
                {
                    "step": step_name,
                    "status": "done",
                    "duration_ms": duration_ms,
                }
            )

        response = ctx.final_response
        if response is None:
            response = str(ctx.metadata.get("pipeline_error", "No response"))

        yield _sse_data({"type": "response", "text": response})
        yield "data: [DONE]\n\n"
    except Exception as exc:
        yield _sse_data({"type": "error", "error": str(exc)})
        yield "data: [DONE]\n\n"


def _step_name(step: TurnStep) -> str:
    name = getattr(step, "name", None)
    if isinstance(name, str) and name:
        return name
    return step.__class__.__name__


def _sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"
