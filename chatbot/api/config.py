"""Identity configuration endpoints for relationship, character, and mood state."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.db import get_db
from chatbot.db.models import (
    Companion,
    CompanionRelationshipModel,
    CharacterProfileModel,
    IdentityStateModel,
    RelationshipConfigModel,
    Session,
)
from chatbot.soul.character import (
    CommunicationStyle,
    CharacterProfile,
    HumorStyle,
)
from chatbot.soul.evolution import IdentityState
from chatbot.soul.relationship import (
    ContentLevel,
    RelationshipConfig,
    RelationshipTone,
    RelationshipType,
)


Ratio = Annotated[FiniteFloat, Field(ge=0.0, le=1.0)]

config_router = APIRouter()


class RelationshipConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: RelationshipType | None = None
    tone: RelationshipTone | None = None
    directness: Ratio | None = None
    warmth: Ratio | None = None
    initiative_level: Ratio | None = None
    content_level: ContentLevel | None = None


class RelationshipConfigResponse(BaseModel):
    type: RelationshipType
    tone: RelationshipTone
    directness: float
    warmth: float
    initiative_level: float
    content_level: ContentLevel


class CharacterProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    backstory: str | None = None
    personality_sliders: dict[str, Ratio] | None = None
    humor_style: HumorStyle | None = None
    communication_style: CommunicationStyle | None = None
    favorite_topics: list[str] | None = None

    @field_validator("name", "backstory")
    @classmethod
    def _validate_non_empty_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("must be a non-empty string")
        return text


class CharacterProfileResponse(BaseModel):
    name: str
    backstory: str
    personality_sliders: dict[str, float]
    humor_style: HumorStyle
    communication_style: CommunicationStyle
    favorite_topics: list[str]


class IdentityStateResponse(BaseModel):
    fixed: dict[str, Any]
    slow: dict[str, Any]
    mood: dict[str, Any]


class IdentityEvolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_context: dict[str, Any] = Field(default_factory=dict)


@config_router.get(
    "/relationship/{session_id}",
    response_model=RelationshipConfigResponse,
)
async def get_relationship_config(
    session_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RelationshipConfigResponse:
    session = await _verify_session_ownership(db, session_id, current_user.user_id)
    model = await _relationship_for_session(db, session, current_user.user_id)
    config = _relationship_from_model(model) if model is not None else RelationshipConfig.defaults()
    return _relationship_response(config)


@config_router.put(
    "/relationship/{session_id}",
    response_model=RelationshipConfigResponse,
)
async def put_relationship_config(
    session_id: UUID,
    body: RelationshipConfigRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RelationshipConfigResponse:
    session = await _verify_session_ownership(db, session_id, current_user.user_id)
    model = await _relationship_for_session(db, session, current_user.user_id, write=True)
    current = _relationship_from_model(model) if model is not None else RelationshipConfig.defaults()
    config_data = _relationship_to_dict(current)
    config_data.update(body.model_dump(exclude_unset=True, exclude_none=True))
    config = RelationshipConfig(**config_data)

    if model is None:
        model = (CompanionRelationshipModel(companion_id=session.companion_id)
                 if session.companion_id else RelationshipConfigModel(session_id=session_id))
        db.add(model)

    _write_relationship_model(model, config)
    if isinstance(model, CompanionRelationshipModel):
        await db.flush()
        await db.execute(
            update(CompanionRelationshipModel)
            .where(CompanionRelationshipModel.companion_id == model.companion_id)
            .values(version=CompanionRelationshipModel.version + 1)
        )
    await _commit_or_session_not_found(db)
    await db.refresh(model)
    await _invalidate_persona_cache(db, session_id)
    return _relationship_response(_relationship_from_model(model))


@config_router.get(
    "/character/{session_id}",
    response_model=CharacterProfileResponse,
)
async def get_character_profile(
    session_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CharacterProfileResponse:
    await _verify_session_ownership(db, session_id, current_user.user_id)
    model = await _get_latest_character_model(db, session_id)
    profile = _character_from_model(model) if model is not None else CharacterProfile.defaults()
    return _character_response(profile)


@config_router.put(
    "/character/{session_id}",
    response_model=CharacterProfileResponse,
)
async def put_character_profile(
    session_id: UUID,
    body: CharacterProfileRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CharacterProfileResponse:
    await _verify_session_ownership(db, session_id, current_user.user_id)
    model = await _get_latest_character_model(db, session_id)
    current = _character_from_model(model) if model is not None else CharacterProfile.defaults()
    profile_data = _character_to_dict(current)
    profile_data.update(body.model_dump(exclude_unset=True, exclude_none=True))
    profile = CharacterProfile(**profile_data)

    if model is None:
        model = CharacterProfileModel(session_id=session_id)
        db.add(model)

    _write_character_model(model, profile)
    await _commit_or_session_not_found(db)
    await db.refresh(model)
    await _invalidate_persona_cache(db, session_id)
    return _character_response(_character_from_model(model))


@config_router.get(
    "/identity/{session_id}",
    response_model=IdentityStateResponse,
)
async def get_identity_state(
    session_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> IdentityStateResponse:
    await _verify_session_ownership(db, session_id, current_user.user_id)
    model = await _get_latest_identity_model(db, session_id)
    state = _identity_from_model(model) if model is not None else IdentityState.defaults()
    return _identity_response(state)


@config_router.post(
    "/identity/{session_id}/evolve",
    response_model=IdentityStateResponse,
)
async def evolve_identity_state(
    session_id: UUID,
    body: IdentityEvolutionRequest | None = Body(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> IdentityStateResponse:
    await _verify_session_ownership(db, session_id, current_user.user_id)
    model = await _get_latest_identity_model(db, session_id)
    state = _identity_from_model(model) if model is not None else IdentityState.defaults()
    context = body.turn_context if body is not None else {}
    state.apply_evolution(context)

    model = _upsert_identity_model(db, session_id, model, state)
    await _commit_or_session_not_found(db)
    await db.refresh(model)
    await _invalidate_persona_cache(db, session_id)
    return _identity_response(_identity_from_model(model))


@config_router.post(
    "/identity/{session_id}/reset-mood",
    response_model=IdentityStateResponse,
)
async def reset_identity_mood(
    session_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> IdentityStateResponse:
    await _verify_session_ownership(db, session_id, current_user.user_id)
    model = await _get_latest_identity_model(db, session_id)
    state = _identity_from_model(model) if model is not None else IdentityState.defaults()
    state.mood = IdentityState.defaults().mood

    model = _upsert_identity_model(db, session_id, model, state)
    await _commit_or_session_not_found(db)
    await db.refresh(model)
    await _invalidate_persona_cache(db, session_id)
    return _identity_response(_identity_from_model(model))


async def _verify_session_ownership(
    db: AsyncSession,
    session_id: UUID,
    user_id: str,
) -> Session:
    """Verify that a session exists and belongs to the current user."""
    from uuid import UUID as _UUID
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == _UUID(user_id))
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return session


async def _relationship_for_session(db, session, user_id, *, write=False):
    if session.companion_id is None:
        return await _get_latest_relationship_model(db, session.id)
    # The session and its companion must both belong to this user.
    statement = select(Companion.companion_id).where(
        Companion.companion_id == session.companion_id, Companion.user_id == UUID(user_id)
    )
    if write:
        statement = statement.with_for_update()
    companion = await db.scalar(statement)
    if companion is None:
        raise HTTPException(status_code=404, detail="Companion not found")
    return await db.get(CompanionRelationshipModel, companion)


async def _get_latest_relationship_model(
    db: AsyncSession,
    session_id: UUID,
) -> RelationshipConfigModel | None:
    result = await db.execute(
        select(RelationshipConfigModel)
        .where(RelationshipConfigModel.session_id == session_id)
        .order_by(
            RelationshipConfigModel.updated_at.desc(),
            RelationshipConfigModel.created_at.desc(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_latest_character_model(
    db: AsyncSession,
    session_id: UUID,
) -> CharacterProfileModel | None:
    result = await db.execute(
        select(CharacterProfileModel)
        .where(CharacterProfileModel.session_id == session_id)
        .order_by(
            CharacterProfileModel.updated_at.desc(),
            CharacterProfileModel.created_at.desc(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_latest_identity_model(
    db: AsyncSession,
    session_id: UUID,
) -> IdentityStateModel | None:
    result = await db.execute(
        select(IdentityStateModel)
        .where(IdentityStateModel.session_id == session_id)
        .order_by(IdentityStateModel.last_updated.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _relationship_from_model(model: RelationshipConfigModel) -> RelationshipConfig:
    return RelationshipConfig(
        type=model.type,
        tone=model.tone,
        directness=model.directness,
        warmth=model.warmth,
        initiative_level=model.initiative_level,
        content_level=model.content_level,
    )


def _relationship_to_dict(config: RelationshipConfig) -> dict[str, Any]:
    return {
        "type": config.type,
        "tone": config.tone,
        "directness": config.directness,
        "warmth": config.warmth,
        "initiative_level": config.initiative_level,
        "content_level": config.content_level,
    }


def _relationship_response(config: RelationshipConfig) -> RelationshipConfigResponse:
    return RelationshipConfigResponse(**_relationship_to_dict(config))


def _write_relationship_model(
    model: RelationshipConfigModel,
    config: RelationshipConfig,
) -> None:
    model.type = config.type
    model.tone = config.tone
    model.directness = config.directness
    model.warmth = config.warmth
    model.initiative_level = config.initiative_level
    model.content_level = config.content_level


def _character_from_model(model: CharacterProfileModel) -> CharacterProfile:
    return CharacterProfile(
        name=model.name,
        backstory=model.backstory,
        personality_sliders=dict(model.personality_sliders),
        humor_style=model.humor_style,
        communication_style=model.communication_style,
        favorite_topics=list(model.favorite_topics),
    )


def _character_to_dict(profile: CharacterProfile) -> dict[str, Any]:
    return {
        "name": profile.name,
        "backstory": profile.backstory,
        "personality_sliders": dict(profile.personality_sliders),
        "humor_style": profile.humor_style,
        "communication_style": profile.communication_style,
        "favorite_topics": list(profile.favorite_topics),
    }


def _character_response(profile: CharacterProfile) -> CharacterProfileResponse:
    return CharacterProfileResponse(**_character_to_dict(profile))


def _write_character_model(
    model: CharacterProfileModel,
    profile: CharacterProfile,
) -> None:
    model.name = profile.name
    model.backstory = profile.backstory
    model.personality_sliders = dict(profile.personality_sliders)
    model.humor_style = profile.humor_style
    model.communication_style = profile.communication_style
    model.favorite_topics = list(profile.favorite_topics)


def _identity_from_model(model: IdentityStateModel) -> IdentityState:
    return IdentityState(
        fixed=dict(model.fixed),
        slow=dict(model.slow),
        mood=dict(model.mood),
    )


def _identity_response(state: IdentityState) -> IdentityStateResponse:
    return IdentityStateResponse(
        fixed=dict(state.fixed),
        slow=dict(state.slow),
        mood=dict(state.mood),
    )


def _upsert_identity_model(
    db: AsyncSession,
    session_id: UUID,
    model: IdentityStateModel | None,
    state: IdentityState,
) -> IdentityStateModel:
    if model is None:
        model = IdentityStateModel(session_id=session_id)
        db.add(model)

    model.fixed = dict(state.fixed)
    model.slow = dict(state.slow)
    model.mood = dict(state.mood)
    return model


async def _commit_or_session_not_found(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        ) from exc


async def _invalidate_persona_cache(
    db: AsyncSession,
    session_id: UUID,
) -> None:
    """Invalidate the persona cache for the session's (user, companion) key."""
    from chatbot.db.models import Session as _Session

    result = await db.execute(select(_Session).where(_Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        return

    user_id = str(session.user_id)
    companion_id = session.companion_id

    try:
        from chatbot.api.chat import _persona_cache
    except ImportError:
        return

    if _persona_cache is not None:
        await _persona_cache.invalidate(user_id, companion_id)
