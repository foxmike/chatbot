"""Concrete speech-to-text adapter for provider-neutral transcription."""

from __future__ import annotations

import base64
import os
from collections.abc import Mapping
from typing import Any

import httpx

from chatbot.voice.base import STTProvider, VoiceTranscriptionRequest, VoiceTranscriptionResult


_VALID_STT_IDENTIFIERS = frozenset(
    {
        ("deepgram", "nova-2"),
        ("deepgram", "deepgram/nova-2"),
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

    def __init__(
        self,
        provider_id: str,
        model_id: str,
        *,
        api_key: str = "",
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_seconds: float = 120.0,
    ) -> None:
        if not self.validate_identifier(provider_id, model_id):
            raise ValueError(f"unsupported STT provider/model pair: {provider_id}/{model_id}")

        self.provider_id = provider_id
        self.model_id = model_id
        self.api_key = api_key or os.getenv("STT_API_KEY", "") or os.getenv("OPENROUTER_API_KEY", "")
        self.base_url = (base_url or "").rstrip("/")
        self.timeout_seconds = timeout_seconds

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
        """Call the STT provider API and return a normalized dict with text, confidence, and language."""
        provider = payload.get("provider", "")

        if provider == "deepgram":
            return await self._call_openai_compatible_stt(payload)

        raise ValueError(f"unsupported STT provider: {provider}")

    async def _call_openai_compatible_stt(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        model = payload.get("model", self.model_id)
        audio_data = payload.get("audio", b"")
        audio_format = payload.get("audio_format", "wav")
        language = payload.get("language")

        if "openrouter.ai" in self.base_url:
            return await self._call_openrouter_stt(
                model=model,
                audio_data=audio_data,
                audio_format=str(audio_format),
                language=str(language) if language else None,
            )

        data: dict[str, Any] = {
            "model": model,
        }
        if language:
            data["language"] = language
        data["response_format"] = "verbose_json"

        files = {
            "file": (_audio_filename(audio_format), audio_data, _audio_mime(audio_format)),
        }

        if not self.api_key:
            raise ValueError("STT_API_KEY, OPENROUTER_API_KEY, or api_key parameter is required for OpenAI-compatible STT")

        headers: dict[str, str] = {"Authorization": f"Bearer {self.api_key}"}

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=self.timeout_seconds,
                )
        except httpx.TimeoutException as exc:
            raise TimeoutError("OpenAI-compatible STT request timed out") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"OpenAI-compatible STT request failed: {exc}") from exc

        if response.status_code >= 400:
            _raise_stt_http_error(response)

        try:
            body: Any = response.json()
        except ValueError:
            body = {"text": response.text.strip() or "", "confidence": 1.0}

        if not isinstance(body, Mapping):
            return {"text": str(body), "confidence": 1.0}

        return dict(body)

    async def _call_openrouter_stt(
        self,
        *,
        model: Any,
        audio_data: Any,
        audio_format: str,
        language: str | None,
    ) -> Mapping[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "input_audio": {
                "data": base64.b64encode(bytes(audio_data)).decode("ascii"),
                "format": _normalize_audio_format(audio_format) or "wav",
            },
        }
        if language:
            body["language"] = language

        if not self.api_key:
            raise ValueError("STT_API_KEY, OPENROUTER_API_KEY, or api_key parameter is required for OpenAI-compatible STT")

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers=headers,
                    json=body,
                    timeout=self.timeout_seconds,
                )
        except httpx.TimeoutException as exc:
            raise TimeoutError("OpenRouter STT request timed out") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"OpenRouter STT request failed: {exc}") from exc

        if response.status_code >= 400:
            _raise_stt_http_error(response)

        try:
            parsed: Any = response.json()
        except ValueError:
            parsed = {"text": response.text.strip() or "", "confidence": 1.0}

        if not isinstance(parsed, Mapping):
            return {"text": str(parsed), "confidence": 1.0}

        return dict(parsed)

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


def _audio_filename(audio_format: str) -> str:
    ext = _normalize_audio_format(audio_format) or "wav"
    return f"audio.{ext}"


def _audio_mime(audio_format: str) -> str:
    fmt = _normalize_audio_format(audio_format)
    _MIME: dict[str, str] = {
        "aac": "audio/aac",
        "flac": "audio/flac",
        "m4a": "audio/mp4",
        "mp3": "audio/mpeg",
        "mp4": "audio/mp4",
        "mpeg": "audio/mpeg",
        "mpga": "audio/mpeg",
        "ogg": "audio/ogg",
        "opus": "audio/opus",
        "wav": "audio/wav",
        "webm": "audio/webm",
    }
    return _MIME.get(fmt, "audio/wav")


def _raise_stt_http_error(response: httpx.Response) -> None:
    message = f"STT provider HTTP {response.status_code}: {response.text}"
    try:
        body = response.json()
        if isinstance(body, Mapping):
            error = body.get("error")
            if isinstance(error, Mapping):
                detail = error.get("message") or error.get("code") or error.get("type")
                if detail:
                    message = f"STT provider HTTP {response.status_code}: {detail}"
            elif error is not None:
                message = f"STT provider HTTP {response.status_code}: {error}"
            elif body.get("message"):
                message = f"STT provider HTTP {response.status_code}: {body['message']}"
    except ValueError:
        pass
    if response.status_code in {401, 403}:
        raise PermissionError(message)
    if response.status_code == 429:
        raise RuntimeError(message)
    raise RuntimeError(message)


def _confidence_value(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


__all__ = ["STTAdapter"]
