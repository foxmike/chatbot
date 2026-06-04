from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


_PROVIDER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_OPENROUTER_VOICE_MODEL_ID_PATTERN = re.compile(r"^openrouter/voice/[a-z0-9._-]+/[a-z0-9._-]+$")


@dataclass(frozen=True, slots=True)
class VoiceSynthesisRequest:
    """Provider-independent text-to-speech request."""

    text: str
    voice_params: dict[str, Any]
    format: str = "mp3"


@dataclass(frozen=True, slots=True)
class VoiceSynthesisResult:
    """Normalized text-to-speech result returned by voice providers."""

    audio_data: bytes
    content_type: str
    duration_ms: float
    format: str


@dataclass(frozen=True, slots=True)
class VoiceTranscriptionRequest:
    """Provider-independent speech-to-text request."""

    audio_data: bytes
    audio_format: str
    language: str | None


@dataclass(frozen=True, slots=True)
class VoiceTranscriptionResult:
    """Normalized speech-to-text result returned by voice providers."""

    text: str
    confidence: float
    language_detected: str | None


class TTSProvider(ABC):
    """Abstract interface implemented by concrete text-to-speech providers."""

    @abstractmethod
    async def synthesize(self, request: VoiceSynthesisRequest) -> VoiceSynthesisResult:
        """Synthesize speech audio from provider-normalized request data."""

    @classmethod
    def validate_identifier(cls, provider_id: str, model_id: str) -> bool:
        return _validate_voice_identifier(provider_id, model_id)


class STTProvider(ABC):
    """Abstract interface implemented by concrete speech-to-text providers."""

    @abstractmethod
    async def transcribe(self, request: VoiceTranscriptionRequest) -> VoiceTranscriptionResult:
        """Transcribe speech audio from provider-normalized request data."""

    @classmethod
    def validate_identifier(cls, provider_id: str, model_id: str) -> bool:
        return _validate_voice_identifier(provider_id, model_id)


def get_voice_providers() -> dict[str, type[TTSProvider | STTProvider]]:
    return {}


def _validate_voice_identifier(provider_id: str, model_id: str) -> bool:
    if not _PROVIDER_ID_PATTERN.fullmatch(provider_id):
        return False

    if provider_id == "openrouter":
        return _OPENROUTER_VOICE_MODEL_ID_PATTERN.fullmatch(model_id) is not None

    return bool(model_id) and model_id == model_id.strip()


__all__ = [
    "STTProvider",
    "TTSProvider",
    "VoiceSynthesisRequest",
    "VoiceSynthesisResult",
    "VoiceTranscriptionRequest",
    "VoiceTranscriptionResult",
    "get_voice_providers",
]
