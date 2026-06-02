from __future__ import annotations

import logging

from chatbot.config import Settings
from chatbot.providers.base import ProviderRegistry
from chatbot.providers.openrouter import OpenRouterProvider

logger = logging.getLogger(__name__)


def build_default_registry(settings: Settings) -> ProviderRegistry:
    registry = ProviderRegistry()

    if not settings.openrouter_api_key:
        logger.warning(
            "OPENROUTER_API_KEY is not set; openrouter provider registered but calls will fail"
        )

    registry.register(
        "openrouter",
        OpenRouterProvider(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            default_model=settings.default_model,
        ),
    )

    return registry


__all__ = ["build_default_registry"]
