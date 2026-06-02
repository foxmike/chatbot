from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from chatbot.providers.base import (
    Provider,
    ProviderAuthError,
    ProviderConfig,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponse,
    ProviderTimeoutError,
)


class OpenAICompatProvider(Provider):
    """Provider adapter for OpenAI-compatible chat completions APIs."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        default_model: str = "",
        *,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("base_url must not be empty")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.default_model = default_model or ""
        self.timeout_seconds = timeout_seconds

    async def complete(
        self,
        prompt: str,
        history: Sequence[Mapping[str, Any]],
        config: ProviderConfig,
    ) -> ProviderResponse:
        model = self._resolve_model(config)
        payload = self._build_payload(prompt, history, config, model)
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=config.timeout_seconds or self.timeout_seconds,
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("OpenAI-compatible provider request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenAI-compatible provider request failed: {exc}") from exc

        if response.status_code >= 400:
            self._raise_provider_http_error(response)

        return self._to_provider_response(response)

    def _resolve_model(self, config: ProviderConfig) -> str:
        model = config.model.strip() if config.model else self.default_model.strip()
        if not model:
            raise ValueError("model must not be empty")
        return model

    def _build_payload(
        self,
        prompt: str,
        history: Sequence[Mapping[str, Any]],
        config: ProviderConfig,
        model: str,
    ) -> dict[str, Any]:
        messages = [dict(message) for message in history]
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if config.temperature is not None:
            payload["temperature"] = config.temperature
        if config.max_tokens is not None:
            payload["max_tokens"] = config.max_tokens
        if config.top_p is not None:
            payload["top_p"] = config.top_p
        if config.stop is not None:
            payload["stop"] = list(config.stop)

        for key, value in config.extra.items():
            if key not in payload:
                payload[key] = value
        return payload

    def _to_provider_response(self, response: httpx.Response) -> ProviderResponse:
        try:
            raw = response.json()
        except ValueError as exc:
            raise ProviderError("OpenAI-compatible provider returned invalid JSON") from exc
        if not isinstance(raw, Mapping):
            raise ProviderError("OpenAI-compatible provider returned an unexpected response shape")

        choices = raw.get("choices")
        if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)) or not choices:
            raise ProviderError("OpenAI-compatible provider response missing choices")

        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise ProviderError("OpenAI-compatible provider response choice has an unexpected shape")

        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise ProviderError("OpenAI-compatible provider response missing message")

        content = message.get("content")
        text = content if isinstance(content, str) else "" if content is None else json.dumps(content)
        finish_reason = choice.get("finish_reason")
        model = raw.get("model")

        return ProviderResponse(
            text=text,
            finish_reason=finish_reason if isinstance(finish_reason, str) else "",
            model=model if isinstance(model, str) else None,
            raw=raw,
        )

    def _raise_provider_http_error(self, response: httpx.Response) -> None:
        message = self._http_error_message(response)
        if response.status_code in {401, 403}:
            raise ProviderAuthError(message)
        if response.status_code == 429:
            raise ProviderRateLimitError(message)
        if response.status_code in {408, 504}:
            raise ProviderTimeoutError(message)
        raise ProviderError(message)

    def _http_error_message(self, response: httpx.Response) -> str:
        detail = response.text
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, Mapping):
            error = body.get("error")
            if isinstance(error, Mapping):
                detail_value = error.get("message") or error.get("code") or error.get("type")
                if detail_value is not None:
                    detail = str(detail_value)
            elif error is not None:
                detail = str(error)
            elif body.get("message") is not None:
                detail = str(body["message"])
        return f"OpenAI-compatible provider HTTP {response.status_code}: {detail}".strip()


OpenAICompatibleProvider = OpenAICompatProvider

__all__ = ["OpenAICompatProvider", "OpenAICompatibleProvider"]
