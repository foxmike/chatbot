"""Voice synthesis and transcription API endpoints."""

from __future__ import annotations

import base64
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.db import get_db
from chatbot.db.models import VoicePreferences
from chatbot.voice.base import VoiceSynthesisRequest, VoiceTranscriptionRequest
from chatbot.voice.cadence import CadenceMapper
from chatbot.voice.stt import STTAdapter
from chatbot.voice.tts import TTSAdapter

voice_router = APIRouter(prefix="/api/voice", tags=["voice"])

DEFAULT_TTS_PROVIDER = "openai"
DEFAULT_TTS_MODEL = "tts-1"
DEFAULT_STT_PROVIDER = "openai"
DEFAULT_STT_MODEL = "whisper-1"
DEFAULT_VOICE_SPEED = 1.0
DEFAULT_VOICE_PITCH = 1.0
DEFAULT_VOICE_ENABLED = True


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SynthesizeRequest(BaseModel):
    text: str
    mood: str | None = None
    mode: str | None = None


class SynthesizeResponse(BaseModel):
    audio_base64: str
    content_type: str
    duration_ms: float


class TranscribeRequest(BaseModel):
    audio_base64: str
    format: str = "webm"
    language: str | None = None


class TranscribeResponse(BaseModel):
    text: str
    confidence: float
    language_detected: str | None = None


class VoicePreferencesResponse(BaseModel):
    tts_provider_id: str | None = None
    tts_model_id: str | None = None
    stt_provider_id: str | None = None
    stt_model_id: str | None = None
    voice_speed: float | None = None
    voice_pitch: float | None = None
    voice_enabled: bool = True


class VoicePreferencesUpdate(BaseModel):
    tts_provider_id: str | None = None
    tts_model_id: str | None = None
    stt_provider_id: str | None = None
    stt_model_id: str | None = None
    voice_speed: float | None = None
    voice_pitch: float | None = None
    voice_enabled: bool | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prefs_to_response(prefs: VoicePreferences) -> VoicePreferencesResponse:
    return VoicePreferencesResponse(
        tts_provider_id=prefs.tts_provider_id,
        tts_model_id=prefs.tts_model_id,
        stt_provider_id=prefs.stt_provider_id,
        stt_model_id=prefs.stt_model_id,
        voice_speed=prefs.voice_speed,
        voice_pitch=prefs.voice_pitch,
        voice_enabled=prefs.voice_enabled,
    )


def _default_preferences() -> VoicePreferencesResponse:
    return VoicePreferencesResponse(
        tts_provider_id=DEFAULT_TTS_PROVIDER,
        tts_model_id=DEFAULT_TTS_MODEL,
        stt_provider_id=DEFAULT_STT_PROVIDER,
        stt_model_id=DEFAULT_STT_MODEL,
        voice_speed=DEFAULT_VOICE_SPEED,
        voice_pitch=DEFAULT_VOICE_PITCH,
        voice_enabled=DEFAULT_VOICE_ENABLED,
    )


def _validate_provider_ids(update: VoicePreferencesUpdate) -> None:
    if update.tts_provider_id is not None and update.tts_model_id is not None:
        if not TTSAdapter.validate_identifier(update.tts_provider_id, update.tts_model_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid TTS provider/model: {update.tts_provider_id}/{update.tts_model_id}",
            )
    if update.stt_provider_id is not None and update.stt_model_id is not None:
        if not STTAdapter.validate_identifier(update.stt_provider_id, update.stt_model_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid STT provider/model: {update.stt_provider_id}/{update.stt_model_id}",
            )


# ---------------------------------------------------------------------------
# POST /synthesize
# ---------------------------------------------------------------------------


@voice_router.post("/synthesize", response_model=SynthesizeResponse)
async def synthesize(request: SynthesizeRequest) -> SynthesizeResponse:
    """Synthesize speech audio from text with optional mood/mode cadence."""
    voice_params: dict[str, Any] = {}
    if request.mood is not None:
        voice_params["mood"] = request.mood
    if request.mode is not None:
        voice_params["mode"] = request.mode

    adapter = TTSAdapter(
        provider_id=DEFAULT_TTS_PROVIDER,
        model_id=DEFAULT_TTS_MODEL,
        cadence_mapper=CadenceMapper(),
    )

    synthesis_request = VoiceSynthesisRequest(
        text=request.text,
        voice_params=voice_params,
    )

    result = await adapter.synthesize(synthesis_request)

    return SynthesizeResponse(
        audio_base64=base64.b64encode(result.audio_data).decode("ascii"),
        content_type=result.content_type,
        duration_ms=result.duration_ms,
    )


# ---------------------------------------------------------------------------
# POST /transcribe
# ---------------------------------------------------------------------------


@voice_router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(request: TranscribeRequest) -> TranscribeResponse:
    """Transcribe speech audio to text."""
    audio_data = base64.b64decode(request.audio_base64)

    adapter = STTAdapter(
        provider_id=DEFAULT_STT_PROVIDER,
        model_id=DEFAULT_STT_MODEL,
    )

    transcription_request = VoiceTranscriptionRequest(
        audio_data=audio_data,
        audio_format=request.format,
        language=request.language,
    )

    result = await adapter.transcribe(transcription_request)

    return TranscribeResponse(
        text=result.text,
        confidence=result.confidence,
        language_detected=result.language_detected,
    )


# ---------------------------------------------------------------------------
# GET /preferences
# ---------------------------------------------------------------------------


@voice_router.get("/preferences", response_model=VoicePreferencesResponse)
async def get_preferences(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VoicePreferencesResponse:
    """Return the current user's stored voice preferences, or defaults."""
    result = await db.execute(
        sa.select(VoicePreferences).where(
            VoicePreferences.user_id == current_user.user_id
        )
    )
    prefs = result.scalar_one_or_none()

    if prefs is None:
        return _default_preferences()

    return _prefs_to_response(prefs)


# ---------------------------------------------------------------------------
# PUT /preferences
# ---------------------------------------------------------------------------


@voice_router.put("/preferences", response_model=VoicePreferencesResponse)
async def update_preferences(
    update: VoicePreferencesUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VoicePreferencesResponse:
    """Update the current user's voice preferences."""
    _validate_provider_ids(update)

    result = await db.execute(
        sa.select(VoicePreferences).where(
            VoicePreferences.user_id == current_user.user_id
        )
    )
    prefs = result.scalar_one_or_none()

    if prefs is None:
        prefs = VoicePreferences(user_id=current_user.user_id)
        db.add(prefs)

    if update.tts_provider_id is not None:
        prefs.tts_provider_id = update.tts_provider_id
    if update.tts_model_id is not None:
        prefs.tts_model_id = update.tts_model_id
    if update.stt_provider_id is not None:
        prefs.stt_provider_id = update.stt_provider_id
    if update.stt_model_id is not None:
        prefs.stt_model_id = update.stt_model_id
    if update.voice_speed is not None:
        prefs.voice_speed = update.voice_speed
    if update.voice_pitch is not None:
        prefs.voice_pitch = update.voice_pitch
    if update.voice_enabled is not None:
        prefs.voice_enabled = update.voice_enabled

    await db.commit()
    await db.refresh(prefs)

    return _prefs_to_response(prefs)
