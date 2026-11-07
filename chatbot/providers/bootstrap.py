from __future__ import annotations

import logging

from chatbot.config import Settings, get_embedding_config
from chatbot.memory.embeddings import EmbeddingProvider
from chatbot.memory.retrieval import DisabledEmbeddingProvider, OpenRouterEmbeddingProvider
from chatbot.providers.base import ProviderRegistry
from chatbot.providers.openrouter import OpenRouterProvider

logger = logging.getLogger(__name__)


def _provider_endpoints(settings: Settings) -> dict[str, dict[str, str]]:
    """Each chat provider's own endpoint, credentials and configured model."""

    active = settings.llm_provider.lower()
    return {
        "openrouter": {
            "base_url": settings.openrouter_base_url,
            "api_key": settings.openrouter_api_key,
            # settings.default_model is rewritten to the active local provider's
            # model when LLM_PROVIDER is not openrouter, so it only belongs to
            # OpenRouter when OpenRouter is the active provider.
            "model": settings.default_model if active == "openrouter" else "",
        },
        "ollama": {
            "base_url": settings.ollama_base_url,
            "api_key": settings.ollama_api_key,
            "model": settings.ollama_model,
        },
        "vllm": {
            "base_url": settings.vllm_base_url,
            "api_key": settings.vllm_api_key,
            "model": settings.vllm_model,
        },
        "sglang": {
            "base_url": settings.sglang_base_url,
            "api_key": settings.sglang_api_key,
            "model": settings.sglang_model,
        },
    }


def build_default_registry(settings: Settings) -> ProviderRegistry:
    """Register every configured chat provider under its own name and endpoint.

    OpenRouter, SGLang, vLLM and Ollama all speak the OpenAI-compatible API but
    each is registered with its own base URL and credentials, so selecting one
    can never reach another's endpoint. The active ``LLM_PROVIDER`` is
    registered first so requests that do not name a provider keep using it.
    """

    registry = ProviderRegistry()
    active = settings.llm_provider.lower()

    if active == "openrouter" and not settings.openrouter_api_key:
        logger.warning(
            "OPENROUTER_API_KEY is not set; openrouter provider registered but calls will fail"
        )

    endpoints = _provider_endpoints(settings)
    ordered = sorted(endpoints, key=lambda name: name != active)
    for name in ordered:
        endpoint = endpoints[name]
        if not (endpoint["base_url"] or "").strip():
            logger.info("Provider %s has no base URL configured; not registering", name)
            continue
        registry.register(
            name,
            OpenRouterProvider(
                api_key=endpoint["api_key"],
                base_url=endpoint["base_url"],
                default_model=endpoint["model"],
                ollama=name == "ollama",
            ),
        )

    return registry


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Initialize the embedding provider from application settings.

    Returns an :class:`OpenRouterEmbeddingProvider` when an API key is
    configured, or a :class:`DisabledEmbeddingProvider` otherwise.
    """
    embed_config = get_embedding_config()
    if embed_config["api_key"]:
        return OpenRouterEmbeddingProvider(
            api_key=embed_config["api_key"],
            base_url=embed_config["base_url"],
            model=embed_config["model"],
        )
    return DisabledEmbeddingProvider(
        "Embedding provider not initialized because no API key is configured"
    )


__all__ = ["build_default_registry", "build_embedding_provider"]
