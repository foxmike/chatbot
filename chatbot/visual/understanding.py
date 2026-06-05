"""Image understanding adapter. Provides concrete ImageUnderstandingProvider implementations and an understanding service that analyzes user-sent images, producing descriptions, object detection, OCR text, and scene classification. Results are persisted as ImageMemory records."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from chatbot.db.models import ImageMemory
from chatbot.visual.base import (
    ImageUnderstandingProvider,
    ImageUnderstandingResult,
    VisualModelIdentifier,
    validate_model_identifier,
)


class UnderstandingError(Exception):
    """Wraps provider errors from image understanding."""


@dataclass
class UnderstandingOptions:
    question: str | None = None
    detail_level: Literal["brief", "standard", "detailed"] = "standard"
    detect_objects: bool = True
    extract_text: bool = True
    classify_scene: bool = True


class ImageUnderstandingAdapter(ImageUnderstandingProvider):
    """Concrete provider adapter that delegates understanding to an injected async callable."""

    def __init__(
        self,
        model_identifier: str,
        understand_fn: Callable[[bytes, str, str | None], Awaitable[ImageUnderstandingResult]],
        provider_label: str = "adapter",
    ) -> None:
        parsed: VisualModelIdentifier = validate_model_identifier(model_identifier)
        super().__init__(parsed)
        self._understand_fn = understand_fn
        self._provider_label = provider_label

    @property
    def provider_name(self) -> str:
        return self._provider_label

    async def understand(
        self,
        image_data: bytes,
        mime_type: str,
        question: str | None = None,
    ) -> ImageUnderstandingResult:
        return await self._understand_fn(image_data, mime_type, question)


class ImageUnderstandingService:
    def __init__(self, provider: ImageUnderstandingProvider) -> None:
        self._provider = provider

    def _build_question(self, options: UnderstandingOptions) -> str | None:
        if options.question is not None:
            return options.question
        parts = ["Describe this image."]
        if options.detect_objects:
            parts.append("Detect objects.")
        if options.extract_text:
            parts.append("Extract any visible text.")
        if options.classify_scene:
            parts.append("Classify the scene.")
        return " ".join(parts)

    async def understand_and_persist(
        self,
        image_data: bytes,
        mime_type: str,
        options: UnderstandingOptions | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        user_id: str | None = None,
        db_session=None,
    ) -> ImageUnderstandingResult:
        if options is None:
            options = UnderstandingOptions()
        question = self._build_question(options)
        start = time.monotonic()
        try:
            result = await self._provider.understand(image_data, mime_type, question)
        except Exception as exc:
            raise UnderstandingError(str(exc)) from exc
        end = time.monotonic()
        _ = end - start

        if db_session is not None:
            record = ImageMemory(
                source_type="understood",
                session_id=session_id,
                turn_id=turn_id,
                user_id=user_id,
                image_data=image_data,
                mime_type=mime_type,
                description=result.description,
                detected_objects=result.detected_objects,
                detected_text=result.detected_text,
                scene_category=result.scene_category,
                understanding_confidence=result.confidence,
                model_used=result.model_used,
                processing_time_ms=result.processing_time_ms,
            )
            db_session.add(record)

        return result

    async def understand_only(
        self,
        image_data: bytes,
        mime_type: str,
        question: str | None = None,
    ) -> ImageUnderstandingResult:
        try:
            return await self._provider.understand(image_data, mime_type, question)
        except Exception as exc:
            raise UnderstandingError(str(exc)) from exc


from chatbot.config import get_settings

__all__ = [
    "ImageUnderstandingAdapter",
    "ImageUnderstandingService",
    "UnderstandingError",
    "UnderstandingOptions",
]


def create_image_understanding_service() -> ImageUnderstandingService:
    """Factory function to create ImageUnderstandingService using configured settings."""
    from chatbot.providers.factory import create_provider
    
    settings = get_settings()
    provider = create_provider(
        provider_type="image_understanding",
        provider_name=settings.image_understanding_provider,
        model=settings.image_understanding_model,
        api_key=settings.image_understanding_api_key,
        base_url=settings.image_understanding_base_url,
    )
    return ImageUnderstandingService(provider)
