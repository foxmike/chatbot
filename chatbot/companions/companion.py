"""Companion model and per-user companion management logic."""

from __future__ import annotations

import json
from uuid import UUID
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.visual.appearance import (
    AppearanceContinuitySnapshot,
    AppearanceManager,
    AppearanceProfile,
    Outfit,
    OutfitManager,
    stable_character_seed,
    stable_outfit_id,
)
from chatbot.visual.generation import GenerationOptions, create_image_generation_service
from chatbot.db.models import Voice


class CompanionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    gender: str | None = None
    persona: str | None = None
    avatar_header: str | None = Field(default=None, max_length=255)
    description: str | None = None
    avatar_url: str | None = None
    backstory: str | None = None
    personality_sliders: dict[str, float] | None = None
    humor_style: str | None = None
    communication_style: str | None = None
    punctuation_habits: dict[str, object] | None = None
    sentence_length_profile: dict[str, object] | None = None
    emoji_style: dict[str, object] | None = None
    vocabulary_density: float | None = Field(default=None, ge=0.0, le=1.0)
    slang_profile: dict[str, object] | None = None
    adaptive_verbosity: float | None = Field(default=None, ge=0.0, le=1.0)
    favorite_topics: list[str] | None = None
    # Default voice (a Voice Library voice id). Only changed when the request includes it, so
    # editors that do not know about voices never clear an assignment.
    voice_id: str | None = Field(default=None, max_length=36)

    @field_validator("gender")
    @classmethod
    def validate_gender(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip().lower()
        if cleaned not in {"male", "female"}:
            raise ValueError("gender must be male or female")
        return cleaned

    @field_validator("avatar_url")
    @classmethod
    def validate_avatar_url(cls, v: str | None) -> str | None:
        if v is not None:
            if v.startswith("data:") or ";base64," in v:
                raise ValueError("avatar_url must be a URL path, not raw base64 data")
        return v


class CompanionDisplay(BaseModel):
    avatar_url: str | None
    appearance: AppearanceContinuitySnapshot


class CompanionResponse(BaseModel):
    companion_id: str
    user_id: str
    name: str
    gender: str | None
    persona: str | None
    avatar_header: str | None
    description: str | None
    avatar_url: str | None
    backstory: str | None
    personality_sliders: dict[str, float] | None
    humor_style: str | None
    communication_style: str | None
    punctuation_habits: dict[str, object] | None
    sentence_length_profile: dict[str, object] | None
    emoji_style: dict[str, object] | None
    vocabulary_density: float | None
    slang_profile: dict[str, object] | None
    adaptive_verbosity: float | None = None
    favorite_topics: list[str] | None
    voice_id: str | None = None
    display: CompanionDisplay
    created_at: str
    updated_at: str | None


class CompanionListResponse(BaseModel):
    companions: list[CompanionResponse]


COMPANION_MODEL_DEFINITIONS: dict[str, dict[str, dict]] = {
    "companions": {
        "companion_id": {"type": "String(36)", "primary_key": True, "default": uuid4},
        "user_id": {
            "type": "String(36)",
            "foreign_key": "users.user_id",
            "nullable": False,
        },
        "name": {"type": "String(128)", "nullable": False},
        "gender": {"type": "String(10)", "nullable": True},
        "persona": {"type": "Text", "nullable": True},
        "avatar_header": {"type": "String(255)", "nullable": True},
        "description": {"type": "Text", "nullable": True},
        "avatar_url": {"type": "String(512)", "nullable": True},
        "backstory": {"type": "Text", "nullable": True},
        "personality_sliders": {"type": "JSON", "nullable": True},
        "humor_style": {"type": "String(20)", "nullable": True},
        "communication_style": {"type": "String(30)", "nullable": True},
        "punctuation_habits": {"type": "JSON", "nullable": True},
        "sentence_length_profile": {"type": "JSON", "nullable": True},
        "emoji_style": {"type": "JSON", "nullable": True},
        "vocabulary_density": {"type": "Float", "nullable": True},
        "slang_profile": {"type": "JSON", "nullable": True},
        "favorite_topics": {"type": "JSON", "nullable": True},
        "created_at": {"type": "DateTime", "default": "func.now()"},
        "updated_at": {"type": "DateTime", "onupdate": "func.now()"},
    },
}


def _clean_display_text(value: str | None) -> str:
    return " ".join(value.split()) if value else ""


def _build_character_description(
    name: str,
    persona: str | None,
    description: str | None,
) -> str:
    parts = [f"Companion named {name}"]
    clean_description = _clean_display_text(description)
    clean_persona = _clean_display_text(persona)
    if clean_description:
        parts.append(clean_description)
    if clean_persona:
        parts.append(f"Persona: {clean_persona}")
    return ". ".join(parts)


def _build_companion_display(
    companion_id: str,
    name: str,
    persona: str | None,
    description: str | None,
    avatar_url: str | None,
    created_at: str,
) -> CompanionDisplay:
    appearance_manager = _build_companion_appearance_manager(
        companion_id=companion_id,
        name=name,
        persona=persona,
        description=description,
        created_at=created_at,
    )
    snapshot = appearance_manager.build_continuity_snapshot(
        f"Companion display portrait of {name}"
    )
    return CompanionDisplay(avatar_url=avatar_url, appearance=snapshot)


def _build_companion_appearance_manager(
    companion_id: str,
    name: str,
    persona: str | None,
    description: str | None,
    created_at: str,
) -> AppearanceManager:
    profile = AppearanceProfile(
        character_seed=stable_character_seed(companion_id),
        character_description=_build_character_description(name, persona, description),
    )
    outfit_manager = OutfitManager(
        outfits=[
            Outfit(
                outfit_id=stable_outfit_id(companion_id, "default"),
                name="default",
                description="Default outfit",
                is_active=True,
                created_at=created_at,
            )
        ]
    )
    appearance_manager = AppearanceManager(
        profile=profile,
        outfit_manager=outfit_manager,
    )
    return appearance_manager


def _row_to_companion(row: object) -> CompanionResponse:
    m = row._mapping  # noqa: SLF001
    companion_id = str(m["companion_id"])
    name = str(m["name"])
    persona = m["persona"]
    description = m["description"]
    avatar_url = m["avatar_url"]
    created_at = str(m["created_at"])
    return CompanionResponse(
        companion_id=companion_id,
        user_id=str(m["user_id"]),
        name=name,
        gender=m.get("gender"),
        persona=persona,
        avatar_header=m.get("avatar_header"),
        description=description,
        avatar_url=avatar_url,
        backstory=m.get("backstory"),
        personality_sliders=m.get("personality_sliders"),
        humor_style=m.get("humor_style"),
        communication_style=m.get("communication_style"),
        punctuation_habits=m.get("punctuation_habits"),
        sentence_length_profile=m.get("sentence_length_profile"),
        emoji_style=m.get("emoji_style"),
        vocabulary_density=m.get("vocabulary_density"),
        slang_profile=m.get("slang_profile"),
        adaptive_verbosity=m.get("adaptive_verbosity"),
        favorite_topics=m.get("favorite_topics"),
        voice_id=m.get("voice_id"),
        display=_build_companion_display(
            companion_id=companion_id,
            name=name,
            persona=persona,
            description=description,
            avatar_url=avatar_url,
            created_at=created_at,
        ),
        created_at=created_at,
        updated_at=str(m["updated_at"]) if m["updated_at"] is not None else None,
    )


class CompanionVoiceError(ValueError):
    """The requested default voice does not exist or is not usable by this user."""


class CompanionManager:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _validated_voice(self, user_id: str, voice_id: str | None) -> str | None:
        """The voice id if the user may use it (their own or built-in, ready); None clears."""
        if voice_id in (None, ""):
            return None
        try:
            owner = UUID(str(user_id))
        except ValueError as exc:
            raise CompanionVoiceError("Voice not found") from exc
        result = await self._session.execute(
            sa.select(Voice.voice_id).where(
                Voice.voice_id == str(voice_id), Voice.status == "ready",
                sa.or_(Voice.is_builtin.is_(True), Voice.user_id == owner)))
        found = result.scalar_one_or_none()
        if found is None:
            raise CompanionVoiceError("Voice not found")
        return str(found)

    async def _set_voice(self, companion_id: str, voice_id: str | None) -> None:
        await self._session.execute(
            text("UPDATE companions SET voice_id = :voice_id WHERE companion_id = :companion_id"),
            dict(voice_id=voice_id, companion_id=companion_id))

    async def create(self, user_id: str, data: CompanionCreateRequest) -> CompanionResponse:
        voice_id = await self._validated_voice(user_id, data.voice_id)
        companion_id = str(uuid4())
        avatar_url = data.avatar_url or None
        # exec_driver_sql sends raw SQL to asyncpg with native $N positional params,
        # avoiding SQLAlchemy text()'s :name parser which misreads ::jsonb as a
        # second named parameter (:jsonb).
        conn = await self._session.connection()
        result = await conn.exec_driver_sql(
            """INSERT INTO companions (
                   companion_id, user_id, name, gender, persona, avatar_header, description, avatar_url,
                   backstory, personality_sliders, humor_style, communication_style,
                   punctuation_habits, sentence_length_profile, emoji_style,
                   vocabulary_density, slang_profile, favorite_topics, created_at
               )
               VALUES (
                   $1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12,
                   $13::jsonb, $14::jsonb, $15::jsonb, $16, $17::jsonb,
                   $18::jsonb, now()
               )
               RETURNING *""",
            (
                companion_id,
                user_id,
                data.name,
                data.gender,
                data.persona,
                data.avatar_header,
                data.description,
                avatar_url,
                data.backstory,
                json.dumps(data.personality_sliders) if data.personality_sliders is not None else None,
                data.humor_style,
                data.communication_style,
                json.dumps(data.punctuation_habits) if data.punctuation_habits is not None else None,
                json.dumps(data.sentence_length_profile) if data.sentence_length_profile is not None else None,
                json.dumps(data.emoji_style) if data.emoji_style is not None else None,
                data.vocabulary_density,
                json.dumps(data.slang_profile) if data.slang_profile is not None else None,
                json.dumps(data.favorite_topics) if data.favorite_topics is not None else None,
            ),
        )
        row = result.fetchone()
        if voice_id is not None:
            await self._set_voice(companion_id, voice_id)
            await self._session.commit()
            return await self.get(user_id, companion_id)
        await self._session.commit()
        return _row_to_companion(row)

    async def _generate_avatar_url(
        self,
        user_id: str,
        companion_id: str,
        data: CompanionCreateRequest,
    ) -> str | None:
        appearance_manager = _build_companion_appearance_manager(
            companion_id=companion_id,
            name=data.name,
            persona=data.persona,
            description=data.description,
            created_at="",
        )
        service = create_image_generation_service(appearance_manager)
        persisted = await service.generate_and_persist_with_reference(
            scene_description=f"Companion avatar portrait of {data.name}",
            options=GenerationOptions(width=512, height=512),
            user_id=user_id,
            companion_id=companion_id,
            db_session=self._session,
        )
        return persisted.image_url

    async def list_by_user(self, user_id: str) -> CompanionListResponse:
        stmt = text(
            """SELECT c.*, u.adaptive_verbosity
               FROM companions c
               JOIN users u ON c.user_id = u.id
               WHERE c.user_id = :user_id"""
        )
        result = await self._session.execute(stmt, dict(user_id=user_id))
        rows = result.fetchall()
        return CompanionListResponse(
            companions=[_row_to_companion(r) for r in rows]
        )

    async def get(self, user_id: str, companion_id: str) -> CompanionResponse | None:
        stmt = text(
            """SELECT c.*, u.adaptive_verbosity
               FROM companions c
               JOIN users u ON c.user_id = u.id
               WHERE c.companion_id = :companion_id"""
        )
        result = await self._session.execute(stmt, dict(companion_id=companion_id))
        row = result.fetchone()
        if row is None:
            return None
        mapped = row._mapping  # noqa: SLF001
        if str(mapped["user_id"]) != user_id:
            return None
        return _row_to_companion(row)

    async def update(
        self, user_id: str, companion_id: str, data: CompanionCreateRequest
    ) -> CompanionResponse | None:
        existing = await self.get(user_id, companion_id)
        if existing is None:
            return None
        voice_given = "voice_id" in data.model_fields_set
        voice_id = await self._validated_voice(user_id, data.voice_id) if voice_given else None

        conn = await self._session.connection()
        await conn.exec_driver_sql(
            """UPDATE companions
               SET name = $1, gender = $2, persona = $3, avatar_header = $4,
                   description = $5, avatar_url = $6,
                   backstory = $7,
                   personality_sliders = $8::jsonb,
                   humor_style = $9,
                   communication_style = $10,
                   punctuation_habits = $11::jsonb,
                   sentence_length_profile = $12::jsonb,
                   emoji_style = $13::jsonb,
                   vocabulary_density = $14,
                   slang_profile = $15::jsonb,
                   favorite_topics = $16::jsonb,
                   updated_at = now()
               WHERE companion_id = $17""",
            (
                data.name,
                data.gender,
                data.persona,
                data.avatar_header,
                data.description,
                data.avatar_url,
                data.backstory,
                json.dumps(data.personality_sliders) if data.personality_sliders is not None else None,
                data.humor_style,
                data.communication_style,
                json.dumps(data.punctuation_habits) if data.punctuation_habits is not None else None,
                json.dumps(data.sentence_length_profile) if data.sentence_length_profile is not None else None,
                json.dumps(data.emoji_style) if data.emoji_style is not None else None,
                data.vocabulary_density,
                json.dumps(data.slang_profile) if data.slang_profile is not None else None,
                json.dumps(data.favorite_topics) if data.favorite_topics is not None else None,
                companion_id,
            ),
        )
        await conn.exec_driver_sql(
            """UPDATE users
               SET adaptive_verbosity = $1,
                   updated_at = now()
               WHERE id = $2""",
            (data.adaptive_verbosity, user_id),
        )
        if voice_given:
            await self._set_voice(companion_id, voice_id)
        await self._session.commit()
        return await self.get(user_id, companion_id)

    async def delete(self, user_id: str, companion_id: str) -> bool:
        existing = await self.get(user_id, companion_id)
        if existing is None:
            return False

        stmt = text("DELETE FROM companions WHERE companion_id = :companion_id")
        await self._session.execute(stmt, dict(companion_id=companion_id))
        await self._session.commit()
        return True
