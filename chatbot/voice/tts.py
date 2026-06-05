"""Concrete text-to-speech adapter with emotional cadence mapping."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from chatbot.voice.base import TTSProvider, VoiceSynthesisRequest, VoiceSynthesisResult
from chatbot.voice.cadence import CadenceMapper, CadenceProfile


_VALID_TTS_IDENTIFIERS = frozenset(
    {
        ("openai", "tts-1"),
        ("openai", "tts-1-hd"),
        ("elevenlabs", "monolingual-v1"),
    }
)


class TTSAdapter(TTSProvider):
    """Provider-neutral TTS adapter that applies mood/mode cadence."""

    def __init__(
        self,
        provider_id: str,
        model_id: str,
        params: Mapping[str, Any] | None = None,
        *,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
        cadence_mapper: CadenceMapper | None = None,
    ) -> None:
        if not self.validate_identifier(provider_id, model_id):
            raise ValueError(f"unsupported TTS provider/model pair: {provider_id}/{model_id}")

        self.provider_id = provider_id
        self.model_id = model_id
        self.params: dict[str, Any] = dict(params or {})
        self.api_key = api_key or ""
        self.base_url = (base_url or "").rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.cadence_mapper = cadence_mapper or CadenceMapper()

    async def synthesize(self, request: VoiceSynthesisRequest) -> VoiceSynthesisResult:
        """Build a provider TTS request from text, cadence, and user preferences."""

        voice_params = dict(request.voice_params or {})
        self._raise_if_voice_disabled(voice_params)

        mood = _string_param(voice_params.get("mood"), default="neutral")
        mode = _string_param(voice_params.get("mode"), default="normal")
        cadence = self.cadence_mapper.map(mood, mode, request.text)
        synthesis_params = self._merge_params(cadence, voice_params)
        payload = self._build_api_payload(request, synthesis_params)

        audio_data = await self._call_provider(payload)
        return VoiceSynthesisResult(
            audio_data=audio_data,
            content_type=_content_type(request.format),
            duration_ms=0.0,
            format=request.format,
        )

    @classmethod
    def validate_identifier(cls, provider_id: str, model_id: str) -> bool:
        return (provider_id, model_id) in _VALID_TTS_IDENTIFIERS

    def _raise_if_voice_disabled(self, voice_params: Mapping[str, Any]) -> None:
        for source in (
            self.params,
            _nested_preferences(self.params),
            voice_params,
            _nested_preferences(voice_params),
        ):
            if _voice_enabled_value(source) is False:
                raise ValueError("voice synthesis is disabled")

    def _merge_params(
        self,
        cadence: CadenceProfile,
        voice_params: Mapping[str, Any],
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "pitch_factor": cadence.pitch_factor,
            "speed_factor": cadence.speed_factor,
            "volume_factor": cadence.volume_factor,
            "pause_ms": cadence.pause_ms,
            "emphasis_words": list(cadence.emphasis_words),
            "tone": cadence.tone,
        }

        for source in (
            self.params,
            _nested_preferences(self.params),
            _nested_preferences(voice_params),
            voice_params,
        ):
            self._merge_voice_preferences(params, source)

        return params

    def _merge_voice_preferences(
        self,
        params: dict[str, Any],
        preferences: Mapping[str, Any] | Any,
    ) -> None:
        voice_speed = _preference_value(preferences, "voice_speed")
        if voice_speed is not None:
            params["speed_factor"] = float(voice_speed)
            params["voice_speed"] = float(voice_speed)

        voice_pitch = _preference_value(preferences, "voice_pitch")
        if voice_pitch is not None:
            params["pitch_factor"] = float(voice_pitch)
            params["voice_pitch"] = float(voice_pitch)

    def _build_api_payload(
        self,
        request: VoiceSynthesisRequest,
        synthesis_params: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider": self.provider_id,
            "model": self.model_id,
            "input": request.text,
            "format": request.format,
            "params": dict(synthesis_params),
        }

        voice = _preference_value(request.voice_params, "voice")
        if voice is None:
            voice = _preference_value(request.voice_params, "voice_id")
        if voice is not None:
            payload["voice"] = voice

        return payload

    async def _call_provider(self, payload: Mapping[str, Any]) -> bytes:
        """Call the TTS provider API and return raw audio bytes."""
        provider = payload.get("provider", "")

        if provider == "openai":
            return await self._call_openai_tts(payload)
        if provider == "elevenlabs":
            return await self._call_elevenlabs_tts(payload)

        raise ValueError(f"unsupported TTS provider: {provider}")

    async def _call_openai_tts(self, payload: Mapping[str, Any]) -> bytes:
        model = payload.get("model", self.model_id)
        text = payload.get("input", "")
        audio_format = payload.get("format", "mp3")
        params = payload.get("params", {})
        voice = payload.get("voice")

        body: dict[str, Any] = {
            "model": model,
            "input": text,
            "voice": voice or "alloy",
        }

        response_format = _normalize_openai_format(audio_format)
        if response_format:
            body["response_format"] = response_format

        speed = params.get("speed_factor") or params.get("voice_speed")
        if speed is not None:
            body["speed"] = float(speed)

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/audio/speech",
                    headers=headers,
                    json=body,
                    timeout=self.timeout_seconds,
                )
        except httpx.TimeoutException as exc:
            raise TimeoutError("TTS provider request timed out") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"TTS provider request failed: {exc}") from exc

        if response.status_code >= 400:
            _raise_tts_http_error(response)

        return response.content

    async def _call_elevenlabs_tts(self, payload: Mapping[str, Any]) -> bytes:
        model = payload.get("model", self.model_id)
        text = payload.get("input", "")
        params = payload.get("params", {})
        voice = payload.get("voice")

        if not voice:
            raise ValueError("voice ID is required for elevenlabs TTS")

        body: dict[str, Any] = {
            "text": text,
            "model_id": model,
        }

        stability = params.get("stability")
        if stability is not None:
            body["voice_settings"] = {"stability": float(stability)}

        speed = params.get("speed_factor") or params.get("voice_speed")
        if speed is not None:
            body.setdefault("voice_settings", {})["speed"] = float(speed)

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        if self.api_key:
            headers["xi-api-key"] = self.api_key

        base = self.base_url or "https://api.elevenlabs.io"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{base}/v1/text-to-speech/{voice}",
                    headers=headers,
                    json=body,
                    timeout=self.timeout_seconds,
                )
        except httpx.TimeoutException as exc:
            raise TimeoutError("TTS provider request timed out") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"TTS provider request failed: {exc}") from exc

        if response.status_code >= 400:
            _raise_tts_http_error(response)

        return response.content


def _nested_preferences(params: Mapping[str, Any]) -> Mapping[str, Any] | Any:
    preferences = params.get("voice_preferences")
    if preferences is None:
        return {}
    return preferences


def _voice_enabled_value(preferences: Mapping[str, Any] | Any) -> bool | None:
    value = _preference_value(preferences, "voice_enabled")
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"false", "0", "off", "no", "disabled"}:
            return False
        if normalized in {"true", "1", "on", "yes", "enabled"}:
            return True
    return bool(value)


def _preference_value(preferences: Mapping[str, Any] | Any, key: str) -> Any:
    if isinstance(preferences, Mapping):
        return preferences.get(key)
    return getattr(preferences, key, None)


def _string_param(value: Any, *, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _content_type(audio_format: str) -> str:
    normalized = str(audio_format or "").strip().lower()
    if normalized == "wav":
        return "audio/wav"
    if normalized == "ogg":
        return "audio/ogg"
    if normalized == "opus":
        return "audio/opus"
    if normalized == "aac":
        return "audio/aac"
    return "audio/mpeg"


def _normalize_openai_format(audio_format: str) -> str:
    """Map a format string to an OpenAI-compatible response_format value."""
    normalized = str(audio_format or "").strip().lower()
    if normalized in {"mp3", "opus", "aac", "flac", "wav", "pcm"}:
        return normalized
    if normalized in {"ogg", "vorbis"}:
        return "opus"
    return "mp3"


def _raise_tts_http_error(response: httpx.Response) -> None:
    """Raise an appropriate error for non-2xx TTS API responses."""
    message = f"TTS provider HTTP {response.status_code}: {response.text}"
    try:
        body = response.json()
        if isinstance(body, Mapping):
            error = body.get("error")
            if isinstance(error, Mapping):
                detail = error.get("message") or error.get("code") or error.get("type")
                if detail:
                    message = f"TTS provider HTTP {response.status_code}: {detail}"
            elif error is not None:
                message = f"TTS provider HTTP {response.status_code}: {error}"
            elif body.get("message"):
                message = f"TTS provider HTTP {response.status_code}: {body['message']}"
    except ValueError:
        pass
    if response.status_code in {401, 403}:
        raise PermissionError(message)
    if response.status_code == 429:
        raise RuntimeError(message)
    raise RuntimeError(message)


__all__ = ["TTSAdapter"]
