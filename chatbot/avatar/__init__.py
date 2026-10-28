"""Avatar media runtime: real-time STT, speech chunking, Qwen3-TTS and AVTR-1 playback.

Only `chatbot.avatar.media`, `session` and `protocol` form the public surface; the engine has
no dependency on chatbot's database, companions or any LLM provider. Heavy optional
dependencies (faster-whisper, scipy) and the Qwen/AVTR child processes load on demand, so
importing this package is cheap and safe in any environment.
"""

from chatbot.avatar.config import MediaConfig
from chatbot.avatar.media import AvatarMediaEngine, MediaError, PortraitResult
from chatbot.avatar.protocol import (
    Diagnostic,
    Interrupted,
    MediaEvent,
    MediaFrame,
    TranscriptReady,
    TurnEnded,
    UiMessage,
    UserSpeechStarted,
)
from chatbot.avatar.session import FastAPIWebSocketTransport, MediaSession, MediaTransport

__all__ = [
    "AvatarMediaEngine", "Diagnostic", "FastAPIWebSocketTransport", "Interrupted", "MediaConfig",
    "MediaError", "MediaEvent", "MediaFrame", "MediaSession", "MediaTransport", "PortraitResult",
    "TranscriptReady", "TurnEnded", "UiMessage", "UserSpeechStarted",
]
