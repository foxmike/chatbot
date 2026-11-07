"""Voice Library API: design/clone previews, saved voices, conversation voice selection.

Every endpoint requires authentication and only touches the caller's own voices (built-in
voices are readable by everyone, writable by no one). Audio is streamed by these endpoints
after authorization; the voice library directory is never served as static files, and
clients never supply filesystem paths.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.api.admin import require_admin
from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.config import get_settings
from chatbot.db import get_db
from chatbot.voices.audio import (
    STT_SAMPLE_RATE,
    AudioValidationError,
    decode_reference,
    encode_wav,
    read_wav,
    resample,
)
from chatbot.voices.service import (
    DEFAULT_VOICE_ID,
    LANGUAGES,
    VoiceError,
    VoiceService,
    clean_language,
    clean_text,
    voice_public,
    voice_spec,
)
from chatbot.voices.storage import PreviewNotFound, VoiceStorage

logger = logging.getLogger(__name__)

voices_router = APIRouter(prefix="/voices", tags=["voices"])

MIN_SAVED_REFERENCE_SECONDS = 2.0
MAX_PREVIEW_TEXT = 400
NO_STORE = {"Cache-Control": "private, no-store"}


# ---------------------------------------------------------------------- dependencies


def get_storage() -> VoiceStorage:
    settings = get_settings()
    return VoiceStorage(settings.voice_library_dir, settings.voice_preview_ttl_seconds)


def get_service(db: AsyncSession = Depends(get_db), storage: VoiceStorage = Depends(get_storage)) -> VoiceService:
    settings = get_settings()
    return VoiceService(db, storage, design_model=settings.avatar_qwen_model,
                        clone_model=settings.avatar_qwen_clone_model)


def get_engine(request: Request):
    """The process-wide media engine whose persistent Qwen worker does all synthesis."""
    host = getattr(request.app.state, "avatar_runtime_host", None)
    engine = getattr(host, "engine", None)
    if engine is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Speech synthesis is not available")
    return engine


def _error(exc: VoiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc), **exc.details})


async def _synthesis(call):
    from chatbot.avatar.media import EngineBusyError, MediaError

    try:
        return await call
    except EngineBusyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (MediaError, asyncio.TimeoutError) as exc:
        detail = str(exc) or "timed out"
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Voice synthesis failed: {detail}") from exc


def _wav(data: bytes) -> Response:
    return Response(content=data, media_type="audio/wav", headers=NO_STORE)


def _preview_public(record: dict) -> dict:
    keys = ("preview_id", "kind", "description", "transcript", "preview_text", "language",
            "reference_duration", "has_sample", "suggested_transcript", "consent", "created_at",
            "source_format", "source_sample_rate", "source_channels", "warnings")
    return {key: record.get(key) for key in keys}


def _reference_warnings(duration: float) -> list[str]:
    if duration < MIN_SAVED_REFERENCE_SECONDS:
        return [f"The reference is only {duration:.1f}s; use a longer preview text "
                f"(at least {MIN_SAVED_REFERENCE_SECONDS:.0f}s is needed to save it)"]
    if duration < 3.0:
        return [f"The reference is only {duration:.1f}s; 3–15 seconds clones most reliably"]
    return []


# ---------------------------------------------------------------------- schemas


class DesignPreviewRequest(BaseModel):
    description: str = Field(min_length=1, max_length=2000)
    preview_text: str = Field(min_length=1, max_length=MAX_PREVIEW_TEXT)
    language: str = "English"


class CloneSampleRequest(BaseModel):
    transcript: str = Field(min_length=1, max_length=2000)
    preview_text: str = Field(min_length=1, max_length=MAX_PREVIEW_TEXT)
    language: str = "English"


class SaveVoiceRequest(BaseModel):
    preview_id: str
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    language: str | None = None
    transcript: str | None = Field(default=None, max_length=2000)
    consent: bool = False


class UpdateVoiceRequest(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    language: str | None = None
    transcript: str | None = Field(default=None, max_length=2000)


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_PREVIEW_TEXT)


class SessionVoiceRequest(BaseModel):
    voice_id: str | None = None


# ---------------------------------------------------------------------- library


@voices_router.get("")
async def list_voices(current_user: CurrentUser = Depends(get_current_user),
                      service: VoiceService = Depends(get_service)) -> dict:
    voices = await service.list(current_user.user_id)
    return {"voices": [voice_public(v) for v in voices], "default_voice_id": DEFAULT_VOICE_ID,
            "default_initialized": any(v.voice_id == DEFAULT_VOICE_ID for v in voices),
            "languages": list(LANGUAGES)}


# ---------------------------------------------------------------------- previews (not saved)


@voices_router.post("/previews/design", status_code=status.HTTP_201_CREATED)
async def create_design_preview(body: DesignPreviewRequest, request: Request,
                                current_user: CurrentUser = Depends(get_current_user),
                                storage: VoiceStorage = Depends(get_storage)) -> dict:
    """Generate one VoiceDesign candidate. It is a temporary preview, not a saved voice."""
    try:
        description = clean_text(body.description, "Voice description")
        text = clean_text(body.preview_text, "Preview text", limit=MAX_PREVIEW_TEXT)
        language = clean_language(body.language)
    except VoiceError as exc:
        return _error(exc)
    engine = get_engine(request)
    storage.purge_expired_previews()
    result = await _synthesis(engine.design_preview(text, description, language))
    wav = encode_wav(result["audio"], result["sr"])
    record = storage.create_preview(current_user.user_id, "design", wav, {
        "description": description, "transcript": text, "preview_text": text, "language": language,
        "source": "voice-design", "model": get_settings().avatar_qwen_model,
        "retried": bool(result.get("retried"))})
    record = storage.update_preview(current_user.user_id, record["preview_id"],
                                    warnings=_reference_warnings(record["reference_duration"]))
    return _preview_public(record)


@voices_router.post("/references", status_code=status.HTTP_201_CREATED)
async def upload_reference(request: Request,
                           file: UploadFile = File(...),
                           consent: bool = Form(False),
                           suggest_transcript: bool = Form(True),
                           language: str = Form("English"),
                           current_user: CurrentUser = Depends(get_current_user),
                           storage: VoiceStorage = Depends(get_storage)) -> Any:
    """Validate and normalize an uploaded recording as a clone candidate (not saved)."""
    settings = get_settings()
    if not consent:
        return _error(VoiceError("Confirm that you own this recording or have permission to use the voice"))
    try:
        language = clean_language(language)
    except VoiceError as exc:
        return _error(exc)
    data = await file.read(settings.voice_upload_max_bytes + 1)
    if len(data) > settings.voice_upload_max_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"The file is larger than {settings.voice_upload_max_bytes // (1024 * 1024)} MB")
    try:
        audio = await asyncio.to_thread(
            decode_reference, data, file.filename, max_bytes=settings.voice_upload_max_bytes,
            min_seconds=settings.voice_reference_min_seconds,
            max_seconds=settings.voice_reference_max_seconds)
    except AudioValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    storage.purge_expired_previews()
    record = storage.create_preview(current_user.user_id, "clone", encode_wav(audio.samples, audio.sample_rate), {
        "transcript": None, "language": language, "consent": True, "source": "upload",
        "source_format": audio.source_format, "source_sample_rate": audio.source_sample_rate,
        "source_channels": audio.source_channels, "suggested_transcript": None})
    if suggest_transcript:
        suggestion = None
        try:
            engine = get_engine(request)
            suggestion = await engine.transcriber.transcribe(
                resample(audio.samples, audio.sample_rate, STT_SAMPLE_RATE))
        except Exception as exc:  # noqa: BLE001 - a suggestion is optional; the user types it
            logger.warning("Reference transcription failed: %s", exc)
        record = storage.update_preview(current_user.user_id, record["preview_id"],
                                        suggested_transcript=suggestion or None)
    return _preview_public(record)


@voices_router.post("/previews/{preview_id}/sample")
async def create_clone_sample(preview_id: str, body: CloneSampleRequest, request: Request,
                              current_user: CurrentUser = Depends(get_current_user),
                              storage: VoiceStorage = Depends(get_storage)) -> Any:
    """Speak `preview_text` with the uploaded reference + transcript (Base model), as it would be saved."""
    try:
        record = storage.get_preview(current_user.user_id, preview_id)
        if record["kind"] != "clone":
            raise VoiceError("Only uploaded recordings take a cloned sample")
        transcript = clean_text(body.transcript, "Reference transcript")
        text = clean_text(body.preview_text, "Preview text", limit=MAX_PREVIEW_TEXT)
        language = clean_language(body.language)
    except PreviewNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preview not found or expired")
    except VoiceError as exc:
        return _error(exc)
    from chatbot.voices.audio import voice_fingerprint

    spec = {"voice_id": f"preview-{preview_id}",
            "fingerprint": voice_fingerprint(record["reference_sha256"], transcript),
            "reference": storage.preview_reference_relative(current_user.user_id, preview_id),
            "transcript": transcript, "language": language}
    engine = get_engine(request)
    result = await _synthesis(engine.voice_preview(text, spec))
    record = storage.set_sample(current_user.user_id, preview_id, encode_wav(result["audio"], result["sr"]),
                                transcript=transcript, preview_text=text, language=language)
    return _preview_public(record)


@voices_router.get("/previews/{preview_id}")
async def get_preview(preview_id: str, current_user: CurrentUser = Depends(get_current_user),
                      storage: VoiceStorage = Depends(get_storage)) -> dict:
    try:
        return _preview_public(storage.get_preview(current_user.user_id, preview_id))
    except PreviewNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preview not found or expired")


@voices_router.get("/previews/{preview_id}/audio/{which}")
async def get_preview_audio(preview_id: str, which: str,
                            current_user: CurrentUser = Depends(get_current_user),
                            storage: VoiceStorage = Depends(get_storage)) -> Response:
    if which not in ("reference", "sample"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown preview audio")
    try:
        return _wav(storage.preview_file(current_user.user_id, preview_id, which).read_bytes())
    except PreviewNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preview audio not found or expired")


@voices_router.delete("/previews/{preview_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_preview(preview_id: str, request: Request,
                         current_user: CurrentUser = Depends(get_current_user),
                         storage: VoiceStorage = Depends(get_storage)) -> Response:
    """Discard a candidate (its files are removed); VoiceDesign is released when no longer needed."""
    try:
        kind = storage.get_preview(current_user.user_id, preview_id).get("kind")
    except PreviewNotFound:
        kind = None
    storage.delete_preview(current_user.user_id, preview_id)
    if kind == "design":
        await _release_design(request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _release_design(request: Request) -> None:
    host = getattr(request.app.state, "avatar_runtime_host", None)
    if host is None:
        return
    try:
        await host.engine.unload_design()
    except Exception as exc:  # noqa: BLE001 - the idle timer releases it anyway
        logger.warning("Could not release VoiceDesign: %s", exc)


# ---------------------------------------------------------------------- saved voices


@voices_router.post("", status_code=status.HTTP_201_CREATED)
async def save_voice(body: SaveVoiceRequest, request: Request,
                     current_user: CurrentUser = Depends(get_current_user),
                     service: VoiceService = Depends(get_service)) -> Any:
    """Save an approved candidate. Its reference recording is kept exactly as previewed."""
    try:
        preview = service.storage.get_preview(current_user.user_id, body.preview_id)
    except PreviewNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preview not found or expired; generate a new one")
    if float(preview.get("reference_duration") or 0) < MIN_SAVED_REFERENCE_SECONDS:
        return _error(VoiceError(_reference_warnings(float(preview.get("reference_duration") or 0))[0]))
    try:
        voice = await service.save_from_preview(
            current_user.user_id, body.preview_id, name=body.name, description=body.description,
            language=body.language, transcript=body.transcript, consent=body.consent)
    except VoiceError as exc:
        return _error(exc)
    if preview.get("kind") == "design":
        await _release_design(request)
    return voice_public(voice)


@voices_router.post("/default/initialize", status_code=status.HTTP_201_CREATED)
async def initialize_default(request: Request, replace: bool = False,
                             admin: CurrentUser = Depends(require_admin),
                             service: VoiceService = Depends(get_service)) -> Any:
    """Admin: render the built-in default voice's reference (explicit initialization)."""
    from chatbot.voices.init_default import InitializationError, initialize_default_voice

    settings = get_settings()
    engine = get_engine(request)
    try:
        return await _synthesis(initialize_default_voice(
            engine, service.db, service.storage, description=settings.avatar_qwen_voice,
            design_model=settings.avatar_qwen_model, clone_model=settings.avatar_qwen_clone_model,
            replace=replace, initialized_by=f"api:{admin.user_id}"))
    except InitializationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@voices_router.get("/sessions/{session_id}")
async def get_session_voice(session_id: str, current_user: CurrentUser = Depends(get_current_user),
                            service: VoiceService = Depends(get_service)) -> Any:
    try:
        return await service.selection(current_user.user_id, session_id)
    except VoiceError as exc:
        return _error(exc)


@voices_router.put("/sessions/{session_id}")
async def set_session_voice(session_id: str, body: SessionVoiceRequest,
                            current_user: CurrentUser = Depends(get_current_user),
                            service: VoiceService = Depends(get_service)) -> Any:
    """Choose who speaks in this conversation (null: the companion's default voice).

    Applies from the next speech turn; a turn already speaking keeps its voice.
    """
    try:
        return await service.set_session_voice(current_user.user_id, session_id, body.voice_id)
    except VoiceError as exc:
        return _error(exc)


@voices_router.get("/{voice_id}")
async def get_voice(voice_id: str, current_user: CurrentUser = Depends(get_current_user),
                    service: VoiceService = Depends(get_service)) -> Any:
    try:
        voice = await service.get_usable(current_user.user_id, voice_id)
    except VoiceError as exc:
        return _error(exc)
    references = await service.references(voice.voice_id) if not voice.is_builtin else {
        "companions": [], "sessions": []}
    return {**voice_public(voice), "references": references}


@voices_router.patch("/{voice_id}")
async def update_voice(voice_id: str, body: UpdateVoiceRequest, request: Request,
                       current_user: CurrentUser = Depends(get_current_user),
                       service: VoiceService = Depends(get_service)) -> Any:
    try:
        voice, reference_changed = await service.update(
            current_user.user_id, voice_id, name=body.name, description=body.description,
            language=body.language, transcript=body.transcript)
    except VoiceError as exc:
        return _error(exc)
    if reference_changed:
        await _forget(request, voice.voice_id)
    return voice_public(voice)


@voices_router.delete("/{voice_id}")
async def delete_voice(voice_id: str, request: Request, replacement_voice_id: str | None = None,
                       clear_session_overrides: bool = False,
                       current_user: CurrentUser = Depends(get_current_user),
                       service: VoiceService = Depends(get_service)) -> Any:
    try:
        result = await service.delete(current_user.user_id, voice_id,
                                      replacement_voice_id=replacement_voice_id,
                                      clear_session_overrides=clear_session_overrides)
    except VoiceError as exc:
        return _error(exc)
    await _forget(request, result["voice_id"])
    return result


async def _forget(request: Request, voice_id: str) -> None:
    host = getattr(request.app.state, "avatar_runtime_host", None)
    if host is None:
        return
    try:
        await host.engine.forget_voice(voice_id)
    except Exception as exc:  # noqa: BLE001 - a stale prompt is keyed by fingerprint anyway
        logger.warning("Could not drop cached voice prompt for %s: %s", voice_id, exc)


@voices_router.get("/{voice_id}/audio")
async def get_voice_audio(voice_id: str, current_user: CurrentUser = Depends(get_current_user),
                          service: VoiceService = Depends(get_service)) -> Response:
    """The voice's saved reference recording."""
    try:
        voice = await service.get_usable(current_user.user_id, voice_id)
        path = service.storage.reference_file(voice.reference_path)
    except VoiceError as exc:
        return _error(exc)
    except Exception:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reference recording missing")
    return _wav(path.read_bytes())


@voices_router.post("/{voice_id}/speak")
async def speak_with_voice(voice_id: str, body: SpeakRequest, request: Request,
                           current_user: CurrentUser = Depends(get_current_user),
                           service: VoiceService = Depends(get_service)) -> Response:
    """Preview a saved voice saying `text`, through the same Base-model path as conversations."""
    try:
        voice = await service.get_usable(current_user.user_id, voice_id)
        service.storage.reference_file(voice.reference_path)
    except VoiceError as exc:
        return _error(exc)
    except Exception:
        raise HTTPException(status.HTTP_409_CONFLICT, "Reference recording missing")
    engine = get_engine(request)
    result = await _synthesis(engine.voice_preview(body.text.strip(), voice_spec(voice)))
    return _wav(encode_wav(result["audio"], result["sr"]))


__all__ = ["voices_router", "read_wav"]
