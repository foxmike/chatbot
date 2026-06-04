from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import asyncio
import inspect
import logging
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
    Sequence,
)
from typing import Any
import uuid

from chatbot.consolidation.contradiction import ContradictionResolver, ContradictionResult
from chatbot.consolidation.decay import MemoryDecayEngine
from chatbot.consolidation.summarizer import Summarizer
from chatbot.perf.background import BackgroundQueue, BackgroundTask, run_background_worker
from chatbot.providers import Provider

try:
    from chatbot.memory.extraction import MemoryExtractor
except ImportError:
    MemoryExtractor = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)

CustomBackgroundHandler = Callable[[BackgroundTask], Awaitable[Any] | Any]


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
        background_queue: BackgroundQueue | None = None,
        custom_handlers: Mapping[str, CustomBackgroundHandler] | None = None,
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
        self.background_queue = background_queue
        self.custom_handlers = dict(custom_handlers or {})
        self.coalescing_window = timedelta(seconds=coalescing_window_seconds)
        self.recent_entry_limit = recent_entry_limit
        self.summarizer = summarizer or Summarizer(provider)
        self.contradiction_resolver = contradiction_resolver or ContradictionResolver(provider)
        self.decay_engine = decay_engine or MemoryDecayEngine()
        self._last_consolidation_runs: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def run(
        self,
        stop_event: asyncio.Event | None = None,
        *,
        background_queue: BackgroundQueue | None = None,
    ) -> None:
        queue = background_queue or self.background_queue
        if queue is None:
            return

        if stop_event is None:
            stop_event = asyncio.Event()
            stop_event.set()

        await run_background_worker(
            queue,
            stop_event,
            self.handle_background_task,
        )

    async def handle_background_task(self, task: BackgroundTask) -> None:
        if task.task_type == "memory_extract":
            await self._handle_memory_extract(task.payload)
            return
        if task.task_type == "consolidate":
            await self._handle_consolidate(task.payload)
            return
        if task.task_type == "sentiment":
            await self._handle_sentiment(task.payload)
            return
        if task.task_type == "index":
            await self._handle_index(task.payload)
            return
        if task.task_type == "custom":
            await self._handle_custom(task)
            return

        raise ValueError(f"Unsupported background task type: {task.task_type}")

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

    async def _handle_memory_extract(self, payload: Mapping[str, Any]) -> None:
        turn_context = payload.get("turn_context")
        if not isinstance(turn_context, Mapping):
            turn_context = {}
        session_id = _payload_text(payload, "session_id") or _payload_text(
            turn_context,
            "session_id",
        )
        if session_id is None:
            raise ValueError("Background task payload missing required field: session_id")
        user_message = _payload_text(payload, "user_message") or _payload_text(
            turn_context,
            "user_message",
        )
        if user_message is None:
            raise ValueError("Background task payload missing required field: user_message")
        provider_response = payload.get("provider_response")
        if provider_response is None:
            provider_response = turn_context.get("provider_response")
        provider_response = str(provider_response or "")
        user_id = payload.get("user_id") or turn_context.get("user_id")

        extracted = await self._call_store_method_with_keywords(
            (
                "extract_memory",
                "run_memory_extraction",
                "extract_turn_memory",
                "memory_extract",
            ),
            session_id=session_id,
            user_id=user_id,
            user_message=user_message,
            provider_response=provider_response,
            turn_context=dict(turn_context),
            payload=dict(payload),
        )
        if extracted is not _MISSING:
            return

        if MemoryExtractor is None:
            raise RuntimeError("MemoryExtractor is unavailable")

        database = _db_session(self.memory_store)
        storage = (
            self.memory_store
            if callable(getattr(self.memory_store, "persist", None))
            else None
        )
        if database is None and storage is None:
            database = self.memory_store

        extractor = MemoryExtractor(database=database, storage=storage)
        await extractor.extract(
            session_id=session_id,
            user_message=user_message,
            provider_response=provider_response,
        )

    async def _handle_consolidate(self, payload: Mapping[str, Any]) -> None:
        session_ids = await self._payload_session_ids(payload)
        if not session_ids:
            user_id = _required_payload_text(payload, "user_id")
            session_ids = [user_id]

        for session_id in session_ids:
            await self.run_consolidation(session_id)

    async def _handle_sentiment(self, payload: Mapping[str, Any]) -> None:
        text = _required_payload_text(payload, "text")
        result = await self._call_store_method_with_keywords(
            ("analyze_sentiment", "sentiment_analysis"),
            text=text,
            payload=dict(payload),
        )
        if result is _MISSING:
            result = _analyze_sentiment(text)

        saved = await self._call_store_method_with_keywords(
            (
                "persist_sentiment_result",
                "save_sentiment_result",
                "persist_sentiment",
                "save_sentiment",
                "store_sentiment",
                "record_sentiment",
            ),
            user_id=payload.get("user_id"),
            session_id=payload.get("session_id"),
            text=text,
            sentiment=result,
            result=result,
            payload=dict(payload),
        )
        if saved is _MISSING:
            raise RuntimeError("memory_store does not support sentiment persistence")

    async def _handle_index(self, payload: Mapping[str, Any]) -> None:
        doc_id = _required_payload_text(payload, "doc_id")
        indexed = await self._call_store_method_with_keywords(
            (
                "reindex_document",
                "index_document",
                "update_search_index",
                "rebuild_document_index",
                "rebuild_index",
            ),
            doc_id=doc_id,
            user_id=payload.get("user_id"),
            payload=dict(payload),
        )
        if indexed is _MISSING:
            raise RuntimeError("memory_store does not support document indexing")

    async def _handle_custom(self, task: BackgroundTask) -> None:
        handler_key = task.payload.get("handler_key")
        if not handler_key:
            logger.warning("Dropping custom background task without handler_key")
            return

        handler = self.custom_handlers.get(str(handler_key))
        if handler is None:
            logger.warning(
                "Dropping custom background task %s; handler %s is not registered",
                task.task_id,
                handler_key,
            )
            return

        await self._call_custom_handler(handler, task)

    async def _payload_session_ids(self, payload: Mapping[str, Any]) -> list[str]:
        session_id = _payload_text(payload, "session_id")
        if session_id:
            return [session_id]

        user_id = _payload_text(payload, "user_id")
        if not user_id:
            return []

        session_ids = await self._call_store_method_with_keywords(
            (
                "fetch_consolidation_session_ids",
                "get_consolidation_session_ids",
                "fetch_session_ids_for_user",
                "get_session_ids_for_user",
                "session_ids_for_user",
                "sessions_for_user",
            ),
            user_id=user_id,
        )
        if session_ids is not _MISSING:
            return _normalize_session_ids(session_ids)

        session = _db_session(self.memory_store)
        if session is None:
            return []

        return await _fetch_db_session_ids_for_user(session, user_id)

    async def _call_custom_handler(
        self,
        handler: CustomBackgroundHandler,
        task: BackgroundTask,
    ) -> None:
        call = _method_call(
            handler,
            (),
            {
                "task": task,
                "payload": task.payload,
                "handler_key": task.payload.get("handler_key"),
            },
        )
        if call is _MISSING:
            call = _method_call(handler, (task,), {})
        if call is _MISSING:
            raise TypeError("custom background handler has an unsupported signature")
        await _maybe_await(call())

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

    async def _call_store_method_with_keywords(
        self,
        names: Iterable[str],
        **kwargs: Any,
    ) -> Any:
        for name in names:
            method = getattr(self.memory_store, name, None)
            if not callable(method):
                continue

            call = _method_call(method, (), kwargs)
            if call is _MISSING:
                continue
            return await _maybe_await(call())

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


def _payload_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_payload_text(payload: Mapping[str, Any], key: str) -> str:
    value = _payload_text(payload, key)
    if value is None:
        raise ValueError(f"Background task payload missing required field: {key}")
    return value


def _normalize_session_ids(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, uuid.UUID)):
        return [str(values)]
    if isinstance(values, Mapping):
        return _normalize_session_ids([values])

    session_ids: list[str] = []
    try:
        iterator = iter(values)
    except TypeError:
        iterator = iter((values,))

    for value in iterator:
        session_id = _session_id_value(value)
        if session_id is not None:
            session_ids.append(session_id)
    return session_ids


def _session_id_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, uuid.UUID)):
        text = str(value).strip()
        return text or None
    if isinstance(value, Mapping):
        for key in ("session_id", "id"):
            text = _payload_text(value, key)
            if text is not None:
                return text
        return None

    for attribute in ("session_id", "id"):
        candidate = getattr(value, attribute, None)
        if candidate is not None:
            text = str(candidate).strip()
            if text:
                return text
    return None


async def _fetch_db_session_ids_for_user(session: Any, user_id: str) -> list[str]:
    user_uuid = _coerce_uuid(user_id)
    if user_uuid is None:
        return []

    import sqlalchemy as sa

    from chatbot.db import models

    stmt = sa.select(models.Session.id).where(models.Session.user_id == user_uuid)
    result = await session.execute(stmt)
    return [str(session_id) for session_id in result.scalars().all()]


def _analyze_sentiment(text: str) -> dict[str, Any]:
    lowered = text.lower()
    positive = sum(1 for word in _POSITIVE_SENTIMENT_WORDS if word in lowered)
    negative = sum(1 for word in _NEGATIVE_SENTIMENT_WORDS if word in lowered)
    score = positive - negative
    if score > 0:
        label = "positive"
    elif score < 0:
        label = "negative"
    else:
        label = "neutral"

    return {
        "label": label,
        "score": score,
        "positive_matches": positive,
        "negative_matches": negative,
    }


_POSITIVE_SENTIMENT_WORDS = (
    "appreciate",
    "calm",
    "confident",
    "delighted",
    "excited",
    "glad",
    "grateful",
    "happy",
    "hopeful",
    "love",
    "proud",
    "relieved",
    "thrilled",
)
_NEGATIVE_SENTIMENT_WORDS = (
    "afraid",
    "angry",
    "anxious",
    "ashamed",
    "depressed",
    "frustrated",
    "heartbroken",
    "hurt",
    "lonely",
    "overwhelmed",
    "panic",
    "sad",
    "scared",
    "stressed",
    "upset",
    "worried",
)


__all__ = ["ConsolidationResult", "ConsolidationWorker"]
