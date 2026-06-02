from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchResult:
    id: str
    score: float
    metadata: dict | None


class VectorStore(ABC):
    @abstractmethod
    async def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        metadata: list[dict] | None,
    ) -> None:
        """Insert vectors with optional metadata."""

    @abstractmethod
    async def search(
        self,
        embedding: list[float],
        top_k: int,
        filter: dict | None,
    ) -> list[SearchResult]:
        """Return cosine-similarity search results."""

    @abstractmethod
    async def delete(self, ids: list[str]) -> int:
        """Delete vectors by id and return the number deleted."""

    @abstractmethod
    async def count(self) -> int:
        """Return the total vector count."""


__all__ = ["SearchResult", "VectorStore"]
