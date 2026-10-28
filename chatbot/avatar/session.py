"""Browser connection ownership for the media engine (framework-neutral).

Ports my_avatar's WebSocket lifecycle (commit da976ad): exactly one browser window owns the
avatar. A newer window takes over and the older one is closed with `REPLACED_CODE` so it
stands down instead of reconnecting and evicting its successor; the evicted window's own
disconnect must not tear down the active session; and the disconnect of the *active* window
interrupts whatever the avatar was doing.

The router owns the actual WebSocket receive loop; it only has to wrap its socket in a
`MediaTransport` and forward each browser frame to `handle_text` / `handle_bytes`.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Protocol

from chatbot.avatar.config import REPLACED_CODE
from chatbot.avatar.media import AvatarMediaEngine
from chatbot.avatar.metrics import log
from chatbot.avatar.protocol import MediaEvent, MediaFrame, UiMessage

__all__ = ["FastAPIWebSocketTransport", "MediaSession", "MediaTransport", "REPLACED_CODE"]


class MediaTransport(Protocol):
    async def send_json(self, data: dict) -> None: ...
    async def send_bytes(self, data: bytes) -> None: ...
    async def close(self, code: int, reason: str) -> None: ...

    @property
    def closed(self) -> bool: ...


class FastAPIWebSocketTransport:
    """Adapts a starlette/FastAPI `WebSocket` to `MediaTransport`."""

    def __init__(self, websocket):
        self.websocket = websocket
        self._closed = False

    async def send_json(self, data: dict) -> None:
        await self.websocket.send_text(json.dumps(data, separators=(",", ":")))

    async def send_bytes(self, data: bytes) -> None:
        await self.websocket.send_bytes(data)

    async def close(self, code: int, reason: str) -> None:
        self._closed = True
        await self.websocket.close(code=code, reason=reason)

    @property
    def closed(self) -> bool:
        if self._closed:
            return True
        from starlette.websockets import WebSocketState
        return self.websocket.client_state != WebSocketState.CONNECTED


class MediaSession:
    """Routes engine output to the owning browser and browser input to the engine.

    `on_event` receives everything that is not browser traffic (TranscriptReady, Interrupted,
    UserSpeechStarted, TurnEnded, Diagnostic) and is where the coordinator hooks in.
    """

    def __init__(self, engine: AvatarMediaEngine,
                 on_event: Callable[[MediaEvent], Awaitable[None]] | None = None):
        self.engine = engine
        self.on_event = on_event
        self.transport: MediaTransport | None = None
        self.connections = 0
        engine.sink = self.deliver

    async def deliver(self, event: MediaEvent) -> None:
        transport = self.transport
        if isinstance(event, (UiMessage, MediaFrame)):
            if transport is None or transport.closed:
                return
            with contextlib.suppress(ConnectionResetError, RuntimeError):
                if isinstance(event, UiMessage):
                    await transport.send_json(event.payload)
                else:
                    await transport.send_bytes(event.data)
        elif self.on_event is not None:
            await self.on_event(event)

    async def attach(self, transport: MediaTransport, tab: str = "?") -> int:
        """Make `transport` the owning window; returns its connection number."""
        self.connections += 1
        number = self.connections
        previous = self.transport
        log("WS", f"Connection {number} opened (browser tab {tab}); "
            + ("replacing connection still open" if previous and not previous.closed
               else "no other connection"))
        self.transport = transport
        if previous and not previous.closed:
            # Tell the older window why, so it stands down instead of reconnecting.
            with contextlib.suppress(Exception):
                await previous.close(REPLACED_CODE, "another window took over")
        await transport.send_json({"type": "config", "sync_buffer_ms": self.engine.config.sync_buffer_ms})
        await transport.send_json({"type": "status", "value": "Listening"})
        return number

    async def detach(self, transport: MediaTransport) -> None:
        """The window behind `transport` went away."""
        if self.transport is not transport:
            return   # an evicted window closing must not drop the active session
        self.transport = None
        if self.engine.busy:
            await self.engine.cancel(reason="disconnect")

    async def handle_text(self, text: str) -> None:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return
        if isinstance(data, dict):
            await self.engine.handle_client_message(data)

    async def handle_bytes(self, data: bytes) -> None:
        await self.engine.handle_client_frame(data)
