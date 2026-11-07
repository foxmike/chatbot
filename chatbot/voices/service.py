"""Voice Library operations: ownership, saving approved previews, references and resolution.

Who speaks in a conversation is resolved at the start of every speech turn:

    1. the conversation's (session's) voice override, if set;
    2. otherwise the voice assigned to the conversation's companion;
    3. otherwise the built-in default voice.

A voice that is selected but unusable (deleted, not owned, reference missing) is an error,
never silently replaced by the next level. Voices are referenced by ID only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.db.models import Companion, Session, Voice
from chatbot.voices.audio import voice_fingerprint
from chatbot.voices.storage import PreviewNotFound, StorageError, VoiceStorage, canonical_id

LANGUAGES = ("English", "Chinese", "Japanese", "Korean", "German", "French", "Russian",
             "Portuguese", "Spanish", "Italian")
MODEL_FAMILY = "qwen3-tts-12hz"
METHODS = ("designed", "cloned")
DEFAULT_VOICE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "chatbot:voice-library:builtin-default"))
MAX_NAME = 80
MAX_TEXT = 2000


class VoiceError(Exception):
    status_code = 400

    def __init__(self, message: str, **details: Any):
        super().__init__(message)
        self.details = details


class VoiceNotFound(VoiceError):
    status_code = 404


class VoicePermissionError(VoiceError):
    status_code = 403


class VoiceInUse(VoiceError):
    status_code = 409


class VoiceUnavailable(VoiceError):
    """The conversation's voice cannot be used; speech must not fall back to another voice."""

    status_code = 409


@dataclass(frozen=True)
class ResolvedVoice:
    voice: Voice
    source: str          # session | companion | default

    @property
    def spec(self) -> dict:
        return voice_spec(self.voice)


def voice_spec(voice: Voice) -> dict:
    """What the TTS worker needs to speak with `voice` (see chatbot.avatar.media.VOICE_FIELDS)."""
    return {"voice_id": voice.voice_id,
            "fingerprint": voice_fingerprint(voice.reference_sha256, voice.reference_transcript),
            "reference": voice.reference_path, "transcript": voice.reference_transcript,
            "language": voice.language}


def clean_name(name: Any) -> str:
    text = " ".join(str(name or "").split())
    if not text:
        raise VoiceError("A voice name is required")
    if len(text) > MAX_NAME:
        raise VoiceError(f"Voice names are at most {MAX_NAME} characters")
    return text


def clean_text(value: Any, label: str, *, required: bool = True, limit: int = MAX_TEXT) -> str | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise VoiceError(f"{label} is required")
        return None
    if len(text) > limit:
        raise VoiceError(f"{label} is at most {limit} characters")
    return text


def clean_language(value: Any) -> str:
    language = str(value or "English").strip()
    for known in LANGUAGES:
        if known.lower() == language.lower():
            return known
    raise VoiceError(f"Unsupported language {language!r}; choose one of {', '.join(LANGUAGES)}")


def _uuid(value: Any, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise VoiceNotFound(f"{what} not found") from exc


class VoiceService:
    def __init__(self, db: AsyncSession, storage: VoiceStorage, *, design_model: str, clone_model: str):
        self.db = db
        self.storage = storage
        self.design_model = design_model
        self.clone_model = clone_model

    # ------------------------------------------------------------------ lookup

    async def list(self, user_id: str) -> list[Voice]:
        rows = await self.db.execute(
            sa.select(Voice)
            .where(sa.or_(Voice.user_id == _uuid(user_id, "User"), Voice.is_builtin.is_(True)))
            .order_by(Voice.is_builtin.desc(), Voice.created_at, Voice.name))
        return list(rows.scalars())

    async def _find(self, voice_id: Any) -> Voice | None:
        try:
            key = canonical_id(voice_id)
        except (TypeError, ValueError):
            return None
        return await self.db.get(Voice, key)

    async def get_usable(self, user_id: str, voice_id: Any) -> Voice:
        """A voice this user may speak with: their own, or a built-in one. Otherwise 404."""
        voice = await self._find(voice_id)
        if voice is None or not (voice.is_builtin or str(voice.user_id) == str(_uuid(user_id, "User"))):
            raise VoiceNotFound("Voice not found")
        if voice.status != "ready":
            raise VoiceUnavailable(f"Voice {voice.name!r} is not ready")
        return voice

    async def get_owned(self, user_id: str, voice_id: Any) -> Voice:
        """A voice this user may modify (built-in voices are read-only)."""
        voice = await self.get_usable(user_id, voice_id)
        if voice.is_builtin:
            raise VoicePermissionError("Built-in voices cannot be changed or deleted")
        return voice

    # ------------------------------------------------------------------ saving

    async def save_from_preview(self, user_id: str, preview_id: str, *, name: Any,
                                description: Any = None, language: Any = None,
                                transcript: Any = None, consent: bool = False) -> Voice:
        """Save the approved candidate exactly as previewed: its reference file is moved, not regenerated."""
        try:
            preview = self.storage.get_preview(user_id, preview_id)
        except PreviewNotFound as exc:
            raise VoiceNotFound("Preview not found or expired; generate a new one") from exc
        name = clean_name(name)
        kind = preview["kind"]
        if kind == "design":
            method = "designed"
            text = preview["transcript"]   # exactly what VoiceDesign spoke in the reference
            description = clean_text(description or preview.get("description"), "Voice description")
            creation_model = self.design_model
        elif kind == "clone":
            method = "cloned"
            if not (consent or preview.get("consent")):
                raise VoiceError("Confirm that you own this recording or have permission to use the voice")
            text = clean_text(transcript or preview.get("transcript"), "Reference transcript")
            description = clean_text(description, "Description", required=False)
            creation_model = "uploaded-recording"
        else:
            raise VoiceError("Unknown preview type")
        language = clean_language(language or preview.get("language"))
        voice_id = str(uuid.uuid4())
        try:
            stored = self.storage.promote(user_id, preview_id, voice_id)
        except (PreviewNotFound, StorageError) as exc:
            raise VoiceError(f"The preview could not be saved: {exc}") from exc
        extra = {"preview_id": preview_id, "source": preview.get("source"),
                 "reference_bytes": stored.size}
        if kind == "clone":
            extra.update(consent_confirmed=True, source_format=preview.get("source_format"),
                         source_sample_rate=preview.get("source_sample_rate"),
                         source_channels=preview.get("source_channels"),
                         transcript_suggested=preview.get("suggested_transcript"))
        voice = Voice(voice_id=voice_id, user_id=_uuid(user_id, "User"), name=name, method=method,
                      description=description, reference_path=stored.reference_path,
                      reference_sha256=stored.sha256, reference_duration=stored.duration,
                      reference_sample_rate=stored.sample_rate, reference_transcript=text,
                      language=language, model_family=MODEL_FAMILY, creation_model=creation_model,
                      synthesis_model=self.clone_model, status="ready", is_builtin=False,
                      extra_metadata={k: v for k, v in extra.items() if v is not None})
        self.db.add(voice)
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            self.storage.delete_voice(voice_id)   # no orphaned file without a record
            raise
        await self.db.refresh(voice)
        return voice

    # ------------------------------------------------------------------ editing

    async def update(self, user_id: str, voice_id: Any, *, name: Any = None, description: Any = None,
                     language: Any = None, transcript: Any = None) -> tuple[Voice, bool]:
        """Edit metadata. Returns (voice, reference_changed); a transcript change re-keys the voice."""
        voice = await self.get_owned(user_id, voice_id)
        before = voice_fingerprint(voice.reference_sha256, voice.reference_transcript)
        if name is not None:
            voice.name = clean_name(name)
        if description is not None:
            voice.description = clean_text(description, "Description", required=False)
        if language is not None:
            voice.language = clean_language(language)
        if transcript is not None:
            voice.reference_transcript = clean_text(transcript, "Reference transcript")
        await self.db.commit()
        await self.db.refresh(voice)
        return voice, voice_fingerprint(voice.reference_sha256, voice.reference_transcript) != before

    async def references(self, voice_id: str) -> dict:
        companions = (await self.db.execute(
            sa.select(Companion.companion_id, Companion.name).where(Companion.voice_id == voice_id))).all()
        sessions = (await self.db.execute(
            sa.select(Session.id, Session.title).where(Session.voice_id == voice_id))).all()
        return {"companions": [{"companion_id": c, "name": n} for c, n in companions],
                "sessions": [{"session_id": str(s), "title": t} for s, t in sessions]}

    async def delete(self, user_id: str, voice_id: Any, *, replacement_voice_id: Any = None,
                     clear_session_overrides: bool = False) -> dict:
        """Delete a voice and its reference file.

        A voice still assigned to a companion, or chosen as a conversation's voice, is not
        deleted unless the caller names a `replacement_voice_id` (all references move to it)
        or, for conversation overrides only, asks to clear them. Nothing is reassigned silently.
        """
        voice = await self.get_owned(user_id, voice_id)
        refs = await self.references(voice.voice_id)
        replacement = None
        if replacement_voice_id:
            replacement = await self.get_usable(user_id, replacement_voice_id)
            if replacement.voice_id == voice.voice_id:
                raise VoiceError("A voice cannot replace itself")
        blocking_companions = refs["companions"] if replacement is None else []
        blocking_sessions = refs["sessions"] if replacement is None and not clear_session_overrides else []
        if blocking_companions or blocking_sessions:
            raise VoiceInUse(
                f"Voice {voice.name!r} is in use; reassign it or choose a replacement voice",
                companions=blocking_companions, sessions=blocking_sessions)
        if refs["companions"]:
            await self.db.execute(sa.update(Companion).where(Companion.voice_id == voice.voice_id)
                                  .values(voice_id=replacement.voice_id))
        if refs["sessions"]:
            await self.db.execute(sa.update(Session).where(Session.voice_id == voice.voice_id)
                                  .values(voice_id=replacement.voice_id if replacement else None))
        await self.db.delete(voice)
        await self.db.commit()
        self.storage.delete_voice(voice.voice_id)
        return {"voice_id": voice.voice_id, "replaced_with": replacement.voice_id if replacement else None,
                "reassigned_companions": len(refs["companions"]),
                "updated_sessions": len(refs["sessions"])}

    async def assignable(self, user_id: str, voice_id: Any) -> str | None:
        """Validate a voice for assignment to a companion (None clears the assignment)."""
        if voice_id in (None, ""):
            return None
        return (await self.get_usable(user_id, voice_id)).voice_id

    # ------------------------------------------------------------------ conversations

    async def _session(self, user_id: str, session_id: Any) -> Session:
        session = await self.db.get(Session, _uuid(session_id, "Conversation"))
        if session is None or str(session.user_id) != str(_uuid(user_id, "User")):
            raise VoiceNotFound("Conversation not found")
        return session

    async def _companion_voice_id(self, user_id: str, session: Session) -> str | None:
        if not session.companion_id:
            return None
        companion = await self.db.get(Companion, session.companion_id)
        if companion is None or str(companion.user_id) != str(_uuid(user_id, "User")):
            return None
        return companion.voice_id

    async def selection(self, user_id: str, session_id: Any) -> dict:
        """The conversation's voice settings, and which voice will actually speak."""
        session = await self._session(user_id, session_id)
        companion_voice_id = await self._companion_voice_id(user_id, session)
        default = await self._find(DEFAULT_VOICE_ID)
        effective = error = None
        source = None
        try:
            resolved = await self.resolve(user_id, session_id)
            effective, source = resolved.voice.voice_id, resolved.source
        except VoiceUnavailable as exc:
            error = str(exc)
        return {"session_id": str(session.id), "override_voice_id": session.voice_id,
                "companion_id": session.companion_id, "companion_voice_id": companion_voice_id,
                "default_voice_id": default.voice_id if default is not None else None,
                "effective_voice_id": effective, "effective_source": source, "error": error}

    async def set_session_voice(self, user_id: str, session_id: Any, voice_id: Any) -> dict:
        """Choose this conversation's voice (None: use the companion's default)."""
        session = await self._session(user_id, session_id)
        session.voice_id = None if voice_id in (None, "") else (await self.get_usable(user_id, voice_id)).voice_id
        await self.db.commit()
        return await self.selection(user_id, session_id)

    async def resolve(self, user_id: str, session_id: Any) -> ResolvedVoice:
        session = await self._session(user_id, session_id)
        if session.voice_id:
            return ResolvedVoice(await self._ready(user_id, session.voice_id,
                                                   "The voice chosen for this conversation"), "session")
        companion_voice_id = await self._companion_voice_id(user_id, session)
        if companion_voice_id:
            return ResolvedVoice(await self._ready(user_id, companion_voice_id,
                                                   "This companion's default voice"), "companion")
        default = await self._find(DEFAULT_VOICE_ID)
        if default is None or not default.is_builtin:
            raise VoiceUnavailable(
                "No voice is available: choose a saved voice, or initialize the built-in default "
                "voice with `python -m chatbot.voices.init_default`")
        return ResolvedVoice(await self._ready(user_id, default.voice_id, "The default voice"), "default")

    async def _ready(self, user_id: str, voice_id: str, label: str) -> Voice:
        try:
            voice = await self.get_usable(user_id, voice_id)
        except (VoiceNotFound, VoiceUnavailable) as exc:
            raise VoiceUnavailable(f"{label} is not available") from exc
        try:
            self.storage.reference_file(voice.reference_path)
        except StorageError as exc:
            raise VoiceUnavailable(f"{label} ({voice.name}) has no reference recording") from exc
        return voice


def voice_public(voice: Voice) -> dict:
    """API representation (no filesystem paths)."""
    extra = voice.extra_metadata or {}
    return {"voice_id": voice.voice_id, "name": voice.name, "method": voice.method,
            "description": voice.description, "reference_transcript": voice.reference_transcript,
            "language": voice.language, "model_family": voice.model_family,
            "creation_model": voice.creation_model, "synthesis_model": voice.synthesis_model,
            "reference_duration": voice.reference_duration,
            "reference_sample_rate": voice.reference_sample_rate,
            "reference_sha256": voice.reference_sha256, "status": voice.status,
            "is_builtin": voice.is_builtin,
            "created_at": voice.created_at.isoformat() if voice.created_at else None,
            "updated_at": voice.updated_at.isoformat() if voice.updated_at else None,
            "metadata": {k: v for k, v in extra.items() if k in (
                "consent_confirmed", "source_format", "source_sample_rate", "source_channels",
                "initialized_by", "initialized_at", "note")}}
