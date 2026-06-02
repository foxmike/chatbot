from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import asyncio
import inspect
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from typing import Any
import uuid

from chatbot.consolidation.contradiction import ContradictionResolver, ContradictionResult
from chatbot.consolidation.decay import MemoryDecayEngine
from chatbot.consolidation.summarizer import Summarizer
from chatbot.providers import Provider


@dataclass(frozen=True, slots=True)
class ConsolidationResult:
    summaries_produced: int
    contradictions_resolved: int
    entries_pruned: int
    run_timestamp: datetime


class ConsolidationWorker:
    """Run memory consolidation subsystems in a transaction-scoped pipeline."""

    def __init__(
        self,
        provider: Provider,
        memory_store: Any,
        *,
        coalescing_window_seconds: float = 60.0,
        recent_entry_limit: int = 50,
        summarizer: Summarizer | None = None,
        contradiction_resolver: ContradictionResolver | None = None,
        decay_engine: MemoryDecayEngine | None = None,
    ) -> None:
        if coalescing_window_seconds < 0:
            raise ValueError("coalescing_window_seconds must be non-negative")
        if recent_entry_limit <= 0:
            raise ValueError("recent_entry_limit must be greater than zero")

        self.provider = provider
        self.memory_store = memory_store
        self.coalescing_window = timedelta(seconds=coalescing_window_seconds)
        self.recent_entry_limit = recent_entry_limit
        self.summarizer = summarizer or Summarizer(provider)
        self.contradiction_resolver = contradiction_resolver or ContradictionResolver(provider)
        self.decay_engine = decay_engine or MemoryDecayEngine()
        self._last_consolidation_runs: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def run_consolidation(self, session_id: str) -> ConsolidationResult:
        run_timestamp = datetime.now(timezone.utc)

        async with self._lock:
            last_run = await self._get_last_consolidation_run(session_id)
            if self._within_coalescing_window(last_run, run_timestamp):
                return _empty_result(run_timestamp)

            async with self._transaction(session_id):
                unsummarized_entries = await self._fetch_unsummarized_entries(session_id)
                if not unsummarized_entries:
                    return _empty_result(run_timestamp)

                recent_entries = await self._fetch_recent_entries(session_id)
                contradiction_entries = recent_entries or unsummarized_entries
                contradictions_resolved = await self._resolve_contradictions(
                    session_id,
                    contradiction_entries[-self.recent_entry_limit :],
                )

                summaries_produced = await self._summarize_entries(
                    session_id,
                    unsummarized_entries,
                )

                all_entries = await self._fetch_all_entries(session_id)
                kept_entries = self.decay_engine.prune(all_entries)
                entries_pruned = await self._persist_pruned_entries(
                    session_id,
                    all_entries,
                    kept_entries,
                )

                await self._set_last_consolidation_run(session_id, run_timestamp)

                return ConsolidationResult(
                    summaries_produced=summaries_produced,
                    contradictions_resolved=contradictions_resolved,
                    entries_pruned=entries_pruned,
                    run_timestamp=run_timestamp,
                )

    def _within_coalescing_window(
        self,
        last_run: datetime | None,
        run_timestamp: datetime,
    ) -> bool:
        if last_run is None:
            return False
        return run_timestamp - _normalize_datetime(last_run) < self.coalescing_window

    async def _resolve_contradictions(
        self,
        session_id: str,
        entries: Sequence[Any],
    ) -> int:
        resolved = 0
        normalized_entries = [_memory_entry(entry) for entry in entries]
        for index, entry_a in enumerate(normalized_entries):
            for entry_b in normalized_entries[index + 1 :]:
                result = await self.contradiction_resolver.resolve(entry_a, entry_b)
                if not result.has_contradiction:
                    continue
                await self._apply_contradiction_resolution(
                    session_id,
                    entry_a,
                    entry_b,
                    result,
                )
                resolved += 1
        return resolved

    async def _summarize_entries(self, session_id: str, entries: Sequence[Any]) -> int:
        messages = [_memory_entry(entry) for entry in entries]
        turn_summary = await self.summarizer.summarize_turn(list(messages))
        turn_summaries = [turn_summary] if turn_summary else []

        summaries_produced = len(turn_summaries)
        session_summary = ""
        long_term_summary = ""
        if turn_summaries:
            session_summary = await self.summarizer.summarize_session(turn_summaries)
            if session_summary:
                summaries_produced += 1
                long_term_summary = await self.summarizer.summarize_long_term([session_summary])
                if long_term_summary:
                    summaries_produced += 1

        await self._persist_summaries(
            session_id,
            entries,
            turn_summaries,
            session_summary,
            long_term_summary,
        )
        await self._mark_entries_summarized(session_id, entries)
        return summaries_produced

    async def _fetch_unsummarized_entries(self, session_id: str) -> list[Any]:
        entries = await self._call_store_method(
            (
                "fetch_unsummarized_memory_entries",
                "get_unsummarized_memory_entries",
                "fetch_unsummarized_entries",
                "get_unsummarized_entries",
                "unsummarized_entries",
            ),
            session_id,
            session_id=session_id,
        )
        if entries is not _MISSING:
            return list(entries or [])

        session = _db_session(self.memory_store)
        if session is not None:
            return await _fetch_db_unsummarized_messages(session, session_id)

        return []

    async def _fetch_recent_entries(self, session_id: str) -> list[Any]:
        entries = await self._call_store_method(
            (
                "fetch_recent_memory_entries",
                "get_recent_memory_entries",
                "fetch_recent_entries",
                "get_recent_entries",
                "recent_entries",
            ),
            session_id,
            session_id=session_id,
            limit=self.recent_entry_limit,
        )
        if entries is not _MISSING:
            return list(entries or [])

        session = _db_session(self.memory_store)
        if session is not None:
            return await _fetch_db_recent_messages(session, session_id, self.recent_entry_limit)

        return []

    async def _fetch_all_entries(self, session_id: str) -> list[Any]:
        entries = await self._call_store_method(
            (
                "fetch_all_memory_entries",
                "get_all_memory_entries",
                "fetch_memory_entries",
                "get_memory_entries",
                "all_entries",
            ),
            session_id,
            session_id=session_id,
        )
        if entries is not _MISSING:
            return list(entries or [])

        session = _db_session(self.memory_store)
        if session is not None:
            return await _fetch_db_all_messages(session, session_id)

        return []

    async def _apply_contradiction_resolution(
        self,
        session_id: str,
        entry_a: Any,
        entry_b: Any,
        result: ContradictionResult,
    ) -> None:
        await self._call_store_method(
            (
                "apply_contradiction_resolution",
                "record_contradiction_resolution",
                "resolve_contradiction",
                "apply_resolution",
            ),
            session_id,
            entry_a,
            entry_b,
            result,
            session_id=session_id,
            entry_a=entry_a,
            entry_b=entry_b,
            result=result,
            resolution=result.resolution,
        )

    async def _persist_summaries(
        self,
        session_id: str,
        entries: Sequence[Any],
        turn_summaries: Sequence[str],
        session_summary: str,
        long_term_summary: str,
    ) -> None:
        saved = await self._call_store_method(
            (
                "persist_consolidation_summaries",
                "save_consolidation_summaries",
                "persist_summaries",
                "save_summaries",
            ),
            session_id,
            entries,
            list(turn_summaries),
            session_summary,
            long_term_summary,
            session_id=session_id,
            entries=list(entries),
            turn_summaries=list(turn_summaries),
            session_summary=session_summary,
            long_term_summary=long_term_summary,
        )
        if saved is not _MISSING:
            return

        for summary in turn_summaries:
            await self._call_store_method(
                ("persist_turn_summary", "save_turn_summary"),
                session_id,
                summary,
                entries,
                session_id=session_id,
                summary=summary,
                entries=list(entries),
            )

        if session_summary:
            await self._call_store_method(
                ("persist_session_summary", "save_session_summary"),
                session_id,
                session_summary,
                entries,
                session_id=session_id,
                summary=session_summary,
                entries=list(entries),
            )

        if long_term_summary:
            await self._call_store_method(
                ("persist_long_term_summary", "save_long_term_summary"),
                session_id,
                long_term_summary,
                session_id=session_id,
                summary=long_term_summary,
            )

        session = _db_session(self.memory_store)
        if session is not None and (session_summary or long_term_summary):
            await _persist_db_summaries(
                session,
                session_id,
                entries,
                session_summary,
                long_term_summary,
            )

    async def _mark_entries_summarized(self, session_id: str, entries: Sequence[Any]) -> None:
        await self._call_store_method(
            (
                "mark_entries_summarized",
                "mark_summarized",
                "mark_memory_entries_summarized",
            ),
            session_id,
            entries,
            session_id=session_id,
            entries=list(entries),
            entry_ids=_entry_ids(entries),
        )

    async def _persist_pruned_entries(
        self,
        session_id: str,
        all_entries: Sequence[Any],
        kept_entries: Sequence[Any],
    ) -> int:
        pruned_entries = _pruned_entries(all_entries, kept_entries)
        if not pruned_entries:
            return 0

        result = await self._call_store_method(
            (
                "persist_pruned_set",
                "persist_pruned_entries",
                "save_pruned_set",
                "replace_memory_entries",
            ),
            session_id,
            kept_entries,
            pruned_entries,
            session_id=session_id,
            kept_entries=list(kept_entries),
            pruned_entries=pruned_entries,
            pruned_entry_ids=_entry_ids(pruned_entries),
        )
        if result is not _MISSING:
            return _count_result(result, len(pruned_entries))

        result = await self._call_store_method(
            (
                "delete_memory_entries",
                "delete_entries",
                "prune_entries",
            ),
            _entry_ids(pruned_entries),
            session_id=session_id,
            entry_ids=_entry_ids(pruned_entries),
            entries=pruned_entries,
        )
        if result is not _MISSING:
            return _count_result(result, len(pruned_entries))

        delete = getattr(self.memory_store, "delete", None)
        if callable(delete):
            result = await _maybe_await(delete(_entry_ids(pruned_entries)))
            return _count_result(result, len(pruned_entries))

        return 0

    async def _get_last_consolidation_run(self, session_id: str) -> datetime | None:
        timestamp = await self._call_store_method(
            (
                "get_last_consolidation_run",
                "fetch_last_consolidation_run",
                "last_consolidation_run",
            ),
            session_id,
            session_id=session_id,
        )
        if timestamp is _MISSING:
            return self._last_consolidation_runs.get(session_id)
        if timestamp is None:
            return None
        return _normalize_datetime(timestamp)

    async def _set_last_consolidation_run(
        self,
        session_id: str,
        run_timestamp: datetime,
    ) -> None:
        saved = await self._call_store_method(
            (
                "set_last_consolidation_run",
                "update_last_consolidation_run",
                "save_last_consolidation_run",
            ),
            session_id,
            run_timestamp,
            session_id=session_id,
            run_timestamp=run_timestamp,
            timestamp=run_timestamp,
        )
        if saved is _MISSING:
            self._last_consolidation_runs[session_id] = run_timestamp

    @asynccontextmanager
    async def _transaction(self, session_id: str) -> AsyncIterator[None]:
        transaction = await self._call_store_method(
            ("transaction",),
            session_id,
            session_id=session_id,
            store_only=True,
        )
        if transaction is not _MISSING:
            async with transaction:
                yield
            return

        session = _db_session(self.memory_store)
        if session is None:
            yield
            return

        begin = getattr(session, "begin_nested", None) if _in_transaction(session) else None
        if begin is None:
            begin = getattr(session, "begin", None)
        if begin is None:
            yield
            return

        async with begin():
            yield

    async def _call_store_method(
        self,
        names: Iterable[str],
        *args: Any,
        store_only: bool = False,
        **kwargs: Any,
    ) -> Any:
        for name in names:
            method = getattr(self.memory_store, name, None)
            if not callable(method):
                continue

            call = _method_call(method, args, kwargs)
            if call is _MISSING:
                continue
            return await _maybe_await(call())

        if store_only:
            return _MISSING
        return _MISSING


class _Missing:
    pass


_MISSING = _Missing()


def _empty_result(run_timestamp: datetime) -> ConsolidationResult:
    return ConsolidationResult(
        summaries_produced=0,
        contradictions_resolved=0,
        entries_pruned=0,
        run_timestamp=run_timestamp,
    )


def _method_call(method: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return lambda: method(*args, **kwargs)

    parameters = list(signature.parameters.values())
    accepts_varargs = any(parameter.kind is parameter.VAR_POSITIONAL for parameter in parameters)
    accepts_varkw = any(parameter.kind is parameter.VAR_KEYWORD for parameter in parameters)
    keyword_names = {
        parameter.name
        for parameter in parameters
        if parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
    }
    filtered_kwargs = kwargs if accepts_varkw else {
        key: value for key, value in kwargs.items() if key in keyword_names
    }

    positional_parameters = [
        parameter
        for parameter in parameters
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]
    positional_args = args if accepts_varargs else args[: len(positional_parameters)]
    positional_names = {
        parameter.name
        for parameter in positional_parameters[: len(positional_args)]
        if parameter.kind is not parameter.POSITIONAL_ONLY
    }
    filtered_kwargs = {
        key: value
        for key, value in filtered_kwargs.items()
        if key not in positional_names
    }

    try:
        signature.bind(*positional_args, **filtered_kwargs)
    except TypeError:
        return _MISSING

    return lambda: method(*positional_args, **filtered_kwargs)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _db_session(memory_store: Any) -> Any | None:
    if hasattr(memory_store, "execute") and hasattr(memory_store, "begin"):
        return memory_store

    session = getattr(memory_store, "session", None)
    if session is not None and hasattr(session, "execute"):
        return session

    return None


def _in_transaction(session: Any) -> bool:
    in_transaction = getattr(session, "in_transaction", None)
    if not callable(in_transaction):
        return False
    return bool(in_transaction())


async def _fetch_db_unsummarized_messages(session: Any, session_id: str) -> list[Any]:
    import sqlalchemy as sa

    from chatbot.db import models

    cutoff = await _latest_episode_end_timestamp(session, session_id)
    stmt = (
        sa.select(models.Message)
        .where(models.Message.session_id == _coerce_session_id(session_id))
        .order_by(models.Message.created_at.asc(), models.Message.id.asc())
    )
    if cutoff is not None:
        stmt = stmt.where(models.Message.created_at > cutoff)

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _fetch_db_recent_messages(session: Any, session_id: str, limit: int) -> list[Any]:
    import sqlalchemy as sa

    from chatbot.db import models

    stmt = (
        sa.select(models.Message)
        .where(models.Message.session_id == _coerce_session_id(session_id))
        .order_by(models.Message.created_at.desc(), models.Message.id.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(reversed(result.scalars().all()))


async def _fetch_db_all_messages(session: Any, session_id: str) -> list[Any]:
    import sqlalchemy as sa

    from chatbot.db import models

    stmt = (
        sa.select(models.Message)
        .where(models.Message.session_id == _coerce_session_id(session_id))
        .order_by(models.Message.created_at.asc(), models.Message.id.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _latest_episode_end_timestamp(session: Any, session_id: str) -> datetime | None:
    import sqlalchemy as sa

    from chatbot.db import models

    stmt = (
        sa.select(models.Message.created_at)
        .join(models.Episode, models.Episode.end_message_id == models.Message.id)
        .where(models.Episode.session_id == _coerce_session_id(session_id))
        .order_by(models.Message.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _persist_db_summaries(
    session: Any,
    session_id: str,
    entries: Sequence[Any],
    session_summary: str,
    long_term_summary: str,
) -> None:
    if not session_summary and not long_term_summary:
        return

    import sqlalchemy as sa

    from chatbot.db import models

    coerced_session_id = _coerce_session_id(session_id)
    if session_summary:
        start_message_id = _entry_id(entries[0]) if entries else None
        end_message_id = _entry_id(entries[-1]) if entries else None
        session.add(
            models.Episode(
                session_id=coerced_session_id,
                title="Consolidation summary",
                summary=session_summary,
                start_message_id=_coerce_uuid(start_message_id),
                end_message_id=_coerce_uuid(end_message_id),
            )
        )

    if long_term_summary:
        result = await session.execute(
            sa.select(models.Session.user_id).where(models.Session.id == coerced_session_id)
        )
        user_id = result.scalar_one_or_none()
        if user_id is not None:
            session.add(
                models.SemanticFact(
                    user_id=user_id,
                    fact_text=long_term_summary,
                    confidence=1.0,
                )
            )


def _memory_entry(entry: Any) -> Any:
    return getattr(entry, "turn", entry)


def _pruned_entries(all_entries: Sequence[Any], kept_entries: Sequence[Any]) -> list[Any]:
    kept_identity = {id(entry) for entry in kept_entries}
    kept_ids = {_entry_id(entry) for entry in kept_entries if _entry_id(entry) is not None}
    return [
        entry
        for entry in all_entries
        if id(entry) not in kept_identity
        and (_entry_id(entry) is None or _entry_id(entry) not in kept_ids)
    ]


def _entry_ids(entries: Sequence[Any]) -> list[str]:
    return [str(entry_id) for entry_id in (_entry_id(entry) for entry in entries) if entry_id]


def _entry_id(entry: Any) -> Any | None:
    entry = _memory_entry(entry)
    if isinstance(entry, Mapping):
        return entry.get("id")
    return getattr(entry, "id", None)


def _count_result(result: Any, default: int) -> int:
    if isinstance(result, bool):
        return default if result else 0
    if isinstance(result, int):
        return result
    return default


def _normalize_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        return _normalize_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))
    raise TypeError("timestamp must be a datetime or ISO datetime string")


def _coerce_session_id(session_id: str) -> uuid.UUID | str:
    return _coerce_uuid(session_id) or session_id


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


__all__ = ["ConsolidationResult", "ConsolidationWorker"]
