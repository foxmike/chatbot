"""Shared conversation preparation for standard, streaming and spoken chat.

``POST /chat/``, ``POST /chat/stream`` and the avatar WebSocket (which reuses
the streaming endpoint) differ in how a reply is *delivered*. They must not
differ in how a turn is *prepared*: which companion, personality, session
state, room, history and memory feed the prompt. This module is the one
implementation of that preparation. ``chat.py`` and ``streaming.py`` remain the
delivery adapters.

Only operations that are implemented identically in both adapters live here,
including the persistence rows of a turn and title scheduling.
Behavior that genuinely differs by delivery mode stays in the adapter: standard
chat's room resolution, room-prompt injection and in-memory history fallback,
and its handling of saved mood/user state; streaming's room filter. Turn
execution (the step list, the standard-chat fast path and its deferred work,
the streaming step loop) and turn completion (persistence and post-turn
effects) are not here either.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import inspect
import logging
from time import monotonic
from typing import Any
import uuid

from fastapi import HTTPException, Request, status
from sqlalchemy import or_, select, update, func, case
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession

from chatbot.api.sessions import sessions_store
from chatbot.core.steps import TurnContext
from chatbot.db.models import BackgroundJob, Companion, CompanionIdentityModel, CompanionRelationshipModel, IdentityStateModel, Message, Room as RoomModel, Session
from chatbot.providers.base import ProviderConfig, ProviderRegistry
from chatbot.providers.router import apply_routing_overrides

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Application state lookup
# ---------------------------------------------------------------------------

# One map for every delivery mode. Values come from ``app.state``.
_PROVIDER_AND_MEMORY_STATE: dict[str, tuple[str, ...]] = {
    "provider_registry": ("provider_registry",),
    "provider_router": ("provider_router",),
    "provider_config": ("provider_config",),
    "provider_policy": ("provider_policy",),
    "provider_name": ("provider_name", "default_provider"),
    "default_provider": ("default_provider",),
    "model": ("model", "default_model"),
    "default_model": ("default_model",),
    "retrieval_engine": ("retrieval_engine", "memory_retriever", "vectorstore"),
    "memory_storage": ("memory_storage", "memory_store", "vectorstore"),
    "memory_database": ("memory_database", "database", "db"),
    "database": ("database", "db"),
    "db": ("db",),
}


def first_state_value(request: Request | None, *names: str) -> Any:
    if request is None:
        return None
    state = getattr(request.app, "state", None)
    if state is None:
        return None

    for name in names:
        value = getattr(state, name, None)
        if value is not None:
            return value
    return None


# ---------------------------------------------------------------------------
# Companion and personality
# ---------------------------------------------------------------------------


async def load_companion_inputs(
    db: AsyncSession,
    db_session: Session,
    user_id: str,
    requested_companion_id: str | None = None,
    *,
    durable_state: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return ``(character_profile, companion_persona)`` for the session's companion.

    ``requested_companion_id`` is only supplied by the standard-chat request
    body: it must exist, belong to the user and match the session binding.
    Otherwise the session-bound companion is used. Both values and optional durable
    metadata are derived from one ownership-filtered LEFT JOIN query.
    ``durable_state`` receives read-only snapshots for the turn context: the
    effective traits and their durable adjustments, the durable relationship and
    its factual interaction history. ``CompileContextStep`` composes them into the
    prompt (Phase 1 precedence, ``chatbot/soul/persistent_identity.py``).
    """
    companion_id = requested_companion_id or db_session.companion_id
    if companion_id is None:
        return None, None
    row = await _load_companion_state(db, user_id, str(companion_id))
    if row is None:
        if requested_companion_id is not None:
            raise HTTPException(status_code=404, detail="Companion not found")
        return None, None
    companion, identity, relationship = row
    if (
        requested_companion_id is not None
        and db_session.companion_id is not None
        and db_session.companion_id != requested_companion_id
    ):
        raise HTTPException(
            status_code=400, detail="Companion mismatch: session is bound to a different companion"
        )
    if durable_state is not None:
        from chatbot.soul.persistent_identity import effective_traits
        from chatbot.soul.relationship import RelationshipConfig

        durable_state["companion_identity"] = effective_traits(companion, identity)
        durable_state["companion_identity_adjustments"] = dict(
            (identity.trait_adjustments if identity is not None else None) or {}
        )
        # Same joined row: the developed self-model reaches every chat mode
        # through this one load, with no extra query.
        durable_state["companion_self_model"] = dict(
            (identity.self_model if identity is not None else None) or {}
        )
        durable_state["companion_self_model_version"] = (
            identity.version if identity is not None else 0
        )
        defaults = asdict(RelationshipConfig.defaults())
        durable_state["companion_relationship"] = (
            {key: getattr(relationship, key) for key in defaults}
            if relationship is not None
            else defaults
        )
        # Factual interaction history from the same row (no extra query).
        durable_state["companion_relationship_history"] = {
            "completed_turns": relationship.completed_turns if relationship is not None else 0,
            "first_interaction_at": getattr(relationship, "first_interaction_at", None),
            "last_interaction_at": getattr(relationship, "last_interaction_at", None),
        }
    return companion_to_profile_dict(companion), companion_to_persona(companion)


async def _load_companion_state(db: AsyncSession, user_id: str, companion_id: str):
    result = await db.execute(
        select(Companion, CompanionIdentityModel, CompanionRelationshipModel)
        .outerjoin(
            CompanionIdentityModel, CompanionIdentityModel.companion_id == Companion.companion_id
        )
        .outerjoin(
            CompanionRelationshipModel,
            CompanionRelationshipModel.companion_id == Companion.companion_id,
        )
        .where(Companion.companion_id == companion_id, Companion.user_id == uuid.UUID(user_id))
    )
    return result.one_or_none()


MEMORY_JOB_TASK_TYPE = "memory_extract"


def _memory_job_payload(
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    companion_id: str | None,
    turn_id: str,
) -> dict[str, Any]:
    """Compact work parameters for the eventual memory handler (Task 2.2c.2).

    Identifiers only: the handler loads the persisted turn messages by
    ``turn_id``; no conversation history is copied into the job row.
    """
    return {
        "session_id": str(session_id),
        "user_id": str(user_id),
        "companion_id": companion_id,
        "turn_id": turn_id,
    }


async def schedule_memory_job(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    companion_id: str | None,
    turn_id: str | None,
) -> None:
    """Insert the durable memory-extraction job for one completed turn.

    The authoritative completion hook (Phase 2 design, Task 2.2) schedules
    extraction only after the turn's successful persistence eligibility is
    known, and inserts the job inside the same transaction that persists the
    completed turn's messages and effects — a rollback leaves neither
    completed turn nor job. Interrupted, failed, or unsaved turns schedule no
    job because the completion hook is not called for them.

    Job identity is deterministic per persisted logical turn and work type:
    the unique constraint on ``(task_type, turn_id)`` makes duplicate
    scheduling a no-op via the conflict-safe insert.
    """
    if turn_id is None:
        return
    statement = postgres_insert(BackgroundJob).values(
        task_type=MEMORY_JOB_TASK_TYPE,
        turn_id=turn_id,
        session_id=session_id,
        user_id=user_id,
        companion_id=companion_id,
        payload=_memory_job_payload(session_id, user_id, companion_id, turn_id),
    ).on_conflict_do_nothing(index_elements=["task_type", "turn_id"])
    await db.execute(statement)


async def record_completed_interaction(
    db: AsyncSession,
    companion_id: str | None,
    user_id: str | uuid.UUID,
    at: datetime | None = None,
) -> None:
    """Atomic factual update, called last in the existing completion transaction.

    No commit here, no identity drift or depth updates. Session/companion owners
    must match even when legacy inconsistent sessions exist.
    """
    if companion_id is None:
        return
    at = at or datetime.now(timezone.utc)
    row = CompanionRelationshipModel
    owned = select(Companion.companion_id).where(Companion.user_id == uuid.UUID(str(user_id)))
    await db.execute(
        update(row)
        .where(row.companion_id == companion_id, row.companion_id.in_(owned))
        .values(
            completed_turns=row.completed_turns + 1,
            first_interaction_at=func.coalesce(row.first_interaction_at, at),
            last_interaction_at=case(
                (row.last_interaction_at > at, row.last_interaction_at), else_=at
            ),
            version=row.version + 1,
        )
        .execution_options(synchronize_session=False)
    )


def companion_to_profile_dict(companion: Any) -> dict[str, Any]:
    return {
        "name": getattr(companion, "name", "Companion"),
        "backstory": getattr(companion, "backstory", None) or "",
        "personality_sliders": getattr(companion, "personality_sliders", None) or {},
        "humor_style": getattr(companion, "humor_style", None) or "witty",
        "communication_style": getattr(companion, "communication_style", None) or "concise",
        "favorite_topics": getattr(companion, "favorite_topics", None) or [],
    }


# Companion field -> key PersonaPromptAssembler already understands. Only the
# attributes ``character_profile`` does not carry are mapped here, so nothing is
# rendered twice: backstory, sliders, humor, communication style and favorite
# topics keep reaching the prompt through the character profile.
_COMPANION_PERSONA_KEYS: tuple[tuple[str, str], ...] = (
    ("name", "name"),
    ("persona", "custom_instructions"),
    ("description", "description"),
    ("punctuation_habits", "punctuation_habits"),
    ("sentence_length_profile", "sentence_length_profile"),
    ("emoji_style", "emoji_style"),
    ("vocabulary_density", "vocabulary_density"),
    ("slang_profile", "slang_profile"),
)


def companion_to_persona(companion: Any) -> dict[str, Any]:
    """Persona mapping built from the companion's own configuration.

    The free-text ``persona`` prompt, the description and the linguistic
    fingerprint are stored on every companion and edited in the companion form,
    but no conversation mode used them. Using them here makes the effective
    personality input identical for every delivery mode.
    """
    persona: dict[str, Any] = {}
    for companion_field, persona_key in _COMPANION_PERSONA_KEYS:
        value = getattr(companion, companion_field, None)
        if isinstance(value, str):
            value = value.strip()
        if value is None or value == "" or value == {} or value == []:
            continue
        persona[persona_key] = value
    return persona


def resolve_persona(
    companion_persona: Mapping[str, Any] | None,
    fallback: Mapping[str, Any],
) -> dict[str, Any]:
    """The persona mapping handed to ``PersonaPromptAssembler.assemble``.

    The bound companion's own configuration is authoritative in every delivery
    mode. *fallback* applies only when no companion configuration is available,
    and is the mode's established fallback (see ``standard_persona_fallback``
    for standard chat; streaming has always used an empty persona).
    """
    if companion_persona:
        return dict(companion_persona)
    return dict(fallback)


def standard_persona_fallback(request: Request | None, session_id: str) -> dict[str, Any]:
    """Standard chat's established fallback: a configured persona, else the default personality."""
    session = sessions_store.get(session_id, {})
    return _first_persona(
        session.get("persona"),
        session.get("persona_config"),
        session.get("personality"),
        _persona_from_state(request, session_id),
    ) or _default_persona()


def _persona_from_state(request: Request | None, session_id: str) -> Any:
    personas = first_state_value(request, "personas", "session_personas")
    if isinstance(personas, Mapping):
        persona = personas.get(session_id)
        if persona is not None:
            return persona

    return first_state_value(
        request,
        "persona",
        "persona_config",
        "default_persona",
        "personality",
    )


def _first_persona(*values: Any) -> dict[str, Any]:
    for value in values:
        persona = _coerce_persona(value)
        if persona:
            return persona
    return {}


def _default_persona() -> dict[str, Any]:
    try:
        from chatbot.soul.personality import DEFAULT_PERSONALITY
    except Exception:
        return {
            "name": "Companion",
            "traits": ["helpful", "clear", "neutral"],
            "tone": "neutral",
        }

    persona = _coerce_persona(DEFAULT_PERSONALITY)
    if persona:
        return persona
    return {
        "name": "Companion",
        "traits": ["helpful", "clear", "neutral"],
        "tone": "neutral",
    }


def _coerce_persona(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            mapped = to_dict()
        except Exception:
            mapped = None
        if isinstance(mapped, Mapping):
            return dict(mapped)

    if is_dataclass(value):
        mapped = asdict(value)
        if isinstance(mapped, Mapping):
            return dict(mapped)

    return {}


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


async def load_session_identity(
    db: AsyncSession,
    session_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Return the most recent IdentityStateModel row for *session_id* as a detached dict."""
    try:
        result = await db.execute(
            select(IdentityStateModel)
            .where(IdentityStateModel.session_id == session_id)
            .order_by(IdentityStateModel.last_updated.desc())
            .limit(1)
        )
        identity = result.scalar_one_or_none()
    except Exception:
        return None
    if identity is None:
        return None
    return {
        "id": identity.id,
        "session_id": identity.session_id,
        "fixed": identity.fixed,
        "slow": identity.slow,
        "mood": identity.mood,
        "last_updated": identity.last_updated,
    }


# ---------------------------------------------------------------------------
# Conversation history
# ---------------------------------------------------------------------------


async def load_db_session_history(
    db: AsyncSession,
    session_id: uuid.UUID,
    room_id: str | None = None,
) -> list[dict[str, Any]]:
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .where(Message.role.in_(("user", "assistant", "system", "tool")))
    )
    if room_id is not None:
        room_session_ids = select(RoomModel.session_id).where(
            RoomModel.room_id == room_id,
            RoomModel.session_id.is_not(None),
        )
        stmt = (
            stmt.join(Session, Message.session_id == Session.id)
            .where(
                or_(
                    Session.room_id == room_id,
                    Session.id.in_(room_session_ids),
                )
            )
        )

    result = await db.execute(
        stmt.order_by(Message.created_at.asc(), Message.id.asc())
    )
    return [
        {
            "role": message.role,
            "content": message.content,
        }
        for message in result.scalars().all()
    ]


# ---------------------------------------------------------------------------
# Turn context
# ---------------------------------------------------------------------------


def build_turn_context(
    request: Request | None,
    *,
    session_id: str,
    user_message: str,
    history: list[dict[str, Any]],
    persona: dict[str, Any],
    db: AsyncSession,
    user_id: str,
    companion_id: str | None,
    room_id: str | None = None,
    room_type: str | None = None,
    room_system_prompt: str | None = None,
    character_profile: dict[str, Any] | None = None,
    identity_state: Any = None,
    durable_state: dict[str, Any] | None = None,
    session_record: dict[str, Any] | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
    thinking: bool = False,
    turn_id: str | None = None,
) -> TurnContext:
    """Build the ``TurnContext`` every delivery mode hands to the pipeline.

    This is the only place turn metadata is assembled from application state
    and the loaded conversation state.
    """
    session = sessions_store.get(session_id, {})
    metadata: dict[str, Any] = {
        "session_id": session_id,
        "durable_memory": True,
        "created_at": session.get("created_at"),
        "message_count": session.get("message_count", len(history)),
        "session": dict(session),
        "session_store": sessions_store,
        "sessions_store": sessions_store,
        "persona": dict(persona),
    }

    for metadata_key, state_names in _PROVIDER_AND_MEMORY_STATE.items():
        value = first_state_value(request, *state_names)
        if value is not None:
            metadata[metadata_key] = value

    working_memory_store = first_state_value(
        request,
        "working_memory_store",
        "working_memory",
        "memory_store",
        "histories",
    )
    if working_memory_store is None and history:
        working_memory_store = {session_id: list(reversed(history))}
    if working_memory_store is not None:
        metadata["working_memory_store"] = working_memory_store
        metadata["working_memory"] = working_memory_store

    apply_routing_overrides(metadata, provider_override, model_override)
    metadata["thinking"] = thinking
    if turn_id is not None:
        metadata["turn_id"] = turn_id

    metadata["db"] = db
    metadata["user_id"] = user_id
    metadata["companion_id"] = companion_id
    metadata["room_id"] = room_id
    if room_type is not None:
        metadata["room_type"] = room_type
    if room_system_prompt is not None:
        metadata["room_system_prompt"] = room_system_prompt
    if session_record is not None:
        metadata["session_record"] = session_record
    if character_profile is not None:
        metadata["character_profile"] = character_profile
    if identity_state is not None:
        metadata["identity_state"] = identity_state

    if durable_state:
        metadata.update(durable_state)

    retrieval_engine = metadata.get("retrieval_engine") or metadata.get("memory_retriever")
    if retrieval_engine is not None and not callable(getattr(retrieval_engine, "retrieve", None)):
        retrieval_engine = None

    return TurnContext(
        session_id=session_id,
        user_message=user_message,
        history=history,
        persona=persona,
        retrieved_memories=[],
        compiled_context=None,
        provider_response=None,
        extracted_memories=[],
        final_response=None,
        metadata=metadata,
        retrieval_engine=retrieval_engine,
    )


# ---------------------------------------------------------------------------
# Embedding preparation and memory retrieval wiring
# ---------------------------------------------------------------------------


async def prepare_memory_workflow(
    request: Request,
    ctx: TurnContext,
    db: AsyncSession,
    session_id: uuid.UUID,
) -> None:
    """Wire embeddings and retrieval before the turn pipeline reaches the LLM."""
    times = ctx.metadata.get("avatar_latency_times")
    if isinstance(times, dict):
        times["embedding_prepare_start"] = monotonic()
    await generate_recent_message_embeddings(db, session_id, ctx)
    if isinstance(times, dict):
        times["embedding_prepare_complete"] = monotonic()

    retrieval_engine = ctx.retrieval_engine or _metadata_retrieval_engine(ctx.metadata)
    if retrieval_engine is None:
        retrieval_engine = _build_chat_retrieval_engine(request, db)

    if retrieval_engine is not None:
        ctx.metadata["retrieval_engine"] = retrieval_engine
        ctx.metadata["memory_retriever"] = retrieval_engine
        ctx.retrieval_engine = retrieval_engine


async def generate_recent_message_embeddings(
    db: AsyncSession,
    session_id: uuid.UUID,
    ctx: TurnContext,
    limit: int = 20,
) -> None:
    try:
        from chatbot.config import get_settings
        from chatbot.memory.retrieval import OpenRouterEmbeddingProvider
    except ImportError:
        return

    try:
        result = await db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .where(Message.role.in_(("user", "assistant")))
            .where(Message.embedding.is_(None))
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
    except Exception:
        logger.warning(
            "Message embedding target load failed for session %s",
            session_id,
            exc_info=True,
        )
        return

    if messages:
        ctx.metadata["message_embeddings_checked"] = len(messages)
    if not messages:
        return

    settings = get_settings()
    if not settings.openrouter_api_key:
        return
    provider = OpenRouterEmbeddingProvider(
        api_key=settings.openrouter_api_key,
        base_url=getattr(settings, "embedding_base_url", settings.openrouter_base_url),
        model=getattr(settings, "embedding_model", "openai/text-embedding-3-small"),
    )
    # The API accepts an input array. One request preserves the same per-message
    # vectors while avoiding up to 20 sequential network round trips before chat.
    try:
        vectors = await provider.embed([f"{m.role}: {m.content}" for m in messages])
        if len(vectors) != len(messages) or any(
            provider.dimension > 0 and len(vector) != provider.dimension for vector in vectors
        ):
            raise ValueError("Embedding batch returned an unexpected shape")
        for message, vector in zip(messages, vectors, strict=True):
            message.embedding = vector
            db.add(message)
        await db.flush()
    except Exception:
        logger.warning("Recent message embedding batch failed for session %s", session_id, exc_info=True)
        return
    ctx.metadata["message_embeddings_generated"] = len(messages)


def _metadata_retrieval_engine(metadata: Mapping[str, Any]) -> Any | None:
    for key in ("retrieval_engine", "memory_retriever"):
        candidate = metadata.get(key)
        if callable(getattr(candidate, "retrieve", None)):
            return candidate
    return None


def _build_chat_retrieval_engine(request: Request, db: AsyncSession) -> Any | None:
    try:
        from chatbot.config import get_settings
        from chatbot.memory.pgvector_store import PgvectorStore
        from chatbot.memory.retrieval import (
            DisabledEmbeddingProvider,
            OpenRouterEmbeddingProvider,
            RetrievalEngine,
        )
    except ImportError:
        return None

    vector_store = first_state_value(
        request,
        "memory_vector_store",
        "vector_store",
        "vectorstore",
    )
    if vector_store is None or not callable(getattr(vector_store, "search", None)):
        vector_store = PgvectorStore(db, Message)

    embeddings = first_state_value(
        request,
        "embedding_provider",
        "embeddings",
        "memory_embeddings",
    )
    if embeddings is None or not callable(getattr(embeddings, "embed", None)):
        settings = get_settings()
        if settings.openrouter_api_key:
            embeddings = OpenRouterEmbeddingProvider(
                api_key=settings.openrouter_api_key,
                base_url=getattr(
                    settings,
                    "embedding_base_url",
                    settings.openrouter_base_url,
                ),
                model=getattr(
                    settings,
                    "embedding_model",
                    "openai/text-embedding-3-small",
                ),
            )
        else:
            embeddings = DisabledEmbeddingProvider(
                "Memory retrieval skipped because no embedding provider API key is configured"
            )

    return RetrievalEngine(vector_store, embeddings)


# ---------------------------------------------------------------------------
# Turn persistence (completion)
# ---------------------------------------------------------------------------

_INTERRUPTION_MARKER = (
    "[Assistant turn interrupted. Only confirmed delivered text "
    "is retained; a partially played speech segment may be omitted.]"
)


async def add_turn_messages(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    companion_id: str | None,
    user_message: str,
    assistant_message: str,
    turn_id: str | None,
    interrupted: bool = False,
    omit_empty_assistant: bool = False,
) -> None:
    """Add one turn's message rows and the session's update time to *db*.

    The one place a turn's rows are built, for every delivery mode. It never
    commits: the caller owns the transaction, so standard chat keeps committing
    the turn atomically with its other writes while streaming commits the
    messages in a session of their own.

    The user row always precedes the assistant row by 1 ms (and the
    interruption marker by another 1 ms) so history orders deterministically.
    ``omit_empty_assistant`` is streaming's established behavior: nothing is
    stored for an assistant reply with no confirmed text. Standard chat stores
    the reply as returned.
    """
    user_msg_ts = datetime.now(timezone.utc)
    assistant_msg_ts = max(datetime.now(timezone.utc), user_msg_ts + timedelta(milliseconds=1))

    def row(role: str, content: str, created_at: datetime) -> Message:
        return Message(
            session_id=session_id,
            user_id=user_id,
            companion_id=companion_id,
            role=role,
            content=content,
            turn_id=turn_id,
            created_at=created_at,
        )

    db.add(row("user", user_message, user_msg_ts))
    if assistant_message or not omit_empty_assistant:
        db.add(row("assistant", assistant_message, assistant_msg_ts))
    if interrupted:
        db.add(row("system", _INTERRUPTION_MARKER, assistant_msg_ts + timedelta(milliseconds=1)))
    await db.execute(
        update(Session)
        .where(Session.id == session_id)
        .values(updated_at=datetime.now(timezone.utc))
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def end_preparation_transaction(db: Any) -> None:
    """Commit what preparation wrote (pre-turn embeddings) and start the effects transaction.

    Called once generation is under way, so no transaction stays open for the
    rest of generation or spoken playback. Retrieval has already run, so the
    committed vectors become visible to the next turn exactly as they do in
    standard chat.

    The effects transaction is begun explicitly because the post-turn steps
    choose between flushing and committing by asking the session whether a
    transaction is open (``_flush_or_commit``, ``SQLAlchemyMemoryStorage``).
    A session that has just committed reports none, so they would commit
    mid-turn. Left open, they are committed together with the turn's messages
    by ``_persist_messages``. ``begin()`` acquires no connection until the
    first statement.
    """
    await _maybe_await(db.commit())
    in_transaction = getattr(db, "in_transaction", None)
    begin = getattr(db, "begin", None)
    if not callable(in_transaction) or not callable(begin):
        return
    if not await _maybe_await(in_transaction()):
        await _maybe_await(begin())


async def discard_effects_transaction(db: Any) -> None:
    """Roll back the turn's in-turn effects (memories, emotional state, timeline).

    Used when a turn must be saved without them (failed generation). A failure
    is logged; the connection is released either way.
    """
    try:
        await _maybe_await(db.rollback())
    except Exception:
        logger.warning("Turn effects could not be discarded", exc_info=True)


# ---------------------------------------------------------------------------
# Title generation (background, existing behavior)
# ---------------------------------------------------------------------------


def schedule_title_generation(
    request: Request | None,
    *,
    session_id: uuid.UUID,
    session_title: str | None,
    user_message: str,
    assistant_message: str,
    session_factory: Callable[[], Any],
    provider_override: str | None = None,
    model_override: str | None = None,
) -> None:
    """Schedule background title generation if the session has no title yet."""
    if session_title is not None:
        return

    if request is None:
        return

    provider_registry = first_state_value(request, "provider_registry")
    if provider_registry is None or not isinstance(provider_registry, ProviderRegistry):
        logger.debug("No provider registry available; skipping title generation")
        return

    provider_name = first_state_value(request, "provider_name", "default_provider")
    model = first_state_value(request, "model", "default_model")
    if provider_override:
        # The user's explicit selection also governs this background call:
        # never send their conversation to a different provider or model.
        provider_name = provider_override
        model = model_override or ""
    elif not provider_name and not model:
        provider_name = "openrouter"

    asyncio.create_task(
        generate_conversation_title(
            provider_registry=provider_registry,
            provider_name=str(provider_name) if provider_name else "openrouter",
            model=str(model) if model else None,
            session_id=session_id,
            user_message=user_message,
            assistant_message=assistant_message,
            session_factory=session_factory,
        )
    )


async def generate_conversation_title(
    *,
    provider_registry: ProviderRegistry,
    provider_name: str,
    model: str | None,
    session_id: uuid.UUID,
    user_message: str,
    assistant_message: str,
    session_factory: Callable[[], Any],
) -> None:
    """Generate a short conversation title using the LLM (runs in background)."""
    try:
        provider = provider_registry.get(provider_name)
    except KeyError:
        logger.debug(
            "Provider %r not found in registry; skipping title generation",
            provider_name,
        )
        return

    if model is None:
        model = ""

    title_prompt = (
        "Generate a short, concise title (2-5 words) for a conversation "
        "that starts with the following exchange. Return ONLY the title, "
        "with no quotes, explanations, or extra text.\n\n"
        f"User: {user_message}\n"
        f"Assistant: {assistant_message}\n"
    )

    config = ProviderConfig(
        model=model,
        temperature=0.3,
        max_tokens=16,
    )

    try:
        llm_response = await provider.complete(
            prompt=title_prompt,
            history=[],
            config=config,
        )
    except Exception:
        logger.warning(
            "Title generation LLM call failed for session %s",
            session_id,
            exc_info=True,
        )
        return

    raw_title = (llm_response.text or "").strip().strip('"').strip("'")
    if not raw_title:
        return

    # Enforce 2-5 word range via truncation
    words = raw_title.split()
    if len(words) > 5:
        raw_title = " ".join(words[:5])

    # Persist the title in a fresh DB session
    try:
        db = session_factory()
        try:
            await db.execute(
                update(Session)
                .where(Session.id == session_id, Session.title.is_(None))
                .values(title=raw_title)
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()
    except Exception:
        logger.warning(
            "Failed to persist title for session %s",
            session_id,
            exc_info=True,
        )
        return

    logger.info("Auto-generated title for session %s: %r", session_id, raw_title)
