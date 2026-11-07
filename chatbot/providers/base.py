from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Backend-independent completion settings."""

    model: str
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    stop: Sequence[str] | None = None
    timeout_seconds: float | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)
    thinking: bool = False
    trace_turn_id: str | None = None
    trace_timings: dict[str, float] | None = None


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """Normalized response returned by all provider adapters."""

    text: str
    finish_reason: str
    model: str | None = None
    raw: Mapping[str, Any] | None = None


class ProviderError(Exception):
    """Base error for provider adapter failures."""


class ProviderTimeoutError(ProviderError):
    """Raised when a provider request times out."""


class ProviderAuthError(ProviderError):
    """Raised when provider authentication or authorization fails."""


class ProviderRateLimitError(ProviderError):
    """Raised when a provider rejects a request due to rate limiting."""


class ProviderSelectionError(ProviderError, LookupError):
    """Raised when an explicitly requested provider cannot be used.

    Explicit provider selections are never substituted with another provider.
    """


class Provider(ABC):
    """Abstract interface implemented by concrete completion providers."""

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        history: Sequence[Mapping[str, Any]],
        config: ProviderConfig,
    ) -> ProviderResponse:
        """Complete a prompt using provider-normalized history and config."""


class ProviderRegistry:
    """Register and resolve provider instances by stable name."""

    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, name: str, provider: Provider) -> None:
        if not name:
            raise ValueError("provider name must not be empty")
        if name in self._providers:
            raise ValueError(f"provider already registered: {name}")
        self._providers[name] = provider

    def get(self, name: str) -> Provider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise KeyError(f"provider not registered: {name}") from exc

    def unregister(self, name: str) -> None:
        try:
            del self._providers[name]
        except KeyError as exc:
            raise KeyError(f"provider not registered: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._providers)
