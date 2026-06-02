from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any, Protocol

from chatbot.providers import Provider, ProviderConfig

try:
    from chatbot.memory.models import MemoryEntry
except ModuleNotFoundError:

    class MemoryEntry(Protocol):
        role: str
        content: str


_PROMPT_VERSION = "hierarchical-summarizer-v1"
_SYSTEM_PROMPT = (
    "You are a deterministic memory consolidation component. Return only the summary text. "
    "Do not invent facts. Preserve stable entities, user preferences, decisions, commitments, "
    "open questions, and important context. Omit filler and transient wording."
)


class Summarizer:
    """Hierarchical memory summarizer backed by a completion provider."""

    def __init__(
        self,
        provider: Provider,
        config: ProviderConfig | None = None,
    ) -> None:
        self.provider = provider
        self.config = config or ProviderConfig(
            model="",
            temperature=0.0,
            max_tokens=512,
        )
        self._cache: dict[tuple[str, str], str] = {}
        self._cache_lock = asyncio.Lock()

    async def summarize_turn(self, messages: list[MemoryEntry | Mapping[str, Any] | str]) -> str:
        """Condense recent turn messages into a concise turn summary."""
        normalized = [_normalize_message(message) for message in messages]
        if not _has_content(normalized):
            return ""

        return await self._summarize(
            "turn",
            "Condense these recent chat messages into one concise turn summary.",
            normalized,
        )

    async def summarize_session(self, turn_summaries: list[str]) -> str:
        """Roll up turn-level summaries into a session-level summary."""
        normalized = _normalize_summaries(turn_summaries)
        if not normalized:
            return ""

        return await self._summarize(
            "session",
            "Roll up these turn summaries into one concise session-level memory summary.",
            normalized,
        )

    async def summarize_long_term(self, session_summaries: list[str]) -> str:
        """Condense session-level summaries into durable long-term memory."""
        normalized = _normalize_summaries(session_summaries)
        if not normalized:
            return ""

        return await self._summarize(
            "long_term",
            "Condense these session summaries into a durable long-term memory summary.",
            normalized,
        )

    async def _summarize(self, level: str, instruction: str, payload: Any) -> str:
        payload_json = _canonical_json(
            {
                "prompt_version": _PROMPT_VERSION,
                "level": level,
                "input": payload,
            }
        )
        cache_key = (level, payload_json)

        async with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

            prompt = (
                f"{instruction}\n\n"
                "Rules:\n"
                "- Return one compact paragraph.\n"
                "- Keep only facts supported by the input.\n"
                "- Prefer stable facts over momentary phrasing.\n"
                "- If the input has no durable information, return an empty string.\n\n"
                f"Input JSON:\n{payload_json}"
            )
            response = await self.provider.complete(
                prompt,
                [{"role": "system", "content": _SYSTEM_PROMPT}],
                self.config,
            )
            summary = response.text.strip()
            self._cache[cache_key] = summary
            return summary


def _normalize_message(message: MemoryEntry | Mapping[str, Any] | str) -> dict[str, Any]:
    if isinstance(message, str):
        return {"role": "message", "content": message}

    source = _message_mapping(message)
    role = _text_value(source.get("role", getattr(message, "role", "")))
    content = _text_value(source.get("content", getattr(message, "content", "")))

    normalized: dict[str, Any] = {
        "role": role,
        "content": content,
    }

    metadata = source.get("metadata", getattr(message, "metadata", None))
    if metadata:
        normalized["metadata"] = _jsonable(metadata)

    return normalized


def _message_mapping(message: MemoryEntry | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(message, Mapping):
        return message

    as_message = getattr(message, "as_message", None)
    if callable(as_message):
        mapped = as_message()
        if isinstance(mapped, Mapping):
            return mapped

    return {}


def _normalize_summaries(summaries: list[str]) -> list[str]:
    return [summary.strip() for summary in summaries if summary and summary.strip()]


def _has_content(messages: Sequence[Mapping[str, Any]]) -> bool:
    return any(bool(message.get("content")) for message in messages)


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return _canonical_json(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if is_dataclass(value):
        return _jsonable(asdict(value))

    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    return str(value)


__all__ = ["MemoryEntry", "Summarizer"]
