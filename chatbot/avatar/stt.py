"""Utterance endpointing/barge-in detection and faster-whisper transcription.

`Endpointer` is the my_avatar microphone state machine, lifted out of its Conversation class
with every threshold unchanged. `Transcriber` keeps Faster-Whisper (base.en, CPU, INT8,
VAD-filtered, beam 1) off the event loop: loading and inference both run in worker threads.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from chatbot.avatar.metrics import elapsed, log
from chatbot.avatar.protocol import MIC_SAMPLE_RATE


@dataclass(frozen=True)
class SpeechStart:
    barge_in: bool  # the user started while a response was in progress


@dataclass(frozen=True)
class Utterance:
    audio: np.ndarray
    ended_at: float
    endpoint_delay: float | None  # VAD-estimated silence before the endpoint (silence endpoints)
    by_silence: bool


Detection = SpeechStart | Utterance


class Endpointer:
    """Energy VAD with an adaptive noise floor.

    While `listening` (idle) the thresholds are sensitive and the noise floor adapts. During a
    response (`listening=False`) they are raised so the avatar's own speech leaking into the
    microphone does not count as the user talking.
    """

    def __init__(self, endpoint_silence: float = 0.55, max_utterance_seconds: int = 30,
                 barge_in_enabled: bool = True):
        self.endpoint_silence = max(0.35, endpoint_silence)
        self.max_utterance_seconds = max(5, max_utterance_seconds)
        self.barge_in_enabled = barge_in_enabled
        self.preroll: deque[np.ndarray] = deque(maxlen=10)
        self.noise_floor = 0.002
        self.reset()

    def reset(self) -> None:
        self.captured: list[np.ndarray] = []
        self.captured_samples = 0
        self.speaking = False
        self.quiet_seconds = 0.0
        self.voice_seconds = 0.0
        self.last_voice_at: float | None = None
        self.speech_started_at: float | None = None
        self.preroll.clear()

    def process(self, data: bytes, now: float, listening: bool) -> Detection | None:
        """Consume one microphone packet (float32le mono 16 kHz)."""
        samples = np.frombuffer(data[: len(data) // 4 * 4], dtype="<f4")
        if not len(samples):
            return None
        if not listening and not self.barge_in_enabled:
            return None
        rms = float(np.sqrt(np.mean(samples * samples)))
        duration = len(samples) / MIC_SAMPLE_RATE
        threshold = max(0.018 if not listening else 0.008,
                        self.noise_floor * (5.0 if not listening else 3.0))
        voiced = rms > threshold
        if voiced:
            self.last_voice_at = now
        if not self.speaking:
            self.preroll.append(samples.copy())
            if voiced:
                self.voice_seconds += duration
            else:
                self.voice_seconds = 0
                if listening:
                    self.noise_floor = 0.98 * self.noise_floor + 0.02 * rms
            if self.voice_seconds >= (0.24 if not listening else 0.16):
                self.speaking = True
                self.captured = list(self.preroll)
                self.captured_samples = sum(len(x) for x in self.captured)
                self.quiet_seconds = 0
                self.speech_started_at = now - self.voice_seconds
                return SpeechStart(barge_in=not listening)
            return None
        self.captured.append(samples.copy())
        self.captured_samples += len(samples)
        self.quiet_seconds = 0 if voiced else self.quiet_seconds + duration
        by_silence = self.quiet_seconds >= self.endpoint_silence
        by_limit = self.captured_samples >= self.max_utterance_seconds * MIC_SAMPLE_RATE
        if not (by_silence or by_limit):
            return None
        audio = np.concatenate(self.captured)
        delay = now - self.last_voice_at if by_silence and self.last_voice_at else None
        self.reset()
        return Utterance(audio=audio, ended_at=now, endpoint_delay=delay, by_silence=by_silence)


class Transcriber:
    """Faster-Whisper, loaded on demand and never run on the event loop."""

    def __init__(self, model: str = "base.en", device: str = "cpu", compute_type: str = "int8",
                 language: str = "en", model_factory: Callable[..., object] | None = None):
        self.model_name, self.device, self.compute_type = model, device, compute_type
        self.language = language
        self._factory = model_factory
        self._model = None
        self._load_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()  # one CPU transcription at a time

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self):
        factory = self._factory
        if factory is None:
            try:
                from faster_whisper import WhisperModel as factory
            except ImportError as exc:  # optional dependency of the chatbot environment
                raise RuntimeError(
                    "faster-whisper is not installed; see chatbot/avatar/requirements-avatar.txt") from exc
        return factory(self.model_name, device=self.device, compute_type=self.compute_type)

    async def load(self) -> None:
        async with self._load_lock:
            if self._model is None:
                log("STT", "Loading Faster-Whisper")
                self._model = await asyncio.to_thread(self._load)
                log("STT", "Ready")

    def _transcribe(self, audio: np.ndarray) -> str:
        segments, _ = self._model.transcribe(
            audio, language=self.language,
            vad_filter=True, beam_size=1, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments).strip()

    async def transcribe(self, audio: np.ndarray) -> str:
        await self.load()
        async with self._run_lock:
            started = time.monotonic()
            text = await asyncio.to_thread(self._transcribe, audio)
            log("STT", f"Transcription completed: {elapsed(started)}")
            return text
