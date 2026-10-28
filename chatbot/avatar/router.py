"""Authenticated browser transport for the chatbot-owned avatar conversation."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from starlette.requests import Request

import anyio

from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.avatar.coordinator import AvatarBinding, get_coordinator
from chatbot.companions.companion import CompanionManager
from chatbot.db import async_session_factory
from chatbot.db.models import Session

logger = logging.getLogger(__name__)
avatar_router = APIRouter(prefix="/api/avatar", tags=["avatar"])
MAX_PACKET_BYTES = 2 * 1024 * 1024
CONTROLS = {
    "mic",
    "segment_scheduled",
    "playback_start",
    "playback_end",
    "video_displayed",
    "buffer_stats",
    "interruption_ack",
    "interrupt",
    "barge_in",
    "skip_speech",
}


class AvatarAuth(BaseModel):
    model_config = ConfigDict(extra="ignore")  # the popup also sends room_id / tab_id
    type: str = Field(pattern="^auth$")
    token: str | None = Field(default=None, max_length=8192)
    session_id: uuid.UUID
    companion_id: str = Field(min_length=1, max_length=36)


async def _authorize(auth: AvatarAuth, user: CurrentUser) -> None:
    """Check both owners and the session/companion relationship, before startup."""
    user_id = uuid.UUID(user.user_id)
    async with async_session_factory() as db:
        result = await db.execute(
            select(Session).where(Session.id == auth.session_id, Session.user_id == user_id)
        )
        session = result.scalar_one_or_none()
        if session is None or session.companion_id != auth.companion_id:
            raise HTTPException(404, "Session or companion not found")
        companion = await CompanionManager(db).get(user.user_id, auth.companion_id)
        if companion is None:
            raise HTTPException(404, "Session or companion not found")


async def _spoken_turn(ws: WebSocket, binding: AvatarBinding, user: CurrentUser, text: str) -> None:
    # Use a real Request so memory/provider state lookup is identical to HTTP SSE.
    from chatbot.api.streaming import StreamRequest, stream_chat

    scope = dict(ws.scope, type="http", method="POST", path="/chat/stream")
    request = Request(scope)
    request.state.avatar_source = "spoken"
    request.state.avatar_binding = binding
    response = None
    try:
        # Show the user's words before the turn's setup (history, memory) runs.
        await binding.send({"type": "transcript", "text": text, "session_id": binding.session_id})
        async with async_session_factory() as db:
            response = await stream_chat(
                http_request=request,
                request=StreamRequest(session_id=binding.session_id, message=text),
                current_user=user,
                db=db,
            )
        async for frame in response.body_iterator:
            if isinstance(frame, bytes):
                frame = frame.decode()
            data = frame.removeprefix("data: ").strip()
            if data == "[DONE]":
                continue
            await binding.send(json.loads(data))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Spoken chatbot turn failed for session %s", binding.session_id)
        await binding.send({"type": "error", "error": "Conversation failed"})
    finally:
        if response is not None:
            with anyio.CancelScope(shield=True):
                await response.body_iterator.aclose()


@avatar_router.websocket("/ws")
async def avatar_websocket(ws: WebSocket) -> None:
    await ws.accept()
    binding = None
    coordinator = get_coordinator(ws.app)
    try:
        async with asyncio.timeout(10):
            raw = await ws.receive_text()
        if len(raw) > 16384:
            await ws.close(code=1009, reason="Authentication message too large")
            return
        auth = AvatarAuth.model_validate_json(raw)
        authorization = ws.headers.get("authorization")
        if auth.token:
            authorization = f"Bearer {auth.token}"
        user = await get_current_user(authorization=authorization)
        await _authorize(auth, user)
        factory = getattr(ws.app.state, "avatar_runtime_factory", None)
        if not callable(factory):
            await ws.send_json({"type": "error", "error": "Avatar media runtime unavailable"})
            await ws.close(code=1013, reason="Avatar media runtime unavailable")
            return
        binding = AvatarBinding(str(auth.session_id), user.user_id, auth.companion_id, ws)
        binding.on_transcript = lambda text: _spoken_turn(ws, binding, user, text)
        await coordinator.connect(binding, factory)
        await binding.send(
            {
                "type": "ready",
                "session_id": binding.session_id,
                "companion_id": binding.companion_id,
            }
        )
        while not binding.closed:
            packet = await ws.receive()
            if packet["type"] == "websocket.disconnect":
                break
            if packet.get("bytes") is not None:
                data = packet["bytes"]
                if len(data) > MAX_PACKET_BYTES:
                    await ws.close(code=1009, reason="Microphone packet too large")
                    break
                if not data.startswith(b"M") or (len(data) - 1) % 4:
                    await ws.close(code=1003, reason="Expected M + float32 PCM")
                    break
                async with asyncio.timeout(5):
                    await binding.runtime.microphone(data[1:])
            elif packet.get("text") is not None:
                if len(packet["text"]) > 16384:
                    await ws.close(code=1009, reason="Control message too large")
                    break
                message = json.loads(packet["text"])
                if not isinstance(message, dict) or message.get("type") not in CONTROLS:
                    await ws.close(code=1008, reason="Unsupported control message")
                    break
                if "id" in message and not isinstance(message["id"], str):
                    raise ValueError("Segment id must be a string")
                await coordinator.control(binding, message)
    except WebSocketDisconnect:
        pass
    except (HTTPException, ValidationError, ValueError, TypeError, KeyError):
        await ws.close(code=1008, reason="Invalid authentication, ownership or message")
    except TimeoutError:
        await ws.close(code=1013, reason="Avatar operation timed out")
    except Exception:
        logger.exception("Avatar connection failed")
        await ws.close(code=1011, reason="Avatar runtime failed")
    finally:
        if binding:
            await coordinator.detach(binding)
