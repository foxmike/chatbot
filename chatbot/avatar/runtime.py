"""Connects the media engine to the avatar coordinator (docs/AVATAR_BACKEND_CONTRACT.md).

`AvatarRuntimeHost` owns the one process-wide `AvatarMediaEngine`, so Whisper, Qwen and AVTR
stay loaded across popup sessions. `MediaAdapter` is the lightweight per-connection
`MediaRuntime` the coordinator drives: it translates coordinator calls into engine calls and
engine events into the coordinator's dict events. It never calls an LLM.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import tempfile
from pathlib import Path
from typing import Any

from chatbot.config import get_settings
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
        self._prepare_task: asyncio.Task | None = None
        self._logged_media: set[str] = set()

    async def start(self) -> None:
        displaced = None
        try:
            async with self.host._owner_lock:
                await self.engine.start()
                if self.host._owners and self.engine.busy:
                    displaced = self.host._owners[-1]
                    await self.engine.cancel(reason="cancel")
                await self._select_portrait()
                await self.engine.avatar.send({"type": "resume_render"})
                self.host._owners.append(self)
                self.engine.sink = self._on_event
            if displaced is not None:
                await displaced.emit({"type": "barge_in"})
        except BaseException:
            async with self.host._owner_lock:
                if self in self.host._owners:
                    self.host._owners.remove(self)
                if self.engine.sink == self._on_event or not self.host._owners:
                    self.engine.sink = self.host._owners[-1]._on_event if self.host._owners else None
                if not self.host._owners and self.engine.avatar.alive:
                    await self.engine.avatar.send({"type": "pause_render"})
            raise
        # Build this conversation's clone prompt now so the first reply does not pay for it.
        self._prepare_task = asyncio.create_task(self._prepare_voice())
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
        if self.engine.sink == self._on_event:
            await self.engine.feed_microphone(pcm)

    async def control(self, message: dict[str, Any]) -> None:
        if self.engine.sink == self._on_event:
            await self.engine.handle_client_message(message)

    async def _resolve_voice(self) -> dict:
        try:
            return await self.host.voices.resolve(self.binding)
        except Exception as exc:
            message = str(exc) or "The conversation's voice could not be resolved"
            await self.emit({"type": "status", "data": {"type": "error", "error": f"Voice: {message}"}})
            raise VoiceResolutionError(message) from exc

    async def _prepare_voice(self) -> None:
        try:
            voice = await self.host.voices.resolve(self.binding)
            reply = await self.engine.prepare_voice(voice, warm=True)
            logger.info("Voice %s (%s) ready: prompt %s in %.0fms", voice["voice_id"], voice.get("source"),
                        reply.get("prompt"), reply.get("prepare_ms") or 0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the turn itself reports a failure explicitly
            logger.warning("Voice preparation failed for session %s: %s", self.binding.session_id, exc)

    async def begin_turn(self, turn_id: str) -> None:
        async with self.host._owner_lock:
            if self.engine.sink != self._on_event:
                raise RuntimeError("Avatar connection no longer owns the media engine")
            voice = await self._resolve_voice()
            engine_turn = await self.engine.begin_response(label=turn_id, speech_mode=self.binding.speech_mode,
                                                           voice=voice)
            self._engine_turns[engine_turn] = turn_id
            self._coordinator_turns[turn_id] = engine_turn

    async def feed_text(self, turn_id: str, text: str) -> None:
        if self.engine.sink != self._on_event:
            return
        engine_turn = self._coordinator_turns.get(turn_id)
        if engine_turn is not None:
            # The coordinator already bounds queued text, so never throttle the LLM here.
            await self.engine.feed_text(engine_turn, text, backpressure=False)

    async def end_turn(self, turn_id: str) -> None:
        if self.engine.sink != self._on_event:
            return
        engine_turn = self._coordinator_turns.get(turn_id)
        if engine_turn is not None:
            await self.engine.end_response(engine_turn)

    async def cancel_turn(self, turn_id: str) -> None:
        async with self.host._owner_lock:
            if self.engine.sink != self._on_event:
                return
            engine_turn = self._coordinator_turns.get(turn_id)
            if engine_turn is not None:
                await self.engine.cancel(engine_turn)

    async def skip_turn_speech(self, turn_id: str) -> None:
        async with self.host._owner_lock:
            if self.engine.sink != self._on_event:
                return
            engine_turn = self._coordinator_turns.get(turn_id)
            if engine_turn is not None:
                await self.engine.skip_speech(engine_turn)

    async def aclose(self) -> None:
        # Detach first so late engine output cannot reach a closed binding, but leave the
        # models loaded for the next window.
        if self._prepare_task is not None and not self._prepare_task.done():
            self._prepare_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._prepare_task
        async with self.host._owner_lock:
            owner = self.engine.sink == self._on_event
            if self in self.host._owners:
                self.host._owners.remove(self)
            if owner:
                if self.engine.busy:
                    await self.engine.cancel(reason="cancel")
                self.engine.sink = self.host._owners[-1]._on_event if self.host._owners else None
            if not self.host._owners and self.engine.avatar.alive:
                await self.engine.avatar.send({"type": "pause_render"})
        self._engine_turns.clear()
        self._coordinator_turns.clear()
        self._logged_media.clear()

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
                if event.kind == "audio" and event.segment_id not in self._logged_media:
                    self._logged_media.add(event.segment_id)
                    logger.info("avatar_media stage=sink_received turn_id=%s segment=%s", turn_id, event.segment_id)
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


class VoiceResolutionError(RuntimeError):
    """The conversation has no usable voice; the turn is not spoken with another one."""


class AvatarRuntimeHost:
    """Process-wide engine + portrait service; `factory` is the coordinator's runtime factory."""

    def __init__(self, config: MediaConfig | None = None, portraits: PortraitService | None = None,
                 engine: AvatarMediaEngine | None = None, voices=None):
        _show_avatar_logs()
        self.engine = engine or AvatarMediaEngine(config or MediaConfig.from_settings(get_settings()))
        self._owner_lock = asyncio.Lock()
        self._owners: list[MediaAdapter] = []
        self._portraits = portraits
        self._voices = voices
        self._portrait_dir = Path(tempfile.gettempdir()) / "chatbot-avatar-portraits"

    @property
    def portraits(self) -> PortraitService:
        if self._portraits is None:   # imported late: pulls in the database layer
            from chatbot.db import async_session_factory
            self._portraits = PortraitService(async_session_factory)
        return self._portraits

    @property
    def voices(self):
        """Resolves each turn's voice from the Voice Library (imported late: database layer)."""
        if self._voices is None:
            from chatbot.db import async_session_factory
            from chatbot.voices.resolver import DatabaseVoiceResolver
            from chatbot.voices.storage import VoiceStorage

            config = self.engine.config
            self._voices = DatabaseVoiceResolver(
                async_session_factory, VoiceStorage(config.voice_root),
                design_model=config.voice_model, clone_model=config.clone_model)
        return self._voices

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
