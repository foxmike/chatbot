"""Visual API endpoints — image generation, understanding, and appearance management."""

from __future__ import annotations

import base64
import uuid as _uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.auth.dependencies import CurrentUser, get_current_user
from chatbot.config import get_settings
from chatbot.db import async_session_factory
from chatbot.db.models import Companion, ImageMemory
from chatbot.visual.appearance import (
    AppearanceManager,
    AppearanceProfile,
    Outfit,
    OutfitManager,
)
from chatbot.visual.base import (
    ImageGenerationProvider,
    ImageUnderstandingProvider,
    VISUAL_PROVIDER_REGISTRY,
    VisualModelIdentifier,
    validate_model_identifier,
)
from chatbot.visual.generation import (
    GenerationError,
    GenerationOptions,
    ImageGenerationService,
)
from chatbot.visual.understanding import (
    ImageUnderstandingService,
    UnderstandingError,
    UnderstandingOptions,
)

visual_router = APIRouter(prefix="/visual", tags=["visual"])

DEFAULT_GENERATION_MODEL = "diffusion/default"
DEFAULT_WIDTH = 512
DEFAULT_HEIGHT = 512


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class UploadRequest(BaseModel):
    image_base64: str
    companion_id: str | None = None


class UploadResponse(BaseModel):
    image_id: str
    image_url: str


class GenerateRequest(BaseModel):
    prompt: str
    companion_id: str | None = None
    negative_prompt: str | None = None
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    seed: int | None = None
    model: str | None = None


class GenerateResponse(BaseModel):
    image_id: str | None = None
    image_url: str | None = None
    image_base64: str
    mime_type: str
    seed_used: int | None
    prompt_used: str
    model_used: str
    width: int
    height: int
    generation_time_ms: float


class CompanionImageResponse(BaseModel):
    image_id: str
    image_url: str
    mime_type: str
    prompt_used: str | None = None
    model_used: str | None = None
    width: int | None = None
    height: int | None = None
    created_at: str


class CompanionImageListResponse(BaseModel):
    images: list[CompanionImageResponse]


class UnderstandRequest(BaseModel):
    image_base64: str
    mime_type: str = "image/png"
    question: str | None = None


class UnderstandResponse(BaseModel):
    description: str
    detected_objects: list[str]
    detected_text: str | None
    scene_category: str | None
    confidence: float
    model_used: str
    processing_time_ms: float


class AppearanceProfileRequest(BaseModel):
    character_seed: int | None = None
    character_description: str | None = None
    visual_traits: list[str] | None = None
    art_style: str | None = None
    color_palette: list[str] | None = None


class AppearanceProfileResponse(BaseModel):
    character_seed: int
    character_description: str
    visual_traits: list[str]
    art_style: str
    color_palette: list[str]
    created_at: str
    updated_at: str | None


class ProviderInfo(BaseModel):
    name: str
    generation: bool = False
    understanding: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _profile_to_response(profile: AppearanceProfile) -> AppearanceProfileResponse:
    return AppearanceProfileResponse(
        character_seed=profile.character_seed,
        character_description=profile.character_description,
        visual_traits=profile.visual_traits,
        art_style=profile.art_style,
        color_palette=profile.color_palette,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _resolve_generation_identifier(model_str: str | None) -> VisualModelIdentifier:
    if model_str is None:
        settings = get_settings()
        if settings.image_generation_provider == "openrouter":
            return VisualModelIdentifier(
                provider="openrouter",
                model=settings.image_generation_model.removeprefix("openrouter/"),
            )
        model_str = (
            settings.image_generation_model
            if "/" in settings.image_generation_model
            else f"{settings.image_generation_provider}/{settings.image_generation_model}"
        )
    try:
        return validate_model_identifier(model_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid model identifier: {model_str}",
    )


def _api_image_url(image_id: str) -> str:
    return f"/api/visual/images/{image_id}"


def _user_uuid(user_id: str) -> _uuid.UUID:
    try:
        return _uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user id",
        )


async def _load_companion_for_user(
    session: AsyncSession,
    companion_id: str,
    user_id: str,
) -> Companion:
    stmt = select(Companion).where(
        Companion.companion_id == companion_id,
        Companion.user_id == _user_uuid(user_id),
    )
    result = await session.execute(stmt)
    companion = result.scalar_one_or_none()
    if companion is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Companion not found",
        )
    return companion


async def _load_appearance_manager_for_user(
    session: AsyncSession,
    user_id: str,
) -> AppearanceManager:
    """Load the DB-backed appearance manager for the given user's first companion.

    If no companion exists yet, returns a default in-memory AppearanceManager
    with the given session attached so callers can persist it later.
    """
    stmt = (
        select(Companion.companion_id)
        .where(Companion.user_id == _uuid.UUID(user_id))
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.fetchone()

    if row is None:
        return AppearanceManager(session=session)

    companion_id: str = str(row[0])
    return await AppearanceManager.load_from_db(
        companion_id=companion_id,
        session=session,
    )


async def _load_appearance_manager_for_companion(
    session: AsyncSession,
    companion_id: str,
) -> AppearanceManager:
    return await AppearanceManager.load_from_db(
        companion_id=companion_id,
        session=session,
    )


# ---------------------------------------------------------------------------
# POST /generate
# ---------------------------------------------------------------------------


@visual_router.post("/generate", response_model=GenerateResponse)
async def generate_image(
    request: GenerateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> GenerateResponse:
    """Generate an image from a text prompt using the configured visual provider."""
    identifier = _resolve_generation_identifier(request.model)
    provider_cls = VISUAL_PROVIDER_REGISTRY.get(identifier.provider)
    if provider_cls is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown visual provider: {identifier.provider}",
        )

    provider = provider_cls(identifier)
    if not isinstance(provider, ImageGenerationProvider):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider '{identifier.provider}' does not support image generation",
        )

    async with async_session_factory() as session:
        companion_id: str | None = None
        if request.companion_id:
            await _load_companion_for_user(
                session,
                request.companion_id,
                current_user.user_id,
            )
            companion_id = request.companion_id
            appearance_manager = await _load_appearance_manager_for_companion(
                session,
                request.companion_id,
            )
        else:
            appearance_manager = await _load_appearance_manager_for_user(
                session, current_user.user_id
            )

        service = ImageGenerationService(
            appearance_manager=appearance_manager,
            provider=provider,
        )

        options = GenerationOptions(
            width=request.width,
            height=request.height,
            negative_prompt=request.negative_prompt,
            seed=request.seed,
        )

        try:
            persisted = await service.generate_and_persist_with_reference(
                scene_description=request.prompt,
                options=options,
                user_id=str(current_user.user_id),
                companion_id=companion_id,
                db_session=session,
            )
            result = persisted.result
        except GenerationError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            )

    return GenerateResponse(
        image_id=persisted.image_id,
        image_url=_api_image_url(persisted.image_id) if persisted.image_id else None,
        image_base64=base64.b64encode(result.image_data).decode("ascii"),
        mime_type=result.mime_type,
        seed_used=result.seed_used,
        prompt_used=result.prompt_used,
        model_used=result.model_used,
        width=result.width,
        height=result.height,
        generation_time_ms=result.generation_time_ms,
    )


@visual_router.post("/upload", response_model=UploadResponse)
async def upload_image(
    request: UploadRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> UploadResponse:
    """Upload base64 image data and save it to the image_memories table."""
    import re
    
    image_base64 = request.image_base64
    mime_type = "image/png"
    
    if image_base64.startswith("data:"):
        match = re.match(r"^data:([^;]+);base64,(.+)$", image_base64)
        if not match:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid base64 data URI format",
            )
        mime_type = match.group(1)
        data_str = match.group(2)
    else:
        data_str = image_base64
        
    try:
        image_bytes = base64.b64decode(data_str)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to decode base64 image data: {exc}",
        )

    companion_id = request.companion_id
    image_id = str(_uuid.uuid4())
    image_url = _api_image_url(image_id)
    
    async with async_session_factory() as session:
        if companion_id:
            await _load_companion_for_user(
                session,
                companion_id,
                current_user.user_id,
            )
            
        record = ImageMemory(
            image_id=image_id,
            source_type="uploaded",
            user_id=_user_uuid(current_user.user_id),
            companion_id=companion_id,
            image_data=image_bytes,
            image_url=image_url,
            mime_type=mime_type,
        )
        session.add(record)
        await session.commit()
        
    return UploadResponse(
        image_id=image_id,
        image_url=image_url,
    )


@visual_router.get(
    "/companion/{companion_id}/images",
    response_model=CompanionImageListResponse,
)
async def list_companion_images(
    companion_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> CompanionImageListResponse:
    """Return generated images associated with a companion."""
    async with async_session_factory() as session:
        await _load_companion_for_user(session, companion_id, current_user.user_id)
        stmt = (
            select(ImageMemory)
            .where(
                ImageMemory.companion_id == companion_id,
                ImageMemory.user_id == _user_uuid(current_user.user_id),
                ImageMemory.source_type == "generated",
            )
            .order_by(ImageMemory.created_at.desc())
        )
        result = await session.execute(stmt)
        images = result.scalars().all()

    return CompanionImageListResponse(
        images=[
            CompanionImageResponse(
                image_id=image.image_id,
                image_url=_api_image_url(image.image_id),
                mime_type=image.mime_type,
                prompt_used=image.prompt_used,
                model_used=image.model_used,
                width=image.width,
                height=image.height,
                created_at=image.created_at.isoformat(),
            )
            for image in images
        ]
    )


@visual_router.get("/images/{image_id}")
async def get_generated_image(image_id: str) -> Response:
    """Return generated image bytes for use in avatar URLs."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(ImageMemory).where(ImageMemory.image_id == image_id)
        )
        image = result.scalar_one_or_none()

    if image is None or image.image_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found",
        )

    return Response(content=image.image_data, media_type=image.mime_type)


# ---------------------------------------------------------------------------
# POST /understand
# ---------------------------------------------------------------------------


@visual_router.post("/understand", response_model=UnderstandResponse)
async def understand_image(
    request: UnderstandRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> UnderstandResponse:
    """Analyze an image and return a structured understanding result."""
    image_data = base64.b64decode(request.image_base64)

    # Use a default understanding adapter; real backends wire through the registry.
    adapter_cls = _resolve_understanding_provider()
    provider = adapter_cls()

    service = ImageUnderstandingService(provider=provider)

    options = UnderstandingOptions(question=request.question)

    try:
        result = await service.understand_and_persist(
            image_data=image_data,
            mime_type=request.mime_type,
            options=options,
            user_id=str(current_user.user_id),
            db_session=async_session_factory(),
        )
    except UnderstandingError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )

    return UnderstandResponse(
        description=result.description,
        detected_objects=result.detected_objects,
        detected_text=result.detected_text,
        scene_category=result.scene_category,
        confidence=result.confidence,
        model_used=result.model_used,
        processing_time_ms=result.processing_time_ms,
    )


def _resolve_understanding_provider() -> ImageUnderstandingProvider:
    """Return the first registered ImageUnderstandingProvider, or a stub."""
    from chatbot.visual.understanding import ImageUnderstandingAdapter

    async def _stub_understand(
        image_data: bytes,
        mime_type: str,
        question: str | None,
    ) -> Any:
        from chatbot.visual.base import ImageUnderstandingResult

        return ImageUnderstandingResult(
            description="No understanding provider configured.",
            detected_objects=[],
            detected_text=None,
            scene_category=None,
            confidence=0.0,
            model_used="stub/default",
            processing_time_ms=0.0,
        )

    return ImageUnderstandingAdapter(
        model_identifier="stub/default",
        understand_fn=_stub_understand,
        provider_label="stub",
    )


# ---------------------------------------------------------------------------
# GET /appearance
# ---------------------------------------------------------------------------


@visual_router.get("/appearance", response_model=AppearanceProfileResponse)
async def get_appearance(
    current_user: CurrentUser = Depends(get_current_user),
) -> AppearanceProfileResponse:
    """Return the current DB-backed appearance profile for the user's companion."""
    async with async_session_factory() as session:
        manager = await _load_appearance_manager_for_user(
            session, current_user.user_id
        )
        return _profile_to_response(manager.get_profile())


# ---------------------------------------------------------------------------
# PUT /appearance
# ---------------------------------------------------------------------------


@visual_router.put("/appearance", response_model=AppearanceProfileResponse)
async def update_appearance(
    update: AppearanceProfileRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> AppearanceProfileResponse:
    """Update the DB-backed appearance profile for the user's companion."""
    async with async_session_factory() as session:
        manager = await _load_appearance_manager_for_user(
            session, current_user.user_id
        )
        if update.character_seed is not None:
            manager.lock_seed(update.character_seed)
        if update.character_description is not None:
            manager.update_character_description(update.character_description)
        if update.visual_traits is not None:
            manager.update_visual_traits(update.visual_traits)
        if update.art_style is not None:
            manager.update_art_style(update.art_style)
        if update.color_palette is not None:
            manager.update_color_palette(update.color_palette)
        await manager.save_to_db()
        return _profile_to_response(manager.get_profile())


# ---------------------------------------------------------------------------
# GET /providers
# ---------------------------------------------------------------------------


@visual_router.get("/providers", response_model=list[ProviderInfo])
async def list_providers(
    current_user: CurrentUser = Depends(get_current_user),
) -> list[ProviderInfo]:
    """List available visual provider names and their capabilities."""
    from chatbot.visual.base import ImageGenerationProvider, ImageUnderstandingProvider

    result: list[ProviderInfo] = []
    for name, cls in VISUAL_PROVIDER_REGISTRY.items():
        result.append(
            ProviderInfo(
                name=name,
                generation=issubclass(cls, ImageGenerationProvider),
                understanding=issubclass(cls, ImageUnderstandingProvider),
            )
        )
    return result
