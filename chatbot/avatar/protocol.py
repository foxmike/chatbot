"""Browser wire protocol and the events the media engine emits.

The binary framing is byte-for-byte the my_avatar protocol (see index.html):

    server -> browser, binary
        b"A" + u16le len(id) + id(utf-8) + u32le sample_rate + float32le mono PCM
        b"V" + u16le len(id) + id(utf-8) + f32le pts_seconds + JPEG
            (an empty id marks an idle/listening frame)
    server -> browser, JSON text:  config, status, interrupt
    browser -> server, binary:     b"M" + float32le mono 16 kHz microphone PCM
    browser -> server, JSON text:  mic, segment_scheduled, playback_start, playback_end,
                                   video_displayed, buffer_stats, interruption_ack
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Literal

MIC_SAMPLE_RATE = 16000
AUDIO_TAG = b"A"
VIDEO_TAG = b"V"
MIC_TAG = b"M"


def pack_audio(segment_id: str, sample_rate: int, pcm: bytes) -> bytes:
    encoded = segment_id.encode()
    return AUDIO_TAG + struct.pack("<H", len(encoded)) + encoded + struct.pack("<I", sample_rate) + pcm


def pack_video(segment_id: str | None, pts: float, jpeg: bytes) -> bytes:
    encoded = segment_id.encode() if segment_id else b""
    return VIDEO_TAG + struct.pack("<H", len(encoded)) + encoded + struct.pack("<f", float(pts)) + jpeg


def unpack_frame(data: bytes) -> tuple[bytes, str, float | int, bytes]:
    """Inverse of pack_audio/pack_video: (tag, segment_id, pts-or-sample_rate, payload)."""
    tag = data[:1]
    (length,) = struct.unpack_from("<H", data, 1)
    segment_id = data[3:3 + length].decode()
    offset = 3 + length
    if tag == AUDIO_TAG:
        return tag, segment_id, struct.unpack_from("<I", data, offset)[0], data[offset + 4:]
    if tag == VIDEO_TAG:
        return tag, segment_id, struct.unpack_from("<f", data, offset)[0], data[offset + 4:]
    raise ValueError(f"unknown frame tag {tag!r}")


def is_microphone_frame(data: bytes) -> bool:
    return data.startswith(MIC_TAG)


# --------------------------------------------------------------------------- events


@dataclass(frozen=True)
class UiMessage:
    """A JSON object to forward to the browser as a text frame."""

    payload: dict[str, Any]


@dataclass(frozen=True)
class MediaFrame:
    """A binary audio/video frame, already packed; forward `data` verbatim as a binary frame."""

    kind: Literal["audio", "video"]
    segment_id: str | None
    data: bytes
    pts: float = 0.0


@dataclass(frozen=True)
class TranscriptReady:
    """A finalized user utterance. Empty `text` means nothing was recognized (the engine has
    already returned to listening; no response should be started)."""

    utterance_id: int
    text: str
    audio_seconds: float
    endpoint_delay: float | None  # VAD-estimated silence before the endpoint, if endpointed
    stt_ms: float
    at: float  # engine monotonic clock at end of utterance


@dataclass(frozen=True)
class UserSpeechStarted:
    """The user began speaking (`barge_in`: while a response was in progress)."""

    barge_in: bool


@dataclass(frozen=True)
class Interrupted:
    """The engine abandoned the in-flight turn on its own (`barge-in` or `disconnect`): playback,
    queued speech and any pending transcription are already cleared. The coordinator must stop
    its LLM stream for that turn. Not emitted for a `cancel()` the coordinator itself requested.
    `turn_id` is None when interrupted before `begin_response` (still transcribing/thinking)."""

    reason: str
    turn_id: str | None


@dataclass(frozen=True)
class TurnEnded:
    """A response finished (`completed`), was cancelled (`cancelled`) or failed (`failed`)."""

    turn_id: str
    status: Literal["completed", "cancelled", "failed"]
    spoken_text: str  # visible text received from the LLM (reasoning tags removed)
    error: str | None = None


@dataclass(frozen=True)
class Diagnostic:
    """Scheduling/performance telemetry: planner decisions, chunk timelines, turn summaries,
    browser buffer statistics, worker status. `data` is JSON-serializable."""

    name: str
    data: dict[str, Any] = field(default_factory=dict)


MediaEvent = (UiMessage | MediaFrame | TranscriptReady | UserSpeechStarted | Interrupted | TurnEnded
              | Diagnostic)
