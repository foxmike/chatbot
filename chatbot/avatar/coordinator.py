"""Turn ownership and a bounded text bridge; no media/model imports belong here."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol
import uuid


logger = logging.getLogger(__name__)

MediaEvent = dict[str, Any]
Emit = Callable[[MediaEvent], Awaitable[None]]


class MediaRuntime(Protocol):
    """Implemented by the avatar media port; see docs/AVATAR_BACKEND_CONTRACT.md."""

    async def start(self) -> None: ...
    async def microphone(self, pcm: bytes) -> None: ...
    async def control(self, message: dict[str, Any]) -> None: ...
    async def begin_turn(self, turn_id: str) -> None: ...
    async def feed_text(self, turn_id: str, text: str) -> None: ...
    async def end_turn(self, turn_id: str) -> None: ...
    async def cancel_turn(self, turn_id: str) -> None: ...
    async def skip_turn_speech(self, turn_id: str) -> None: ...
    async def aclose(self) -> None: ...


RuntimeFactory = Callable[["AvatarBinding", Emit], MediaRuntime]


@dataclass
class AvatarTurn:
    turn_id: str
    source: str
    task: asyncio.Task[Any] | None
    queue: asyncio.Queue[str | None] = field(default_factory=lambda: asyncio.Queue(128))
    queued_chars: int = 0
    generated_chars: int = 0
    text_delivered: str = ""
    segments: dict[str, str] = field(default_factory=dict)
    acknowledged: set[str] = field(default_factory=set)
    drained: bool = False
    interrupted: bool = False
    speech_skipped: bool = False  # user skipped speech; the LLM turn itself carries on
    speech_failed: bool = False
    persisted: bool = False
    persistence_error: bool = False
    worker: asyncio.Task[None] | None = None
    playback_changed: asyncio.Event = field(default_factory=asyncio.Event)
    finished: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def heard_text(self) -> str:
        # Only a contiguous prefix of COMPLETED segments is evidence of delivery.
        parts = []
        for segment_id, text in self.segments.items():
            if segment_id not in self.acknowledged:
                break
            parts.append(text)
        return "".join(parts)

    @property
    def delivered_text(self) -> str:
        return self.heard_text if self.source == "spoken" else self.text_delivered


@dataclass
class AvatarBinding:
    session_id: str
    user_id: str
    companion_id: str
    websocket: Any
    runtime: MediaRuntime | None = None
    turn: AvatarTurn | None = None
    spoken_task: asyncio.Task[None] | None = None
    closed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    on_transcript: Callable[[str], Awaitable[None]] | None = None

    async def send(
        self, payload: dict[str, Any] | bytes, *, turn: AvatarTurn | None = None
    ) -> None:
        if self.closed:
            return
        async with asyncio.timeout(2):
            async with self.send_lock:
                # A frame can become stale while waiting behind another send.
                if self.closed or (
                    turn and (self.turn is not turn or turn.interrupted or turn.speech_failed
                              or turn.speech_skipped)
                ):
                    return
                if isinstance(payload, bytes):
                    await self.websocket.send_bytes(payload)
                else:
                    await self.websocket.send_json(payload)


class AvatarCoordinator:
    """One owner per chatbot session, within one ASGI process.

    A slow media branch fails closed at 128 tokens / 32K chars or a 5s adapter
    call deadline. SSE token publication never awaits speech synthesis.
    """

    def __init__(self) -> None:
        self.bindings: dict[str, AvatarBinding] = {}
        self._connection_lock = asyncio.Lock()

    def get(self, session_id: str, user_id: str) -> AvatarBinding | None:
        binding = self.bindings.get(session_id)
        if binding is not None and not binding.closed and binding.user_id == user_id:
            return binding
        return None

    async def connect(self, binding: AvatarBinding, factory: RuntimeFactory) -> None:
        async with self._connection_lock:
            previous = self.bindings.get(binding.session_id)
            if previous is not None:
                await self._detach(previous, takeover=True)
            self.bindings[binding.session_id] = binding
            try:
                binding.runtime = factory(binding, lambda event: self.emit(binding, event))
                async with asyncio.timeout(120):
                    await binding.runtime.start()
            except BaseException:
                await self._detach(binding)
                raise

    async def detach(self, binding: AvatarBinding) -> None:
        async with self._connection_lock:
            await self._detach(binding)

    async def _detach(self, binding: AvatarBinding, *, takeover: bool = False) -> None:
        if binding.closed:
            return
        # A replacement must not load history while the predecessor is still
        # saving it. Let a cleanup timeout reject takeover instead of hiding it.
        await self.interrupt(binding)
        binding.closed = True
        if self.bindings.get(binding.session_id) is binding:
            self.bindings.pop(binding.session_id, None)
        if takeover:
            with suppress(Exception):
                await binding.websocket.close(code=4001, reason="another window took over")
        if binding.spoken_task and binding.spoken_task is not asyncio.current_task():
            if not binding.spoken_task.done():
                binding.spoken_task.cancel()
            await asyncio.gather(binding.spoken_task, return_exceptions=True)
        if binding.runtime:
            with suppress(Exception):
                async with asyncio.timeout(5):
                    await binding.runtime.aclose()

    async def close(self) -> None:
        for binding in list(self.bindings.values()):
            try:
                await self.detach(binding)
            except Exception:
                logger.exception("Avatar shutdown failed for session %s", binding.session_id)
                if binding.runtime:
                    with suppress(Exception):
                        async with asyncio.timeout(5):
                            await binding.runtime.aclose()

    async def begin(self, binding: AvatarBinding, *, source: str) -> AvatarTurn:
        async with binding.lock:
            await self.interrupt(binding)
            if binding.turn and binding.turn.persistence_error:
                raise RuntimeError("Previous avatar turn could not be persisted")
            if binding.closed or self.bindings.get(binding.session_id) is not binding:
                raise RuntimeError("Avatar connection replaced")
            turn = AvatarTurn(str(uuid.uuid4()), source, asyncio.current_task())
            binding.turn = turn
            try:
                async with asyncio.timeout(5):
                    await binding.runtime.begin_turn(turn.turn_id)
            except BaseException:
                turn.finished.set()
                raise
            turn.worker = asyncio.create_task(self._consume(binding, turn))
            if turn.speech_skipped:  # skipped while the runtime was still starting the turn
                await self._skip_runtime_speech(binding, turn)
            return turn

    def publish(self, binding: AvatarBinding, turn: AvatarTurn, text: str) -> None:
        turn.generated_chars += len(text)
        if turn.interrupted or turn.speech_failed or turn.speech_skipped or binding.turn is not turn:
            return
        if turn.queued_chars + len(text) > 32768:
            self._speech_failure(binding, turn)
            return
        try:
            turn.queue.put_nowait(text)
            turn.queued_chars += len(text)
        except asyncio.QueueFull:
            self._speech_failure(binding, turn)

    def end(self, binding: AvatarBinding, turn: AvatarTurn) -> None:
        if turn.interrupted or turn.speech_failed or turn.speech_skipped:
            return
        try:
            turn.queue.put_nowait(None)
        except asyncio.QueueFull:
            self._speech_failure(binding, turn)

    def _speech_failure(self, binding: AvatarBinding, turn: AvatarTurn) -> None:
        if turn.speech_failed:
            return
        turn.speech_failed = True
        turn.playback_changed.set()
        # Retain this task so teardown cannot leave detached runtime operations.
        if turn.worker and turn.worker is not asyncio.current_task():
            turn.worker.cancel()
        turn.worker = asyncio.create_task(self._stop_failed_speech(binding, turn))

    async def _stop_failed_speech(self, binding: AvatarBinding, turn: AvatarTurn) -> None:
        if binding.turn is not turn:
            return
        if turn.source == "spoken":
            await self.interrupt(binding)
        else:
            await self.stop_speech(binding, turn)
            with suppress(Exception):
                await binding.send({"type": "error", "error": "Avatar speech unavailable"})

    async def _consume(self, binding: AvatarBinding, turn: AvatarTurn) -> None:
        try:
            while not turn.interrupted:
                text = await turn.queue.get()
                async with asyncio.timeout(5):
                    if text is None:
                        await binding.runtime.end_turn(turn.turn_id)
                        return
                    turn.queued_chars -= len(text)
                    await binding.runtime.feed_text(turn.turn_id, text)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._speech_failure(binding, turn)

    async def wait_playback(self, turn: AvatarTurn) -> None:
        async with asyncio.timeout(180):
            while True:
                turn.playback_changed.clear()
                if turn.speech_skipped and not turn.interrupted:
                    return  # nothing will be played; the text was generated and displayed in full
                if turn.interrupted or turn.speech_failed:
                    raise asyncio.CancelledError
                if turn.drained and len(turn.acknowledged) == len(turn.segments):
                    return
                await turn.playback_changed.wait()

    async def stop_speech(self, binding: AvatarBinding, turn: AvatarTurn) -> None:
        while not turn.queue.empty():
            turn.queue.get_nowait()
        turn.queued_chars = 0
        if turn.worker and turn.worker is not asyncio.current_task():
            turn.worker.cancel()
            await asyncio.gather(turn.worker, return_exceptions=True)
        with suppress(Exception):
            await binding.send({"type": "interrupt", "turn_id": turn.turn_id})
        if binding.runtime:
            with suppress(Exception):
                async with asyncio.timeout(2):
                    await binding.runtime.cancel_turn(turn.turn_id)

    async def interrupt(self, binding: AvatarBinding) -> None:
        turn = binding.turn
        if turn is None:
            return
        task = turn.task
        if not turn.interrupted:
            turn.interrupted = True  # Invalidate output BEFORE any await/cancellation.
            turn.playback_changed.set()
            if task and task is not asyncio.current_task() and not turn.finished.is_set():
                task.cancel()
            await self.stop_speech(binding, turn)
        if task and task is not asyncio.current_task() and not turn.finished.is_set():
            # Persistence is part of cancellation, not an optional side effect.
            async with asyncio.timeout(10):
                await turn.finished.wait()

    async def skip_speech(self, binding: AvatarBinding) -> None:
        """Stop speaking the current response; text generation and persistence carry on.

        Unlike `interrupt`, this neither cancels the chat generation nor marks the turn
        interrupted. The flag lives on the turn, so the next turn speaks normally. The browser
        always gets a `speech_skipped` acknowledgement (even with nothing to skip), ordered after
        any media already sent, so it can drop everything before it and nothing after.
        """
        turn = binding.turn
        if turn is not None and not turn.interrupted and not turn.speech_skipped:
            turn.speech_skipped = True  # invalidate speech output BEFORE any await
            turn.playback_changed.set()
            while not turn.queue.empty():
                turn.queue.get_nowait()
            turn.queued_chars = 0
            if turn.worker and turn.worker is not asyncio.current_task():
                turn.worker.cancel()
                await asyncio.gather(turn.worker, return_exceptions=True)
            with suppress(Exception):
                await binding.send({"type": "speech_skipped", "turn_id": turn.turn_id})
            await self._skip_runtime_speech(binding, turn)
            return
        with suppress(Exception):
            await binding.send({"type": "speech_skipped", "turn_id": None})

    async def _skip_runtime_speech(self, binding: AvatarBinding, turn: AvatarTurn) -> None:
        if binding.runtime:
            with suppress(Exception):
                async with asyncio.timeout(2):
                    await binding.runtime.skip_turn_speech(turn.turn_id)

    async def control(self, binding: AvatarBinding, message: dict[str, Any]) -> None:
        if message.get("type") == "skip_speech":
            await self.skip_speech(binding)
            return
        if message.get("type") in {"interrupt", "barge_in"}:
            await self.interrupt(binding)
            return
        turn = binding.turn
        if message.get("type") == "playback_end" and turn and not turn.interrupted:
            segment_id = message.get("id")
            if segment_id in turn.segments:
                turn.acknowledged.add(segment_id)
                turn.playback_changed.set()
        async with asyncio.timeout(5):
            await binding.runtime.control(message)

    async def emit(self, binding: AvatarBinding, event: MediaEvent) -> None:
        if binding.closed or self.bindings.get(binding.session_id) is not binding:
            return
        kind = event.get("type")
        if kind == "barge_in":
            await self.interrupt(binding)
            return
        if kind == "transcript":
            text = str(event.get("text", "")).strip()
            if text and len(text) <= 32768 and binding.on_transcript:
                # Never run the conversation inline in the STT/media event pump.
                if binding.spoken_task and not binding.spoken_task.done():
                    await self.interrupt(binding)
                    binding.spoken_task.cancel()
                    await asyncio.gather(binding.spoken_task, return_exceptions=True)
                binding.spoken_task = asyncio.create_task(binding.on_transcript(text))
            return
        turn = binding.turn
        turn_id = event.get("turn_id")
        if turn_id is not None:
            if (not turn or turn.turn_id != turn_id or turn.interrupted or turn.speech_failed
                    or turn.speech_skipped):
                return
        elif kind not in {"idle", "status", "config"}:
            raise ValueError("Speech events require a turn_id")
        if kind == "segment":
            if turn.drained or event["id"] in turn.segments:
                raise ValueError("Segments must be unique and precede drained")
            turn.segments[event["id"]] = event["text"]
        elif kind == "drained":
            turn.drained = True
            turn.playback_changed.set()
        elif kind in {"media", "idle", "json", "status", "config"}:
            await binding.send(event["data"], turn=turn if turn_id is not None else None)
        elif kind == "error":
            self._speech_failure(binding, turn)


def get_coordinator(app: Any) -> AvatarCoordinator:
    coordinator = getattr(app.state, "avatar_coordinator", None)
    if not isinstance(coordinator, AvatarCoordinator):
        coordinator = AvatarCoordinator()
        app.state.avatar_coordinator = coordinator
    return coordinator
