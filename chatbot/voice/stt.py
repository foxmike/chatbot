"""Concrete speech-to-text adapter for provider-neutral transcription."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from chatbot.voice.base import STTProvider, VoiceTranscriptionRequest, VoiceTranscriptionResult


_VALID_STT_IDENTIFIERS = frozenset(
    {
        ("openai", "whisper-1"),
        ("deepgram", "nova-2"),
    }
)

_SUPPORTED_AUDIO_FORMATS = frozenset(
    {
        "aac",
        "flac",
        "m4a",
        "mp3",
        "mp4",
        "mpeg",
        "mpga",
        "ogg",
        "opus",
        "wav",
        "webm",
    }
)


class STTAdapter(STTProvider):
    """Provider-neutral STT adapter for speech transcription requests."""

    def __init__(self, provider_id: str, model_id: str) -> None:
        if not self.validate_identifier(provider_id, model_id):
            raise ValueError(f"unsupported STT provider/model pair: {provider_id}/{model_id}")

        self.provider_id = provider_id
        self.model_id = model_id

    async def transcribe(self, request: VoiceTranscriptionRequest) -> VoiceTranscriptionResult:
        """Build a provider STT request from audio bytes and transcription options."""

        if not request.audio_data:
            raise ValueError("audio_data must not be empty")

        audio_format = _normalize_audio_format(request.audio_format)
        if audio_format not in _SUPPORTED_AUDIO_FORMATS:
            raise ValueError(f"unsupported audio format: {request.audio_format}")

        payload = self._build_api_payload(request, audio_format)
        response = await self._call_provider(payload)
        return self._to_transcription_result(response, request.language)

    @classmethod
    def validate_identifier(cls, provider_id: str, model_id: str) -> bool:
        return (provider_id, model_id) in _VALID_STT_IDENTIFIERS

    def _build_api_payload(
        self,
        request: VoiceTranscriptionRequest,
        audio_format: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider": self.provider_id,
            "model": self.model_id,
            "audio": request.audio_data,
            "audio_format": audio_format,
        }

        if request.language:
            payload["language"] = request.language

        return payload

    async def _call_provider(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        raise NotImplementedError(
            "STT provider API calls are not implemented yet; "
            f"would call {payload.get('provider')}/{payload.get('model')}"
        )

    def _to_transcription_result(
        self,
        response: Mapping[str, Any],
        requested_language: str | None,
    ) -> VoiceTranscriptionResult:
        text = response.get("text")
        confidence = response.get("confidence")
        language_detected = response.get("language_detected", response.get("language"))

        return VoiceTranscriptionResult(
            text=text if isinstance(text, str) else "",
            confidence=_confidence_value(confidence),
            language_detected=language_detected if isinstance(language_detected, str) else requested_language,
        )


def _normalize_audio_format(audio_format: str) -> str:
    return str(audio_format or "").strip().lower().lstrip(".")


def _confidence_value(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


__all__ = ["STTAdapter"]
