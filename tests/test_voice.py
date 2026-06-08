"""Tests for voice synthesis, transcription, cadence mapping, and voice API."""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.db import get_db
from chatbot.voice.base import (
    STTProvider,
    TTSProvider,
    VoiceSynthesisRequest,
    VoiceSynthesisResult,
    VoiceTranscriptionRequest,
    VoiceTranscriptionResult,
    get_voice_providers,
)
from chatbot.voice.cadence import CadenceMapper, CadenceProfile, DEFAULT_CADENCE
from chatbot.voice.stt import STTAdapter
from chatbot.voice.tts import TTSAdapter


# ---------------------------------------------------------------------------
# TestVoiceBase
# ---------------------------------------------------------------------------


class TestVoiceBase:
    """Verify dataclass construction and abstract provider enforcement."""

    def test_synthesis_request_construction(self) -> None:
        req = VoiceSynthesisRequest(text="hello world", voice_params={"mood": "happy"})
        assert req.text == "hello world"
        assert req.voice_params == {"mood": "happy"}
        assert req.format == "mp3"

    def test_synthesis_request_default_format(self) -> None:
        req = VoiceSynthesisRequest(text="test", voice_params={})
        assert req.format == "mp3"

    def test_synthesis_request_custom_format(self) -> None:
        req = VoiceSynthesisRequest(text="test", voice_params={}, format="wav")
        assert req.format == "wav"

    def test_synthesis_result_construction(self) -> None:
        result = VoiceSynthesisResult(
            audio_data=b"audio bytes",
            content_type="audio/mpeg",
            duration_ms=1500.0,
            format="mp3",
        )
        assert result.audio_data == b"audio bytes"
        assert result.content_type == "audio/mpeg"
        assert result.duration_ms == 1500.0
        assert result.format == "mp3"

    def test_synthesis_result_zero_duration(self) -> None:
        result = VoiceSynthesisResult(
            audio_data=b"data",
            content_type="audio/wav",
            duration_ms=0.0,
            format="wav",
        )
        assert result.duration_ms == 0.0

    def test_transcription_request_construction(self) -> None:
        req = VoiceTranscriptionRequest(
            audio_data=b"fake wav data",
            audio_format="wav",
            language="en",
        )
        assert req.audio_data == b"fake wav data"
        assert req.audio_format == "wav"
        assert req.language == "en"

    def test_transcription_request_none_language(self) -> None:
        req = VoiceTranscriptionRequest(
            audio_data=b"audio",
            audio_format="mp3",
            language=None,
        )
        assert req.language is None

    def test_transcription_result_construction(self) -> None:
        result = VoiceTranscriptionResult(
            text="hello world",
            confidence=0.95,
            language_detected="en",
        )
        assert result.text == "hello world"
        assert result.confidence == 0.95
        assert result.language_detected == "en"

    def test_transcription_result_none_language(self) -> None:
        result = VoiceTranscriptionResult(
            text="hola",
            confidence=0.8,
            language_detected=None,
        )
        assert result.language_detected is None

    def test_tts_provider_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            TTSProvider()  # type: ignore[abstract]

    def test_stt_provider_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            STTProvider()  # type: ignore[abstract]

    def test_get_voice_providers_returns_dict(self) -> None:
        providers = get_voice_providers()
        assert isinstance(providers, dict)
        assert providers == {}

    def test_dataclass_frozen(self) -> None:
        req = VoiceSynthesisRequest(text="hello", voice_params={})
        with pytest.raises(Exception):
            req.text = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestCadenceMapper
# ---------------------------------------------------------------------------


class TestCadenceMapper:
    """Verify mood-to-profile and mode-to-profile mappings."""

    @pytest.fixture
    def mapper(self) -> CadenceMapper:
        return CadenceMapper()

    def test_map_happy(self, mapper: CadenceMapper) -> None:
        profile = mapper.map("happy", "normal", "")
        assert profile.pitch_factor > 1.0
        assert profile.speed_factor > 1.0
        assert profile.tone == "warm"
        assert profile.pause_ms < DEFAULT_CADENCE.pause_ms

    def test_map_sad(self, mapper: CadenceMapper) -> None:
        profile = mapper.map("sad", "normal", "")
        assert profile.pitch_factor < 1.0
        assert profile.speed_factor < 1.0
        assert profile.tone == "gentle"
        assert profile.pause_ms > DEFAULT_CADENCE.pause_ms

    def test_map_angry(self, mapper: CadenceMapper) -> None:
        profile = mapper.map("angry", "normal", "")
        assert profile.speed_factor >= 1.0
        assert profile.tone == "urgent"

    def test_map_calm(self, mapper: CadenceMapper) -> None:
        profile = mapper.map("calm", "normal", "")
        assert profile.speed_factor < 1.0
        assert profile.tone == "gentle"
        assert profile.pause_ms > DEFAULT_CADENCE.pause_ms

    def test_map_excited(self, mapper: CadenceMapper) -> None:
        profile = mapper.map("excited", "normal", "")
        assert profile.pitch_factor > 1.0
        assert profile.speed_factor > 1.0
        assert profile.tone == "excited"
        assert profile.pause_ms < DEFAULT_CADENCE.pause_ms

    def test_map_neutral_returns_default(self, mapper: CadenceMapper) -> None:
        profile = mapper.map("neutral", "normal", "")
        assert profile == DEFAULT_CADENCE

    def test_map_unknown_mood_returns_default(self, mapper: CadenceMapper) -> None:
        profile = mapper.map("nonexistent_mood_xyz", "normal", "")
        assert profile == DEFAULT_CADENCE

    def test_map_empty_mood_returns_default(self, mapper: CadenceMapper) -> None:
        profile = mapper.map("", "normal", "")
        assert profile == DEFAULT_CADENCE

    def test_map_none_mood_returns_default(self, mapper: CadenceMapper) -> None:
        profile = mapper.map(None, "normal", "")  # type: ignore[arg-type]
        assert profile == DEFAULT_CADENCE

    def test_mode_storytelling_slower_more_pauses(self, mapper: CadenceMapper) -> None:
        profile = mapper.map("neutral", "storytelling", "")
        assert profile.speed_factor < 1.0
        assert profile.pause_ms > DEFAULT_CADENCE.pause_ms
        assert profile.tone == "warm"

    def test_mode_coaching_measured_emphatic(self, mapper: CadenceMapper) -> None:
        profile = mapper.map("neutral", "coaching", "")
        assert profile.tone == "warm"
        assert profile.speed_factor >= 0.9

    def test_mode_playful_faster_livelier(self, mapper: CadenceMapper) -> None:
        profile = mapper.map("neutral", "playful", "")
        assert profile.speed_factor > 1.0
        assert profile.tone == "excited"
        assert profile.pause_ms < DEFAULT_CADENCE.pause_ms

    def test_mode_normal_no_override(self, mapper: CadenceMapper) -> None:
        happy = mapper.map("happy", "normal", "")
        happy_no_mode = mapper.map("happy", "normal", "")
        assert happy == happy_no_mode

    def test_mood_with_mode_merged_profile(self, mapper: CadenceMapper) -> None:
        profile = mapper.map("happy", "storytelling", "")
        assert profile.tone == "warm"
        assert profile.speed_factor < 1.0
        assert profile.pause_ms > DEFAULT_CADENCE.pause_ms

    def test_emphasis_words_from_text(self, mapper: CadenceMapper) -> None:
        profile = mapper.map("happy", "normal", "This is **GREAT** news")
        assert "great" in profile.emphasis_words

    def test_emphasis_words_uppercase_text(self, mapper: CadenceMapper) -> None:
        profile = mapper.map("neutral", "normal", "ATTENTION: This is IMPORTANT work")
        assert "attention" in profile.emphasis_words
        assert "important" in profile.emphasis_words

    def test_emphasis_words_no_duplicates(self, mapper: CadenceMapper) -> None:
        profile = mapper.map("happy", "normal", "**great** and **GREAT**")
        assert profile.emphasis_words.count("great") == 1

    def test_map_with_hyphenated_mood(self, mapper: CadenceMapper) -> None:
        profile = mapper.map("deep-talk", "normal", "")
        assert profile == DEFAULT_CADENCE

    def test_map_with_spaces_in_mood(self, mapper: CadenceMapper) -> None:
        profile = mapper.map("  happy  ", "normal", "")
        assert profile.tone == "warm"
        assert profile.speed_factor > 1.0

    def test_return_type_is_cadence_profile(self, mapper: CadenceMapper) -> None:
        profile = mapper.map("happy", "normal", "")
        assert isinstance(profile, CadenceProfile)


# ---------------------------------------------------------------------------
# TestTTSAdapter
# ---------------------------------------------------------------------------


class TestTTSAdapter:
    """Verify TTS adapter construction, validation, and synthesis."""

    def test_construct_valid_kokoro(self) -> None:
        adapter = TTSAdapter("kokoro", "kokoro-82m")
        assert adapter.provider_id == "kokoro"
        assert adapter.model_id == "kokoro-82m"

    def test_construct_openai_removed(self) -> None:
        with pytest.raises(ValueError, match="unsupported TTS provider/model"):
            TTSAdapter("openai", "tts-1")

    def test_construct_elevenlabs(self) -> None:
        adapter = TTSAdapter("elevenlabs", "monolingual-v1")
        assert adapter.provider_id == "elevenlabs"

    def test_construct_with_params(self) -> None:
        adapter = TTSAdapter("kokoro", "kokoro-82m", params={"voice_speed": 1.2})
        assert adapter.params == {"voice_speed": 1.2}

    def test_construct_with_cadence_mapper(self) -> None:
        custom_mapper = CadenceMapper()
        adapter = TTSAdapter("kokoro", "kokoro-82m", cadence_mapper=custom_mapper)
        assert adapter.cadence_mapper is custom_mapper

    def test_construct_invalid_provider(self) -> None:
        with pytest.raises(ValueError, match="unsupported TTS provider/model"):
            TTSAdapter("invalid_provider", "tts-1")

    def test_construct_invalid_model(self) -> None:
        with pytest.raises(ValueError, match="unsupported TTS provider/model"):
            TTSAdapter("kokoro", "invalid-model")

    def test_construct_both_invalid(self) -> None:
        with pytest.raises(ValueError, match="unsupported TTS provider/model"):
            TTSAdapter("nope", "nope")

    def test_validate_identifier_valid(self) -> None:
        assert TTSAdapter.validate_identifier("kokoro", "kokoro-82m") is True
        assert TTSAdapter.validate_identifier("elevenlabs", "monolingual-v1") is True

    def test_validate_identifier_invalid(self) -> None:
        assert TTSAdapter.validate_identifier("openai", "tts-1") is False
        assert TTSAdapter.validate_identifier("openai", "tts-1-hd") is False
        assert TTSAdapter.validate_identifier("openai", "nonexistent") is False
        assert TTSAdapter.validate_identifier("invalid", "tts-1") is False
        assert TTSAdapter.validate_identifier("", "") is False

    def test_synthesize_disabled_raises(self) -> None:
        async def _run() -> None:
            adapter = TTSAdapter("kokoro", "kokoro-82m", params={"voice_enabled": False})
            request = VoiceSynthesisRequest(text="hello", voice_params={})
            with pytest.raises(ValueError, match="voice synthesis is disabled"):
                await adapter.synthesize(request)

        asyncio.run(_run())

    def test_synthesize_disabled_in_voice_params(self) -> None:
        async def _run() -> None:
            adapter = TTSAdapter("kokoro", "kokoro-82m")
            request = VoiceSynthesisRequest(
                text="hello",
                voice_params={"voice_enabled": False},
            )
            with pytest.raises(ValueError, match="voice synthesis is disabled"):
                await adapter.synthesize(request)

        asyncio.run(_run())

    def test_synthesize_disabled_nested_preferences(self) -> None:
        async def _run() -> None:
            adapter = TTSAdapter("kokoro", "kokoro-82m")
            request = VoiceSynthesisRequest(
                text="hello",
                voice_params={"voice_preferences": {"voice_enabled": False}},
            )
            with pytest.raises(ValueError, match="voice synthesis is disabled"):
                await adapter.synthesize(request)

        asyncio.run(_run())

    def test_synthesize_disabled_string_false(self) -> None:
        async def _run() -> None:
            adapter = TTSAdapter("kokoro", "kokoro-82m", params={"voice_enabled": "false"})
            request = VoiceSynthesisRequest(text="hello", voice_params={})
            with pytest.raises(ValueError, match="voice synthesis is disabled"):
                await adapter.synthesize(request)

        asyncio.run(_run())

    def test_synthesize_disabled_string_off(self) -> None:
        async def _run() -> None:
            adapter = TTSAdapter("kokoro", "kokoro-82m", params={"voice_enabled": "off"})
            request = VoiceSynthesisRequest(text="hello", voice_params={})
            with pytest.raises(ValueError, match="voice synthesis is disabled"):
                await adapter.synthesize(request)

        asyncio.run(_run())

    def test_synthesize_returns_correct_result_structure(self) -> None:
        async def _run() -> None:
            adapter = TTSAdapter("kokoro", "kokoro-82m")
            request = VoiceSynthesisRequest(text="hello world", voice_params={})

            adapter._call_provider = AsyncMock(return_value=b"fake audio data")

            result = await adapter.synthesize(request)
            assert isinstance(result, VoiceSynthesisResult)
            assert result.audio_data == b"fake audio data"
            assert result.content_type == "audio/mpeg"
            assert result.duration_ms == 0.0
            assert result.format == "mp3"

        asyncio.run(_run())

    def test_synthesize_with_mood_and_mode(self) -> None:
        async def _run() -> None:
            adapter = TTSAdapter("kokoro", "kokoro-82m")
            request = VoiceSynthesisRequest(
                text="hello",
                voice_params={"mood": "happy", "mode": "playful"},
            )
            adapter._call_provider = AsyncMock(return_value=b"audio")

            result = await adapter.synthesize(request)
            assert isinstance(result, VoiceSynthesisResult)
            assert result.audio_data == b"audio"

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# TestSTTAdapter
# ---------------------------------------------------------------------------


class TestSTTAdapter:
    """Verify STT adapter construction, validation, and transcription."""

    def test_construct_deepgram(self) -> None:
        adapter = STTAdapter("deepgram", "nova-2")
        assert adapter.provider_id == "deepgram"
        assert adapter.model_id == "nova-2"

    def test_construct_openai_removed(self) -> None:
        with pytest.raises(ValueError, match="unsupported STT provider/model"):
            STTAdapter("openai", "whisper-1")

    def test_construct_invalid_provider(self) -> None:
        with pytest.raises(ValueError, match="unsupported STT provider/model"):
            STTAdapter("invalid", "whisper-1")

    def test_construct_invalid_model(self) -> None:
        with pytest.raises(ValueError, match="unsupported STT provider/model"):
            STTAdapter("deepgram", "invalid")

    def test_construct_both_invalid(self) -> None:
        with pytest.raises(ValueError, match="unsupported STT provider/model"):
            STTAdapter("nope", "nope")

    def test_validate_identifier_valid(self) -> None:
        assert STTAdapter.validate_identifier("deepgram", "nova-2") is True

    def test_validate_identifier_invalid(self) -> None:
        assert STTAdapter.validate_identifier("openai", "whisper-1") is False
        assert STTAdapter.validate_identifier("openai", "nonexistent") is False
        assert STTAdapter.validate_identifier("deepgram", "whisper-1") is False
        assert STTAdapter.validate_identifier("", "") is False

    def test_transcribe_empty_audio_raises(self) -> None:
        async def _run() -> None:
            adapter = STTAdapter("deepgram", "nova-2")
            request = VoiceTranscriptionRequest(
                audio_data=b"", audio_format="mp3", language=None
            )
            with pytest.raises(ValueError, match="audio_data must not be empty"):
                await adapter.transcribe(request)

        asyncio.run(_run())

    def test_transcribe_unsupported_format_raises(self) -> None:
        async def _run() -> None:
            adapter = STTAdapter("deepgram", "nova-2")
            request = VoiceTranscriptionRequest(
                audio_data=b"audio", audio_format="xyz_unsupported", language=None
            )
            with pytest.raises(ValueError, match="unsupported audio format"):
                await adapter.transcribe(request)

        asyncio.run(_run())

    def test_transcribe_returns_correct_result_structure(self) -> None:
        async def _run() -> None:
            adapter = STTAdapter("deepgram", "nova-2")
            request = VoiceTranscriptionRequest(
                audio_data=b"fake audio bytes", audio_format="mp3", language=None
            )
            adapter._call_provider = AsyncMock(
                return_value={"text": "hello world", "confidence": 0.95}
            )

            result = await adapter.transcribe(request)
            assert isinstance(result, VoiceTranscriptionResult)
            assert result.text == "hello world"
            assert result.confidence == 0.95
            assert result.language_detected is None

        asyncio.run(_run())

    def test_transcribe_with_language(self) -> None:
        async def _run() -> None:
            adapter = STTAdapter("deepgram", "nova-2")
            request = VoiceTranscriptionRequest(
                audio_data=b"audio", audio_format="mp3", language="fr"
            )
            adapter._call_provider = AsyncMock(
                return_value={"text": "bonjour", "confidence": 0.9}
            )

            result = await adapter.transcribe(request)
            assert result.text == "bonjour"
            assert result.language_detected == "fr"

        asyncio.run(_run())

    def test_transcribe_with_language_detected_in_response(self) -> None:
        async def _run() -> None:
            adapter = STTAdapter("deepgram", "nova-2")
            request = VoiceTranscriptionRequest(
                audio_data=b"audio", audio_format="mp3", language="en"
            )
            adapter._call_provider = AsyncMock(
                return_value={
                    "text": "hello",
                    "confidence": 0.88,
                    "language_detected": "en",
                }
            )

            result = await adapter.transcribe(request)
            assert result.language_detected == "en"

        asyncio.run(_run())

    def test_transcribe_missing_text_defaults_to_empty(self) -> None:
        async def _run() -> None:
            adapter = STTAdapter("deepgram", "nova-2")
            request = VoiceTranscriptionRequest(
                audio_data=b"audio", audio_format="mp3", language=None
            )
            adapter._call_provider = AsyncMock(
                return_value={"confidence": 0.5}
            )

            result = await adapter.transcribe(request)
            assert result.text == ""

        asyncio.run(_run())

    def test_transcribe_missing_confidence_defaults_to_zero(self) -> None:
        async def _run() -> None:
            adapter = STTAdapter("deepgram", "nova-2")
            request = VoiceTranscriptionRequest(
                audio_data=b"audio", audio_format="mp3", language=None
            )
            adapter._call_provider = AsyncMock(
                return_value={"text": "test"}
            )

            result = await adapter.transcribe(request)
            assert result.confidence == 0.0

        asyncio.run(_run())

    def test_transcribe_all_supported_formats(self) -> None:
        supported = {"aac", "flac", "m4a", "mp3", "mp4", "mpeg", "mpga",
                     "ogg", "opus", "wav", "webm"}

        async def _run(fmt: str) -> None:
            adapter = STTAdapter("deepgram", "nova-2")
            request = VoiceTranscriptionRequest(
                audio_data=b"audio", audio_format=fmt, language=None
            )
            adapter._call_provider = AsyncMock(
                return_value={"text": "test", "confidence": 0.5}
            )
            result = await adapter.transcribe(request)
            assert isinstance(result, VoiceTranscriptionResult)

        for fmt in supported:
            asyncio.run(_run(fmt))

    def test_transcribe_format_normalization(self) -> None:
        async def _run() -> None:
            adapter = STTAdapter("deepgram", "nova-2")
            request = VoiceTranscriptionRequest(
                audio_data=b"audio", audio_format=".MP3", language=None
            )
            adapter._call_provider = AsyncMock(
                return_value={"text": "test", "confidence": 0.5}
            )
            result = await adapter.transcribe(request)
            assert result.text == "test"

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# TestVoiceAPI
# ---------------------------------------------------------------------------


class TestVoiceAPI:
    """Test voice API endpoints with TestClient and mocked dependencies."""

    @pytest.fixture
    def test_app(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        import sys
        import sqlalchemy as sa
        from sqlalchemy.orm import DeclarativeBase

        # Prevent chatbot.db.models import from triggering the pre-existing
        # AgencyRitual.metadata SQLAlchemy error by injecting a minimal
        # ORM-mapped VoicePreferences stub before voice_router is imported.
        class _MockBase(DeclarativeBase):
            pass

        class _FakeVoicePreferences(_MockBase):
            __tablename__ = "voice_preferences"

            id = sa.Column(sa.String, primary_key=True, default="vp-fake")
            user_id = sa.Column(sa.String(64), nullable=False)
            tts_provider_id = sa.Column(sa.String(64), nullable=True)
            tts_model_id = sa.Column(sa.String(128), nullable=True)
            stt_provider_id = sa.Column(sa.String(64), nullable=True)
            stt_model_id = sa.Column(sa.String(128), nullable=True)
            voice_speed = sa.Column(sa.Float, nullable=True)
            voice_pitch = sa.Column(sa.Float, nullable=True)
            voice_enabled = sa.Column(sa.Boolean, nullable=False, default=True)

            # Ensure voice_enabled defaults to True at the instance level
            # (SA Column defaults only apply at the database level).
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                if self.voice_enabled is None:
                    self.voice_enabled = True

        _fake_models = MagicMock(name="chatbot.db.models")
        _fake_models.VoicePreferences = _FakeVoicePreferences
        sys.modules["chatbot.db.models"] = _fake_models

        from chatbot.api.voice import voice_router

        # Mock _call_provider on TTSAdapter to return fake audio bytes
        async def fake_tts_call(self: Any, payload: Any) -> bytes:
            return b"fake synthesized audio data"

        # Mock _call_provider on STTAdapter to return fake transcription
        async def fake_stt_call(self: Any, payload: Any) -> dict[str, Any]:
            return {"text": "transcribed text", "confidence": 0.98}

        monkeypatch.setattr(TTSAdapter, "_call_provider", fake_tts_call)
        monkeypatch.setattr(STTAdapter, "_call_provider", fake_stt_call)

        app = FastAPI()
        app.include_router(voice_router)

        # Override auth dependency
        async def override_get_current_user() -> CurrentUser:
            return CurrentUser(
                user_id="test-user-id",
                username="testuser",
                email="test@example.com",
            )

        app.dependency_overrides[get_current_user] = override_get_current_user

        # Override db dependency with a mock session.
        # db.add() is sync; db.execute(), db.commit(), db.refresh() are async.
        async def override_get_db() -> Any:
            mock_session = MagicMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session.commit = AsyncMock()
            mock_session.refresh = AsyncMock()
            yield mock_session

        app.dependency_overrides[get_db] = override_get_db

        return TestClient(app)

    # --- synthesize ---

    def test_synthesize_returns_200(self, test_app: TestClient) -> None:
        response = test_app.post(
            "/api/voice/synthesize",
            json={"text": "Hello world"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "audio_base64" in data
        assert "content_type" in data
        assert "duration_ms" in data
        assert data["content_type"] == "audio/mpeg"

        decoded = base64.b64decode(data["audio_base64"])
        assert decoded == b"fake synthesized audio data"

    def test_synthesize_with_mood_and_mode(self, test_app: TestClient) -> None:
        response = test_app.post(
            "/api/voice/synthesize",
            json={"text": "Hello", "mood": "happy", "mode": "coaching"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "audio_base64" in data

    def test_synthesize_empty_text(self, test_app: TestClient) -> None:
        response = test_app.post(
            "/api/voice/synthesize",
            json={"text": ""},
        )
        assert response.status_code == 200
        assert "audio_base64" in response.json()

    # --- transcribe ---

    def test_transcribe_returns_200(self, test_app: TestClient) -> None:
        audio_b64 = base64.b64encode(b"fake audio data").decode("ascii")
        response = test_app.post(
            "/api/voice/transcribe",
            json={"audio_base64": audio_b64, "format": "webm"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "text" in data
        assert "confidence" in data
        assert data["text"] == "transcribed text"
        assert data["confidence"] == 0.98

    def test_transcribe_with_language(self, test_app: TestClient) -> None:
        audio_b64 = base64.b64encode(b"audio").decode("ascii")
        response = test_app.post(
            "/api/voice/transcribe",
            json={"audio_base64": audio_b64, "format": "mp3", "language": "fr"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["text"] == "transcribed text"

    def test_transcribe_invalid_base64_raises(self, test_app: TestClient) -> None:
        """Malformed base64 triggers a binascii.Error inside the endpoint."""
        with pytest.raises(Exception):
            test_app.post(
                "/api/voice/transcribe",
                json={"audio_base64": "!!!not-base64!!!", "format": "webm"},
            )

    # --- GET preferences ---

    def test_get_preferences_returns_200(self, test_app: TestClient) -> None:
        response = test_app.get("/api/voice/preferences")
        assert response.status_code == 200
        data = response.json()
        assert "tts_provider_id" in data
        assert "tts_model_id" in data
        assert "stt_provider_id" in data
        assert "stt_model_id" in data
        assert "voice_speed" in data
        assert "voice_pitch" in data
        assert "voice_enabled" in data

    def test_get_preferences_returns_data(self, test_app: TestClient) -> None:
        response = test_app.get("/api/voice/preferences")
        assert response.status_code == 200
        data = response.json()
        assert data["voice_enabled"] is True

    # --- PUT preferences ---

    def test_put_preferences_updates_success(self, test_app: TestClient) -> None:
        response = test_app.put(
            "/api/voice/preferences",
            json={
                "tts_provider_id": "kokoro",
                "tts_model_id": "kokoro-82m",
                "voice_speed": 1.2,
                "voice_pitch": 0.9,
                "voice_enabled": True,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tts_provider_id"] == "kokoro"
        assert data["tts_model_id"] == "kokoro-82m"
        assert data["voice_speed"] == 1.2
        assert data["voice_pitch"] == 0.9
        assert data["voice_enabled"] is True

    def test_put_preferences_partial_update(self, test_app: TestClient) -> None:
        response = test_app.put(
            "/api/voice/preferences",
            json={"voice_enabled": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["voice_enabled"] is False

    def test_put_preferences_invalid_tts(self, test_app: TestClient) -> None:
        response = test_app.put(
            "/api/voice/preferences",
            json={
                "tts_provider_id": "invalid_provider",
                "tts_model_id": "invalid_model",
            },
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "Invalid TTS provider/model" in detail

    def test_put_preferences_invalid_stt(self, test_app: TestClient) -> None:
        response = test_app.put(
            "/api/voice/preferences",
            json={
                "stt_provider_id": "bad_provider",
                "stt_model_id": "bad_model",
            },
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "Invalid STT provider/model" in detail

    def test_put_preferences_empty_body(self, test_app: TestClient) -> None:
        response = test_app.put("/api/voice/preferences", json={})
        assert response.status_code == 200

    def test_put_preferences_stt_only(self, test_app: TestClient) -> None:
        response = test_app.put(
            "/api/voice/preferences",
            json={
                "stt_provider_id": "deepgram",
                "stt_model_id": "nova-2",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["stt_provider_id"] == "deepgram"
        assert data["stt_model_id"] == "nova-2"
