"""Reference-audio validation, normalization and WAV helpers for the Voice Library.

Uploaded recordings are decoded entirely in memory with PyAV (already a faster-whisper
dependency). Only an allow-list of plain audio containers is accepted: playlist/stream
formats (HLS, DASH, concat, SDP, ...) that could make the demuxer open other files or URLs
are refused, and nothing in an upload's metadata is ever used as a path or URL.

The canonical reference is mono 24 kHz 16-bit PCM WAV (Qwen3-TTS's codec rate).
"""

from __future__ import annotations

import hashlib
import io
import wave
from dataclasses import dataclass
from pathlib import PurePath

import numpy as np

REFERENCE_SAMPLE_RATE = 24000
STT_SAMPLE_RATE = 16000
ALLOWED_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".oga", ".opus", ".webm", ".m4a", ".mp4", ".aac"}
# PyAV/FFmpeg demuxer names (a demuxer may report several, comma-separated).
ALLOWED_CONTAINERS = {"wav", "mp3", "flac", "ogg", "matroska", "webm", "mov", "mp4", "m4a", "aac"}
MIN_SOURCE_RATE, MAX_SOURCE_RATE = 8000, 192000
MAX_CHANNELS = 2
TARGET_PEAK = 0.89          # peak-normalized level of the canonical reference
MIN_PEAK, MIN_RMS = 0.02, 0.003   # below this the recording is effectively silent


class AudioValidationError(ValueError):
    """The upload is not usable as a voice reference; the message says why."""


@dataclass(frozen=True)
class ReferenceAudio:
    samples: np.ndarray          # float32 mono, REFERENCE_SAMPLE_RATE
    duration: float
    source_format: str
    source_sample_rate: int
    source_channels: int

    @property
    def sample_rate(self) -> int:
        return REFERENCE_SAMPLE_RATE


def _container_ok(name: str) -> bool:
    return any(part.strip() in ALLOWED_CONTAINERS for part in (name or "").split(","))


def decode_reference(data: bytes, filename: str | None, *, max_bytes: int,
                     min_seconds: float, max_seconds: float) -> ReferenceAudio:
    """Validate an uploaded recording and normalize it to the canonical reference format."""
    if not data:
        raise AudioValidationError("The uploaded file is empty")
    if len(data) > max_bytes:
        raise AudioValidationError(f"The file is larger than {max_bytes // (1024 * 1024)} MB")
    suffix = PurePath(filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise AudioValidationError(
            f"Unsupported file type {suffix or '(none)'}; use one of {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    import av  # local: keeps `import chatbot.voices` light

    try:
        container = av.open(io.BytesIO(data), mode="r")
    except Exception as exc:  # noqa: BLE001 - any demuxer failure means "not audio we can read"
        raise AudioValidationError("The file is not a readable audio recording") from exc
    try:
        if not _container_ok(container.format.name):
            raise AudioValidationError(f"Unsupported audio container ({container.format.name})")
        if not container.streams.audio:
            raise AudioValidationError("The file contains no audio")
        stream = container.streams.audio[0]
        context = stream.codec_context
        rate, channels = int(context.sample_rate or 0), int(context.channels or 0)
        if not MIN_SOURCE_RATE <= rate <= MAX_SOURCE_RATE:
            raise AudioValidationError(f"Unsupported sample rate {rate} Hz")
        if not 1 <= channels <= MAX_CHANNELS:
            raise AudioValidationError(f"Unsupported channel count {channels}; use mono or stereo")
        resampler = av.AudioResampler(format="flt", layout="mono", rate=REFERENCE_SAMPLE_RATE)
        limit = int((max_seconds + 1.0) * REFERENCE_SAMPLE_RATE)   # bounds decompression work
        parts, total = [], 0
        try:
            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    chunk = out.to_ndarray().reshape(-1).astype(np.float32, copy=False)
                    parts.append(chunk)
                    total += len(chunk)
                if total > limit:
                    break
            if total <= limit:
                for out in resampler.resample(None):
                    parts.append(out.to_ndarray().reshape(-1).astype(np.float32, copy=False))
        except AudioValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AudioValidationError("The audio could not be decoded") from exc
        format_name = container.format.name
    finally:
        container.close()

    samples = np.concatenate(parts) if parts else np.zeros(0, np.float32)
    duration = len(samples) / REFERENCE_SAMPLE_RATE
    if duration > max_seconds:
        raise AudioValidationError(f"The recording is longer than {max_seconds:.0f} seconds; trim it")
    if duration < min_seconds:
        raise AudioValidationError(f"The recording is shorter than {min_seconds:.0f} seconds")
    if not np.all(np.isfinite(samples)):
        raise AudioValidationError("The audio contains invalid samples")
    samples = normalize(samples)
    return ReferenceAudio(samples, duration, format_name, rate, channels)


def normalize(samples: np.ndarray) -> np.ndarray:
    """Peak-normalize; refuse silence. Never changes timing or content."""
    samples = np.asarray(samples, dtype=np.float32)
    peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
    rms = float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0
    if peak < MIN_PEAK or rms < MIN_RMS:
        raise AudioValidationError("The recording is silent or too quiet")
    return (samples * (TARGET_PEAK / peak)).astype(np.float32)


def encode_wav(samples: np.ndarray, sample_rate: int) -> bytes:
    """16-bit PCM mono WAV bytes."""
    pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(int(sample_rate))
        wav.writeframes((pcm * 32767.0).round().astype("<i2").tobytes())
    return out.getvalue()


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """(float32 mono samples, sample rate) of a WAV written by `encode_wav`."""
    with wave.open(io.BytesIO(data), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise AudioValidationError("Reference WAV must be 16-bit PCM")
        frames = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32767.0
        channels, rate = wav.getnchannels(), wav.getframerate()
    if channels > 1:
        frames = frames.reshape(-1, channels).mean(axis=1)
    return frames, rate


def wav_duration(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as wav:
        return wav.getnframes() / float(wav.getframerate())


def resample(samples: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    if from_rate == to_rate:
        return np.asarray(samples, dtype=np.float32)
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(int(from_rate), int(to_rate))
    return resample_poly(samples, to_rate // g, from_rate // g).astype(np.float32)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def voice_fingerprint(reference_sha256: str, transcript: str) -> str:
    """Identity of a reference for prompt caching: the audio bytes and the transcript."""
    return hashlib.sha256(f"{reference_sha256}\n{transcript}".encode()).hexdigest()
