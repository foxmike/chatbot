"""Fact and episode extraction from chat turns for the chatbot engine."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import inspect
import re
import uuid
from typing import Any, Protocol


_MAX_FACTS_PER_TURN = 8
_MAX_SENTENCE_CHARS = 500
_MAX_EPISODE_SUMMARY_CHARS = 1_200
_FACT_EXTRACTOR = "heuristic-v1"

_FACT_PATTERNS = (
    re.compile(
        r"\b(?:i am|i'm|i’m|i was|i work as|i work at|i live in|i live near|"
        r"i grew up in|i study|i use|i have|i own|i prefer|i like|i love|"
        r"i dislike|i hate|i need|i want|i usually|i always|i often|"
        r"i never|remember that i)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bmy (?:name|email|phone|birthday|job|role|company|team|project|"
        r"preference|favorite|favourite|address|timezone|partner|spouse|"
        r"wife|husband|child|kid|manager)\b",
        re.IGNORECASE,
    ),
)
_ASSISTANT_MEMORY_PATTERN = re.compile(
    r"\b(?:i(?:'ll| will) remember(?: that)? (?:you|your)|"
    r"i(?:'ve| have) noted(?: that)? (?:you|your)|"
    r"i(?:'ll| will) keep in mind(?: that)? (?:you|your))\b",
    re.IGNORECASE,
)
_NEGATED_MEMORY_PATTERN = re.compile(
    r"\b(?:do not remember|don't remember|forget that|ignore that)\b",
    re.IGNORECASE,
)
_REQUEST_PATTERN = re.compile(
    r"^(?:please|can you|could you|would you|tell me|explain|write|make|create|"
    r"show me|help me|what|why|how|when|where|who)\b",
    re.IGNORECASE,
)
_WHITESPACE_PATTERN = re.compile(r"\s+")
_SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True, slots=True)
class ExtractedFact:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": "fact",
            "content": self.content,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ExtractedEpisode:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": "episode",
            "content": self.content,
            "metadata": dict(self.metadata),
        }


class MemoryStorageAdapter(Protocol):
    async def persist(
        self,
        session_id: str,
        entries: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Persist extracted memory entries and return the entries that were written."""


class MemoryExtractor:
    def __init__(
        self,
        database: Any | None = None,
        *,
        storage: MemoryStorageAdapter | None = None,
    ) -> None:
        self.database = database
        self.storage = storage

    async def extract(
        self,
        session_id: str,
        user_message: str,
        provider_response: str,
    ) -> list[dict]:
        if self.database is None and self.storage is None:
            return []

        entries = _extract_entries(
            session_id=session_id,
            user_message=user_message,
            provider_response=provider_response,
        )
        if not entries:
            return []

        storage = self.storage or SQLAlchemyMemoryStorage(self.database)
        try:
            return await storage.persist(session_id, entries)
        except Exception:
            return []


class SQLAlchemyMemoryStorage:
    def __init__(self, database: Any | None) -> None:
        self.database = database

    async def persist(
        self,
        session_id: str,
        entries: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if self.database is None:
            return []

        database = self.database
        if _is_engine_like(database):
            return await self._persist_engine(database, session_id, entries)

        if callable(database) and not hasattr(database, "execute"):
            return await self._persist_factory(database, session_id, entries)

        if hasattr(database, "execute"):
            return await self._persist_session_or_connection(database, session_id, entries)

        return []

    async def _persist_engine(
        self,
        engine: Any,
        session_id: str,
        entries: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        begin = getattr(engine, "begin", None)
        if not callable(begin):
            return []

        async with begin() as connection:
            return await _persist_rows(connection, session_id, entries)

    async def _persist_factory(
        self,
        factory: Any,
        session_id: str,
        entries: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        session = factory()
        if hasattr(session, "__aenter__"):
            async with session as active_session:
                return await self._persist_session_or_connection(
                    active_session,
                    session_id,
                    entries,
                )
        if hasattr(session, "__enter__"):
            with session as active_session:
                return await self._persist_session_or_connection(
                    active_session,
                    session_id,
                    entries,
                )
        return await self._persist_session_or_connection(session, session_id, entries)

    async def _persist_session_or_connection(
        self,
        session: Any,
        session_id: str,
        entries: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        begin = getattr(session, "begin_nested", None) if _in_transaction(session) else None
        if not callable(begin):
            begin = getattr(session, "begin", None)

        if callable(begin):
            async with _transaction_context(begin()):
                return await _persist_rows(session, session_id, entries)

        commit = getattr(session, "commit", None)
        rollback = getattr(session, "rollback", None)
        try:
            written = await _persist_rows(session, session_id, entries)
            if callable(commit):
                await _maybe_await(commit())
            return written
        except Exception:
            if callable(rollback):
                await _maybe_await(rollback())
            raise


def _extract_entries(
    *,
    session_id: str,
    user_message: str,
    provider_response: str,
) -> list[dict[str, Any]]:
    user_message = _clean_text(user_message)
    provider_response = _clean_text(provider_response)
    if not user_message and not provider_response:
        return []

    facts = _extract_facts(session_id, user_message, provider_response)
    episode = _extract_episode(session_id, user_message, provider_response)
    entries = [fact.as_dict() for fact in facts]
    if episode is not None:
        entries.append(episode.as_dict())
    return entries


def _extract_facts(
    session_id: str,
    user_message: str,
    provider_response: str,
) -> list[ExtractedFact]:
    facts: list[ExtractedFact] = []
    seen: set[str] = set()

    for sentence in _split_sentences(user_message):
        if not _looks_like_user_fact(sentence):
            continue
        _append_fact(
            facts,
            seen,
            sentence,
            session_id=session_id,
            source="user_message",
            confidence=0.72,
        )

    for sentence in _split_sentences(provider_response):
        if not _looks_like_assistant_memory_confirmation(sentence):
            continue
        _append_fact(
            facts,
            seen,
            sentence,
            session_id=session_id,
            source="provider_response",
            confidence=0.55,
        )

    return facts[:_MAX_FACTS_PER_TURN]


def _append_fact(
    facts: list[ExtractedFact],
    seen: set[str],
    sentence: str,
    *,
    session_id: str,
    source: str,
    confidence: float,
) -> None:
    content = _trim_sentence(sentence, _MAX_SENTENCE_CHARS)
    key = content.casefold()
    if key in seen:
        return

    facts.append(
        ExtractedFact(
            content=content,
            metadata={
                "session_id": session_id,
                "source": source,
                "confidence": confidence,
                "extractor": _FACT_EXTRACTOR,
            },
        )
    )
    seen.add(key)


def _extract_episode(
    session_id: str,
    user_message: str,
    provider_response: str,
) -> ExtractedEpisode | None:
    if not user_message and not provider_response:
        return None

    parts: list[str] = []
    if user_message:
        parts.append(f"User said: {_trim_sentence(user_message, 520)}")
    if provider_response:
        parts.append(f"Assistant responded: {_trim_sentence(provider_response, 620)}")

    summary = _trim_sentence(" ".join(parts), _MAX_EPISODE_SUMMARY_CHARS)
    return ExtractedEpisode(
        content=summary,
        metadata={
            "session_id": session_id,
            "title": _episode_title(user_message, provider_response),
            "source": "turn",
            "extractor": _FACT_EXTRACTOR,
        },
    )


async def _persist_rows(
    executor: Any,
    session_id: str,
    entries: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    try:
        import sqlalchemy as sa

        from chatbot.db import models
    except Exception:
        return []

    coerced_session_id = _coerce_uuid(session_id)
    if coerced_session_id is None:
        return []

    user_id = await _session_user_id(executor, sa, models, coerced_session_id)
    if user_id is None:
        return []

    fact_rows: list[dict[str, Any]] = []
    episode_rows: list[dict[str, Any]] = []
    writable_entries: list[dict[str, Any]] = []

    for entry in entries:
        entry_type = entry.get("type")
        content = _clean_text(str(entry.get("content") or ""))
        metadata = entry.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        if not content:
            continue

        if entry_type == "fact":
            fact_rows.append(
                {
                    "user_id": user_id,
                    "fact_text": content,
                    "source_message_id": None,
                    "confidence": float(metadata.get("confidence", 1.0)),
                }
            )
            writable_entries.append(_copy_entry(entry, content, metadata))
        elif entry_type == "episode":
            episode_rows.append(
                {
                    "session_id": coerced_session_id,
                    "title": _trim_sentence(str(metadata.get("title") or "Conversation turn"), 160),
                    "summary": content,
                    "start_message_id": None,
                    "end_message_id": None,
                }
            )
            writable_entries.append(_copy_entry(entry, content, metadata))

    if not writable_entries:
        return []

    if fact_rows:
        await _maybe_await(executor.execute(sa.insert(models.SemanticFact), fact_rows))
    if episode_rows:
        await _maybe_await(executor.execute(sa.insert(models.Episode), episode_rows))

    flush = getattr(executor, "flush", None)
    if callable(flush):
        await _maybe_await(flush())

    return writable_entries


async def _session_user_id(
    executor: Any,
    sa: Any,
    models: Any,
    session_id: uuid.UUID,
) -> Any | None:
    result = await _maybe_await(
        executor.execute(
            sa.select(models.Session.user_id).where(models.Session.id == session_id)
        )
    )
    scalar_one_or_none = getattr(result, "scalar_one_or_none", None)
    if callable(scalar_one_or_none):
        return scalar_one_or_none()

    scalar = getattr(result, "scalar", None)
    if callable(scalar):
        return scalar()

    return None


def _copy_entry(
    entry: dict[str, Any],
    content: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": str(entry.get("type") or ""),
        "content": content,
        "metadata": dict(metadata),
    }


def _looks_like_user_fact(sentence: str) -> bool:
    if not sentence or sentence.endswith("?"):
        return False
    if _NEGATED_MEMORY_PATTERN.search(sentence):
        return False
    if _REQUEST_PATTERN.search(sentence):
        return False
    return any(pattern.search(sentence) for pattern in _FACT_PATTERNS)


def _looks_like_assistant_memory_confirmation(sentence: str) -> bool:
    if not sentence or sentence.endswith("?"):
        return False
    if _NEGATED_MEMORY_PATTERN.search(sentence):
        return False
    return bool(_ASSISTANT_MEMORY_PATTERN.search(sentence))


def _split_sentences(text: str) -> list[str]:
    text = _clean_text(text)
    if not text:
        return []

    sentences = []
    for sentence in _SENTENCE_PATTERN.split(text):
        sentence = _clean_text(sentence)
        if sentence:
            sentences.append(sentence)
    return sentences


def _episode_title(user_message: str, provider_response: str) -> str:
    source = user_message or provider_response or "Conversation turn"
    first_sentence = _split_sentences(source)
    if first_sentence:
        return _trim_sentence(first_sentence[0], 80)
    return "Conversation turn"


def _trim_sentence(text: str, max_chars: int) -> str:
    text = _clean_text(text)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def _clean_text(text: str | None) -> str:
    if text is None:
        return ""
    return _WHITESPACE_PATTERN.sub(" ", str(text)).strip()


def _is_engine_like(value: Any) -> bool:
    return hasattr(value, "begin") and not hasattr(value, "execute")


def _in_transaction(session: Any) -> bool:
    in_transaction = getattr(session, "in_transaction", None)
    if not callable(in_transaction):
        return False
    return bool(in_transaction())


@asynccontextmanager
async def _transaction_context(transaction: Any) -> Any:
    if hasattr(transaction, "__aenter__"):
        async with transaction:
            yield
        return
    if hasattr(transaction, "__enter__"):
        with transaction:
            yield
        return
    await _maybe_await(transaction)
    yield


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


__all__ = [
    "ExtractedEpisode",
    "ExtractedFact",
    "MemoryExtractor",
    "MemoryStorageAdapter",
    "SQLAlchemyMemoryStorage",
]
