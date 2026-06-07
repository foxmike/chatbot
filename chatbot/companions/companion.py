"""Companion model and per-user companion management logic."""

from __future__ import annotations

import json
from uuid import uuid4

from pydantic import BaseModel, Field
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


class CompanionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    persona: str | None = None
    description: str | None = None
    avatar_url: str | None = None
    backstory: str | None = None
    personality_sliders: dict[str, float] | None = None
    humor_style: str | None = None
    communication_style: str | None = None
    favorite_topics: list[str] | None = None


class CompanionDisplay(BaseModel):
    avatar_url: str | None
    appearance: AppearanceContinuitySnapshot


class CompanionResponse(BaseModel):
    companion_id: str
    user_id: str
    name: str
    persona: str | None
    description: str | None
    avatar_url: str | None
    backstory: str | None
    personality_sliders: dict[str, float] | None
    humor_style: str | None
    communication_style: str | None
    favorite_topics: list[str] | None
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
        "persona": {"type": "Text", "nullable": True},
        "description": {"type": "Text", "nullable": True},
        "avatar_url": {"type": "String(512)", "nullable": True},
        "backstory": {"type": "Text", "nullable": True},
        "personality_sliders": {"type": "JSON", "nullable": True},
        "humor_style": {"type": "String(20)", "nullable": True},
        "communication_style": {"type": "String(30)", "nullable": True},
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
        persona=persona,
        description=description,
        avatar_url=avatar_url,
        backstory=m.get("backstory"),
        personality_sliders=m.get("personality_sliders"),
        humor_style=m.get("humor_style"),
        communication_style=m.get("communication_style"),
        favorite_topics=m.get("favorite_topics"),
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


class CompanionManager:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, user_id: str, data: CompanionCreateRequest) -> CompanionResponse:
        companion_id = str(uuid4())
        avatar_url = data.avatar_url or await self._generate_avatar_url(
            user_id=user_id,
            companion_id=companion_id,
            data=data,
        )
        # exec_driver_sql sends raw SQL to asyncpg with native $N positional params,
        # avoiding SQLAlchemy text()'s :name parser which misreads ::jsonb as a
        # second named parameter (:jsonb).
        conn = await self._session.connection()
        result = await conn.exec_driver_sql(
            """INSERT INTO companions (
                   companion_id, user_id, name, persona, description, avatar_url,
                   backstory, personality_sliders, humor_style, communication_style,
                   favorite_topics, created_at
               )
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11::jsonb, now())
               RETURNING *""",
            (
                companion_id,
                user_id,
                data.name,
                data.persona,
                data.description,
                avatar_url,
                data.backstory,
                json.dumps(data.personality_sliders) if data.personality_sliders is not None else None,
                data.humor_style,
                data.communication_style,
                json.dumps(data.favorite_topics) if data.favorite_topics is not None else None,
            ),
        )
        row = result.fetchone()
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
            db_session=self._session,
        )
        return persisted.image_url

    async def list_by_user(self, user_id: str) -> CompanionListResponse:
        stmt = text("SELECT * FROM companions WHERE user_id = :user_id")
        result = await self._session.execute(stmt, dict(user_id=user_id))
        rows = result.fetchall()
        return CompanionListResponse(
            companions=[_row_to_companion(r) for r in rows]
        )

    async def get(self, user_id: str, companion_id: str) -> CompanionResponse | None:
        stmt = text(
            "SELECT * FROM companions WHERE companion_id = :companion_id"
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

        conn = await self._session.connection()
        result = await conn.exec_driver_sql(
            """UPDATE companions
               SET name = $1, persona = $2, description = $3, avatar_url = $4,
                   backstory = $5,
                   personality_sliders = $6::jsonb,
                   humor_style = $7,
                   communication_style = $8,
                   favorite_topics = $9::jsonb,
                   updated_at = now()
               WHERE companion_id = $10
               RETURNING *""",
            (
                data.name,
                data.persona,
                data.description,
                data.avatar_url,
                data.backstory,
                json.dumps(data.personality_sliders) if data.personality_sliders is not None else None,
                data.humor_style,
                data.communication_style,
                json.dumps(data.favorite_topics) if data.favorite_topics is not None else None,
                companion_id,
            ),
        )
        row = result.fetchone()
        await self._session.commit()
        return _row_to_companion(row)

    async def delete(self, user_id: str, companion_id: str) -> bool:
        existing = await self.get(user_id, companion_id)
        if existing is None:
            return False

        stmt = text("DELETE FROM companions WHERE companion_id = :companion_id")
        await self._session.execute(stmt, dict(companion_id=companion_id))
        await self._session.commit()
        return True
