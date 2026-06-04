"""Cached persona context to avoid recompiling persona/prompt every turn."""

from __future__ import annotations

import time
from typing import TypedDict


DEFAULT_PERSONA_TTL_SECONDS = 300


class _CacheEntry(TypedDict):
    persona_context: str
    ttl_seconds: int
    cached_at: float


class PersonaCache:
    """In-process persona context cache keyed by user and companion."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str | None], _CacheEntry] = {}

    async def get(self, user_id: str, companion_id: str | None) -> str | None:
        """Return cached persona context, or None when absent or stale."""

        key = self._key(user_id, companion_id)
        entry = self._cache.get(key)
        if entry is None:
            return None

        now = time.time()
        if entry["cached_at"] + entry["ttl_seconds"] <= now:
            return None

        return entry["persona_context"]

    async def set(
        self,
        user_id: str,
        companion_id: str | None,
        persona_context: str,
        ttl_seconds: int = DEFAULT_PERSONA_TTL_SECONDS,
    ) -> None:
        """Store persona context with a TTL and cached timestamp."""

        self._cache[self._key(user_id, companion_id)] = {
            "persona_context": persona_context,
            "ttl_seconds": ttl_seconds,
            "cached_at": time.time(),
        }

    async def invalidate(self, user_id: str, companion_id: str | None) -> None:
        """Remove a cached persona entry after character or relationship changes."""

        self._cache.pop(self._key(user_id, companion_id), None)

    async def invalidate_user(self, user_id: str) -> None:
        """Remove all cached persona entries for a user."""

        keys = [key for key in self._cache if key[0] == user_id]
        for key in keys:
            self._cache.pop(key, None)

    def _key(self, user_id: str, companion_id: str | None) -> tuple[str, str | None]:
        return (user_id, companion_id)


__all__ = ["DEFAULT_PERSONA_TTL_SECONDS", "PersonaCache"]
