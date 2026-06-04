"""Visual provider interface hierarchy. Defines abstract base classes for image-generation and image-understanding providers, plus provider model validation that mirrors the LLM Provider pattern."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import BaseModel, Field


class VisualModelIdentifier(BaseModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)

    def __str__(self) -> str:
        return f"{self.provider}/{self.model}"


VisualProviderModel = VisualModelIdentifier


def validate_model_identifier(identifier: str) -> VisualModelIdentifier:
    parts = identifier.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("Invalid visual model identifier: expected provider/model format")
    return VisualModelIdentifier(provider=parts[0], model=parts[1])


@dataclass(frozen=True, slots=True)
class ImageGenerationResult:
    image_data: bytes
    mime_type: str
    seed_used: int | None
    prompt_used: str
    model_used: str
    width: int
    height: int
    generation_time_ms: float


@dataclass(frozen=True, slots=True)
class ImageUnderstandingResult:
    description: str
    detected_objects: list[str]
    detected_text: str | None
    scene_category: str | None
    confidence: float
    model_used: str
    processing_time_ms: float


class VisualProvider(ABC):
    def __init__(self, identifier: VisualModelIdentifier) -> None:
        self._identifier = identifier

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return a human-readable provider label."""

    @property
    def identifier(self) -> VisualModelIdentifier:
        return self._identifier

    @property
    def provider_model_string(self) -> str:
        return str(self.identifier)


class ImageGenerationProvider(VisualProvider):
    @abstractmethod
    async def generate(
        self,
        prompt: str,
        negative_prompt: str | None = None,
        width: int = 512,
        height: int = 512,
        seed: int | None = None,
        appearance_profile: dict | None = None,
    ) -> ImageGenerationResult:
        """Generate an image from a text prompt."""


class ImageUnderstandingProvider(VisualProvider):
    @abstractmethod
    async def understand(
        self,
        image_data: bytes,
        mime_type: str,
        question: str | None = None,
    ) -> ImageUnderstandingResult:
        """Analyze image bytes and return normalized understanding output."""


VISUAL_PROVIDER_REGISTRY: dict[str, type[VisualProvider]] = {}


def register_visual_provider(provider_cls: type[VisualProvider]) -> type[VisualProvider]:
    provider_name = provider_cls.provider_name
    if not isinstance(provider_name, str) or not provider_name:
        raise ValueError("provider name must not be empty")
    if provider_name in VISUAL_PROVIDER_REGISTRY:
        raise ValueError(f"provider already registered: {provider_name}")
    VISUAL_PROVIDER_REGISTRY[provider_name] = provider_cls
    return provider_cls


__all__ = [
    "ImageGenerationProvider",
    "ImageGenerationResult",
    "ImageUnderstandingProvider",
    "ImageUnderstandingResult",
    "VISUAL_PROVIDER_REGISTRY",
    "VisualModelIdentifier",
    "VisualProviderModel",
    "VisualProvider",
    "register_visual_provider",
    "validate_model_identifier",
]
