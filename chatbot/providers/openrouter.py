from __future__ import annotations

from chatbot.providers.openai_compat import OpenAICompatProvider

_DEFAULT_MODEL = "google/gemma-4-31b-it"
_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider(OpenAICompatProvider):
    """OpenAI-compatible adapter pre-configured for OpenRouter."""

    def __init__(
        self,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        default_model: str = _DEFAULT_MODEL,
        *,
        timeout_seconds: float = 60.0,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            default_model=default_model,
            timeout_seconds=timeout_seconds,
        )


__all__ = ["OpenRouterProvider"]
