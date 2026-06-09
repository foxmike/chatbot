from __future__ import annotations

import logging

from chatbot.config import Settings, get_llm_config
from chatbot.providers.base import ProviderRegistry
from chatbot.providers.openrouter import OpenRouterProvider

logger = logging.getLogger(__name__)


def build_default_registry(settings: Settings) -> ProviderRegistry:
    registry = ProviderRegistry()

    llm_config = get_llm_config()
    provider_name = settings.llm_provider.lower()

    if provider_name == "openrouter" and not settings.openrouter_api_key:
        logger.warning(
            "OPENROUTER_API_KEY is not set; openrouter provider registered but calls will fail"
        )

    # Register under the active provider name. OpenRouter, SGLang, vLLM,
    # and Ollama all use the same OpenAI-compatible adapter here.
    registry.register(
        provider_name,
        OpenRouterProvider(
            api_key=llm_config["api_key"],
            base_url=llm_config["base_url"],
            default_model=llm_config["model"],
        ),
    )

    # Also register as "openrouter" as a fallback alias if not openrouter.
    if provider_name != "openrouter":
        registry.register(
            "openrouter",
            OpenRouterProvider(
                api_key=llm_config["api_key"],
                base_url=llm_config["base_url"],
                default_model=llm_config["model"],
            ),
        )

    return registry


__all__ = ["build_default_registry"]
