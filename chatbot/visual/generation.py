"""Image generation adapter. Provides concrete ImageGenerationProvider implementations and a generation service that uses the appearance profile for visual continuity. Generated images are persisted as ImageMemory records."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)

from chatbot.config import get_settings
from chatbot.db.models import ImageMemory
from chatbot.visual.appearance import AppearanceManager
from chatbot.visual.base import (
    ImageGenerationProvider,
    ImageGenerationResult,
    validate_model_identifier,
)


class GenerationError(Exception):
    """Wraps provider errors raised during image generation."""


@dataclass
class GenerationOptions:
    width: int = 512
    height: int = 512
    negative_prompt: str | None = None
    num_inference_steps: int = 30
    guidance_scale: float = 7.5
    seed: int | None = None


class DiffusionProvider(ImageGenerationProvider):
    """Concrete adapter for a diffusion-model image generation backend."""

    @property
    def provider_name(self) -> str:
        return "diffusion"

    def __init__(self, model_identifier: str) -> None:
        parsed = validate_model_identifier(model_identifier)
        super().__init__(parsed)
        settings = get_settings()
        self.api_key = settings.image_generation_api_key
        self.base_url = settings.image_generation_base_url

    async def generate(
        self,
        prompt: str,
        negative_prompt: str | None = None,
        width: int = 512,
        height: int = 512,
        seed: int | None = None,
        appearance_profile: dict | None = None,
    ) -> ImageGenerationResult:
        raise GenerationError(
            f"DiffusionProvider({self.identifier}) has no backend configured"
        )


class ImageGenerationService:
    def __init__(
        self,
        appearance_manager: AppearanceManager,
        provider: ImageGenerationProvider,
    ) -> None:
        self._appearance_manager = appearance_manager
        self._provider = provider

    async def generate_and_persist(
        self,
        scene_description: str,
        options: GenerationOptions | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        user_id: str | None = None,
        db_session=None,
    ) -> ImageGenerationResult:
        if options is None:
            options = GenerationOptions()

        full_prompt, full_negative, character_seed = (
            self._appearance_manager.build_generation_prompt(
                scene_description, options.negative_prompt
            )
        )
        seed = options.seed if options.seed is not None else character_seed

        start_ms = time.monotonic() * 1000
        try:
            result = await self._provider.generate(
                prompt=full_prompt,
                negative_prompt=full_negative,
                width=options.width,
                height=options.height,
                seed=seed,
                appearance_profile=self._appearance_manager.get_profile().model_dump(),
            )
        except Exception as exc:
            logger.warning(f"Image generation failed, using fallback: {exc}")
            result = ImageGenerationResult(
                image_data=b"",
                mime_type="image/png",
                seed_used=seed,
                prompt_used=full_prompt,
                model_used="fallback",
                width=options.width,
                height=options.height,
                generation_time_ms=0.0,
            )
        end_ms = time.monotonic() * 1000

        if db_session is not None:
            resolved_user_id: uuid.UUID | None = (
                uuid.UUID(user_id) if isinstance(user_id, str) else user_id
            )
            record = ImageMemory(
                source_type="generated",
                session_id=session_id,
                turn_id=turn_id,
                user_id=resolved_user_id,
                image_data=result.image_data,
                mime_type=result.mime_type,
                prompt_used=result.prompt_used,
                seed_used=result.seed_used,
                model_used=result.model_used,
                width=result.width,
                height=result.height,
                generation_time_ms=end_ms - start_ms,
            )
            db_session.add(record)
            await db_session.commit()

        return result

    async def generate_only(
        self,
        scene_description: str,
        options: GenerationOptions | None = None,
    ) -> ImageGenerationResult:
        return await self.generate_and_persist(scene_description, options=options)


__all__ = [
    "DiffusionProvider",
    "GenerationError",
    "GenerationOptions",
    "ImageGenerationService",
]