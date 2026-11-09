"""Room and scene management API endpoints."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from chatbot.auth.dependencies import get_current_user, CurrentUser

from chatbot.db import async_session_factory
from chatbot.db.models import Companion, Room as RoomRecord, Scene as SceneModel, Session as SessionRecord

from chatbot.core.mode_contracts import (
    ModeName as ContractModeName,
    get_contract,
)
from chatbot.prompts.templates import get_prompt_template, render_room_prompt
from chatbot.rooms.room import (
    Room,
    RoomMemoryPolicy,
    RoomType,
    create_room,
    get_room,
    get_room_session,
    get_user_rooms,
    link_room_session,
    unlink_room_session,
    update_room_memory_policy,
)
from chatbot.rooms.roleplay import RoleplayEngine, RoleplayPersona
from chatbot.rooms.scene import (
    SceneState,
    create_scene,
    end_scene,
    get_active_scene,
    get_scene,
    pause_scene,
    resume_scene,
    update_scene,
)

rooms_router = APIRouter(prefix="/rooms", tags=["rooms"])


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


class RoomCreateRequest(BaseModel):
    user_id: str | None = None  # Legacy input; ownership always comes from authentication.
    room_type: RoomType
    display_name: str | None = None
    companion_id: str | None = None


class RoomResponse(BaseModel):
    room_id: str
    user_id: str
    companion_id: str | None
    room_type: RoomType
    display_name: str
    memory_policy: dict[str, Any]
    scene_id: str | None
    created_at: str
    metadata: dict[str, Any]


class RoomListResponse(BaseModel):
    rooms: list[RoomResponse]


class MemoryPolicyUpdateRequest(BaseModel):
    persist_to_durable: bool | None = None
    scope: str | None = None
    max_context_turns: int | None = None
    include_embeddings: bool | None = None


class SceneCreateRequest(BaseModel):
    room_id: str | None = None  # The path identifies the room.
    location: str = ""
    activity: str = ""


class SceneUpdateRequest(BaseModel):
    location: str | None = None
    activity: str | None = None
    tone: str | None = None
    intensity: int | None = None
    scene_context: str | None = None
    participants: list[str] | None = None


class SceneResponse(BaseModel):
    scene_id: str
    room_id: str
    location: str
    activity: str
    tone: str
    intensity: int
    is_paused: bool
    started_at: str
    paused_at: str | None
    resumed_at: str | None
    ended_at: str | None
    participants: list[str]
    scene_context: str
    metadata: dict[str, Any]


class PersonaRequest(BaseModel):
    name: str
    description: str
    traits: list[str]
    voice_style: str
    backstory: str = ""
    scene_goals: list[str] = []
    constraints: list[str] = []


class ScenePromptResponse(BaseModel):
    system_prompt: str


class ClassifyRequest(BaseModel):
    text: str


class MemoryClassificationResponse(BaseModel):
    classification: Literal["scene_only", "durable", "both"]


class PromptTemplateRequest(BaseModel):
    """Optional values for prompt template interpolation."""

    user_name: str = ""
    companion_name: str = ""
    backstory: str = ""
    personality_sliders: str = ""
    humor_style: str = ""
    communication_style: str = ""
    favorite_topics: str = ""
    conversation_mode: str = ""
    scene_location: str = ""
    scene_activity: str = ""
    scene_tone: str = ""
    persona_description: str = ""
    traits: str = ""
    voice_style: str = ""
    scene_goals: str = ""
    constraints: str = ""
    display_name: str = ""
    session_count: str = ""


class PromptTemplateResponse(BaseModel):
    room_type: RoomType
    system_prompt: str


class RoomSessionResponse(BaseModel):
    session_id: str
    room_id: str
    title: str | None
    is_active: bool
    created_at: str


class LinkSessionRequest(BaseModel):
    session_id: str


class RoomModeContractResponse(BaseModel):
    room_id: str
    room_type: RoomType
    mode: str
    tone: str
    length: str
    pacing: str
    max_tokens_ratio: float
    temperature_modifier: float
    initiative_level: str
    should_suggest_topics: bool
    custom_directives: list[str]
    active_prompt_sections: list[str]


# ---------------------------------------------------------------------------
# Room-type → mode contract mapping
# ---------------------------------------------------------------------------

ROOM_TYPE_MODE_MAP: dict[RoomType, str] = {
    RoomType.ONE_ON_ONE_CHAT: ContractModeName.COMPANION,
    RoomType.ROLEPLAY_SCENE: ContractModeName.ROLEPLAY,
    RoomType.PROJECT: ContractModeName.PLANNING,
    RoomType.EMOTIONAL_SUPPORT: ContractModeName.VENTING,
    RoomType.MEMORY_REVIEW: ContractModeName.ADVISOR,
}


def get_room_mode_contract(room_type: RoomType | str) -> RoomModeContractResponse:
    """Resolve the mode contract for a given room type."""
    if isinstance(room_type, str):
        try:
            rt = RoomType(room_type)
        except ValueError:
            rt = RoomType.ONE_ON_ONE_CHAT  # fallback for unknown room types
    else:
        rt = room_type

    mode_name = ROOM_TYPE_MODE_MAP.get(rt, ContractModeName.COMPANION)
    contract = get_contract(mode_name)
    return RoomModeContractResponse(
        room_id="",
        room_type=rt,
        mode=contract.mode.value,
        tone=contract.tone,
        length=contract.length,
        pacing=contract.pacing,
        max_tokens_ratio=contract.max_tokens_ratio,
        temperature_modifier=contract.temperature_modifier,
        initiative_level=contract.initiative_level,
        should_suggest_topics=contract.should_suggest_topics,
        custom_directives=list(contract.custom_directives),
        active_prompt_sections=[s.value for s in contract.active_prompt_sections],
    )


# ---------------------------------------------------------------------------
# Conversion helpers (domain → response)
# ---------------------------------------------------------------------------


def _room_to_response(room: Room) -> RoomResponse:
    return RoomResponse(
        room_id=room.room_id,
        user_id=room.user_id,
        companion_id=room.companion_id,
        room_type=room.room_type,
        display_name=room.display_name,
        memory_policy=asdict(room.memory_policy),
        scene_id=room.scene_id,
        created_at=room.created_at,
        metadata=room.metadata,
    )


def _datetime_to_response(value: datetime) -> str:
    return value.isoformat()


def _optional_datetime_to_response(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _scene_to_response(scene: SceneState) -> SceneResponse:
    return SceneResponse(
        scene_id=scene.scene_id,
        room_id=scene.room_id,
        location=scene.location,
        activity=scene.activity,
        tone=scene.tone,
        intensity=scene.intensity,
        is_paused=scene.is_paused,
        started_at=_datetime_to_response(scene.started_at),
        paused_at=_optional_datetime_to_response(scene.paused_at),
        resumed_at=_optional_datetime_to_response(scene.resumed_at),
        ended_at=_optional_datetime_to_response(scene.ended_at),
        participants=list(scene.participants),
        scene_context=scene.scene_context,
        metadata=scene.metadata,
    )


async def _require_owned_session(session_id: str, user: CurrentUser) -> SessionRecord:
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(404, "Session not found")
    async with async_session_factory() as db:
        result = await db.execute(sa.select(SessionRecord).where(
            SessionRecord.id == sid, SessionRecord.user_id == uuid.UUID(user.user_id)
        ))
        record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(404, "Session not found")
    return record


async def _require_owned_companion(companion_id: str, user: CurrentUser) -> None:
    async with async_session_factory() as db:
        result = await db.execute(sa.select(Companion.companion_id).where(
            Companion.companion_id == companion_id,
            Companion.user_id == uuid.UUID(user.user_id),
        ))
        if result.scalar_one_or_none() is None:
            raise HTTPException(404, "Companion not found")


async def _require_owned_room(room_id: str, user: CurrentUser) -> Room:
    room = await get_room(room_id)
    if room is None or room.user_id != user.user_id:
        raise HTTPException(404, "Room not found")
    # Reject stale cross-account links as well as direct identifier attacks.
    if room.companion_id is not None:
        await _require_owned_companion(room.companion_id, user)
    if room.session_id is not None:
        await _require_owned_session(room.session_id, user)
    return room


async def _require_owned_scene(scene_id: str, user: CurrentUser) -> SceneState:
    scene = await get_scene(scene_id)
    if scene is None:
        raise HTTPException(404, "Scene not found")
    await _require_owned_room(scene.room_id, user)
    return scene


# ---------------------------------------------------------------------------
# Room endpoints
# ---------------------------------------------------------------------------


@rooms_router.post(
    "/",
    response_model=RoomResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_room_endpoint(
    body: RoomCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> RoomResponse:
    if body.companion_id is not None:
        await _require_owned_companion(body.companion_id, current_user)
    room = await create_room(
        user_id=current_user.user_id,
        room_type=body.room_type,
        display_name=body.display_name,
        companion_id=body.companion_id,
    )
    return _room_to_response(room)


@rooms_router.get("/detail/{room_id}", response_model=RoomResponse)
async def get_room_detail(
    room_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> RoomResponse:
    room = await _require_owned_room(room_id, current_user)
    return _room_to_response(room)


@rooms_router.get("/{user_id}", response_model=RoomListResponse)
async def list_user_rooms(
    user_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> RoomListResponse:
    if user_id != current_user.user_id:
        raise HTTPException(404, "User rooms not found")
    rooms = await get_user_rooms(current_user.user_id)
    return RoomListResponse(rooms=[_room_to_response(r) for r in rooms])


@rooms_router.put("/{room_id}/memory-policy", response_model=RoomResponse)
async def update_memory_policy(
    room_id: str,
    body: MemoryPolicyUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> RoomResponse:
    room = await _require_owned_room(room_id, current_user)

    current = room.memory_policy
    policy = RoomMemoryPolicy(
        persist_to_durable=(
            body.persist_to_durable
            if body.persist_to_durable is not None
            else current.persist_to_durable
        ),
        scope=body.scope if body.scope is not None else current.scope,  # type: ignore[arg-type]
        max_context_turns=(
            body.max_context_turns
            if body.max_context_turns is not None
            else current.max_context_turns
        ),
        include_embeddings=(
            body.include_embeddings
            if body.include_embeddings is not None
            else current.include_embeddings
        ),
    )
    await update_room_memory_policy(room_id, policy)

    updated = await get_room(room_id)
    assert updated is not None
    return _room_to_response(updated)


@rooms_router.delete("/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_room_endpoint(
    room_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> None:
    room = await _require_owned_room(room_id, current_user)

    async with async_session_factory() as session:
        await session.execute(
            sa.update(RoomRecord).where(RoomRecord.room_id == room_id).values(scene_id=None),
        )
        await session.execute(
            sa.update(SessionRecord).where(
                SessionRecord.room_id == room_id,
                SessionRecord.user_id == uuid.UUID(current_user.user_id),
            ).values(room_id=None),
        )
        await session.execute(
            sa.delete(SceneModel).where(SceneModel.room_id == room_id),
        )
        await session.execute(
            sa.delete(RoomRecord).where(RoomRecord.room_id == room_id),
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Scene endpoints
# ---------------------------------------------------------------------------


@rooms_router.post(
    "/{room_id}/scenes",
    response_model=SceneResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_scene_endpoint(
    room_id: str,
    body: SceneCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> SceneResponse:
    await _require_owned_room(room_id, current_user)
    if body.room_id is not None and body.room_id != room_id:
        raise HTTPException(400, "Body room_id must match the path")
    scene = await create_scene(
        room_id=room_id,
        location=body.location,
        activity=body.activity,
    )
    return _scene_to_response(scene)


@rooms_router.get("/{room_id}/scenes/active", response_model=SceneResponse)
async def get_active_scene_endpoint(
    room_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> SceneResponse:
    await _require_owned_room(room_id, current_user)
    scene = await get_active_scene(room_id)
    if scene is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active scene",
        )
    return _scene_to_response(scene)


@rooms_router.get("/scenes/{scene_id}", response_model=SceneResponse)
async def get_scene_endpoint(
    scene_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> SceneResponse:
    scene = await _require_owned_scene(scene_id, current_user)
    return _scene_to_response(scene)


@rooms_router.patch("/scenes/{scene_id}", response_model=SceneResponse)
async def update_scene_endpoint(
    scene_id: str,
    body: SceneUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> SceneResponse:
    await _require_owned_scene(scene_id, current_user)
    kwargs: dict[str, Any] = {}
    if body.location is not None:
        kwargs["location"] = body.location
    if body.activity is not None:
        kwargs["activity"] = body.activity
    if body.tone is not None:
        kwargs["tone"] = body.tone
    if body.intensity is not None:
        kwargs["intensity"] = body.intensity
    if body.scene_context is not None:
        kwargs["scene_context"] = body.scene_context
    if body.participants is not None:
        kwargs["participants"] = body.participants

    if not kwargs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No update fields provided",
        )

    try:
        scene = await update_scene(scene_id, **kwargs)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    return _scene_to_response(scene)


@rooms_router.post("/scenes/{scene_id}/pause", response_model=SceneResponse)
async def pause_scene_endpoint(
    scene_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> SceneResponse:
    await _require_owned_scene(scene_id, current_user)
    try:
        scene = await pause_scene(scene_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    return _scene_to_response(scene)


@rooms_router.post("/scenes/{scene_id}/resume", response_model=SceneResponse)
async def resume_scene_endpoint(
    scene_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> SceneResponse:
    await _require_owned_scene(scene_id, current_user)
    try:
        scene = await resume_scene(scene_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    return _scene_to_response(scene)


@rooms_router.post("/scenes/{scene_id}/end", response_model=SceneResponse)
async def end_scene_endpoint(
    scene_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> SceneResponse:
    await _require_owned_scene(scene_id, current_user)
    try:
        scene = await end_scene(scene_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    return _scene_to_response(scene)


# ---------------------------------------------------------------------------
# Room-session linking endpoints
# ---------------------------------------------------------------------------


@rooms_router.get("/{room_id}/session", response_model=RoomSessionResponse)
async def get_room_session_endpoint(
    room_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> RoomSessionResponse:
    room = await _require_owned_room(room_id, current_user)

    session_record = await get_room_session(room_id)
    if session_record is None or str(session_record.user_id) != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No session linked to this room",
        )

    return RoomSessionResponse(
        session_id=str(session_record.id),
        room_id=room_id,
        title=session_record.title,
        is_active=session_record.is_active,
        created_at=session_record.created_at.isoformat(),
    )


@rooms_router.post(
    "/{room_id}/link-session",
    response_model=RoomResponse,
)
async def link_session_endpoint(
    room_id: str,
    body: LinkSessionRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> RoomResponse:
    await _require_owned_room(room_id, current_user)
    await _require_owned_session(body.session_id, current_user)
    try:
        room = await link_room_session(room_id, body.session_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    return _room_to_response(room)


@rooms_router.delete(
    "/{room_id}/session",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unlink_session_endpoint(
    room_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> None:
    await _require_owned_room(room_id, current_user)
    try:
        await unlink_room_session(room_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


# ---------------------------------------------------------------------------
# Mode contract endpoint
# ---------------------------------------------------------------------------


@rooms_router.get(
    "/{room_id}/mode-contract",
    response_model=RoomModeContractResponse,
)
async def get_room_mode_contract_endpoint(
    room_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> RoomModeContractResponse:
    """Return the mode contract (tone, length, pacing, etc.) for a room's type."""
    room = await _require_owned_room(room_id, current_user)
    response = get_room_mode_contract(room.room_type)
    response.room_id = room_id
    return response


# ---------------------------------------------------------------------------
# Prompt template endpoints
# ---------------------------------------------------------------------------


@rooms_router.get(
    "/{room_id}/prompt-template",
    response_model=PromptTemplateResponse,
)
async def get_room_prompt_template(
    room_id: str,
    user_name: str = "",
    companion_name: str = "",
    backstory: str = "",
    personality_sliders: str = "",
    humor_style: str = "",
    communication_style: str = "",
    favorite_topics: str = "",
    conversation_mode: str = "",
    scene_location: str = "",
    scene_activity: str = "",
    scene_tone: str = "",
    persona_description: str = "",
    traits: str = "",
    voice_style: str = "",
    scene_goals: str = "",
    constraints: str = "",
    display_name: str = "",
    session_count: str = "",
    current_user: CurrentUser = Depends(get_current_user),
) -> PromptTemplateResponse:
    """Return the rendered prompt template for a room's type.

    Accepts optional query parameters that are interpolated into the
    room-type-specific template.
    """
    room = await _require_owned_room(room_id, current_user)

    kwargs: dict[str, str] = {}
    for key in (
        "user_name",
        "companion_name",
        "backstory",
        "personality_sliders",
        "humor_style",
        "communication_style",
        "favorite_topics",
        "conversation_mode",
        "scene_location",
        "scene_activity",
        "scene_tone",
        "persona_description",
        "traits",
        "voice_style",
        "scene_goals",
        "constraints",
        "display_name",
        "session_count",
    ):
        val = locals().get(key, "")
        if val:
            kwargs[key] = val

    system_prompt = render_room_prompt(room.room_type, **kwargs)
    return PromptTemplateResponse(
        room_type=room.room_type,
        system_prompt=system_prompt,
    )


@rooms_router.post(
    "/{room_id}/prompt-template",
    response_model=PromptTemplateResponse,
)
async def post_room_prompt_template(
    room_id: str,
    body: PromptTemplateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> PromptTemplateResponse:
    """Return the rendered prompt template for a room's type using POST body."""
    room = await _require_owned_room(room_id, current_user)

    kwargs: dict[str, str] = {}
    for field_name, field_value in body.model_dump().items():
        if field_value:
            kwargs[field_name] = str(field_value)

    system_prompt = render_room_prompt(room.room_type, **kwargs)
    return PromptTemplateResponse(
        room_type=room.room_type,
        system_prompt=system_prompt,
    )


# ---------------------------------------------------------------------------
# Roleplay endpoints
# ---------------------------------------------------------------------------


@rooms_router.post(
    "/scenes/{scene_id}/persona",
    response_model=ScenePromptResponse,
)
async def set_persona_endpoint(
    scene_id: str,
    body: PersonaRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> ScenePromptResponse:
    scene = await _require_owned_scene(scene_id, current_user)

    persona = RoleplayPersona(
        name=body.name,
        description=body.description,
        traits=body.traits,
        voice_style=body.voice_style,
        backstory=body.backstory,
        scene_goals=body.scene_goals,
        constraints=body.constraints,
    )

    # Resolve the room's memory policy for the engine
    room = await get_room(scene.room_id)
    memory_policy = room.memory_policy if room is not None else None

    engine = RoleplayEngine(
        scene=scene,
        persona=persona,
        memory_policy=memory_policy,
    )
    return ScenePromptResponse(system_prompt=engine.build_scene_prompt())


@rooms_router.post(
    "/scenes/{scene_id}/classify",
    response_model=MemoryClassificationResponse,
)
async def classify_memory_endpoint(
    scene_id: str,
    body: ClassifyRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> MemoryClassificationResponse:
    scene = await _require_owned_scene(scene_id, current_user)

    # A persona is structurally required by RoleplayEngine but not
    # used by classify_memory_importance; supply a minimal stub.
    persona = RoleplayPersona(
        name="",
        description="",
        traits=[],
        voice_style="",
        backstory="",
    )
    engine = RoleplayEngine(scene=scene, persona=persona)
    classification = engine.classify_memory_importance(body.text)
    return MemoryClassificationResponse(classification=classification)
