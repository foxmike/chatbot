"""Real-time voice/avatar media engine.

A port of my_avatar's pipeline with the LLM and conversation state removed: the caller owns
the conversation and streams response text in, the engine returns audio, video and telemetry.

    microphone PCM --> Endpointer --> Faster-Whisper --> TranscriptReady
    response text  --> VisibleTextFilter --> AdaptiveChunker --(pull)--> Qwen3-TTS
                       --> trim silence --> PCM queue --> AVTR-1 --> MediaFrame (A, V...)

All stages overlap. The TTS stage *pulls* text sized by `SpeechPlanner` from the playback
lead the browser actually has, and every stage is bounded. See docs/avatar-media-runtime.md.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import re
import subprocess
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from chatbot.avatar.config import MediaConfig
from chatbot.avatar.metrics import chunk_report, elapsed, format_chunk_row, format_summary, log
from chatbot.avatar.protocol import (
    Diagnostic,
    Interrupted,
    MediaEvent,
    MediaFrame,
    TranscriptReady,
    TurnEnded,
    UiMessage,
    UserSpeechStarted,
    pack_audio,
    pack_video,
)
from chatbot.avatar.speech_chunks import (
    AdaptiveChunker,
    SpeechPlanner,
    VisibleTextFilter,
    edge_silence,
    trim_silence,
)
from chatbot.avatar.stt import Endpointer, SpeechStart, Transcriber, Utterance
from chatbot.avatar.workers import Worker, WorkerError, build_workers

MAX_PENDING_CHARS = 600   # LLM backpressure: unsent text held for the next chunk
MAX_AUDIO_CHUNKS = 3
MAX_LEAD_SECONDS = 10.0   # stop synthesizing further ahead; more text keeps better boundaries
FIRST_CHUNK_WAIT = 0.3    # seconds after the first token before a word cut is acceptable
URGENT_LEAD = 0.8         # queued audio below this means playback is about to run dry
AVTR_SAMPLE_RATE = 16000
MAX_PORTRAIT_BYTES = 25 * 1024 * 1024
_SENTENCE_TAIL = re.compile(r"[.!?][\"'’)]*$")
_IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF", b"BM")  # PNG, JPEG, WebP(RIFF), BMP

EventSink = Callable[[MediaEvent], Awaitable[None]]
Resampler = Callable[[np.ndarray, int], np.ndarray]


class MediaError(RuntimeError):
    """The engine cannot do what was asked (not started, no portrait, unknown turn...)."""


@dataclass(frozen=True)
class PortraitResult:
    ok: bool
    portrait_id: str | None = None
    error: str | None = None
    load_ms: float = 0.0
    cached: bool = False     # AVTR already held this portrait: no image processing was needed
    switched: bool = True    # False when it was already the active portrait


@dataclass
class _Turn:
    turn_id: str
    seq: int
    label: str | None
    metrics: dict
    chunker: AdaptiveChunker = field(default_factory=AdaptiveChunker)
    planner: SpeechPlanner = field(default_factory=SpeechPlanner)
    visible: VisibleTextFilter = field(default_factory=VisibleTextFilter)
    text_changed: asyncio.Event = field(default_factory=asyncio.Event)
    pcm_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=MAX_AUDIO_CHUNKS))
    playback_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(MAX_AUDIO_CHUNKS))
    response_text: str = ""
    alive: bool = True            # False once cancelled or finished: feed_text becomes a no-op
    input_closed: bool = False
    segment_ids: set = field(default_factory=set)
    tasks: list = field(default_factory=list)
    runner: asyncio.Task | None = None
    done: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


def default_resampler(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """Qwen's native rate -> AVTR's 16 kHz, with the polyphase filter my_avatar used."""
    try:
        from scipy.signal import resample_poly
    except ImportError as exc:  # optional dependency of the chatbot environment
        raise MediaError("scipy is not installed; see chatbot/avatar/requirements-avatar.txt") from exc
    return np.asarray(resample_poly(audio, AVTR_SAMPLE_RATE, sample_rate), dtype="<f4")


class AvatarMediaEngine:
    """Narrow asynchronous media interface; see docs/avatar-media-runtime.md.

    Typical coordinator flow::

        await engine.start()                      # on demand; idempotent; models stay loaded
        await engine.set_portrait(path)           # may be repeated between turns
        await engine.feed_microphone(pcm_bytes)   # float32le mono 16 kHz, as the browser sends
        # -> sink receives TranscriptReady(text)
        turn = await engine.begin_response()
        async for token in my_llm_stream:         # the engine makes no LLM request itself
            await engine.feed_text(turn, token)   # awaits when the engine is saturated
        await engine.end_response(turn)
        ended = await engine.wait_turn(turn)      # or watch for TurnEnded in the sink
        ...
        await engine.cancel()                     # caller-initiated interruption
        await engine.stop()                       # shutdown: releases both child processes
    """

    def __init__(
        self,
        config: MediaConfig | None = None,
        *,
        sink: EventSink | None = None,
        transcriber: Transcriber | None = None,
        worker_factory: Callable[[MediaConfig], tuple[Worker, Worker]] = build_workers,
        resampler: Resampler = default_resampler,
    ):
        self.config = config or MediaConfig()
        self.sink = sink
        self.transcriber = transcriber or Transcriber(
            self.config.stt_model, self.config.stt_device,
            self.config.stt_compute_type, self.config.stt_language)
        self._worker_factory = worker_factory
        self._resample = resampler
        self.avatar, self.tts = worker_factory(self.config)
        self.endpointer = Endpointer(
            self.config.endpoint_silence, self.config.max_utterance_seconds, self.config.barge_in_enabled)
        self._start_lock = asyncio.Lock()
        self._portrait_lock = asyncio.Lock()
        self._pumps: list[asyncio.Task] = []
        self._started = False
        self.portrait_id: str | None = None
        self._portrait_requests: dict[str, asyncio.Future] = {}
        # Per-segment bookkeeping, keyed by segment id "<turn seq>:<chunk number>".
        self.tts_pending: dict[str, asyncio.Future] = {}
        self.audio_pending: dict[str, tuple] = {}
        self.avatar_audio_received: dict[str, float] = {}
        self.playback_pending: dict[str, asyncio.Future] = {}
        self.segment_metrics: dict[str, dict] = {}
        self.timeline: dict[str, dict] = {}
        self.turn: _Turn | None = None
        self._turns: dict[str, _Turn] = {}
        self._seq = 0
        self._invalid_through = 0   # segments of turns <= this were interrupted: drop their output
        self._utterance = 0
        self._stt_task: asyncio.Task | None = None
        self._utterance_info: dict | None = None   # transcript awaiting begin_response
        self.listening = True
        self.mic_enabled = True
        self.planner: SpeechPlanner | None = None
        self.metrics: dict = {}
        self.interrupt_requested_at: float | None = None
        self.gpu_samples: deque = deque(maxlen=600)
        self._gpu_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ lifecycle

    @property
    def ready(self) -> bool:
        return self._started and self.avatar.alive and self.tts.alive

    async def start(self) -> None:
        """Load Faster-Whisper and start Qwen and AVTR. Idempotent; restarts a dead worker.

        Models stay loaded between turns; only `stop()` releases them.
        """
        async with self._start_lock:
            if self.ready and self.transcriber.loaded:
                return
            log("CONFIG", f"Portrait: {self.config.portrait}; TTS: {self.config.voice_model}; "
                f"voice: {self.config.voice}")
            if self._resample is default_resampler:
                default_resampler(np.zeros(8, "<f4"), 24000)   # fail now, not mid-conversation
            avatar_restarted = not self.avatar.alive
            wanted = [self.transcriber.load()]
            wanted += [w.start() for w in (self.avatar, self.tts) if not w.alive]
            results = await asyncio.gather(*wanted, return_exceptions=True)
            failure = next((r for r in results if isinstance(r, BaseException)), None)
            if failure is not None:
                await self._stop_workers()
                raise failure
            if not self._pumps or any(task.done() for task in self._pumps):
                self._pumps = [asyncio.create_task(self._avatar_messages()),
                               asyncio.create_task(self._tts_messages())]
            info = self.avatar.ready_info or {}
            if info:
                log("AVTR", f"Audio window {info['window']/16000*1000:.0f}ms; "
                    f"frame step {info['step']/16000*1000:.0f}ms; "
                    f"look-ahead {(info['window']-info['step'])/16000*1000:.0f}ms")
            tts = self.tts.ready_info or {}
            if tts:
                log("TTS", f"Loaded {tts['checkpoint']} with {tts['backend']} in {tts['load_ms']:.0f}ms "
                    f"(warm-up {tts['warmup_ms']:.0f}ms); PyTorch reserved {tts['torch_reserved_mib']:.0f}MiB")
            if self.config.gpu_sampling and not self._gpu_task:
                self._gpu_task = asyncio.create_task(self._sample_gpu())
            self._started = True
            if avatar_restarted:
                self.portrait_id = None   # a fresh AVTR has no avatar selected
        if self.config.portrait and self.portrait_id is None:
            result = await self.set_portrait(self.config.portrait)
            if not result.ok:
                log("AVTR", f"Configured portrait rejected: {result.error}")

    async def stop(self) -> None:
        """Cancel everything in flight and release both child processes."""
        await self._interrupt("shutdown", notify=False)
        self._started = False
        if self._gpu_task:
            self._gpu_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._gpu_task
            self._gpu_task = None
        await self._stop_workers()
        for task in self._pumps:
            task.cancel()
        await asyncio.gather(*self._pumps, return_exceptions=True)
        self._pumps = []
        self.portrait_id = None

    async def _stop_workers(self) -> None:
        await asyncio.gather(self.avatar.stop(), self.tts.stop(), return_exceptions=True)

    async def _ensure_ready(self) -> None:
        if not self.ready:
            await self.start()

    @property
    def busy(self) -> bool:
        """A turn, a pending transcription or an unanswered transcript is in flight."""
        return (self.turn is not None or self._utterance_info is not None
                or (self._stt_task is not None and not self._stt_task.done()))

    def status(self) -> dict[str, Any]:
        return {"ready": self.ready, "portrait_id": self.portrait_id, "listening": self.listening,
                "active_turn": self.turn.turn_id if self.turn else None,
                "avatar_alive": self.avatar.alive, "tts_alive": self.tts.alive,
                "dropped_frames": self.avatar.dropped_frames, "stt_loaded": self.transcriber.loaded}

    async def _sample_gpu(self) -> None:
        while True:
            try:
                def query():
                    result = subprocess.run(
                        ["nvidia-smi", "--query-gpu=memory.used,utilization.gpu",
                         "--format=csv,noheader,nounits"], capture_output=True,
                        text=True, timeout=2, check=True)
                    memory, utilization = result.stdout.splitlines()[0].split(",")
                    return int(memory.strip()), int(utilization.strip())
                memory, utilization = await asyncio.to_thread(query)
                if not self.gpu_samples:
                    log("GPU", f"Models loaded: total device memory {memory}MiB; utilization {utilization}%")
                self.gpu_samples.append((time.monotonic(), memory, utilization))
            except (FileNotFoundError, subprocess.SubprocessError, ValueError):
                return
            await asyncio.sleep(0.5)

    # ------------------------------------------------------------------ output

    async def _emit(self, event: MediaEvent) -> None:
        if self.sink is None:
            return
        try:
            await self.sink(event)
        except (ConnectionResetError, RuntimeError):
            pass   # the browser went away; the session layer handles reconnection
        except Exception as exc:  # a faulty consumer must not kill the media pumps
            log("SINK", f"Event sink failed: {exc!r}")

    async def _status(self, value: str) -> None:
        await self._emit(UiMessage({"type": "status", "value": value}))

    async def _diagnostic(self, name: str, **data: Any) -> None:
        await self._emit(Diagnostic(name, data))

    def mark(self, segment_id: str, **fields) -> None:
        """Record monotonic-clock stage times for one speech chunk."""
        self.timeline.setdefault(segment_id, {}).update(fields)

    def _is_stale(self, segment_id: str | None) -> bool:
        """True for output of a turn that was interrupted (ids are "<seq>:<n>")."""
        if not segment_id:
            return False
        try:
            return int(segment_id.split(":", 1)[0]) <= self._invalid_through
        except ValueError:
            return False

    # ------------------------------------------------------------------ portraits

    @staticmethod
    def check_portrait_file(path: str | Path) -> Path:
        """Cheap pre-flight checks; deep validation (decoding, face detection) happens in AVTR."""
        path = Path(path)
        if not path.is_file():
            raise MediaError(f"portrait not found: {path}")
        size = path.stat().st_size
        if not 0 < size <= MAX_PORTRAIT_BYTES:
            raise MediaError(f"portrait size out of range ({size} bytes): {path}")
        with path.open("rb") as handle:
            head = handle.read(8)
        if not head.startswith(_IMAGE_MAGIC):
            raise MediaError(f"not a PNG, JPEG, WebP or BMP image: {path}")
        return path

    @staticmethod
    def portrait_key(path: Path) -> str:
        stat = path.resolve().stat()
        raw = f"{path.resolve()}:{stat.st_mtime_ns}:{stat.st_size}".encode()
        return hashlib.sha1(raw).hexdigest()[:16]

    async def set_portrait(self, path: str | Path, portrait_id: str | None = None,
                           timeout: float = 120.0) -> PortraitResult:
        """Select the avatar's face from an image file. Never raises for a bad image.

        `portrait_id` identifies the picture for AVTR's per-process avatar cache (default: a
        hash of path, mtime and size). Switching reuses the loaded TensorRT engines: only
        AVTR's ONNX face models run, once per new portrait. Refused while a response is active
        and, on any failure, the previous portrait stays selected.
        """
        try:
            checked = self.check_portrait_file(path)
            key = portrait_id or self.portrait_key(checked)
            await self._ensure_ready()
        except (MediaError, OSError, WorkerError) as exc:
            return PortraitResult(False, portrait_id, str(exc))
        async with self._portrait_lock:
            if self.turn is not None:
                return PortraitResult(False, key, "a response is in progress; cancel it first")
            if key == self.portrait_id:
                return PortraitResult(True, key, switched=False, cached=True)
            request_id = uuid.uuid4().hex
            future = asyncio.get_running_loop().create_future()
            self._portrait_requests[request_id] = future
            started = time.monotonic()
            try:
                await self.avatar.send({"type": "portrait", "request_id": request_id,
                                        "portrait_id": key, "path": str(checked.resolve())})
                reply = await asyncio.wait_for(future, timeout)
            except asyncio.TimeoutError:
                return PortraitResult(False, key, f"AVTR did not load the portrait within {timeout:.0f}s")
            except (WorkerError, RuntimeError, OSError) as exc:
                return PortraitResult(False, key, str(exc))
            finally:
                self._portrait_requests.pop(request_id, None)
            if reply.get("type") == "portrait_error":
                log("AVTR", f"Portrait rejected: {reply.get('error')}")
                return PortraitResult(False, key, reply.get("error", "portrait rejected"))
            self.portrait_id = key
            log("AVTR", f"Portrait {key} ready in {elapsed(started)} "
                f"({'cached' if reply.get('cached') else 'loaded'})")
            await self._diagnostic("portrait", portrait_id=key, load_ms=reply.get("load_ms", 0.0),
                                   cached=bool(reply.get("cached")))
            return PortraitResult(True, key, load_ms=reply.get("load_ms", 0.0), cached=bool(reply.get("cached")))

    # ------------------------------------------------------------------ worker pumps

    async def _avatar_messages(self) -> None:
        count = 0
        last = time.monotonic()
        while True:
            message = await self.avatar.messages.get()
            kind = message.get("type")
            if kind == "frame":
                now = time.monotonic()
                count += 1
                if now - last > 0.4 and count > 1:
                    log("AVTR", f"Frame delivery interruption: {elapsed(last, now)}")
                last = now
                segment_id = message.get("id")
                if self._is_stale(segment_id):
                    continue
                if message.get("kind") == "speech" and message.get("first") and segment_id in self.audio_pending:
                    audio, sr, metrics = self.audio_pending.pop(segment_id)
                    metrics.setdefault("first_video_delivered", now)
                    metrics.setdefault("first_video_generated", message["generated_at"])
                    self.mark(segment_id, first_frame=message["generated_at"], browser_sent=now)
                    receipt = self.avatar_audio_received.pop(segment_id, None)
                    if receipt:
                        log("AVTR", f"First frame generated: {elapsed(receipt, message['generated_at'])} after audio received")
                    log("AVTR", f"First synchronized frame delivered: {elapsed(metrics['speech_end'], now)} after speech end")
                    await self._emit(MediaFrame("audio", segment_id, pack_audio(segment_id, sr, audio.tobytes())))
                pts = float(message.get("pts", 0))
                await self._emit(MediaFrame("video", segment_id or None,
                                            pack_video(segment_id, pts, base64.b64decode(message["jpeg"])), pts))
            elif kind == "fps":
                log("AVTR", f"Animation FPS: {message['value']:.1f}; render compute {message.get('step_ms', 0):.0f}ms per 200ms step")
                await self._diagnostic("avtr_fps", fps=message["value"], step_ms=message.get("step_ms", 0))
            elif kind == "audio_received":
                if self._is_stale(message["id"]):
                    continue
                self.avatar_audio_received[message["id"]] = message["at"]
                self.mark(message["id"], avtr_received=message["at"])
                log("AVTR", f"Audio received: {message['duration']:.2f}s")
            elif kind == "interrupted":
                if self.interrupt_requested_at:
                    log("AVTR", f"Speech queue cleared: {elapsed(self.interrupt_requested_at, message['at'])} "
                        "after interruption request")
                    await self._diagnostic("avtr_interrupted", ms=1000 * (message["at"] - self.interrupt_requested_at))
            elif kind in ("portrait_loaded", "portrait_error"):
                future = self._portrait_requests.get(message.get("request_id"))
                if future and not future.done():
                    future.set_result(message)
            elif kind == "error":
                log("AVTR", f"Error: {message['error']}")
                failure = RuntimeError(message["error"])
                for future in (*self.playback_pending.values(), *self._portrait_requests.values()):
                    if not future.done():
                        future.set_exception(failure)
                await self._status("Avatar error")

    async def _tts_messages(self) -> None:
        while True:
            message = await self.tts.messages.get()
            kind = message.get("type")
            if kind in ("audio", "error"):
                future = self.tts_pending.pop(message.get("id"), None)
                if kind == "error" and message.get("id") is None:
                    log("TTS", f"Worker error: {message['error']}")
                    for pending in self.tts_pending.values():
                        if not pending.done():
                            pending.set_exception(RuntimeError(message["error"]))
                if future and not future.done():
                    if kind == "error":
                        future.set_exception(RuntimeError(message["error"]))
                    else:
                        future.set_result(message)
                # A result for an interrupted segment finds no future here and is discarded.

    # ------------------------------------------------------------------ microphone / STT

    def set_microphone_enabled(self, enabled: bool) -> None:
        self.mic_enabled = enabled
        if not enabled:
            self.endpointer.reset()
        log("MIC", "Enabled" if enabled else "Disabled")

    async def feed_microphone(self, data: bytes) -> None:
        """One packet of float32le mono 16 kHz PCM (the payload after the browser's b"M" tag)."""
        if not self.mic_enabled or not self._started or len(data) < 4:
            return
        if not self.listening and not self.config.barge_in_enabled:
            return
        if self.listening and self.avatar.alive:
            # The avatar's idle animation reacts to what it hears.
            with contextlib.suppress(WorkerError, ConnectionError, OSError):
                await self.avatar.send({"type": "listen", "audio": base64.b64encode(data).decode("ascii")})
        detected = self.endpointer.process(data, time.monotonic(), self.listening)
        if isinstance(detected, SpeechStart):
            if detected.barge_in:
                await self._interrupt("barge-in")
            log("MIC", "Speech detected")
            await self._emit(UserSpeechStarted(detected.barge_in))
            await self._status("Listening to you…")
        elif isinstance(detected, Utterance):
            self.listening = False
            if detected.by_silence:
                log("MIC", f"End of utterance; capture {len(detected.audio)/16000:.2f}s; "
                    f"VAD-estimated endpoint delay {detected.endpoint_delay*1000:.0f}ms"
                    if detected.endpoint_delay is not None else "End of utterance")
            else:
                log("MIC", f"Maximum capture duration reached at {len(detected.audio)/16000:.2f}s")
            self._stt_task = asyncio.create_task(self._transcribe(detected))

    async def _transcribe(self, utterance: Utterance) -> None:
        self._utterance += 1
        number = self._utterance
        try:
            await self._status("Thinking…")
            log("STT", "Transcription started")
            started = time.monotonic()
            text = await self.transcriber.transcribe(utterance.audio)
            finished = time.monotonic()
            log("USER", text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log("ERROR", f"Transcription failed: {exc}")
            self.listening = True
            await self._status(f"Error: {exc}")
            return
        info = {"speech_end": utterance.ended_at, "endpoint_delay": utterance.endpoint_delay,
                "stt_start": started, "stt_end": finished}
        if not text:
            log("STT", "No speech recognized")
            self.listening = True
            await self._status("Listening to you…" if self.endpointer.speaking else "Listening")
        else:
            self._utterance_info = info
        await self._emit(TranscriptReady(number, text, len(utterance.audio) / 16000, utterance.endpoint_delay,
                                         1000 * (finished - started), utterance.ended_at))

    # ------------------------------------------------------------------ response turns

    async def begin_response(self, label: str | None = None) -> str:
        """Open a response stream; returns the engine's turn id for the calls below.

        Starts the engine on demand and requires a portrait. A still-active previous turn is
        cancelled first. `label` is an opaque caller tag echoed in diagnostics.
        """
        await self._ensure_ready()
        if self.portrait_id is None:
            raise MediaError("no portrait selected; call set_portrait() first")
        if self.turn is not None:
            await self.cancel(self.turn.turn_id)
        self._seq += 1
        now = time.monotonic()
        info, self._utterance_info = self._utterance_info or {}, None
        if now - info.get("stt_end", now) > 120:
            info = {}   # a stale transcript is not this response's origin
        metrics = {"turn": self._seq, "speech_end": info.get("speech_end", now),
                   "endpoint_delay": info.get("endpoint_delay"), "chunks": 0}
        for key in ("stt_start", "stt_end"):
            if key in info:
                metrics[key] = info[key]
        turn = _Turn(str(self._seq), self._seq, label, metrics)
        self.turn = turn
        self._turns[turn.turn_id] = turn
        if len(self._turns) > 20:   # finished turns are only kept so wait_turn() can be late
            for old in [k for k, t in self._turns.items() if t.done.done()][:-5]:
                del self._turns[old]
        if len(self.timeline) > 100:
            self.timeline.clear()
        self.metrics = metrics
        self.planner = turn.planner
        self.listening = False
        await self._status("Responding…")
        turn.runner = asyncio.create_task(self._run_turn(turn))
        turn.runner.add_done_callback(lambda _: self._settle(turn))
        return turn.turn_id

    @staticmethod
    def _settle(turn: _Turn) -> None:
        """Resolve wait_turn() even if the runner was cancelled before it ever started."""
        if not turn.done.done():
            turn.done.set_result(TurnEnded(turn.turn_id, "cancelled", turn.response_text.strip()))

    def _live_turn(self, turn_id: str) -> _Turn | None:
        turn = self._turns.get(turn_id)
        return turn if turn is not None and turn.alive else None

    async def feed_text(self, turn_id: str, text: str, backpressure: bool = True) -> bool:
        """Append incremental response text. Returns False when the turn is no longer live.

        Reasoning tags (<think> etc.) are filtered out. With `backpressure`, awaits while more
        than MAX_PENDING_CHARS of unspoken text is already waiting for synthesis; a caller that
        bounds its own queue (the coordinator) passes False so the LLM is never throttled.
        """
        turn = self._live_turn(turn_id)
        if turn is None or turn.input_closed:
            return False
        visible = turn.visible.feed(text)
        if visible:
            turn.metrics.setdefault("first_token", time.monotonic())
        turn.response_text += visible
        turn.chunker.feed(visible, time.monotonic())
        turn.text_changed.set()
        while backpressure and len(turn.chunker.pending) > MAX_PENDING_CHARS and turn.alive:
            await asyncio.sleep(0.05)   # bounded: TTS drains text as it frees up
        return turn.alive

    async def end_response(self, turn_id: str) -> None:
        """No more text is coming: speak what remains and finish the turn."""
        turn = self._live_turn(turn_id)
        if turn is None or turn.input_closed:
            return
        turn.input_closed = True
        final = turn.visible.finish()
        turn.response_text += final
        turn.chunker.feed(final, time.monotonic())
        turn.metrics["llm_end"] = time.monotonic()
        turn.chunker.finish()
        turn.text_changed.set()

    async def wait_turn(self, turn_id: str) -> TurnEnded:
        turn = self._turns.get(turn_id)
        if turn is None:
            raise MediaError(f"unknown turn {turn_id}")
        return await asyncio.shield(turn.done)

    async def cancel(self, turn_id: str | None = None, *, reason: str = "cancel") -> None:
        """Interrupt: clear queues and playback, discard stale synthesis, return AVTR to idle.

        `turn_id=None` cancels whatever is active (including a pending transcription). Qwen is
        never restarted; an inference already running finishes and its result is dropped.
        """
        if turn_id is not None and (self.turn is None or self.turn.turn_id != turn_id):
            return
        turn = self.turn
        await self._interrupt(reason)
        if turn is not None and turn.runner is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait({turn.runner}, timeout=2)
        await self._status("Listening to you…" if self.endpointer.speaking else "Listening")

    async def _interrupt(self, reason: str, notify: bool = True) -> None:
        turn = self.turn
        in_flight = self.busy
        self.interrupt_requested_at = time.monotonic()
        log("MIC", f"Interruption ({reason}); stopping playback and queued speech")
        self._invalid_through = self._seq
        self._utterance_info = None
        if notify:
            await self._emit(UiMessage({"type": "interrupt"}))
        with contextlib.suppress(WorkerError, ConnectionError, OSError):
            await self.avatar.send({"type": "interrupt"})
        for future in (*self.playback_pending.values(), *self.tts_pending.values()):
            if not future.done():
                future.cancel()
        self.playback_pending.clear()
        self.segment_metrics.clear()
        self.tts_pending.clear()
        self.audio_pending.clear()
        self.avatar_audio_received.clear()
        if self._stt_task and not self._stt_task.done():
            self._stt_task.cancel()
        self._stt_task = None
        if turn is not None:
            turn.alive = False
            self.turn = None
            if turn.runner and not turn.runner.done():
                turn.runner.cancel()
        self.listening = True
        log("TTS", "Current inference may finish internally; its result will be discarded")
        if notify and reason not in ("cancel",) and in_flight:
            await self._emit(Interrupted(reason, turn.turn_id if turn else None))

    async def _run_turn(self, turn: _Turn) -> None:
        """Run synthesis and audio dispatch for one turn until the text is spoken."""
        status, error = "completed", None
        try:
            synthesizer = asyncio.create_task(self._synthesize_chunks(turn))
            dispatcher = asyncio.create_task(self._dispatch_audio(turn))
            turn.tasks = [synthesizer, dispatcher]
            done, _ = await asyncio.wait(turn.tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                if task.cancelled():
                    raise RuntimeError("A pipeline stage was cancelled")
                if exc := task.exception():
                    raise exc
            await asyncio.gather(*turn.tasks)
            log("ASSISTANT", turn.response_text.strip())
        except asyncio.CancelledError:
            status = "cancelled"
            turn.metrics["interrupted"] = True
            log("TURN", f"Turn {turn.seq} cancelled")
            raise
        except Exception as exc:
            status, error = "failed", str(exc)
            log("ERROR", str(exc))
            await self._status(f"Error: {exc}")
        finally:
            turn.alive = False
            for task in turn.tasks:
                if not task.done():
                    task.cancel()
            if turn.tasks:
                await asyncio.gather(*turn.tasks, return_exceptions=True)
            for segment_id in turn.segment_ids:
                future = self.playback_pending.pop(segment_id, None)
                if future and not future.done():
                    future.cancel()
                self.segment_metrics.pop(segment_id, None)
            summary = format_summary(turn.metrics, self.gpu_samples)
            log("TOTAL", summary)
            rows = chunk_report(self.timeline, str(turn.seq), turn.metrics["speech_end"])
            for row in rows:
                log("CHUNK", format_chunk_row(row))
            await self._diagnostic("turn_summary", turn_id=turn.turn_id, label=turn.label, summary=summary,
                                   status=status, metrics={k: v for k, v in turn.metrics.items()
                                                           if isinstance(v, (int, float, bool, type(None)))})
            await self._diagnostic("chunk_report", turn_id=turn.turn_id, rows=rows)
            if self.turn is turn:   # not already superseded by an interruption
                self.turn = None
                self.listening = True
                await self._status("Listening to you…" if self.endpointer.speaking else "Listening")
            ended = TurnEnded(turn.turn_id, status, turn.response_text.strip(), error)
            if not turn.done.done():
                turn.done.set_result(ended)
            await self._emit(ended)

    async def _next_chunk(self, turn: _Turn, number: int):
        """Pull the next speech chunk, sized for the audio queued right now."""
        chunker, planner, metrics = turn.chunker, turn.planner, turn.metrics
        while True:
            turn.text_changed.clear()
            now = time.monotonic()
            lead = planner.lead(now)
            if number and lead > MAX_LEAD_SECONDS:
                await asyncio.sleep(0.05)
                continue
            budget = planner.budget(now, number)
            urgent = (chunker.age(now) >= FIRST_CHUNK_WAIT) if not number else lead < URGENT_LEAD
            chunk = chunker.take(now, budget, urgent)
            if chunk:
                metrics.setdefault("first_speech_chunk", chunk.at)
                if chunk.reason in ("first sentence", "sentence"):
                    metrics.setdefault("first_sentence", chunk.at)
                metrics["chunks"] += 1
                log("PLAN", f"Chunk {number + 1}: {len(chunk.text)} chars ({chunk.reason}); budget {budget}; "
                    f"queued audio {lead:.2f}s; synthesis rate {planner.rate:.2f}; "
                    f"audio/char {planner.audio_per_char*1000:.0f}ms; text pending {len(chunker.pending)}")
                self.mark(f"{turn.seq}:{number + 1}", text_started=chunk.started_at,
                          text_ready=chunk.ready_at, text_emit=chunk.at, chars=len(chunk.text),
                          reason=chunk.reason)
                await self._diagnostic("chunk_planned", turn_id=turn.turn_id, number=number + 1,
                                       text=chunk.text, chars=len(chunk.text), reason=chunk.reason, budget=budget,
                                       queued_audio=lead, synthesis_rate=planner.rate,
                                       audio_per_char=planner.audio_per_char, pending=len(chunker.pending))
                return chunk
            if chunker.done:
                return None
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(turn.text_changed.wait(), 0.05)

    async def _synthesize(self, text: str, segment_id: str, metrics: dict):
        log("TTS", f"Chunk {segment_id} started; text: {text}")
        start = time.monotonic()
        self.mark(segment_id, synth_start=start)
        future = asyncio.get_running_loop().create_future()
        self.tts_pending[segment_id] = future
        await self.tts.send({"type": "speak", "id": segment_id, "text": text,
                             "voice": self.config.voice, "mode": self.config.tts_mode})
        try:
            result = await asyncio.wait_for(future, timeout=120)
        finally:
            self.tts_pending.pop(segment_id, None)
        now = time.monotonic()
        metrics.setdefault("first_audio", now)
        self.mark(segment_id, synth_end=now, generation=result["generation"])
        raw = np.frombuffer(base64.b64decode(result["audio"]), dtype="<f4")
        generation, duration = result["generation"], result["duration"]
        metrics["tts_total"] = metrics.get("tts_total", 0) + generation
        metrics["audio_duration"] = metrics.get("audio_duration", 0) + duration
        sentence_end = bool(_SENTENCE_TAIL.search(text.rstrip()))
        audio = trim_silence(raw, result["sr"], sentence_end)
        raw_lead, raw_trail = edge_silence(raw, result["sr"])
        self.mark(segment_id, duration=len(audio) / result["sr"], raw_duration=duration,
                  retried=bool(result.get("retried")), raw_lead_sil=raw_lead, raw_trail_sil=raw_trail,
                  sentence_end=sentence_end)
        if result.get("retried"):
            log("TTS", f"Chunk {segment_id} hit the length guard (runaway generation); regenerated once")
        log("TTS", f"Chunk {segment_id} PCM ready: {elapsed(start)}; synthesis {generation*1000:.0f}ms; "
            f"audio {duration:.2f}s (trimmed to {len(audio)/result['sr']:.2f}s); RTF {generation/duration:.2f}")
        return audio, result["sr"]

    async def _synthesize_chunks(self, turn: _Turn) -> None:
        planner, pcm_queue = turn.planner, turn.pcm_queue
        number = 0
        cancelled = False
        try:
            idle_since = time.monotonic()
            while (chunk := await self._next_chunk(turn, number)) is not None:
                number += 1
                segment_id = f"{turn.seq}:{number}"
                turn.segment_ids.add(segment_id)
                self.mark(segment_id, tts_idle_since=idle_since, tts_got=time.monotonic())
                audio_chunk, sr = await self._synthesize(chunk.text, segment_id, turn.metrics)
                if not turn.alive:
                    return   # interrupted while Qwen was busy: drop the stale waveform
                info = self.timeline.get(segment_id, {})
                if _SENTENCE_TAIL.search(chunk.text):
                    pause = planner.extra_pause(time.monotonic())
                    audio_chunk = np.concatenate((audio_chunk, np.zeros(int(pause * sr), "<f4")))
                    self.mark(segment_id, extra_pause=pause)
                lead_sil, trail_sil = edge_silence(audio_chunk, sr)
                self.mark(segment_id, lead_sil=lead_sil, trail_sil=trail_sil)
                duration = len(audio_chunk) / sr
                if not info.get("retried"):  # runaway timings would mislead the planner
                    planner.observe(len(chunk.text), info.get("raw_duration", duration),
                                    info.get("generation", 0.0))
                self.mark(segment_id, est_end=planner.audio_ready(segment_id, time.monotonic(), duration))
                if pcm_queue.full():
                    log("QUEUE", f"PCM backpressure at depth {pcm_queue.qsize()}/{MAX_AUDIO_CHUNKS}")
                await pcm_queue.put((segment_id, audio_chunk, sr))
                idle_since = time.monotonic()
                self.mark(segment_id, pcm_queued=idle_since)
                log("QUEUE", f"PCM chunk {segment_id} queued; depth {pcm_queue.qsize()}/{MAX_AUDIO_CHUNKS}")
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            if not cancelled:
                await pcm_queue.put(None)

    async def _dispatch_audio(self, turn: _Turn) -> None:
        playback = []
        while (item := await turn.pcm_queue.get()) is not None:
            segment_id, audio_chunk, sr = item
            self.mark(segment_id, pcm_got=time.monotonic())
            if turn.playback_slots.locked():
                log("QUEUE", f"AVTR/playback backpressure; {MAX_AUDIO_CHUNKS} chunks in flight")
            await turn.playback_slots.acquire()
            if not turn.alive:
                return
            self.mark(segment_id, slot_acquired=time.monotonic())
            future = asyncio.get_running_loop().create_future()
            future.add_done_callback(lambda _: turn.playback_slots.release())
            self.playback_pending[segment_id] = future
            self.segment_metrics[segment_id] = turn.metrics
            playback.append(future)
            await self._send_speech_audio(segment_id, audio_chunk, sr, turn.metrics)
        if playback:
            await asyncio.wait_for(asyncio.gather(*playback), timeout=180)

    async def _send_speech_audio(self, segment_id: str, audio: np.ndarray, sr: int, metrics: dict) -> None:
        started = time.monotonic()
        mono16 = await asyncio.to_thread(self._resample, audio, sr)
        mono16 = np.asarray(mono16, dtype="<f4")
        self.audio_pending[segment_id] = (audio, sr, metrics)
        await self.avatar.send({"type": "speak", "id": segment_id,
                                "audio": base64.b64encode(mono16.tobytes()).decode("ascii")})
        self.mark(segment_id, avatar_sent=time.monotonic())
        log("AVTR", f"Sent chunk {segment_id}, {len(mono16)/16000:.2f}s; resample/IPC {elapsed(started)}")

    # ------------------------------------------------------------------ browser feedback

    async def handle_client_message(self, data: dict) -> None:
        """A JSON message from the browser (timing feedback and the microphone switch)."""
        kind = data.get("type")
        if kind == "mic":
            self.set_microphone_enabled(bool(data["enabled"]))
        elif kind == "segment_scheduled":
            now = time.monotonic()
            offset = now - data["ctx_now"]  # browser audio clock -> server monotonic clock
            if self.planner:
                self.planner.correct(data["id"], data["end"] + offset)
            self.mark(data["id"], browser_received=now, b_begin=data["begin"] + offset,
                      b_end=data["end"] + offset, b_prev_end=data["prev_end"] + offset, b_scheduled_at=now)
            await self._diagnostic("segment_scheduled", id=data["id"], begin=data["begin"] + offset,
                                   end=data["end"] + offset, prev_end=data["prev_end"] + offset)
        elif kind == "playback_start":
            now = time.monotonic()
            self.mark(data["id"], playback_start=now)
            metrics = self.segment_metrics.get(data.get("id", ""))
            if metrics:
                metrics.setdefault("first_playback", now)
            log("AUDIO", f"Playback clock started: {data.get('id')}; "
                f"initial buffer {data.get('buffer_delay_ms', 0):.0f}ms; "
                f"reported output latency {data.get('output_latency_ms', 0):.0f}ms; "
                f"gap {data.get('gap_ms', 0):.0f}ms")
            await self._diagnostic("playback_start", id=data.get("id"),
                                   buffer_delay_ms=data.get("buffer_delay_ms", 0),
                                   output_latency_ms=data.get("output_latency_ms", 0),
                                   gap_ms=data.get("gap_ms", 0))
        elif kind == "playback_end":
            self.mark(data["id"], playback_end=time.monotonic())
            log("AUDIO", f"Playback completed: {data['id']}")
            future = self.playback_pending.pop(data["id"], None)
            self.segment_metrics.pop(data["id"], None)
            if future and not future.done():
                future.set_result(True)
        elif kind == "video_displayed":
            self.mark(data["id"], drift_ms=data.get("drift_ms", 0.0))
            metrics = self.segment_metrics.get(data.get("id", ""))
            if metrics and "first_video_displayed" not in metrics:
                metrics["first_video_displayed"] = time.monotonic()
                log("AVTR", f"First frame displayed: {elapsed(metrics['speech_end'], metrics['first_video_displayed'])} "
                    f"after speech end; audio-clock drift {data.get('drift_ms', 0):.0f}ms")
                await self._diagnostic("first_video_displayed", id=data["id"], drift_ms=data.get("drift_ms", 0.0))
        elif kind == "buffer_stats":
            if self.playback_pending:
                log("BUFFER", f"browser audio {max(0.0, float(data.get('audio_seconds', 0))):.2f}s; "
                    f"video {data.get('video_frames', 0)} frames; dropped {data.get('dropped', 0)}; "
                    f"PCM bounded at {MAX_AUDIO_CHUNKS}")
                await self._diagnostic("buffer_stats", audio_seconds=float(data.get("audio_seconds", 0)),
                                       video_frames=data.get("video_frames", 0), dropped=data.get("dropped", 0))
        elif kind == "interruption_ack":
            ack = elapsed(self.interrupt_requested_at) if self.interrupt_requested_at else "unknown"
            log("AUDIO", f"Browser acknowledged interruption after {ack}; "
                f"fade scheduled for {data.get('fade_ms', 0):.0f}ms")

    async def handle_client_frame(self, data: bytes) -> None:
        """A binary frame from the browser: microphone audio tagged b"M"."""
        if data.startswith(b"M"):
            await self.feed_microphone(data[1:])
