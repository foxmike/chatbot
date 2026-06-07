"""Image generation adapter. Provides concrete ImageGenerationProvider implementations and a generation service that uses the appearance profile for visual continuity. Generated images are persisted as ImageMemory records."""

from __future__ import annotations

import base64
import logging
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from chatbot.config import get_settings
from chatbot.db.models import ImageMemory
from chatbot.visual.appearance import AppearanceManager
from chatbot.visual.base import (
    ImageGenerationProvider,
    ImageGenerationResult,
    VisualModelIdentifier,
    register_visual_provider,
    validate_model_identifier,
)

logger = logging.getLogger(__name__)


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


class OpenRouterImageGenerationProvider(ImageGenerationProvider):
    """Image generation provider backed by OpenRouter chat completions."""

    provider_name = "openrouter"

    def __init__(self, model_identifier: str | VisualModelIdentifier) -> None:
        parsed = _parse_model_identifier(model_identifier)
        super().__init__(parsed)
        settings = get_settings()
        self.api_key = settings.image_generation_api_key or settings.openrouter_api_key
        self.base_url = _resolve_openrouter_base_url(
            settings.image_generation_base_url,
            settings.openrouter_base_url,
        )
        self.timeout_seconds = 120.0

    async def generate(
        self,
        prompt: str,
        negative_prompt: str | None = None,
        width: int = 512,
        height: int = 512,
        seed: int | None = None,
        appearance_profile: dict | None = None,
    ) -> ImageGenerationResult:
        if not self.api_key:
            raise GenerationError("OpenRouter image generation API key is not configured")

        model = _openrouter_model_name(self.identifier)
        payload = self._build_payload(
            model=model,
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            seed=seed,
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        start = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
        except httpx.TimeoutException as exc:
            raise GenerationError("OpenRouter image generation request timed out") from exc
        except httpx.HTTPError as exc:
            raise GenerationError(f"OpenRouter image generation request failed: {exc}") from exc

        if response.status_code >= 400:
            raise GenerationError(_http_error_message(response))

        image_data, mime_type, model_used = _parse_openrouter_image_response(
            response=response,
            fallback_model=model,
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        return ImageGenerationResult(
            image_data=image_data,
            mime_type=mime_type,
            seed_used=seed,
            prompt_used=prompt,
            model_used=model_used,
            width=width,
            height=height,
            generation_time_ms=elapsed_ms,
        )

    def _build_payload(
        self,
        *,
        model: str,
        prompt: str,
        negative_prompt: str | None,
        width: int,
        height: int,
        seed: int | None,
    ) -> dict[str, Any]:
        content = prompt
        if negative_prompt:
            content = f"{content}\n\nAvoid: {negative_prompt}"

        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "modalities": ["image", "text"],
            "stream": False,
            "image_config": {"aspect_ratio": _aspect_ratio(width, height)},
        }
        if seed is not None:
            payload["seed"] = seed
        return payload


def _parse_model_identifier(
    model_identifier: str | VisualModelIdentifier,
) -> VisualModelIdentifier:
    if isinstance(model_identifier, VisualModelIdentifier):
        return model_identifier
    if model_identifier.startswith("openrouter/"):
        model = model_identifier.removeprefix("openrouter/")
        if not model:
            raise ValueError("Invalid OpenRouter model identifier: missing model")
        return VisualModelIdentifier(provider="openrouter", model=model)
    return validate_model_identifier(model_identifier)


def _resolve_openrouter_base_url(image_base_url: str, openrouter_base_url: str) -> str:
    if image_base_url and image_base_url.rstrip("/") != "https://api.openai.com/v1":
        return image_base_url.rstrip("/")
    return (openrouter_base_url or "https://openrouter.ai/api/v1").rstrip("/")


def _openrouter_model_name(identifier: VisualModelIdentifier) -> str:
    if identifier.provider == "openrouter":
        return identifier.model
    return str(identifier)


def _aspect_ratio(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return "1:1"
    divisor = _gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def _gcd(left: int, right: int) -> int:
    while right:
        left, right = right, left % right
    return abs(left) or 1


def _parse_openrouter_image_response(
    *,
    response: httpx.Response,
    fallback_model: str,
) -> tuple[bytes, str, str]:
    try:
        raw = response.json()
    except ValueError as exc:
        raise GenerationError("OpenRouter image generation returned invalid JSON") from exc
    if not isinstance(raw, Mapping):
        raise GenerationError("OpenRouter image generation returned an unexpected response")

    data_url = _extract_first_image_data_url(raw)
    if data_url is None:
        raise GenerationError("OpenRouter image generation response did not contain an image")

    image_data, mime_type = _decode_data_url(data_url)
    model = raw.get("model")
    return image_data, mime_type, model if isinstance(model, str) else fallback_model


def _extract_first_image_data_url(raw: Mapping[str, Any]) -> str | None:
    choices = raw.get("choices")
    if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)):
        return None
    for choice in choices:
        if not isinstance(choice, Mapping):
            continue
        message = choice.get("message")
        if not isinstance(message, Mapping):
            continue
        data_url = _extract_image_from_message(message)
        if data_url:
            return data_url
    return None


def _extract_image_from_message(message: Mapping[str, Any]) -> str | None:
    images = message.get("images")
    if isinstance(images, Sequence) and not isinstance(images, (str, bytes)):
        for image in images:
            data_url = _extract_image_url(image)
            if data_url:
                return data_url

    content = message.get("content")
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        for part in content:
            data_url = _extract_image_url(part)
            if data_url:
                return data_url
    return None


def _extract_image_url(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    image_url = value.get("image_url") or value.get("imageUrl")
    if isinstance(image_url, Mapping):
        url = image_url.get("url")
        return url if isinstance(url, str) else None
    if isinstance(image_url, str):
        return image_url
    url = value.get("url")
    return url if isinstance(url, str) else None


def _decode_data_url(data_url: str) -> tuple[bytes, str]:
    header, separator, encoded = data_url.partition(",")
    if separator != "," or not header.startswith("data:") or ";base64" not in header:
        raise GenerationError("OpenRouter image generation returned a non-base64 image URL")
    mime_type = header[5:].split(";", 1)[0] or "image/png"
    try:
        return base64.b64decode(encoded, validate=True), mime_type
    except ValueError as exc:
        raise GenerationError("OpenRouter image generation returned invalid image data") from exc


def _http_error_message(response: httpx.Response) -> str:
    detail = response.text
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, Mapping):
        error = body.get("error")
        if isinstance(error, Mapping):
            detail = str(error.get("message") or error.get("code") or detail)
        elif error is not None:
            detail = str(error)
        elif body.get("message") is not None:
            detail = str(body["message"])
    return f"OpenRouter image generation HTTP {response.status_code}: {detail}".strip()


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


def create_image_generation_service(
    appearance_manager: AppearanceManager | None = None,
) -> ImageGenerationService:
    """Create an image generation service from configured visual settings."""
    settings = get_settings()
    if settings.image_generation_provider != "openrouter":
        provider: ImageGenerationProvider = DiffusionProvider(settings.image_generation_model)
    else:
        provider = OpenRouterImageGenerationProvider(
            f"openrouter/{settings.image_generation_model}"
            if not settings.image_generation_model.startswith("openrouter/")
            else settings.image_generation_model
        )
    return ImageGenerationService(
        appearance_manager=appearance_manager or AppearanceManager(),
        provider=provider,
    )


__all__ = [
    "DiffusionProvider",
    "OpenRouterImageGenerationProvider",
    "GenerationError",
    "GenerationOptions",
    "ImageGenerationService",
    "create_image_generation_service",
]


register_visual_provider(OpenRouterImageGenerationProvider)
