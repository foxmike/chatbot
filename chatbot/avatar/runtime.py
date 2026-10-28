"""Connects the media engine to the avatar coordinator (docs/AVATAR_BACKEND_CONTRACT.md).

`AvatarRuntimeHost` owns the one process-wide `AvatarMediaEngine`, so Whisper, Qwen and AVTR
stay loaded across popup sessions. `MediaAdapter` is the lightweight per-connection
`MediaRuntime` the coordinator drives: it translates coordinator calls into engine calls and
engine events into the coordinator's dict events. It never calls an LLM.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
from pathlib import Path
from typing import Any

from chatbot.avatar.config import MediaConfig
from chatbot.avatar.coordinator import AvatarBinding, Emit
from chatbot.avatar.media import AvatarMediaEngine
from chatbot.avatar.portraits import PortraitError, PortraitService
from chatbot.avatar.protocol import (
    Diagnostic,
    Interrupted,
    MediaEvent,
    MediaFrame,
    TranscriptReady,
    TurnEnded,
    UiMessage,
)

logger = logging.getLogger(__name__)


def _show_avatar_logs() -> None:
    """Engine/worker diagnostics ([STT], [TTS], [CHUNK]...) go to the server console at INFO."""
    media_log = logging.getLogger("chatbot.avatar")
    if not media_log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", "%H:%M:%S"))
        media_log.addHandler(handler)
        media_log.setLevel(logging.INFO)
        media_log.propagate = False


class MediaAdapter:
    def __init__(self, host: AvatarRuntimeHost, binding: AvatarBinding, emit: Emit):
        self.host, self.binding, self.emit = host, binding, emit
        self.engine = host.engine
        self._engine_turns: dict[str, str] = {}   # engine turn id -> coordinator turn id
        self._coordinator_turns: dict[str, str] = {}

    async def start(self) -> None:
        self.engine.sink = self._on_event
        try:
            await self.engine.start()
            await self._select_portrait()
        except BaseException:
            self._release_sink()
            raise
        self.engine.set_microphone_enabled(True)
        await self.emit({"type": "config", "data": {"type": "config",
                                                    "sync_buffer_ms": self.engine.config.sync_buffer_ms}})
        await self.emit({"type": "status", "data": {"type": "status", "value": "Listening"}})

    async def _select_portrait(self) -> None:
        binding = self.binding
        try:
            prepared = await self.host.portraits.get_portrait(
                binding.user_id, session_id=binding.session_id, companion_id=binding.companion_id)
            path = self.host.write_portrait(prepared)
            result = await self.engine.set_portrait(path, hashlib.sha1(prepared.cache_key.encode()).hexdigest()[:16])
            error = None if result.ok else result.error
        except PortraitError as exc:
            error = str(exc)
        if error:
            await self.emit({"type": "status", "data": {"type": "error", "error": f"Avatar portrait: {error}"}})
            raise RuntimeError(f"portrait unavailable: {error}")

    async def microphone(self, pcm: bytes) -> None:
        await self.engine.feed_microphone(pcm)

    async def control(self, message: dict[str, Any]) -> None:
        await self.engine.handle_client_message(message)

    async def begin_turn(self, turn_id: str) -> None:
        engine_turn = await self.engine.begin_response(label=turn_id)
        self._engine_turns[engine_turn] = turn_id
        self._coordinator_turns[turn_id] = engine_turn

    async def feed_text(self, turn_id: str, text: str) -> None:
        engine_turn = self._coordinator_turns.get(turn_id)
        if engine_turn is not None:
            # The coordinator already bounds queued text, so never throttle the LLM here.
            await self.engine.feed_text(engine_turn, text, backpressure=False)

    async def end_turn(self, turn_id: str) -> None:
        engine_turn = self._coordinator_turns.get(turn_id)
        if engine_turn is not None:
            await self.engine.end_response(engine_turn)

    async def cancel_turn(self, turn_id: str) -> None:
        engine_turn = self._coordinator_turns.get(turn_id)
        if engine_turn is not None:
            await self.engine.cancel(engine_turn)

    async def skip_turn_speech(self, turn_id: str) -> None:
        engine_turn = self._coordinator_turns.get(turn_id)
        if engine_turn is not None:
            await self.engine.skip_speech(engine_turn)

    async def aclose(self) -> None:
        # Detach first so late engine output cannot reach a closed binding, but leave the
        # models loaded for the next window.
        self._release_sink()
        if self.engine.busy:
            await self.engine.cancel(reason="cancel")

    def _release_sink(self) -> None:
        if self.engine.sink == self._on_event:
            self.engine.sink = None

    async def _on_event(self, event: MediaEvent) -> None:
        if isinstance(event, MediaFrame):
            if event.kind == "video" and not event.segment_id:
                await self.emit({"type": "idle", "data": event.data})
                return
            turn_id = self._turn_of(event.segment_id)
            if turn_id is not None:
                await self.emit({"type": "media", "turn_id": turn_id, "data": event.data})
        elif isinstance(event, UiMessage):
            # Interrupt messages belong to the coordinator, which stamps them with the turn id.
            if event.payload.get("type") != "interrupt":
                await self.emit({"type": "status", "data": event.payload})
        elif isinstance(event, TranscriptReady):
            if event.text.strip():
                await self.emit({"type": "transcript", "text": event.text})
        elif isinstance(event, Interrupted):
            await self.emit({"type": "barge_in"})
        elif isinstance(event, TurnEnded):
            turn_id = self._engine_turns.pop(event.turn_id, None)
            if turn_id is not None:
                self._coordinator_turns.pop(turn_id, None)
                if event.status == "completed":
                    await self.emit({"type": "drained", "turn_id": turn_id})
                elif event.status == "failed":
                    await self.emit({"type": "error", "turn_id": turn_id})
        elif isinstance(event, Diagnostic) and event.name == "chunk_planned":
            turn_id = self._engine_turns.get(event.data["turn_id"])
            if turn_id is not None:
                segment_id = f"{event.data['turn_id']}:{event.data['number']}"
                # Trailing space: segment texts concatenate into the heard-text ledger.
                await self.emit({"type": "segment", "turn_id": turn_id, "id": segment_id,
                                 "text": event.data["text"] + " "})

    def _turn_of(self, segment_id: str | None) -> str | None:
        return self._engine_turns.get((segment_id or "").split(":", 1)[0])


class AvatarRuntimeHost:
    """Process-wide engine + portrait service; `factory` is the coordinator's runtime factory."""

    def __init__(self, config: MediaConfig | None = None, portraits: PortraitService | None = None,
                 engine: AvatarMediaEngine | None = None):
        _show_avatar_logs()
        self.engine = engine or AvatarMediaEngine(config or MediaConfig.from_env())
        self._portraits = portraits
        self._portrait_dir = Path(tempfile.gettempdir()) / "chatbot-avatar-portraits"

    @property
    def portraits(self) -> PortraitService:
        if self._portraits is None:   # imported late: pulls in the database layer
            from chatbot.db import async_session_factory
            self._portraits = PortraitService(async_session_factory)
        return self._portraits

    def write_portrait(self, prepared) -> Path:
        """Materialize Pi's prepared PNG so AVTR (another process) can read it."""
        self._portrait_dir.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha1(prepared.cache_key.encode()).hexdigest()[:16]
        path = self._portrait_dir / f"{name}.png"
        if not path.is_file():
            partial = path.with_suffix(".tmp")
            partial.write_bytes(prepared.png_bytes)
            partial.replace(path)
        return path

    def factory(self, binding: AvatarBinding, emit: Emit) -> MediaAdapter:
        return MediaAdapter(self, binding, emit)

    async def aclose(self) -> None:
        await self.engine.stop()
