"""Integration tests for the visual system — provider interface, appearance management, image generation, image understanding, and API endpoints."""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter, FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ---------------------------------------------------------------------------
# Inject fake db.models to bypass AgencyRitual.metadata SQLAlchemy error
# ---------------------------------------------------------------------------

_fake_db_models = MagicMock(name="chatbot.db.models")


class _FakeImageMemory:
    """Stand-in for ImageMemory to avoid SQLAlchemy mapping errors."""

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


_fake_db_models.ImageMemory = _FakeImageMemory
_fake_db_models.Appearance = MagicMock()
_fake_db_models.OutfitRecord = MagicMock()
sys.modules["chatbot.db.models"] = _fake_db_models

# ---------------------------------------------------------------------------
# Real imports (safe after the sys.modules injection above)
# ---------------------------------------------------------------------------

from chatbot.visual.appearance import (  # noqa: E402
    AppearanceManager,
    AppearanceProfile,
    Outfit,
    OutfitManager,
)
from chatbot.visual.base import (  # noqa: E402
    ImageGenerationProvider,
    ImageGenerationResult,
    ImageUnderstandingProvider,
    ImageUnderstandingResult,
    VISUAL_PROVIDER_REGISTRY,
    VisualModelIdentifier,
    VisualProvider,
    register_visual_provider,
    validate_model_identifier,
)
from chatbot.visual.generation import (  # noqa: E402
    GenerationOptions,
    ImageGenerationService,
    generated_image_url,
)
from chatbot.visual.understanding import (  # noqa: E402
    ImageUnderstandingService,
    UnderstandingOptions,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def appearance_manager() -> AppearanceManager:
    """Creates an AppearanceManager with default profile and default outfit manager."""
    return AppearanceManager()


@pytest.fixture
def sample_appearance() -> AppearanceProfile:
    """Creates an AppearanceProfile with known character details."""
    return AppearanceProfile(
        character_seed=123,
        character_description="A helpful AI companion",
        visual_traits=["blue_eyes", "warm_smile"],
        art_style="digital illustration",
    )


# ===========================================================================
# TestVisualProviderInterface
# ===========================================================================


class TestVisualProviderInterface:
    """Covers ABC instantiation error, provider registration, and model identifier validation."""

    def test_instantiate_visual_provider_without_abstract_methods_raises_type_error(
        self,
    ) -> None:
        """Instantiating VisualProvider without implementing abstract methods raises TypeError."""
        with pytest.raises(TypeError):
            VisualProvider(  # type: ignore[abstract]
                VisualModelIdentifier(provider="test", model="model")
            )

    def test_register_concrete_image_generation_provider(self) -> None:
        """A minimal concrete ImageGenerationProvider can be registered and appears in registry."""

        class _MinimalGenProvider(ImageGenerationProvider):
            provider_name = "minimal_gen_test"

            async def generate(
                self,
                prompt: str,
                negative_prompt: str | None = None,
                width: int = 512,
                height: int = 512,
                seed: int | None = None,
                appearance_profile: dict | None = None,
            ) -> ImageGenerationResult:
                return ImageGenerationResult(
                    image_data=b"fake",
                    mime_type="image/png",
                    seed_used=seed,
                    prompt_used=prompt,
                    model_used=str(self.identifier),
                    width=width,
                    height=height,
                    generation_time_ms=0.0,
                )

        registered = register_visual_provider(_MinimalGenProvider)
        assert registered is _MinimalGenProvider
        assert "minimal_gen_test" in VISUAL_PROVIDER_REGISTRY
        assert VISUAL_PROVIDER_REGISTRY["minimal_gen_test"] is _MinimalGenProvider

    # --- validate_model_identifier happy-path ---

    def test_validate_model_identifier_valid(self) -> None:
        result = validate_model_identifier("valid/model")
        assert isinstance(result, VisualModelIdentifier)
        assert result.provider == "valid"
        assert result.model == "model"
        assert str(result) == "valid/model"

    # --- validate_model_identifier error cases ---

    def test_validate_model_identifier_no_slash_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid visual model identifier"):
            validate_model_identifier("invalid")

    def test_validate_model_identifier_empty_model_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid visual model identifier"):
            validate_model_identifier("invalid/")

    def test_validate_model_identifier_empty_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid visual model identifier"):
            validate_model_identifier("/invalid")

    def test_validate_model_identifier_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid visual model identifier"):
            validate_model_identifier("")


# ===========================================================================
# TestAppearanceManager
# ===========================================================================


class TestAppearanceManager:
    """Tests appearance and outfit management."""

    def test_default_creates_profile_with_seed_42(
        self, appearance_manager: AppearanceManager
    ) -> None:
        profile = appearance_manager.get_profile()
        assert profile.character_seed == 42
        assert profile.character_description == ""
        assert profile.visual_traits == []
        assert profile.art_style == "digital illustration"

    def test_default_outfit_manager_has_one_default_outfit(
        self, appearance_manager: AppearanceManager
    ) -> None:
        om = appearance_manager.get_outfit_manager()
        outfits = om.list_outfits()
        assert len(outfits) == 1
        assert outfits[0].name == "default"
        assert outfits[0].is_active is True

    def test_update_character_description(
        self, appearance_manager: AppearanceManager
    ) -> None:
        appearance_manager.update_character_description("New description")
        assert appearance_manager.get_profile().character_description == "New description"
        assert appearance_manager.get_profile().updated_at is not None

    def test_update_visual_traits(
        self, appearance_manager: AppearanceManager
    ) -> None:
        appearance_manager.update_visual_traits(["green_eyes", "tall"])
        assert appearance_manager.get_profile().visual_traits == ["green_eyes", "tall"]
        assert appearance_manager.get_profile().updated_at is not None

    def test_build_generation_prompt_contains_all_profile_details(
        self, appearance_manager: AppearanceManager
    ) -> None:
        appearance_manager.update_character_description("A robot companion")
        appearance_manager.update_visual_traits(["red_leds", "chrome_body"])
        prompt, negative, seed = appearance_manager.build_generation_prompt("A park scene")
        assert "A park scene" in prompt
        assert "A robot companion" in prompt
        assert "red_leds" in prompt
        assert "chrome_body" in prompt
        assert "digital illustration" in prompt
        assert seed == 42

    def test_build_generation_prompt_returns_default_anti_traits_as_negative(
        self, appearance_manager: AppearanceManager
    ) -> None:
        _, negative, _ = appearance_manager.build_generation_prompt("test")
        assert isinstance(negative, str)
        assert "inconsistent character identity" in negative
        assert "blurry" in negative
        assert "distorted anatomy" in negative

    def test_build_generation_prompt_uses_custom_negative_when_provided(
        self, appearance_manager: AppearanceManager
    ) -> None:
        _, negative, _ = appearance_manager.build_generation_prompt(
            "test", negative_prompt="custom negative prompt"
        )
        assert negative == "custom negative prompt"

    def test_lock_seed_changes_and_returns_new_seed(
        self, appearance_manager: AppearanceManager
    ) -> None:
        result = appearance_manager.lock_seed(777)
        assert result == 777
        assert appearance_manager.get_profile().character_seed == 777
        assert appearance_manager.get_profile().updated_at is not None

    # --- OutfitManager ---

    def test_outfit_manager_add_outfit_creates_non_active_outfit(
        self, appearance_manager: AppearanceManager
    ) -> None:
        om = appearance_manager.get_outfit_manager()
        outfit = om.add_outfit("formal", "A formal suit", ["suit", "tie"])
        assert outfit.name == "formal"
        assert outfit.description == "A formal suit"
        assert outfit.visual_tags == ["suit", "tie"]
        assert outfit.is_active is False
        assert len(om.list_outfits()) == 2

    def test_outfit_manager_activate_outfit_makes_only_one_active(
        self, appearance_manager: AppearanceManager
    ) -> None:
        om = appearance_manager.get_outfit_manager()
        new_outfit = om.add_outfit("casual", "Casual outfit", ["jeans"])
        om.activate_outfit(new_outfit.outfit_id)
        active = om.get_active_outfit()
        assert active is not None
        assert active.outfit_id == new_outfit.outfit_id
        # Ensure original default is no longer active
        for outfit in om.list_outfits():
            if outfit.outfit_id != new_outfit.outfit_id:
                assert outfit.is_active is False

    def test_outfit_manager_remove_only_outfit_raises_value_error(
        self, appearance_manager: AppearanceManager
    ) -> None:
        om = appearance_manager.get_outfit_manager()
        only_outfit = om.list_outfits()[0]
        with pytest.raises(ValueError, match="Cannot remove the only remaining outfit"):
            om.remove_outfit(only_outfit.outfit_id)


# ===========================================================================
# TestImageGenerationService
# ===========================================================================


class TestImageGenerationService:
    """Tests the generation service with mocked providers and DB."""

    @staticmethod
    def _make_result(seed: int = 1, prompt: str = "test") -> ImageGenerationResult:
        return ImageGenerationResult(
            image_data=b"fake_image_data",
            mime_type="image/png",
            seed_used=seed,
            prompt_used=prompt,
            model_used="test/model",
            width=512,
            height=512,
            generation_time_ms=100.0,
        )

    def test_generate_and_persist_calls_provider_with_prompt_from_appearance_manager(
        self,
    ) -> None:
        profile = AppearanceProfile(
            character_seed=42,
            character_description="A wise owl",
            art_style="oil painting",
        )
        am = AppearanceManager(profile=profile)

        mock_provider = MagicMock(spec=ImageGenerationProvider)
        mock_provider.generate = AsyncMock(return_value=self._make_result())

        svc = ImageGenerationService(appearance_manager=am, provider=mock_provider)

        async def _run() -> ImageGenerationResult:
            return await svc.generate_and_persist("a forest scene")

        result = asyncio.run(_run())
        assert result.image_data == b"fake_image_data"

        mock_provider.generate.assert_called_once()
        call_prompt = mock_provider.generate.call_args.kwargs["prompt"]
        assert "a forest scene" in call_prompt
        assert "A wise owl" in call_prompt
        assert "oil painting" in call_prompt

    def test_generate_and_persist_uses_character_seed_when_options_seed_is_none(
        self,
    ) -> None:
        profile = AppearanceProfile(character_seed=99)
        am = AppearanceManager(profile=profile)

        mock_provider = MagicMock(spec=ImageGenerationProvider)
        mock_provider.generate = AsyncMock(return_value=self._make_result(seed=99))

        svc = ImageGenerationService(appearance_manager=am, provider=mock_provider)

        async def _run() -> ImageGenerationResult:
            return await svc.generate_and_persist("test", options=GenerationOptions())

        asyncio.run(_run())
        assert mock_provider.generate.call_args.kwargs["seed"] == 99

    def test_generation_options_seed_overrides_character_seed(self) -> None:
        profile = AppearanceProfile(character_seed=99)
        am = AppearanceManager(profile=profile)

        mock_provider = MagicMock(spec=ImageGenerationProvider)
        mock_provider.generate = AsyncMock(return_value=self._make_result(seed=42))

        svc = ImageGenerationService(appearance_manager=am, provider=mock_provider)

        async def _run() -> ImageGenerationResult:
            return await svc.generate_and_persist(
                "test", options=GenerationOptions(seed=42)
            )

        asyncio.run(_run())
        assert mock_provider.generate.call_args.kwargs["seed"] == 42

    def test_generate_and_persist_creates_image_memory_record(self) -> None:
        profile = AppearanceProfile(character_seed=1)
        am = AppearanceManager(profile=profile)

        mock_provider = MagicMock(spec=ImageGenerationProvider)
        mock_provider.generate = AsyncMock(return_value=self._make_result())

        svc = ImageGenerationService(appearance_manager=am, provider=mock_provider)

        mock_session = MagicMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        async def _run() -> ImageGenerationResult:
            return await svc.generate_and_persist("a scene", db_session=mock_session)

        asyncio.run(_run())

        mock_session.add.assert_called_once()
        record = mock_session.add.call_args[0][0]
        assert isinstance(record, _FakeImageMemory)
        assert record.image_url == generated_image_url(record.image_id)
        assert record.source_type == "generated"
        assert record.image_data == b"fake_image_data"
        assert record.mime_type == "image/png"
        assert record.seed_used == 1
        mock_session.commit.assert_called_once()

    def test_generate_and_persist_with_reference_returns_image_url(self) -> None:
        profile = AppearanceProfile(character_seed=1)
        am = AppearanceManager(profile=profile)

        mock_provider = MagicMock(spec=ImageGenerationProvider)
        mock_provider.generate = AsyncMock(return_value=self._make_result())

        svc = ImageGenerationService(appearance_manager=am, provider=mock_provider)

        mock_session = MagicMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        async def _run():
            return await svc.generate_and_persist_with_reference(
                "an avatar portrait",
                db_session=mock_session,
            )

        persisted = asyncio.run(_run())

        assert persisted.image_id is not None
        assert persisted.image_url == generated_image_url(persisted.image_id)

    def test_generate_only_does_not_create_database_record(self) -> None:
        profile = AppearanceProfile(character_seed=1)
        am = AppearanceManager(profile=profile)

        mock_provider = MagicMock(spec=ImageGenerationProvider)
        mock_provider.generate = AsyncMock(return_value=self._make_result())

        svc = ImageGenerationService(appearance_manager=am, provider=mock_provider)

        mock_session = MagicMock()
        mock_session.add = MagicMock()

        async def _run() -> ImageGenerationResult:
            return await svc.generate_only("test")

        asyncio.run(_run())

        # generate_only passes db_session=None → no db writes
        mock_session.add.assert_not_called()


# ===========================================================================
# TestImageUnderstandingService
# ===========================================================================


class TestImageUnderstandingService:
    """Tests the understanding service with mocked providers and DB."""

    @staticmethod
    def _make_result(description: str = "A cat") -> ImageUnderstandingResult:
        return ImageUnderstandingResult(
            description=description,
            detected_objects=["cat"],
            detected_text=None,
            scene_category="indoor",
            confidence=0.95,
            model_used="test/model",
            processing_time_ms=100.0,
        )

    def test_understand_and_persist_calls_provider_with_correct_args(self) -> None:
        mock_provider = MagicMock(spec=ImageUnderstandingProvider)
        mock_provider.understand = AsyncMock(return_value=self._make_result())

        svc = ImageUnderstandingService(provider=mock_provider)

        async def _run() -> ImageUnderstandingResult:
            return await svc.understand_and_persist(b"fake png", "image/png")

        result = asyncio.run(_run())
        assert result.description == "A cat"

        mock_provider.understand.assert_called_once()
        call_args = mock_provider.understand.call_args
        assert call_args[0][0] == b"fake png"
        assert call_args[0][1] == "image/png"
        # Default composite question
        question = call_args[0][2]
        assert "Describe this image" in question
        assert "Detect objects" in question
        assert "Extract any visible text" in question
        assert "Classify the scene" in question

    def test_understanding_options_question_passed_directly_to_provider(self) -> None:
        mock_provider = MagicMock(spec=ImageUnderstandingProvider)
        mock_provider.understand = AsyncMock(return_value=self._make_result())

        svc = ImageUnderstandingService(provider=mock_provider)
        options = UnderstandingOptions(question="What is this?")

        async def _run() -> ImageUnderstandingResult:
            return await svc.understand_and_persist(
                b"data", "image/jpeg", options=options
            )

        asyncio.run(_run())
        assert mock_provider.understand.call_args[0][2] == "What is this?"

    def test_understand_and_persist_creates_image_memory_record(self) -> None:
        mock_provider = MagicMock(spec=ImageUnderstandingProvider)
        mock_provider.understand = AsyncMock(
            return_value=ImageUnderstandingResult(
                description="A dog",
                detected_objects=["dog"],
                detected_text="woof",
                scene_category="outdoor",
                confidence=0.90,
                model_used="test/model",
                processing_time_ms=200.0,
            )
        )

        svc = ImageUnderstandingService(provider=mock_provider)

        mock_session = MagicMock()
        mock_session.add = MagicMock()

        async def _run() -> ImageUnderstandingResult:
            return await svc.understand_and_persist(
                b"img_bytes", "image/png", db_session=mock_session
            )

        asyncio.run(_run())

        mock_session.add.assert_called_once()
        record = mock_session.add.call_args[0][0]
        assert isinstance(record, _FakeImageMemory)
        assert record.source_type == "understood"
        assert record.image_data == b"img_bytes"
        assert record.description == "A dog"
        assert record.detected_objects == ["dog"]
        assert record.detected_text == "woof"
        assert record.scene_category == "outdoor"
        assert record.understanding_confidence == 0.90
        assert record.model_used == "test/model"

    def test_understand_only_does_not_create_database_record(self) -> None:
        mock_provider = MagicMock(spec=ImageUnderstandingProvider)
        mock_provider.understand = AsyncMock(return_value=self._make_result())

        svc = ImageUnderstandingService(provider=mock_provider)

        mock_session = MagicMock()
        mock_session.add = MagicMock()

        async def _run() -> ImageUnderstandingResult:
            return await svc.understand_only(b"img", "image/png")

        asyncio.run(_run())

        # understand_only does NOT take db_session → no DB writes
        mock_session.add.assert_not_called()


# ===========================================================================
# TestImageAPI
# ===========================================================================
# Simulated images_router — the real images.py (task b.8) does not exist yet.
# This simulation serves as a candidate conformance check for the real router.
# ===========================================================================


class TestImageAPI:
    """Tests the FastAPI image endpoints against a simulated images_router."""

    # ------------------------------------------------------------------
    # Pydantic models matching expected endpoint shapes
    # ------------------------------------------------------------------

    class _ImageGenerateResponse(BaseModel):
        image_id: str
        image_base64: str
        mime_type: str
        prompt_used: str
        seed_used: int
        model_used: str
        width: int
        height: int
        generation_time_ms: float

    class _ImageUnderstandResponse(BaseModel):
        image_id: str
        description: str
        detected_objects: list[str]
        detected_text: str | None = None
        scene_category: str | None = None
        confidence: float
        model_used: str
        processing_time_ms: float

    class _ImageView(BaseModel):
        image_id: str
        image_base64: str
        mime_type: str
        source_type: str
        prompt_used: str | None = None
        description: str | None = None

    class _ImageListResponse(BaseModel):
        images: list["TestImageAPI._ImageView"]
        total: int

    class _AppearanceResponse(BaseModel):
        appearance_id: str
        character_seed: int
        character_description: str
        visual_traits: list[str]
        art_style: str
        color_palette: list[str]
        created_at: str
        updated_at: str | None = None

    class _OutfitResponse(BaseModel):
        outfit_id: str
        name: str
        description: str
        visual_tags: list[str]
        is_active: bool

    class _OutfitListResponse(BaseModel):
        outfits: list["TestImageAPI._OutfitResponse"]

    class _ImageUploadRequest(BaseModel):
        image_base64: str
        companion_id: str | None = None

    class _ImageUploadResponse(BaseModel):
        image_id: str
        image_url: str

    # ------------------------------------------------------------------
    # Request bodies
    # ------------------------------------------------------------------

    class _GenerateRequest(BaseModel):
        scene_description: str = Field(..., min_length=1)

    class _AppearancePatchRequest(BaseModel):
        character_description: str | None = None
        visual_traits: list[str] | None = None
        art_style: str | None = None
        color_palette: list[str] | None = None

    class _SeedUpdateRequest(BaseModel):
        seed: int

    class _OutfitCreateRequest(BaseModel):
        name: str = Field(..., min_length=1)
        description: str = ""
        visual_tags: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Fixture
    # ------------------------------------------------------------------

    @pytest.fixture
    def test_app(self) -> TestClient:
        """Constructs a FastAPI app with simulated images_router and returns TestClient."""
        images_router = APIRouter(prefix="/api/images", tags=["images"])

        # In-memory stores
        images_store: dict[str, dict] = {}
        appearances_store: dict[str, dict] = {}
        outfits_store: dict[str, list[dict]] = {}

        # -------------------------------------------------- generate
        @images_router.post(
            "/generate", response_model=TestImageAPI._ImageGenerateResponse
        )
        async def generate(req: TestImageAPI._GenerateRequest):
            image_id = str(uuid.uuid4())
            record = {
                "image_id": image_id,
                "image_base64": "aW1hZ2VfZGF0YQ==",
                "mime_type": "image/png",
                "prompt_used": req.scene_description,
                "seed_used": 42,
                "model_used": "test/model",
                "width": 512,
                "height": 512,
                "generation_time_ms": 150.0,
            }
            images_store[image_id] = record
            return TestImageAPI._ImageGenerateResponse(**record)

        # -------------------------------------------------- upload
        @images_router.post(
            "/upload", response_model=TestImageAPI._ImageUploadResponse
        )
        async def upload(req: TestImageAPI._ImageUploadRequest):
            image_id = str(uuid.uuid4())
            record = {
                "image_id": image_id,
                "image_url": f"/api/images/{image_id}",
            }
            images_store[image_id] = record
            return TestImageAPI._ImageUploadResponse(**record)

        # -------------------------------------------------- delete
        @images_router.delete(
            "/images/{image_id}", status_code=204
        )
        async def delete(image_id: str):
            if image_id not in images_store:
                raise HTTPException(status_code=404, detail="Image not found")
            del images_store[image_id]

        # -------------------------------------------------- understand
        @images_router.post(
            "/understand", response_model=TestImageAPI._ImageUnderstandResponse
        )
        async def understand(image: UploadFile = File(...)):
            content = await image.read()
            image_id = str(uuid.uuid4())
            record = {
                "image_id": image_id,
                "description": "An uploaded image",
                "detected_objects": ["object"],
                "detected_text": None,
                "scene_category": "general",
                "confidence": 0.95,
                "model_used": "test/model",
                "processing_time_ms": 100.0,
            }
            images_store[image_id] = record
            return TestImageAPI._ImageUnderstandResponse(**record)

        # -------------------------------------------------- get image
        @images_router.get("/{image_id}", response_model=TestImageAPI._ImageView)
        async def get_image(image_id: str):
            if image_id not in images_store:
                raise HTTPException(status_code=404, detail="Image not found")
            rec = images_store[image_id]
            return TestImageAPI._ImageView(
                image_id=rec.get("image_id", image_id),
                image_base64=rec.get("image_base64", ""),
                mime_type=rec.get("mime_type", "image/png"),
                source_type=rec.get("source_type", "generated"),
                prompt_used=rec.get("prompt_used"),
                description=rec.get("description"),
            )

        # -------------------------------------------------- list images
        @images_router.get("/", response_model=TestImageAPI._ImageListResponse)
        async def list_images():
            items = [
                TestImageAPI._ImageView(
                    image_id=k,
                    image_base64=v.get("image_base64", ""),
                    mime_type=v.get("mime_type", "image/png"),
                    source_type=v.get("source_type", "generated"),
                    prompt_used=v.get("prompt_used"),
                    description=v.get("description"),
                )
                for k, v in images_store.items()
            ]
            return TestImageAPI._ImageListResponse(images=items, total=len(items))

        # -------------------------------------------------- get appearance
        @images_router.get(
            "/appearance/{companion_id}",
            response_model=TestImageAPI._AppearanceResponse,
        )
        async def get_appearance(companion_id: str):
            if companion_id in appearances_store:
                return TestImageAPI._AppearanceResponse(**appearances_store[companion_id])
            return TestImageAPI._AppearanceResponse(
                appearance_id=companion_id,
                character_seed=42,
                character_description="",
                visual_traits=[],
                art_style="digital illustration",
                color_palette=[],
                created_at=_utcnow_iso(),
            )

        # -------------------------------------------------- patch appearance
        @images_router.patch(
            "/appearance/{companion_id}",
            response_model=TestImageAPI._AppearanceResponse,
        )
        async def patch_appearance(
            companion_id: str, body: TestImageAPI._AppearancePatchRequest
        ):
            current = appearances_store.get(
                companion_id,
                {
                    "appearance_id": companion_id,
                    "character_seed": 42,
                    "character_description": "",
                    "visual_traits": [],
                    "art_style": "digital illustration",
                    "color_palette": [],
                    "created_at": _utcnow_iso(),
                },
            )
            if body.character_description is not None:
                current["character_description"] = body.character_description
            if body.visual_traits is not None:
                current["visual_traits"] = body.visual_traits
            if body.art_style is not None:
                current["art_style"] = body.art_style
            if body.color_palette is not None:
                current["color_palette"] = body.color_palette
            current["updated_at"] = _utcnow_iso()
            appearances_store[companion_id] = current
            return TestImageAPI._AppearanceResponse(**current)

        # -------------------------------------------------- update seed
        @images_router.put(
            "/appearance/{companion_id}/seed",
            response_model=TestImageAPI._AppearanceResponse,
        )
        async def update_seed(
            companion_id: str, body: TestImageAPI._SeedUpdateRequest
        ):
            if companion_id not in appearances_store:
                appearances_store[companion_id] = {
                    "appearance_id": companion_id,
                    "character_seed": body.seed,
                    "character_description": "",
                    "visual_traits": [],
                    "art_style": "digital illustration",
                    "color_palette": [],
                    "created_at": _utcnow_iso(),
                }
            else:
                appearances_store[companion_id]["character_seed"] = body.seed
                appearances_store[companion_id]["updated_at"] = _utcnow_iso()
            return TestImageAPI._AppearanceResponse(
                **appearances_store[companion_id]
            )

        # -------------------------------------------------- list outfits
        @images_router.get(
            "/outfits/{appearance_id}",
            response_model=TestImageAPI._OutfitListResponse,
        )
        async def list_outfits(appearance_id: str):
            outfits = outfits_store.get(appearance_id, [])
            return TestImageAPI._OutfitListResponse(
                outfits=[
                    TestImageAPI._OutfitResponse(**o) for o in outfits
                ]
            )

        # -------------------------------------------------- create outfit
        @images_router.post(
            "/outfits/{appearance_id}",
            response_model=TestImageAPI._OutfitResponse,
        )
        async def create_outfit(
            appearance_id: str, body: TestImageAPI._OutfitCreateRequest
        ):
            if appearance_id not in outfits_store:
                outfits_store[appearance_id] = []
            outfit = {
                "outfit_id": str(uuid.uuid4()),
                "name": body.name,
                "description": body.description,
                "visual_tags": body.visual_tags,
                "is_active": False,
            }
            outfits_store[appearance_id].append(outfit)
            return TestImageAPI._OutfitResponse(**outfit)

        # -------------------------------------------------- activate outfit
        @images_router.put(
            "/outfits/{appearance_id}/{outfit_id}/activate",
            response_model=TestImageAPI._OutfitResponse,
        )
        async def activate_outfit(appearance_id: str, outfit_id: str):
            if appearance_id not in outfits_store:
                raise HTTPException(status_code=404, detail="Appearance not found")
            for outf in outfits_store[appearance_id]:
                outf["is_active"] = outf["outfit_id"] == outfit_id
            for outf in outfits_store[appearance_id]:
                if outf["outfit_id"] == outfit_id:
                    return TestImageAPI._OutfitResponse(**outf)
            raise HTTPException(status_code=404, detail="Outfit not found")

        # -------------------------------------------------- build app
        app = FastAPI()
        app.include_router(images_router)
        return TestClient(app)

    # ----------------------------------------------------------------
    # Generate endpoint
    # ----------------------------------------------------------------

    def test_post_generate_valid_request_returns_200(
        self, test_app: TestClient
    ) -> None:
        response = test_app.post(
            "/api/images/generate",
            json={"scene_description": "A sunset over the mountains"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "image_id" in data
        assert "image_base64" in data
        assert data["mime_type"] == "image/png"
        assert data["width"] == 512
        assert data["height"] == 512
        assert data["generation_time_ms"] == 150.0

    def test_post_generate_empty_scene_description_returns_422(
        self, test_app: TestClient
    ) -> None:
        response = test_app.post(
            "/api/images/generate",
            json={"scene_description": ""},
        )
        assert response.status_code == 422

    def test_post_upload_returns_200(self, test_app: TestClient) -> None:
        response = test_app.post(
            "/api/images/upload",
            json={
                "image_base64": "data:image/png;base64,aW1hZ2VfZGF0YQ==",
                "companion_id": "companion-123",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "image_id" in data
        assert "image_url" in data
        assert data["image_url"] == f"/api/images/{data['image_id']}"

    def test_delete_image_returns_204(self, test_app: TestClient) -> None:
        # First upload an image
        upload_resp = test_app.post(
            "/api/images/upload",
            json={
                "image_base64": "data:image/png;base64,aW1hZ2VfZGF0YQ==",
                "companion_id": "companion-123",
            },
        )
        assert upload_resp.status_code == 200
        image_id = upload_resp.json()["image_id"]

        # Delete it
        del_resp = test_app.delete(f"/api/images/images/{image_id}")
        assert del_resp.status_code == 204

        # Verify it is gone
        get_resp = test_app.get(f"/api/images/{image_id}")
        assert get_resp.status_code == 404

    def test_delete_image_nonexistent_returns_404(self, test_app: TestClient) -> None:
        response = test_app.delete("/api/images/images/nonexistent-id-54321")
        assert response.status_code == 404

    # ----------------------------------------------------------------
    # Understand endpoint
    # ----------------------------------------------------------------

    def test_post_understand_with_file_upload_returns_200(
        self, test_app: TestClient
    ) -> None:
        response = test_app.post(
            "/api/images/understand",
            files={"image": ("test.png", b"fake_image_bytes", "image/png")},
        )
        assert response.status_code == 200
        data = response.json()
        assert "image_id" in data
        assert data["description"] == "An uploaded image"
        assert data["detected_objects"] == ["object"]
        assert data["confidence"] == 0.95

    # ----------------------------------------------------------------
    # Get / list images
    # ----------------------------------------------------------------

    def test_get_image_with_valid_id_returns_200(
        self, test_app: TestClient
    ) -> None:
        # First create an image via generate
        gen = test_app.post(
            "/api/images/generate",
            json={"scene_description": "test"},
        )
        assert gen.status_code == 200
        image_id = gen.json()["image_id"]

        response = test_app.get(f"/api/images/{image_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["image_id"] == image_id
        assert data["mime_type"] == "image/png"

    def test_get_image_with_nonexistent_id_returns_404(
        self, test_app: TestClient
    ) -> None:
        response = test_app.get("/api/images/nonexistent-id-12345")
        assert response.status_code == 404

    def test_get_images_list_returns_200(
        self, test_app: TestClient
    ) -> None:
        # Seed an image
        test_app.post(
            "/api/images/generate",
            json={"scene_description": "for list"},
        )
        response = test_app.get("/api/images/")
        assert response.status_code == 200
        data = response.json()
        assert "images" in data
        assert isinstance(data["images"], list)
        assert "total" in data
        assert data["total"] == len(data["images"])

    # ----------------------------------------------------------------
    # Appearance CRUD
    # ----------------------------------------------------------------

    def test_get_appearance_returns_200(
        self, test_app: TestClient
    ) -> None:
        response = test_app.get("/api/images/appearance/comp-1")
        assert response.status_code == 200
        data = response.json()
        assert data["appearance_id"] == "comp-1"
        assert data["character_seed"] == 42
        assert "character_description" in data
        assert "visual_traits" in data
        assert "art_style" in data

    def test_patch_appearance_partial_fields_returns_200(
        self, test_app: TestClient
    ) -> None:
        response = test_app.patch(
            "/api/images/appearance/comp-1",
            json={"character_description": "A friendly ghost"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["character_description"] == "A friendly ghost"
        assert data["updated_at"] is not None

    def test_put_seed_returns_200_with_updated_seed(
        self, test_app: TestClient
    ) -> None:
        response = test_app.put(
            "/api/images/appearance/comp-1/seed",
            json={"seed": 999},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["character_seed"] == 999

    # ----------------------------------------------------------------
    # Outfit endpoints
    # ----------------------------------------------------------------

    def test_get_outfits_returns_200_with_list_shape(
        self, test_app: TestClient
    ) -> None:
        # Create an outfit first so the list is non-empty
        test_app.post(
            "/api/images/outfits/app-1",
            json={"name": "test_outfit", "description": "", "visual_tags": []},
        )
        response = test_app.get("/api/images/outfits/app-1")
        assert response.status_code == 200
        data = response.json()
        assert "outfits" in data
        assert isinstance(data["outfits"], list)
        assert len(data["outfits"]) >= 1

    def test_post_outfits_returns_200_with_outfit_shape(
        self, test_app: TestClient
    ) -> None:
        response = test_app.post(
            "/api/images/outfits/app-1",
            json={
                "name": "battle_armor",
                "description": "Heavy armor",
                "visual_tags": ["metal", "spikes"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "battle_armor"
        assert data["description"] == "Heavy armor"
        assert data["visual_tags"] == ["metal", "spikes"]
        assert data["is_active"] is False

    def test_put_activate_outfit_returns_200(
        self, test_app: TestClient
    ) -> None:
        # Create an outfit to activate
        create = test_app.post(
            "/api/images/outfits/app-2",
            json={"name": "casual", "description": "", "visual_tags": []},
        )
        assert create.status_code == 200
        outfit_id = create.json()["outfit_id"]

        response = test_app.put(
            f"/api/images/outfits/app-2/{outfit_id}/activate",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["outfit_id"] == outfit_id
        assert data["is_active"] is True
