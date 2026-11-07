from __future__ import annotations

import json
import logging
import time
from time import perf_counter
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import httpx

logger = logging.getLogger(__name__)

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
        ollama: bool = False,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("base_url must not be empty")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.default_model = default_model or ""
        self.timeout_seconds = timeout_seconds
        self.ollama = ollama

    async def complete(
        self,
        prompt: str,
        history: Sequence[Mapping[str, Any]],
        config: ProviderConfig,
    ) -> ProviderResponse:
        model = self._resolve_model(config)
        started = perf_counter()
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

        result = self._to_provider_response(response)
        if self.ollama:
            raw = result.raw or {}
            message = (raw.get("choices") or [{}])[0].get("message") or {}
            usage = raw.get("usage") or {}
            reasoning_chars = len(message.get("reasoning") or message.get("reasoning_content") or "")
            if not config.thinking and reasoning_chars:
                raise ProviderError("Ollama generated reasoning despite Thinking Off; this model or server does not support disabling it")
            logger.info("Ollama generation model=%s thinking=%s duration_s=%.3f completion_tokens=%s reasoning_chars=%s",
                        model, config.thinking, perf_counter() - started,
                        usage.get("completion_tokens"), reasoning_chars)

        return result

    async def stream(
        self,
        prompt: str,
        history: Sequence[Mapping[str, Any]],
        config: ProviderConfig,
    ) -> AsyncIterator[str]:
        model = self._resolve_model(config)
        started = perf_counter()
        first_raw = None
        first_visible = None
        logged_visible = False
        logged_yield = False
        reasoning_chars = 0
        usage = {}
        payload = self._build_payload(prompt, history, config, model)
        payload["stream"] = True
        if self.ollama:
            payload["stream_options"] = {"include_usage": True}
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with httpx.AsyncClient() as client:
                if config.trace_turn_id:
                    if config.trace_timings is not None:
                        config.trace_timings["provider_http_start"] = time.monotonic()
                    logger.info("avatar_latency turn_id=%s stage=provider_http_start t=%.6f", config.trace_turn_id, time.monotonic())
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=config.timeout_seconds or self.timeout_seconds,
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        self._raise_provider_http_error(response)

                    async for line in response.aiter_lines():
                        if config.trace_turn_id and first_raw is None and line.startswith("data: "):
                            if config.trace_timings is not None:
                                config.trace_timings["provider_first_event"] = time.monotonic()
                            logger.info("avatar_latency turn_id=%s stage=provider_first_event t=%.6f", config.trace_turn_id, time.monotonic())
                            first_raw = perf_counter() - started
                        if self.ollama and line.startswith("data: ") and line[6:] != "[DONE]":
                            try:
                                event = json.loads(line[6:])
                                usage = event.get("usage") or usage
                                delta = (event.get("choices") or [{}])[0].get("delta") or {}
                                reasoning = delta.get("reasoning") or delta.get("reasoning_content") or ""
                                visible = delta.get("content") or ""
                                if reasoning and not config.thinking:
                                    raise ProviderError("Ollama generated reasoning despite Thinking Off; this model or server does not support disabling it")
                                if (reasoning or visible) and first_raw is None:
                                    first_raw = perf_counter() - started
                                reasoning_chars += len(reasoning)
                                if visible and first_visible is None:
                                    first_visible = perf_counter() - started
                            except (ValueError, TypeError, AttributeError):
                                pass
                        token = self._token_from_sse_line(line)
                        if token is None:
                            continue
                        if token == "[DONE]":
                            if self.ollama:
                                logger.info("Ollama generation model=%s thinking=%s first_raw_s=%s first_visible_s=%s duration_s=%.3f reasoning_chars=%s completion_tokens=%s",
                                            model, config.thinking, first_raw, first_visible, perf_counter() - started, reasoning_chars, usage.get("completion_tokens"))
                            return
                        if config.trace_turn_id and not logged_visible:
                            if config.trace_timings is not None:
                                config.trace_timings["provider_first_visible"] = time.monotonic()
                            logged_visible = True
                            logger.info("avatar_latency turn_id=%s stage=provider_first_visible t=%.6f", config.trace_turn_id, time.monotonic())
                        if config.trace_turn_id and not logged_yield:
                            if config.trace_timings is not None:
                                config.trace_timings["adapter_yield"] = time.monotonic()
                            logged_yield = True
                            logger.info("avatar_latency turn_id=%s stage=adapter_yield t=%.6f", config.trace_turn_id, time.monotonic())
                        yield token
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("OpenAI-compatible provider stream timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenAI-compatible provider stream failed: {exc}") from exc

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
        if self.ollama:
            payload["reasoning_effort"] = "high" if config.thinking else "none"
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

    def _token_from_sse_line(self, line: str) -> str | None:
        stripped = line.strip()
        if not stripped or not stripped.startswith("data:"):
            return None

        data = stripped.removeprefix("data:").strip()
        if data == "[DONE]":
            return data

        try:
            raw = json.loads(data)
        except ValueError as exc:
            raise ProviderError("OpenAI-compatible provider returned invalid stream JSON") from exc

        if not isinstance(raw, Mapping):
            return None

        choices = raw.get("choices")
        if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)) or not choices:
            return None

        choice = choices[0]
        if not isinstance(choice, Mapping):
            return None

        delta = choice.get("delta")
        if isinstance(delta, Mapping):
            content = delta.get("content")
            if isinstance(content, str):
                return content

        text = choice.get("text")
        return text if isinstance(text, str) else None

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
