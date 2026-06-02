from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return the output embedding dimension."""


__all__ = ["EmbeddingProvider"]
