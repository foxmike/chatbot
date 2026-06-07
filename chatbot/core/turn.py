"""First-party turn pipeline for the chatbot engine. No framework dependency."""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import inspect
from time import perf_counter
from typing import Any

from chatbot.core.steps import StepResult, TurnContext, TurnStep
from chatbot.perf.background import BackgroundQueue, BackgroundTask
from chatbot.perf.cache import PersonaCache
from chatbot.providers.base import ProviderConfig

try:
    from chatbot.continuity.rituals import get_due_rituals, trigger_ritual
except ImportError:
    get_due_rituals = None  # type: ignore[assignment]
    trigger_ritual = None  # type: ignore[assignment]

try:
    from chatbot.continuity.timeline import add_timeline_entry
except ImportError:
    add_timeline_entry = None  # type: ignore[assignment]

try:
    from chatbot.db.models import (
        BotMoodLog,
        CharacterProfileModel,
        IdentityStateModel,
        Session,
        UserStateLog,
    )
except ImportError:
    BotMoodLog = None  # type: ignore[assignment]
    CharacterProfileModel = None  # type: ignore[assignment]
    IdentityStateModel = None  # type: ignore[assignment]
    Session = None  # type: ignore[assignment]
    UserStateLog = None  # type: ignore[assignment]

try:
    from chatbot.memory.extraction import MemoryExtractor, SQLAlchemyMemoryStorage
except ImportError:
    MemoryExtractor = None  # type: ignore[assignment]
    SQLAlchemyMemoryStorage = None  # type: ignore[assignment]

try:
    from chatbot.memory.working_memory import WorkingMemoryLoader
except ImportError:
    WorkingMemoryLoader = None  # type: ignore[assignment]

try:
    from chatbot.providers.router import ProviderRouter
except ImportError:
    ProviderRouter = None  # type: ignore[assignment]

try:
    from chatbot.observability.tracing import TraceStep, trace_collector
except ImportError:
    TraceStep = None  # type: ignore[assignment]
    trace_collector = None  # type: ignore[assignment]

try:
    from chatbot.observability.metrics import metrics_collector
except ImportError:
    metrics_collector = None  # type: ignore[assignment]

try:
    from chatbot.observability.logs import structured_logger
except ImportError:
    structured_logger = None  # type: ignore[assignment]

try:
    from chatbot.soul.persona_prompt import PersonaPromptAssembler
except ImportError:
    PersonaPromptAssembler = None  # type: ignore[assignment]

try:
    from chatbot.soul.character import CharacterProfile
except ImportError:
    CharacterProfile = None  # type: ignore[assignment]

try:
    from chatbot.soul.evolution import IdentityState
except ImportError:
    IdentityState = None  # type: ignore[assignment]

try:
    from chatbot.soul.mood import MoodEngine, MoodState
except ImportError:
    MoodEngine = None  # type: ignore[assignment]
    MoodState = None  # type: ignore[assignment]

try:
    from chatbot.soul.user_state import UserStateEngine, UserState
except ImportError:
    UserStateEngine = None  # type: ignore[assignment]
    UserState = None  # type: ignore[assignment]

try:
    from chatbot.soul.modes import ModeSelector, ConversationMode
except ImportError:
    ModeSelector = None  # type: ignore[assignment]
    ConversationMode = None  # type: ignore[assignment]

try:
    from chatbot.rooms.room import get_room
except ImportError:
    get_room = None  # type: ignore[assignment]

try:
    from chatbot.rooms.scene import get_active_scene
except ImportError:
    get_active_scene = None  # type: ignore[assignment]

try:
    from chatbot.rooms.roleplay import RoleplayEngine, RoleplayPersona
except ImportError:
    RoleplayEngine = None  # type: ignore[assignment]
    RoleplayPersona = None  # type: ignore[assignment]

try:
    from chatbot.memory.retrieval import build_room_context
except ImportError:
    build_room_context = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

FAST_PATH_STEPS: list[str] = [
    "receive",
    "retrieve",
    "compile_context",
    "call_provider",
    "respond",
]
ROOM_FAST_PATH_STEPS: list[str] = [
    "receive",
    "room_context",
    "retrieve",
    "compile_context",
    "call_provider",
    "respond",
]
DEFERRED_STEPS: list[str] = ["extract_memory", "consolidate"]

_MILESTONE_KEYWORDS: tuple[str, ...] = (
    "accepted",
    "bought a house",
    "broke up",
    "completed",
    "diagnosed",
    "finished",
    "first day",
    "got a new job",
    "got engaged",
    "got married",
    "graduated",
    "had a baby",
    "launched",
    "lost my job",
    "moved to",
    "passed",
    "promoted",
    "released",
    "shipped",
    "started a new job",
    "won",
)
_MILESTONE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("career change", r"\b(?:started|got|accepted)\s+(?:a\s+)?new job\b"),
    ("completed work", r"\b(?:finished|completed|shipped|released|launched)\b"),
    ("life change", r"\b(?:moved|graduated|married|engaged|promoted)\b"),
)
_EMOTIONAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "distress": (
        "afraid",
        "angry",
        "anxious",
        "ashamed",
        "burned out",
        "burnt out",
        "crying",
        "depressed",
        "frustrated",
        "grieving",
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
    ),
    "positive": (
        "celebrating",
        "excited",
        "grateful",
        "happy",
        "hopeful",
        "proud",
        "relieved",
        "thrilled",
    ),
}
_PROMISE_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "future commitment",
        r"\b(?:i|we)(?:'ll| will)\s+"
        r"(?:build|check|create|do|draft|fix|follow|handle|help|look|make|prepare|"
        r"remember|review|send|set|share|take|update|walk|work|write)\b",
    ),
    (
        "going to commitment",
        r"\b(?:i|we)(?:'m| am|'re| are)\s+going to\s+"
        r"(?:build|check|create|do|draft|fix|follow|handle|help|look|make|prepare|"
        r"remember|review|send|set|share|take|update|walk|work|write)\b",
    ),
    ("explicit help offer", r"\blet me help you\s+(?:by|with)\b"),
    ("explicit promise", r"\bi promise\b"),
)
_PROJECT_KEYWORDS: tuple[str, ...] = (
    "app",
    "book",
    "campaign",
    "client work",
    "dashboard",
    "feature",
    "game",
    "migration",
    "paper",
    "plugin",
    "project",
    "prototype",
    "refactor",
    "repo",
    "repository",
    "roadmap",
    "startup",
    "thesis",
    "website",
)
_PROJECT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("new project", r"\b(?:new|next|side|client|work)\s+project\b"),
    (
        "active project work",
        r"\b(?:building|creating|launching|making|planning|starting|started|"
        r"working on)\s+(?:a|an|the)?\s*[\w -]{3,80}\b",
    ),
)


class ReceiveStep(TurnStep):
    name = "receive"

    async def run(self, ctx: TurnContext) -> TurnContext:
        ctx.user_message = ctx.user_message.strip()
        return ctx


class RetrieveStep(TurnStep):
    name = "retrieve"

    def __init__(
        self,
        loader_cls: Any | None = WorkingMemoryLoader,
        retrieval_engine: Any | None = None,
    ) -> None:
        self._loader_cls = loader_cls
        self._retrieval_engine = retrieval_engine

    async def run(self, ctx: TurnContext) -> TurnContext:
        preloaded_history = list(ctx.history)
        if self._loader_cls is None:
            logger.warning("WorkingMemoryLoader is unavailable; using preloaded history")
            ctx.history = preloaded_history
        else:
            store = _first_metadata_value(
                ctx,
                "working_memory_store",
                "working_memory",
                "memory_store",
                "database",
                "db",
            )
            if store is None:
                logger.warning("Working memory store is unavailable; using preloaded history")
                ctx.history = preloaded_history
            else:
                try:
                    loaded_history = await self._loader_cls(store).load(ctx.session_id)
                    ctx.history = loaded_history or preloaded_history
                except Exception:
                    logger.warning(
                        "Working memory load failed for session %s",
                        ctx.session_id,
                        exc_info=True,
                    )
                    ctx.history = preloaded_history

        ctx.retrieved_memories = _coerce_memory_list(
            ctx.metadata.get("retrieved_memories")
            or ctx.metadata.get("retrieved_memory")
            or ctx.retrieved_memories
        )

        retrieval_engine = (
            _first_metadata_value(ctx, "retrieval_engine", "memory_retriever")
            or self._retrieval_engine
        )
        if retrieval_engine is None:
            _store_retrieved_memories(ctx)
            return ctx
        if not callable(getattr(retrieval_engine, "retrieve", None)):
            logger.warning("Retrieval engine is unavailable; using existing memories")
            _store_retrieved_memories(ctx)
            return ctx
        try:
            results = await self._do_retrieve(ctx, retrieval_engine)
            ctx.retrieved_memories = _coerce_memory_list(results)
        except Exception as exc:
            logger.warning(
                "Memory retrieval failed for session %s: %s",
                ctx.session_id,
                exc,
            )
            ctx.retrieved_memories = []
        _store_retrieved_memories(ctx)
        return ctx

    async def _do_retrieve(self, ctx: TurnContext, engine: Any) -> Any:
        return await engine.retrieve(
            query=ctx.user_message,
            session_id=ctx.session_id,
        )


class InferUserStateStep(TurnStep):
    name = "infer_user_state"

    def __init__(
        self,
        engine_cls: Any | None = UserStateEngine,
        mood_engine_cls: Any | None = MoodEngine,
    ) -> None:
        self._engine_cls = engine_cls
        self._mood_engine_cls = mood_engine_cls

    async def run(self, ctx: TurnContext) -> TurnContext:
        if not _emotional_intelligence_ready(ctx):
            return ctx
        if self._engine_cls is None or self._mood_engine_cls is None:
            return ctx

        mood_engine = _configured_component(
            ctx,
            ("mood_engine", "bot_mood_engine"),
            self._mood_engine_cls,
        )
        user_state_engine = _configured_component(
            ctx,
            ("user_state_engine",),
            self._engine_cls,
        )
        if mood_engine is None or user_state_engine is None:
            return ctx

        mood_state = _current_mood_state(ctx, mood_engine)
        if mood_state is None:
            return ctx

        prior_user_state = _current_user_state(ctx)
        messages = _recent_turn_messages(ctx)
        topics = _current_topics(ctx)

        try:
            user_state = user_state_engine.infer(
                messages=messages,
                topics=topics,
                mood_state=mood_state,
                prior_user_state=prior_user_state,
            )
        except Exception:
            logger.warning(
                "User state inference failed for session %s",
                ctx.session_id,
                exc_info=True,
            )
            return ctx

        ctx.metadata["mood_state"] = mood_state
        ctx.metadata["previous_user_state"] = prior_user_state
        ctx.metadata["user_state"] = user_state
        return ctx


class SelectConversationModeStep(TurnStep):
    name = "select_conversation_mode"

    def __init__(self, selector_cls: Any | None = ModeSelector) -> None:
        self._selector_cls = selector_cls

    async def run(self, ctx: TurnContext) -> TurnContext:
        if not _emotional_intelligence_ready(ctx) or self._selector_cls is None:
            return ctx

        selector = _configured_component(
            ctx,
            ("mode_selector", "conversation_mode_selector"),
            self._selector_cls,
        )
        if selector is None:
            return ctx

        mood_state = _current_mood_state(ctx)
        if mood_state is None:
            return ctx

        user_state = _current_user_state(ctx)
        messages = _recent_turn_messages(ctx)
        try:
            mode = selector.select(
                messages=messages,
                user_state=user_state,
                mood_state=mood_state,
            )
        except Exception:
            logger.warning(
                "Conversation mode selection failed for session %s",
                ctx.session_id,
                exc_info=True,
            )
            return ctx

        ctx.metadata["conversation_mode"] = mode
        response_params = getattr(selector, "get_response_params", None)
        if callable(response_params):
            try:
                ctx.metadata["conversation_mode_response_params"] = response_params(mode)
            except Exception:
                logger.warning("Conversation mode response params failed", exc_info=True)
        return ctx


class CompileContextStep(TurnStep):
    name = "compile_context"

    def __init__(
        self,
        assembler: Any | None = None,
        persona_cache: PersonaCache | None = None,
    ) -> None:
        if assembler is not None:
            self._assembler = assembler
        elif PersonaPromptAssembler is not None:
            self._assembler = PersonaPromptAssembler()
        else:
            self._assembler = None
        self._persona_cache = persona_cache

    def set_persona_cache(self, persona_cache: PersonaCache | None) -> None:
        self._persona_cache = persona_cache

    async def run(self, ctx: TurnContext) -> TurnContext:
        mood_state = _current_mood_state(ctx) if _emotional_intelligence_ready(ctx) else None
        mode = _current_conversation_mode(ctx) if _emotional_intelligence_ready(ctx) else None
        persona_prompt = await self._persona_prompt(ctx, mood=mood_state, mode=mode)
        ctx.compiled_context = _compile_context_text(
            persona_prompt=persona_prompt,
            history=ctx.history,
            retrieved_memories=ctx.retrieved_memories,
        )
        ctx.metadata["persona_prompt"] = persona_prompt
        ctx.metadata["compiled_context"] = ctx.compiled_context
        return ctx

    async def _persona_prompt(
        self,
        ctx: TurnContext,
        *,
        mood: Any | None = None,
        mode: Any | None = None,
    ) -> str:
        character_profile, identity_state = await _persona_identity_inputs(ctx)
        uses_identity_inputs = character_profile is not None or identity_state is not None

        if self._persona_cache is None:
            return _assemble_persona_prompt(
                self._assembler,
                ctx.persona,
                mood=mood,
                mode=mode,
                character_profile=character_profile,
                identity_state=identity_state,
            )

        user_id = _persona_cache_user_id(ctx)
        companion_id = _persona_cache_companion_id(ctx)
        if user_id is None:
            return _assemble_persona_prompt(
                self._assembler,
                ctx.persona,
                mood=mood,
                mode=mode,
                character_profile=character_profile,
                identity_state=identity_state,
            )

        if not uses_identity_inputs:
            cached_prompt = await self._persona_cache.get(user_id, companion_id)
            if cached_prompt is not None:
                ctx.metadata["persona_prompt_cache_hit"] = True
                return cached_prompt

        persona_prompt = _assemble_persona_prompt(
            self._assembler,
            ctx.persona,
            mood=mood,
            mode=mode,
            character_profile=character_profile,
            identity_state=identity_state,
        )
        if uses_identity_inputs:
            ctx.metadata["persona_prompt_cache_hit"] = False
        else:
            await self._persona_cache.set(user_id, companion_id, persona_prompt)
            ctx.metadata["persona_prompt_cache_hit"] = False
        return persona_prompt


class CallProviderStep(TurnStep):
    name = "call_provider"

    async def run(self, ctx: TurnContext) -> TurnContext:
        provider = _select_provider(ctx)
        config = _provider_config(ctx)
        if provider is None:
            ctx.provider_response = _fallback_provider_response(ctx)
            _store_llm_trace_metadata(ctx, provider=None, config=config, result=None)
            return ctx

        history = _provider_history(ctx)
        ctx.metadata["llm_history"] = history
        ctx.metadata["llm_prompt"] = _render_trace_prompt(ctx.user_message, history)
        try:
            result = await provider.complete(
                prompt=ctx.user_message,
                history=history,
                config=config,
            )
            ctx.provider_response = result.text
            _store_llm_trace_metadata(ctx, provider=provider, config=config, result=result)
        except Exception as exc:
            logger.warning("Provider call failed: %s", exc, exc_info=True)
            ctx.provider_response = f"Provider error: {exc}"
            _store_llm_trace_metadata(ctx, provider=provider, config=config, result=None)
        return ctx


class UpdateMoodStep(TurnStep):
    name = "update_mood"

    def __init__(self, engine_cls: Any | None = MoodEngine) -> None:
        self._engine_cls = engine_cls

    async def run(self, ctx: TurnContext) -> TurnContext:
        if not _emotional_intelligence_ready(ctx) or self._engine_cls is None:
            return ctx

        mood_engine = _configured_component(
            ctx,
            ("mood_engine", "bot_mood_engine"),
            self._engine_cls,
        )
        if mood_engine is None:
            return ctx

        current_state = _current_mood_state(ctx, mood_engine)
        if current_state is None:
            return ctx

        turn_result = {
            "session_id": ctx.session_id,
            "user_message": ctx.user_message,
            "provider_response": ctx.provider_response,
            "user_state": _json_safe(_current_user_state(ctx)),
            "conversation_mode": _json_safe(_current_conversation_mode(ctx)),
            "retrieved_memories": _json_safe(ctx.retrieved_memories),
            "extracted_memories": _json_safe(ctx.extracted_memories),
        }
        try:
            updated_state = mood_engine.update(turn_result, current_state)
        except Exception:
            logger.warning(
                "Mood update failed for session %s",
                ctx.session_id,
                exc_info=True,
            )
            return ctx

        ctx.metadata["previous_mood_state"] = current_state
        ctx.metadata["mood_state"] = updated_state
        return ctx


class PersistEmotionalStateStep(TurnStep):
    name = "persist_emotional_state"

    async def run(self, ctx: TurnContext) -> TurnContext:
        if not _emotional_intelligence_ready(ctx):
            return ctx

        mood_state = _current_mood_state(ctx)
        user_state = _current_user_state(ctx)
        mode = _current_conversation_mode(ctx)
        if mood_state is None and user_state is None and mode is None:
            return ctx

        _persist_session_mapping(ctx, mood_state, user_state, mode)
        await _persist_db_emotional_state(ctx, mood_state, user_state, mode)
        return ctx


class ExtractMemoryStep(TurnStep):
    name = "extract_memory"

    def __init__(self, extractor_cls: Any | None = MemoryExtractor) -> None:
        self._extractor_cls = extractor_cls

    async def run(self, ctx: TurnContext) -> TurnContext:
        if self._extractor_cls is None:
            logger.warning("MemoryExtractor is unavailable; no memories extracted")
            ctx.extracted_memories = []
            return ctx

        storage = ctx.metadata.get("memory_storage")
        database = _first_metadata_value(
            ctx,
            "memory_database",
            "database",
            "db",
            "episodic_store",
        )
        if storage is None and database is None:
            logger.warning("Memory extraction store is unavailable; no memories extracted")
            ctx.extracted_memories = []
            return ctx

        try:
            if ctx.metadata.get("scene_memory_isolation"):
                storage = _scene_memory_storage(ctx, storage, database)
                database = None
                if storage is None:
                    ctx.extracted_memories = []
                    return ctx

            extractor = self._extractor_cls(database=database, storage=storage)
            ctx.extracted_memories = await extractor.extract(
                session_id=ctx.session_id,
                user_message=ctx.user_message,
                provider_response=ctx.provider_response or "",
            )
        except Exception:
            logger.warning(
                "Memory extraction failed for session %s",
                ctx.session_id,
                exc_info=True,
            )
            ctx.extracted_memories = []
        return ctx


class TimelineUpdateStep(TurnStep):
    name = "timeline_update"

    async def run(self, ctx: TurnContext) -> TurnContext:
        await _trigger_due_rituals(ctx)

        if add_timeline_entry is None:
            logger.warning("Timeline module is unavailable; no timeline entries added")
            ctx.metadata["new_timeline_entries"] = _metadata_text_list(
                ctx,
                "new_timeline_entries",
            )
            return ctx

        entry_ids = _metadata_text_list(ctx, "new_timeline_entries")
        for event in _detect_timeline_events(ctx):
            try:
                entry = await add_timeline_entry(
                    session_id=ctx.session_id,
                    entry_type=event["entry_type"],
                    title=event["title"],
                    description=event["description"],
                    content=_timeline_turn_content(ctx),
                    turn_id=_timeline_turn_id(ctx),
                    tags=event["tags"],
                    metadata={
                        "source": "turn_pipeline",
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                        "signals": event["signals"],
                    },
                )
            except Exception:
                logger.warning(
                    "Timeline update failed for session %s",
                    ctx.session_id,
                    exc_info=True,
                )
                continue

            entry_id = getattr(entry, "entry_id", None)
            if entry_id:
                entry_ids.append(str(entry_id))

        ctx.metadata["new_timeline_entries"] = entry_ids
        return ctx


class RespondStep(TurnStep):
    name = "respond"

    async def run(self, ctx: TurnContext) -> TurnContext:
        if ctx.provider_response is None:
            ctx.provider_response = "No response"
        ctx.final_response = ctx.provider_response
        return ctx


class RoomContextStep(TurnStep):
    name = "room_context"

    async def run(self, ctx: TurnContext) -> TurnContext:
        room_id = ctx.metadata.get("room_id")
        if room_id is None:
            return ctx

        if get_room is None:
            logger.warning("Room module is unavailable; skipping room context")
            return ctx

        try:
            room = await get_room(str(room_id))
        except Exception:
            logger.warning("Room load failed for room %s", room_id, exc_info=True)
            return ctx

        if room is None:
            logger.warning("Room not found: %s", room_id)
            return ctx

        ctx.metadata["_room_object"] = room
        ctx.metadata["room_type"] = room.room_type.value
        ctx.metadata["scene_memory_isolation"] = room.room_type.value == "roleplay_scene"

        if get_active_scene is not None:
            try:
                scene = await get_active_scene(str(room_id))
            except Exception:
                logger.warning(
                    "Active scene load failed for room %s", room_id, exc_info=True
                )
                scene = None

            if scene is not None:
                ctx.metadata["active_scene"] = scene
                ctx.metadata["scene_id"] = scene.scene_id
            elif room.scene_id is not None:
                ctx.metadata["scene_id"] = room.scene_id

        return ctx


class SceneAwareRetrievalStep(RetrieveStep):
    name = "retrieve"

    async def _do_retrieve(self, ctx: TurnContext, engine: Any) -> Any:
        room_context = self._build_room_context(ctx)
        return await engine.retrieve(
            query=ctx.user_message,
            session_id=ctx.session_id,
            room_context=room_context,
        )

    def _build_room_context(self, ctx: TurnContext) -> Any | None:
        if not ctx.metadata.get("scene_memory_isolation"):
            return None
        if build_room_context is None:
            return None
        room = ctx.metadata.get("_room_object")
        if room is None:
            return None
        scene = ctx.metadata.get("active_scene")
        try:
            return build_room_context(room, scene)
        except Exception:
            logger.warning("Room context build failed", exc_info=True)
            return None


class SceneMemoryGateStep(TurnStep):
    name = "scene_memory_gate"

    async def run(self, ctx: TurnContext) -> TurnContext:
        if not ctx.metadata.get("scene_memory_isolation"):
            return ctx

        if RoleplayEngine is None:
            logger.warning("RoleplayEngine is unavailable; skipping memory gate")
            return ctx

        scene = ctx.metadata.get("active_scene")
        engine = None

        if scene is not None and RoleplayPersona is not None:
            try:
                engine = RoleplayEngine(
                    scene=scene,
                    persona=RoleplayPersona(
                        name="",
                        description="",
                        traits=[],
                        voice_style="",
                        backstory="",
                    ),
                )
            except Exception:
                logger.warning(
                    "RoleplayEngine instantiation failed; using text classifier",
                    exc_info=True,
                )

        classifications: dict[str, str] = {}
        durable_memories: list[dict] = []

        for memory in ctx.extracted_memories:
            content = _memory_entry_content(memory)
            try:
                is_durable = _is_scene_durable_candidate(ctx, content, engine=engine)
            except Exception:
                logger.warning(
                    "Memory classification failed; treating as non-durable",
                    exc_info=True,
                )
                is_durable = False

            classifications[content[:64]] = "durable" if is_durable else "scene_only"
            if is_durable:
                durable_memories.append(memory)

        ctx.metadata["memory_classifications"] = classifications
        ctx.extracted_memories = durable_memories
        return ctx


class _SceneMemoryStorage:
    def __init__(self, ctx: TurnContext, storage: Any) -> None:
        self._ctx = ctx
        self._storage = storage

    async def persist(
        self,
        session_id: str,
        entries: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        durable_entries = _filter_scene_memory_entries(self._ctx, entries)
        if not durable_entries:
            return []
        return await _maybe_await(self._storage.persist(session_id, durable_entries))


def _scene_memory_storage(
    ctx: TurnContext,
    storage: Any | None,
    database: Any | None,
) -> _SceneMemoryStorage | None:
    if storage is None and database is not None and SQLAlchemyMemoryStorage is not None:
        storage = SQLAlchemyMemoryStorage(database)
    if storage is None or not callable(getattr(storage, "persist", None)):
        return None
    return _SceneMemoryStorage(ctx, storage)


def _filter_scene_memory_entries(
    ctx: TurnContext,
    entries: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    durable_entries: list[dict[str, Any]] = []
    classifications = ctx.metadata.get("memory_classifications")
    if isinstance(classifications, Mapping):
        memory_classifications: dict[str, str] = dict(classifications)
    else:
        memory_classifications = {}

    for entry in entries:
        content = _memory_entry_content(entry)
        try:
            is_durable = _is_scene_durable_candidate(ctx, content)
        except Exception:
            logger.warning(
                "Memory classification failed; treating as non-durable",
                exc_info=True,
            )
            is_durable = False

        memory_classifications[content[:64]] = (
            "durable" if is_durable else "scene_only"
        )
        if is_durable:
            durable_entries.append(entry)

    ctx.metadata["memory_classifications"] = memory_classifications
    return durable_entries


def _is_scene_durable_candidate(
    ctx: TurnContext,
    content: str,
    *,
    engine: Any | None = None,
) -> bool:
    if not content or RoleplayEngine is None:
        return False

    if engine is None:
        scene = ctx.metadata.get("active_scene")
        if scene is not None and RoleplayPersona is not None:
            try:
                engine = RoleplayEngine(
                    scene=scene,
                    persona=RoleplayPersona(
                        name="",
                        description="",
                        traits=[],
                        voice_style="",
                        backstory="",
                    ),
                )
            except Exception:
                logger.warning(
                    "RoleplayEngine instantiation failed; using text classifier",
                    exc_info=True,
                )
                engine = None

    if engine is not None:
        return bool(engine.is_durable_candidate(content))

    classify = getattr(RoleplayEngine, "is_durable_text_candidate", None)
    if callable(classify):
        return bool(classify(content))
    return False


def _memory_entry_content(memory: Mapping[str, Any]) -> str:
    return str(
        memory.get("content")
        or memory.get("summary")
        or memory.get("fact_text")
        or ""
    )


def _room_id_present(ctx: TurnContext) -> bool:
    room_id = ctx.metadata.get("room_id")
    if room_id is None:
        return False
    return bool(str(room_id).strip())


class TurnPipeline:
    def __init__(
        self,
        steps: list[TurnStep],
        persona_cache: PersonaCache | None = None,
        background_queue: BackgroundQueue | None = None,
    ) -> None:
        self._steps = list(steps)
        self._persona_cache = persona_cache
        self._background_queue = background_queue
        self._last_ctx: TurnContext | None = None
        if persona_cache is not None:
            self._configure_persona_cache(persona_cache)

    async def run(self, ctx: TurnContext) -> TurnContext:
        if self._background_queue is not None:
            if _room_id_present(ctx):
                return await self._run_steps(ctx, self._steps)

            ctx = await self.run_fast(ctx)
            await self.enqueue_deferred(ctx)
            return ctx

        return await self._run_steps(ctx, self._steps)

    async def run_fast(self, ctx: TurnContext) -> TurnContext:
        step_names = ROOM_FAST_PATH_STEPS if _room_id_present(ctx) else FAST_PATH_STEPS
        return await self._run_steps(ctx, self._selected_steps(step_names))

    async def enqueue_deferred(self, ctx: TurnContext) -> None:
        if self._background_queue is None:
            return

        for step_name in DEFERRED_STEPS:
            await self._background_queue.enqueue(
                BackgroundTask(
                    task_id=str(uuid.uuid4()),
                    task_type=_background_task_type(step_name),
                    payload=_deferred_turn_payload(ctx),
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )

    async def _run_steps(
        self,
        ctx: TurnContext,
        steps: Sequence[TurnStep],
    ) -> TurnContext:
        step_results: list[StepResult] = []
        ctx.metadata["step_results"] = step_results
        self._last_ctx = ctx
        trace_id = await _start_turn_trace(ctx)

        for step in steps:
            step_name = _step_name(step)
            started_at = perf_counter()
            trace_step = _new_trace_step(step_name)

            try:
                if trace_step is None:
                    ctx = await step.run(ctx)
                else:
                    with trace_step:
                        ctx = await step.run(ctx)
            except Exception as exc:
                duration_ms = _trace_step_duration_ms(trace_step, started_at)
                step_results.append(
                    StepResult(
                        step_name=step_name,
                        duration_ms=duration_ms,
                        success=False,
                        error=str(exc),
                    )
                )
                ctx.metadata["pipeline_error"] = str(exc)
                self._last_ctx = ctx
                await _record_turn_trace_step(
                    ctx,
                    trace_id,
                    step_name,
                    duration_ms,
                    success=False,
                    error=str(exc),
                )
                await _complete_turn_trace(ctx, trace_id)
                return ctx

            duration_ms = _trace_step_duration_ms(trace_step, started_at)
            step_results.append(
                StepResult(
                    step_name=step_name,
                    duration_ms=duration_ms,
                    success=True,
                )
            )
            ctx.metadata["step_results"] = step_results
            self._last_ctx = ctx
            await _record_turn_trace_step(
                ctx,
                trace_id,
                step_name,
                duration_ms,
                success=True,
            )

        await _complete_turn_trace(ctx, trace_id)
        return ctx

    def _selected_steps(self, step_names: Sequence[str]) -> list[TurnStep]:
        selected: list[TurnStep] = []
        wanted = set(step_names)
        for step in self._steps:
            if _step_name(step) in wanted:
                selected.append(step)

        selected_by_name = {_step_name(step): step for step in selected}
        return [selected_by_name[name] for name in step_names if name in selected_by_name]

    def _configure_persona_cache(self, persona_cache: PersonaCache) -> None:
        for step in self._steps:
            if _step_name(step) != "compile_context":
                continue
            set_cache = getattr(step, "set_persona_cache", None)
            if callable(set_cache):
                set_cache(persona_cache)

    @property
    def step_results(self) -> list[StepResult]:
        if self._last_ctx is None:
            return []

        results = self._last_ctx.metadata.get("step_results", [])
        return list(results)


class _IdentityTurnStep(TurnStep):
    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, ctx: TurnContext) -> TurnContext:
        return ctx


def build_default_pipeline(
    persona_cache: PersonaCache | None = None,
    background_queue: BackgroundQueue | None = None,
) -> TurnPipeline:
    if WorkingMemoryLoader is None:
        logger.warning("WorkingMemoryLoader import failed; RetrieveStep will use fallback")
    if PersonaPromptAssembler is None:
        logger.warning("PersonaPromptAssembler import failed; CompileContextStep will use fallback")
    if MemoryExtractor is None:
        logger.warning("MemoryExtractor import failed; ExtractMemoryStep will use fallback")
    if not _emotional_intelligence_imports_available():
        logger.warning("Emotional intelligence imports unavailable; EI steps will no-op")
    if get_room is None:
        logger.warning("Room module import failed; RoomContextStep will no-op")
    if get_active_scene is None:
        logger.warning("Scene module import failed; RoomContextStep will skip scene loading")
    if RoleplayEngine is None:
        logger.warning("RoleplayEngine import failed; SceneMemoryGateStep will no-op")
    if build_room_context is None:
        logger.warning("Retrieval room context import failed; SceneAwareRetrievalStep will use default retrieval")

    return TurnPipeline(
        [
            ReceiveStep(),
            RoomContextStep(),
            SceneAwareRetrievalStep(WorkingMemoryLoader),
            InferUserStateStep(UserStateEngine, MoodEngine),
            SelectConversationModeStep(ModeSelector),
            CompileContextStep(persona_cache=persona_cache),
            CallProviderStep(),
            UpdateMoodStep(MoodEngine),
            PersistEmotionalStateStep(),
            ExtractMemoryStep(MemoryExtractor),
            SceneMemoryGateStep(),
            TimelineUpdateStep(),
            RespondStep(),
        ],
        persona_cache=persona_cache,
        background_queue=background_queue,
    )


def _new_trace_step(step_name: str) -> Any | None:
    if TraceStep is None:
        return None
    try:
        return TraceStep(step_name=step_name, started_at=time.monotonic())
    except Exception:
        logger.warning("Trace step creation failed for %s", step_name, exc_info=True)
        return None


def _trace_step_duration_ms(trace_step: Any | None, started_at: float) -> float:
    duration_ms = getattr(trace_step, "duration_ms", 0) if trace_step is not None else 0
    try:
        duration = float(duration_ms)
    except (TypeError, ValueError):
        duration = 0
    if duration > 0:
        return duration
    return (perf_counter() - started_at) * 1000


async def _start_turn_trace(ctx: TurnContext) -> str | None:
    if trace_collector is None:
        return None

    try:
        turn_id = _trace_turn_id(ctx)
        session_id = str(ctx.session_id)
        user_id = _trace_user_id(ctx)
        task_class = _trace_task_class(ctx)
        trace_id = await trace_collector.start_trace(
            turn_id,
            session_id,
            user_id,
            task_class,
        )
        ctx.metadata["trace_id"] = trace_id
        ctx.metadata["task_class"] = task_class
        return str(trace_id)
    except Exception as exc:
        await _log_trace_warning(ctx, "Trace start failed", exc)
        return None


async def _record_turn_trace_step(
    ctx: TurnContext,
    trace_id: str | None,
    step_name: str,
    duration_ms: float,
    *,
    success: bool,
    error: str | None = None,
) -> None:
    if trace_id is None or trace_collector is None:
        return

    try:
        await trace_collector.record_step(
            trace_id,
            step_name,
            duration_ms,
            metadata=_trace_step_metadata(ctx, step_name, success=success, error=error),
        )
    except Exception as exc:
        await _log_trace_warning(ctx, "Trace step recording failed", exc)


async def _complete_turn_trace(ctx: TurnContext, trace_id: str | None) -> None:
    if trace_id is None or trace_collector is None:
        return

    prompt = _trace_prompt(ctx)
    prompt_tokens, completion_tokens = _trace_token_counts(ctx, prompt)
    model_provider = _trace_model_provider(ctx)
    model_id = _trace_model_id(ctx)

    try:
        trace_record = await trace_collector.complete_trace(
            trace_id,
            model_provider,
            model_id,
            prompt_tokens,
            completion_tokens,
            _trace_cost_usd_micros(ctx, prompt_tokens, completion_tokens),
            prompt,
            _trace_routing_decision(ctx, model_provider, model_id),
            _trace_retrieval_snippets(ctx),
            _trace_total_latency_ms(ctx),
        )
        ctx.metadata["trace_record"] = trace_record
    except Exception as exc:
        await _log_trace_warning(ctx, "Trace completion failed", exc)
        return

    if metrics_collector is None:
        return
    try:
        await metrics_collector.record_trace(trace_record)
    except Exception as exc:
        await _log_trace_warning(ctx, "Trace metric recording failed", exc)


async def _log_trace_warning(
    ctx: TurnContext,
    message: str,
    exc: BaseException | None = None,
) -> None:
    logger.warning("%s: %s", message, exc, exc_info=exc is not None)
    if structured_logger is None:
        return

    details = {
        "message": message,
        "error": str(exc) if exc is not None else None,
    }
    for method_name in ("warning", "warn", "log_warning"):
        method = getattr(structured_logger, method_name, None)
        if not callable(method):
            continue
        try:
            await _maybe_await(
                method(
                    message,
                    session_id=str(ctx.session_id),
                    turn_id=str(ctx.metadata.get("turn_id") or ""),
                    user_id=_trace_user_id(ctx),
                    details=details,
                )
            )
        except Exception:
            logger.warning("Structured trace warning log failed", exc_info=True)
        return


def _trace_turn_id(ctx: TurnContext) -> str:
    for key in ("turn_id", "current_turn_id", "request_id"):
        value = ctx.metadata.get(key)
        if value:
            turn_id = str(value)
            ctx.metadata["turn_id"] = turn_id
            return turn_id

    turn_id = str(uuid.uuid4())
    ctx.metadata["turn_id"] = turn_id
    return turn_id


def _trace_user_id(ctx: TurnContext) -> str | None:
    for key in ("user_id", "current_user_id", "account_id"):
        value = ctx.metadata.get(key)
        if value is not None:
            text = str(value).strip()
            return text or None

    value = _read_session_field(ctx, "user_id")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _trace_task_class(ctx: TurnContext) -> str:
    for key in (
        "task_class",
        "task_type",
        "intent_class",
        "intent",
        "conversation_task_class",
    ):
        value = ctx.metadata.get(key)
        if value is None:
            continue
        if isinstance(value, Enum):
            value = value.value
        if isinstance(value, Mapping):
            value = value.get("task_class") or value.get("class") or value.get("name")
        text = str(value or "").strip()
        if text:
            return text
    return "casual"


def _trace_step_metadata(
    ctx: TurnContext,
    step_name: str,
    *,
    success: bool,
    error: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"success": success}
    if error:
        metadata["error"] = error

    normalized_name = step_name.lower()
    if "retrieve" in normalized_name:
        metadata["snippet_count"] = len(_trace_retrieval_snippets(ctx))
        metadata["history_count"] = len(ctx.history)
    elif normalized_name == "compile_context":
        metadata["compiled_context_chars"] = len(str(ctx.compiled_context or ""))
        metadata["snippet_count"] = len(_trace_retrieval_snippets(ctx))
    elif normalized_name == "call_provider":
        metadata["model_provider"] = _trace_model_provider(ctx)
        metadata["model_id"] = _trace_model_id(ctx)
        prompt_tokens, completion_tokens = _trace_token_counts(ctx, _trace_prompt(ctx))
        metadata["prompt_tokens"] = prompt_tokens
        metadata["completion_tokens"] = completion_tokens
        finish_reason = ctx.metadata.get("llm_finish_reason")
        if finish_reason is not None:
            metadata["finish_reason"] = str(finish_reason)
    elif normalized_name == "extract_memory":
        metadata["extracted_memory_count"] = len(ctx.extracted_memories)
    elif normalized_name == "scene_memory_gate":
        metadata["durable_memory_count"] = len(ctx.extracted_memories)
        classifications = ctx.metadata.get("memory_classifications")
        if isinstance(classifications, Mapping):
            metadata["classification_count"] = len(classifications)
    elif normalized_name == "respond":
        metadata["response_chars"] = len(str(ctx.final_response or ""))

    return _json_safe(metadata)


def _store_llm_trace_metadata(
    ctx: TurnContext,
    *,
    provider: Any | None,
    config: ProviderConfig,
    result: Any | None,
) -> None:
    model_id = _result_model_id(result) or config.model or _trace_model_id(ctx)
    model_provider = _trace_provider_name(ctx, provider, model_id)
    usage = _provider_usage(result)

    if model_id:
        ctx.metadata["model_id"] = model_id
    if model_provider:
        ctx.metadata["model_provider"] = model_provider
    if usage:
        ctx.metadata["llm_usage"] = usage

    finish_reason = getattr(result, "finish_reason", None) if result is not None else None
    if finish_reason is not None:
        ctx.metadata["llm_finish_reason"] = str(finish_reason)

    routing_decision = ctx.metadata.get("routing_decision")
    if not isinstance(routing_decision, Mapping):
        routing_decision = {}
    ctx.metadata["routing_decision"] = {
        **dict(routing_decision),
        "provider": model_provider,
        "model": model_id,
        "task_class": _trace_task_class(ctx),
    }

    if "llm_prompt" not in ctx.metadata:
        ctx.metadata["llm_prompt"] = _trace_prompt(ctx)


def _result_model_id(result: Any | None) -> str | None:
    if result is None:
        return None
    model = getattr(result, "model", None)
    if model:
        return str(model)
    raw = getattr(result, "raw", None)
    if isinstance(raw, Mapping) and raw.get("model"):
        return str(raw["model"])
    return None


def _provider_usage(result: Any | None) -> dict[str, Any]:
    if result is None:
        return {}

    for source in (
        getattr(result, "usage", None),
        getattr(result, "token_usage", None),
    ):
        if isinstance(source, Mapping):
            return dict(source)

    raw = getattr(result, "raw", None)
    if not isinstance(raw, Mapping):
        return {}

    for key in ("usage", "token_usage"):
        usage = raw.get(key)
        if isinstance(usage, Mapping):
            return dict(usage)
    return {}


def _trace_prompt(ctx: TurnContext) -> str:
    prompt = ctx.metadata.get("llm_prompt")
    if isinstance(prompt, str) and prompt:
        return prompt

    history = ctx.metadata.get("llm_history")
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes)):
        history = _provider_history(ctx)
    return _render_trace_prompt(ctx.user_message, history)


def _render_trace_prompt(
    user_message: str,
    history: Sequence[Mapping[str, Any]],
) -> str:
    parts: list[str] = []
    for message in history:
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role") or "message").strip() or "message"
        content = str(message.get("content") or "").strip()
        if content:
            parts.append(f"{role}: {content}")

    prompt = str(user_message or "").strip()
    if prompt:
        parts.append(f"user: {prompt}")
    return "\n\n".join(parts)


def _trace_retrieval_snippets(ctx: TurnContext) -> list[str]:
    for key in ("retrieval_snippets", "retrieved_snippets"):
        snippets = _coerce_text_list(ctx.metadata.get(key))
        if snippets:
            return snippets

    snippets: list[str] = []
    for memory in ctx.retrieved_memories:
        if not isinstance(memory, Mapping):
            continue
        line = _memory_line(memory).strip()
        if line.startswith("- "):
            line = line[2:].strip()
        if line:
            snippets.append(line)
    return snippets


def _trace_model_provider(ctx: TurnContext) -> str | None:
    value = _first_metadata_value(
        ctx,
        "model_provider",
        "selected_provider_name",
        "provider_name",
        "default_provider",
    )
    if value is not None:
        text = str(value).strip()
        if text:
            return text

    env_provider = os.getenv("CHATBOT_PROVIDER")
    if env_provider:
        return env_provider

    model_id = _trace_model_id(ctx)
    if model_id and "/" in model_id:
        provider = model_id.split("/", 1)[0].strip()
        return provider or None
    return None


def _trace_provider_name(
    ctx: TurnContext,
    provider: Any | None,
    model_id: str | None,
) -> str | None:
    configured = _trace_model_provider(ctx)
    if configured:
        return configured

    for attribute in ("name", "provider_id", "id"):
        value = getattr(provider, attribute, None)
        if value:
            return str(value)

    if model_id and "/" in model_id:
        provider_id = model_id.split("/", 1)[0].strip()
        if provider_id:
            return provider_id

    if provider is not None:
        return provider.__class__.__name__
    return None


def _trace_model_id(ctx: TurnContext) -> str | None:
    value = _first_metadata_value(
        ctx,
        "model_id",
        "selected_model_id",
        "model",
        "default_model",
    )
    if value is not None:
        text = str(value).strip()
        if text:
            return text

    config = ctx.metadata.get("provider_config")
    if isinstance(config, ProviderConfig):
        return config.model or None
    if isinstance(config, Mapping):
        model = str(config.get("model") or "").strip()
        if model:
            return model
    return None


def _trace_token_counts(ctx: TurnContext, prompt: str) -> tuple[int, int]:
    usage = ctx.metadata.get("llm_usage")
    if not isinstance(usage, Mapping):
        usage = {}

    prompt_tokens = _first_int(
        ctx.metadata.get("prompt_tokens"),
        usage.get("prompt_tokens"),
        usage.get("input_tokens"),
    )
    completion_tokens = _first_int(
        ctx.metadata.get("completion_tokens"),
        usage.get("completion_tokens"),
        usage.get("output_tokens"),
    )

    if prompt_tokens is None:
        prompt_tokens = _estimate_tokens(prompt)
    if completion_tokens is None:
        completion_tokens = _estimate_tokens(
            str(ctx.provider_response or ctx.final_response or "")
        )
    return max(0, prompt_tokens), max(0, completion_tokens)


def _trace_cost_usd_micros(
    ctx: TurnContext,
    prompt_tokens: int,
    completion_tokens: int,
) -> int:
    direct_cost = _first_int(
        ctx.metadata.get("cost_usd_micros"),
        ctx.metadata.get("llm_cost_usd_micros"),
    )
    if direct_cost is not None:
        return max(0, direct_cost)

    usage = ctx.metadata.get("llm_usage")
    if isinstance(usage, Mapping):
        usage_cost = _first_int(
            usage.get("cost_usd_micros"),
            usage.get("cost_micros"),
        )
        if usage_cost is not None:
            return max(0, usage_cost)

    prompt_rate = _optional_float(ctx.metadata.get("prompt_cost_usd_micros_per_1k"))
    completion_rate = _optional_float(
        ctx.metadata.get("completion_cost_usd_micros_per_1k")
    )
    if prompt_rate is None and completion_rate is None:
        return 0
    prompt_cost = prompt_tokens * (prompt_rate or 0) / 1000
    completion_cost = completion_tokens * (completion_rate or 0) / 1000
    return max(0, int(round(prompt_cost + completion_cost)))


def _trace_routing_decision(
    ctx: TurnContext,
    model_provider: str | None,
    model_id: str | None,
) -> dict[str, Any]:
    routing_decision = ctx.metadata.get("routing_decision")
    if isinstance(routing_decision, Mapping):
        decision = dict(routing_decision)
    else:
        decision = {}

    decision.setdefault("provider", model_provider)
    decision.setdefault("model", model_id)
    decision.setdefault("task_class", _trace_task_class(ctx))
    return _json_safe(decision)


def _trace_total_latency_ms(ctx: TurnContext) -> float:
    total = 0.0
    for result in ctx.metadata.get("step_results", []):
        if isinstance(result, Mapping):
            duration = result.get("duration_ms")
        else:
            duration = getattr(result, "duration_ms", None)
        total += _coerce_float(duration, 0.0)
    return total


def _first_int(*values: Any) -> int | None:
    for value in values:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _estimate_tokens(text: str) -> int:
    compact = str(text or "").strip()
    if not compact:
        return 0
    return max(1, (len(compact) + 3) // 4)


def _step_name(step: TurnStep) -> str:
    name = getattr(step, "name", None)
    if isinstance(name, str) and name:
        return name
    return step.__class__.__name__


def _first_metadata_value(ctx: TurnContext, *keys: str) -> Any | None:
    for key in keys:
        value = ctx.metadata.get(key)
        if value is not None:
            return value
    return None


def _persona_cache_user_id(ctx: TurnContext) -> str | None:
    return _trace_user_id(ctx) or str(ctx.session_id)


def _persona_cache_companion_id(ctx: TurnContext) -> str | None:
    value = _first_metadata_value(ctx, "companion_id", "current_companion_id")
    if value is None:
        value = _read_session_field(ctx, "companion_id")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _deferred_turn_payload(ctx: TurnContext) -> dict[str, Any]:
    user_id = _trace_user_id(ctx)
    companion_id = _persona_cache_companion_id(ctx)
    turn_context = {
        "session_id": ctx.session_id,
        "user_message": ctx.user_message,
        "provider_response": ctx.provider_response,
        "user_id": user_id,
        "companion_id": companion_id,
    }
    return {
        "session_id": ctx.session_id,
        "user_id": user_id,
        "companion_id": companion_id,
        "user_message": ctx.user_message,
        "provider_response": ctx.provider_response,
        "turn_context": turn_context,
    }


def _background_task_type(step_name: str) -> str:
    if step_name == "extract_memory":
        return "memory_extract"
    if step_name == "consolidate":
        return "consolidate"
    return "custom"


async def _trigger_due_rituals(ctx: TurnContext) -> None:
    prompts = _metadata_text_list(ctx, "ritual_prompts")
    if get_due_rituals is None or trigger_ritual is None:
        return

    try:
        due_rituals = await get_due_rituals(ctx.session_id)
    except Exception:
        logger.warning(
            "Due ritual lookup failed for session %s",
            ctx.session_id,
            exc_info=True,
        )
        return

    for ritual in due_rituals:
        ritual_id = str(getattr(ritual, "ritual_id", "") or "").strip()
        if not ritual_id:
            continue
        try:
            trigger_prompt = await trigger_ritual(ritual_id)
        except Exception:
            logger.warning(
                "Ritual trigger failed for session %s",
                ctx.session_id,
                exc_info=True,
            )
            continue

        prompt = str(trigger_prompt or getattr(ritual, "trigger_prompt", "") or "").strip()
        if prompt:
            prompts.append(prompt)

    ctx.metadata["ritual_prompts"] = prompts


def _detect_timeline_events(ctx: TurnContext) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for source, text in (
        ("user", ctx.user_message),
        ("assistant", _timeline_response_text(ctx)),
    ):
        cleaned = str(text or "").strip()
        if not cleaned:
            continue
        events.extend(_timeline_events_for_text(cleaned, source))

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for event in events:
        key = (
            str(event["entry_type"]),
            _normalized_timeline_text(str(event["description"]))[:120],
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped[:8]


def _timeline_events_for_text(text: str, source: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    milestone = _first_signal_sentence(text, _MILESTONE_KEYWORDS, _MILESTONE_PATTERNS)
    if milestone is not None:
        sentence, signals = milestone
        events.append(
            _timeline_event(
                entry_type="milestone",
                source=source,
                sentence=sentence,
                signals=signals,
                tags=["milestone"],
            )
        )

    emotional = _first_emotional_sentence(text)
    if emotional is not None:
        sentence, signals, emotion_tags = emotional
        events.append(
            _timeline_event(
                entry_type="emotional_event",
                source=source,
                sentence=sentence,
                signals=signals,
                tags=["emotional", *emotion_tags],
            )
        )

    promise = _first_pattern_sentence(text, _PROMISE_PATTERNS)
    if promise is not None:
        sentence, signals = promise
        events.append(
            _timeline_event(
                entry_type="promise",
                source=source,
                sentence=sentence,
                signals=signals,
                tags=["promise"],
            )
        )

    project = _first_project_sentence(text)
    if project is not None:
        sentence, signals, project_tags = project
        events.append(
            _timeline_event(
                entry_type="running_project",
                source=source,
                sentence=sentence,
                signals=signals,
                tags=["project", *project_tags],
            )
        )

    return events


def _timeline_event(
    *,
    entry_type: str,
    source: str,
    sentence: str,
    signals: list[str],
    tags: list[str],
) -> dict[str, Any]:
    description = _compact_timeline_text(sentence, max_chars=500)
    return {
        "entry_type": entry_type,
        "title": _timeline_title(entry_type, description),
        "description": description,
        "signals": signals,
        "tags": _unique_texts(["turn_pipeline", source, *tags]),
    }


def _first_signal_sentence(
    text: str,
    keywords: Sequence[str],
    patterns: Sequence[tuple[str, str]],
) -> tuple[str, list[str]] | None:
    for sentence in _timeline_sentences(text):
        signals = _keyword_matches(sentence, keywords)
        signals.extend(_pattern_matches(sentence, patterns))
        if signals:
            return sentence, _unique_texts(signals)
    return None


def _first_pattern_sentence(
    text: str,
    patterns: Sequence[tuple[str, str]],
) -> tuple[str, list[str]] | None:
    for sentence in _timeline_sentences(text):
        signals = _pattern_matches(sentence, patterns)
        if signals:
            return sentence, _unique_texts(signals)
    return None


def _first_emotional_sentence(text: str) -> tuple[str, list[str], list[str]] | None:
    for sentence in _timeline_sentences(text):
        signals: list[str] = []
        tags: list[str] = []
        for tag, keywords in _EMOTIONAL_KEYWORDS.items():
            matches = _keyword_matches(sentence, keywords)
            if matches:
                signals.extend(matches)
                tags.append(f"emotion_{tag}")
        if signals:
            return sentence, _unique_texts(signals), _unique_texts(tags)
    return None


def _first_project_sentence(text: str) -> tuple[str, list[str], list[str]] | None:
    for sentence in _timeline_sentences(text):
        project_terms = _keyword_matches(sentence, _PROJECT_KEYWORDS)
        action_terms = _pattern_matches(sentence, _PROJECT_PATTERNS)
        if not project_terms and not action_terms:
            continue
        if action_terms or project_terms:
            tags = [f"project_{_tag_text(term)}" for term in project_terms[:3]]
            return sentence, _unique_texts([*project_terms, *action_terms]), tags
    return None


def _timeline_sentences(text: str) -> list[str]:
    compact = _compact_timeline_text(text, max_chars=1200)
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", compact)
        if sentence.strip()
    ]
    return sentences or ([compact] if compact else [])


def _keyword_matches(text: str, keywords: Sequence[str]) -> list[str]:
    matches: list[str] = []
    normalized = _normalized_timeline_text(text)
    for keyword in keywords:
        normalized_keyword = _normalized_timeline_text(keyword)
        if not normalized_keyword:
            continue
        if " " in normalized_keyword:
            if normalized_keyword in normalized:
                matches.append(keyword)
            continue
        if re.search(rf"\b{re.escape(normalized_keyword)}\b", normalized):
            matches.append(keyword)
    return matches


def _pattern_matches(text: str, patterns: Sequence[tuple[str, str]]) -> list[str]:
    matches: list[str] = []
    for label, pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            matches.append(label)
    return matches


def _timeline_title(entry_type: str, description: str) -> str:
    labels = {
        "milestone": "Milestone",
        "emotional_event": "Emotional event",
        "promise": "Promise",
        "running_project": "Project",
    }
    label = labels.get(entry_type, "Timeline")
    summary = _compact_timeline_text(description, max_chars=72)
    if not summary:
        return label
    return _compact_timeline_text(f"{label}: {summary}", max_chars=96)


def _timeline_response_text(ctx: TurnContext) -> str:
    final_response = str(ctx.final_response or "").strip()
    if final_response:
        return final_response
    return str(ctx.provider_response or "").strip()


def _timeline_turn_content(ctx: TurnContext) -> str:
    parts: list[str] = []
    user_message = str(ctx.user_message or "").strip()
    response = _timeline_response_text(ctx)
    if user_message:
        parts.append(f"User: {user_message}")
    if response:
        parts.append(f"Assistant: {response}")
    return "\n\n".join(parts)


def _timeline_turn_id(ctx: TurnContext) -> str:
    for key in ("turn_id", "current_turn_id"):
        value = ctx.metadata.get(key)
        if value:
            return str(value)

    turn_id = str(uuid.uuid4())
    ctx.metadata["turn_id"] = turn_id
    return turn_id


def _metadata_text_list(ctx: TurnContext, key: str) -> list[str]:
    value = ctx.metadata.get(key)
    if value is None:
        items: list[str] = []
    elif isinstance(value, (str, bytes)):
        text = value.decode() if isinstance(value, bytes) else value
        items = [text] if text.strip() else []
    else:
        try:
            raw_items = list(value)
        except TypeError:
            raw_items = [value]
        items = [str(item) for item in raw_items if str(item).strip()]

    ctx.metadata[key] = items
    return items


def _normalized_timeline_text(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _compact_timeline_text(text: str, *, max_chars: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _tag_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _normalized_timeline_text(text)).strip("_")


def _unique_texts(values: Sequence[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


def _store_retrieved_memories(ctx: TurnContext) -> None:
    memories = list(ctx.retrieved_memories)
    ctx.metadata["retrieved_memories"] = memories
    ctx.metadata["retrieved_memory"] = memories


async def _persona_identity_inputs(ctx: TurnContext) -> tuple[Any | None, Any | None]:
    character_profile = _current_character_profile(ctx)
    identity_state = _current_identity_state(ctx)

    db_session = _db_session(ctx)
    session_id = _coerce_uuid(ctx.session_id)
    if db_session is not None and session_id is not None:
        if character_profile is None:
            character_profile = await _load_character_profile(db_session, session_id)
        if identity_state is None:
            identity_state = await _load_identity_state(db_session, session_id)

    if character_profile is not None:
        ctx.metadata["character_profile"] = character_profile
    if identity_state is not None:
        ctx.metadata["identity_state"] = identity_state
    return character_profile, identity_state


def _current_character_profile(ctx: TurnContext) -> Any | None:
    for value in (
        _first_metadata_value(
            ctx,
            "character_profile",
            "current_character_profile",
            "persona_character_profile",
            "character_profile_model",
        ),
        _read_session_field(ctx, "character_profile"),
        _read_session_field(ctx, "character_profiles"),
    ):
        profile = _coerce_character_profile(value)
        if profile is not None:
            return profile
    return None


def _current_identity_state(ctx: TurnContext) -> Any | None:
    for value in (
        _first_metadata_value(
            ctx,
            "identity_state",
            "current_identity_state",
            "persona_identity_state",
            "identity_state_model",
        ),
        _read_session_field(ctx, "identity_state"),
        _read_session_field(ctx, "identity_states"),
    ):
        state = _coerce_identity_state(value)
        if state is not None:
            return state
    return None


def _coerce_character_profile(value: Any) -> Any | None:
    if value is None:
        return None
    if CharacterProfile is not None and isinstance(value, CharacterProfile):
        return value
    if CharacterProfileModel is not None and isinstance(value, CharacterProfileModel):
        return _character_profile_from_model(value)
    if isinstance(value, Mapping):
        return _character_profile_from_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in reversed(list(value)):
            profile = _coerce_character_profile(item)
            if profile is not None:
                return profile
        return None
    if all(
        hasattr(value, field)
        for field in (
            "name",
            "backstory",
            "personality_sliders",
            "humor_style",
            "communication_style",
            "favorite_topics",
        )
    ):
        return _character_profile_from_model(value)
    return None


def _coerce_identity_state(value: Any) -> Any | None:
    if value is None:
        return None
    if IdentityState is not None and isinstance(value, IdentityState):
        return value
    if IdentityStateModel is not None and isinstance(value, IdentityStateModel):
        return _identity_state_from_model(value)
    if isinstance(value, Mapping):
        return _identity_state_from_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in reversed(list(value)):
            state = _coerce_identity_state(item)
            if state is not None:
                return state
        return None
    if all(hasattr(value, field) for field in ("fixed", "slow", "mood")):
        return _identity_state_from_model(value)
    return None


def _character_profile_from_model(model: Any) -> Any | None:
    if CharacterProfile is None:
        return None
    try:
        return CharacterProfile(
            name=getattr(model, "name"),
            backstory=getattr(model, "backstory"),
            personality_sliders=dict(getattr(model, "personality_sliders")),
            humor_style=getattr(model, "humor_style"),
            communication_style=getattr(model, "communication_style"),
            favorite_topics=list(getattr(model, "favorite_topics")),
        )
    except Exception:
        logger.warning("Character profile conversion failed", exc_info=True)
        return None


def _character_profile_from_mapping(value: Mapping[str, Any]) -> Any | None:
    if CharacterProfile is None:
        return None
    try:
        defaults = CharacterProfile.defaults()
        return CharacterProfile(
            name=str(value.get("name", defaults.name)),
            backstory=str(value.get("backstory", defaults.backstory)),
            personality_sliders=dict(
                value.get("personality_sliders", defaults.personality_sliders)
            ),
            humor_style=str(value.get("humor_style", defaults.humor_style)),
            communication_style=str(
                value.get("communication_style", defaults.communication_style)
            ),
            favorite_topics=list(value.get("favorite_topics", defaults.favorite_topics)),
        )
    except Exception:
        logger.warning("Character profile mapping conversion failed", exc_info=True)
        return None


def _identity_state_from_model(model: Any) -> Any | None:
    if IdentityState is None:
        return None
    try:
        return IdentityState(
            fixed=dict(getattr(model, "fixed")),
            slow=dict(getattr(model, "slow")),
            mood=dict(getattr(model, "mood")),
        )
    except Exception:
        logger.warning("Identity state conversion failed", exc_info=True)
        return None


def _identity_state_from_mapping(value: Mapping[str, Any]) -> Any | None:
    if IdentityState is None:
        return None
    try:
        defaults = IdentityState.defaults()
        return IdentityState(
            fixed=dict(value.get("fixed", defaults.fixed)),
            slow=dict(value.get("slow", defaults.slow)),
            mood=dict(value.get("mood", defaults.mood)),
        )
    except Exception:
        logger.warning("Identity state mapping conversion failed", exc_info=True)
        return None


async def _load_character_profile(db_session: Any, session_id: uuid.UUID) -> Any | None:
    if CharacterProfileModel is None:
        return None
    try:
        import sqlalchemy as sa

        result = await _maybe_await(
            db_session.execute(
                sa.select(CharacterProfileModel)
                .where(CharacterProfileModel.session_id == session_id)
                .order_by(
                    CharacterProfileModel.updated_at.desc(),
                    CharacterProfileModel.created_at.desc(),
                )
                .limit(1)
            )
        )
        scalar = getattr(result, "scalar_one_or_none", None)
        if callable(scalar):
            return _coerce_character_profile(scalar())
    except Exception:
        logger.warning("Character profile DB load failed", exc_info=True)
    return None


async def _load_identity_state(db_session: Any, session_id: uuid.UUID) -> Any | None:
    if IdentityStateModel is None:
        return None
    try:
        import sqlalchemy as sa

        result = await _maybe_await(
            db_session.execute(
                sa.select(IdentityStateModel)
                .where(IdentityStateModel.session_id == session_id)
                .order_by(IdentityStateModel.last_updated.desc())
                .limit(1)
            )
        )
        scalar = getattr(result, "scalar_one_or_none", None)
        if callable(scalar):
            return _coerce_identity_state(scalar())
    except Exception:
        logger.warning("Identity state DB load failed", exc_info=True)
    return None


def _assemble_persona_prompt(
    assembler: Any | None,
    persona: dict[str, Any],
    *,
    mood: Any | None = None,
    mode: Any | None = None,
    character_profile: Any | None = None,
    identity_state: Any | None = None,
) -> str:
    if assembler is None:
        logger.warning("Persona prompt assembler is unavailable; using fallback persona")
        return "You are a helpful, neutral assistant. Respond clearly and accurately."

    try:
        return str(
            assembler.assemble(
                persona,
                mood=mood,
                mode=mode,
                character_profile=character_profile,
                identity_state=identity_state,
            )
        )
    except TypeError:
        try:
            return str(assembler.assemble(persona, mood=mood, mode=mode))
        except TypeError:
            try:
                return str(assembler.assemble(persona))
            except Exception:
                logger.warning(
                    "Persona prompt assembly failed; using fallback persona",
                    exc_info=True,
                )
        except Exception:
            logger.warning(
                "Persona prompt assembly failed; using fallback persona",
                exc_info=True,
            )
    except Exception:
        logger.warning(
            "Persona prompt assembly failed; using fallback persona",
            exc_info=True,
        )
        return "You are a helpful, neutral assistant. Respond clearly and accurately."
    return "You are a helpful, neutral assistant. Respond clearly and accurately."


def _compile_context_text(
    *,
    persona_prompt: str,
    history: Sequence[Mapping[str, Any]],
    retrieved_memories: Sequence[Mapping[str, Any]],
) -> str:
    sections: list[str] = []
    if persona_prompt.strip():
        sections.append(persona_prompt.strip())

    memory_lines = [_memory_line(memory) for memory in retrieved_memories]
    memory_lines = [line for line in memory_lines if line]
    if memory_lines:
        sections.append("Retrieved memories:\n" + "\n".join(memory_lines))

    history_lines = [_message_line(message) for message in history]
    history_lines = [line for line in history_lines if line]
    if history_lines:
        sections.append("Conversation history:\n" + "\n".join(history_lines))

    return "\n\n".join(sections)


def _message_line(message: Mapping[str, Any]) -> str:
    role = str(message.get("role") or "message").strip() or "message"
    content = str(message.get("content") or "").strip()
    if not content:
        return ""
    return f"{role}: {content}"


def _memory_line(memory: Mapping[str, Any]) -> str:
    content = memory.get("content")
    if content is None:
        content = memory.get("summary")
    if content is None:
        content = memory.get("fact_text")
    text = str(content or "").strip()
    if not text:
        return ""
    memory_type = str(memory.get("type") or memory.get("kind") or "memory").strip()
    return f"- {memory_type}: {text}" if memory_type else f"- {text}"


def _coerce_memory_list(value: Any) -> list[dict]:
    if value is None or isinstance(value, (str, bytes)):
        return []

    try:
        items = list(value)
    except TypeError:
        return []

    memories: list[dict] = []
    for item in items:
        if isinstance(item, Mapping):
            memories.append(dict(item))
            continue
        as_dict = getattr(item, "as_dict", None)
        if callable(as_dict):
            try:
                mapped = as_dict()
            except Exception:
                mapped = None
            if isinstance(mapped, Mapping):
                memories.append(dict(mapped))
                continue
        content = getattr(item, "content", None)
        if content is None:
            turn = getattr(item, "turn", None)
            content = getattr(turn, "content", None)
        if content is not None:
            memories.append({"content": str(content)})
    return memories


def _select_provider(ctx: TurnContext) -> Any | None:
    provider = ctx.metadata.get("provider")
    if provider is not None:
        return provider

    router = ctx.metadata.get("provider_router")
    config = _provider_config(ctx)
    if router is not None and hasattr(router, "select"):
        try:
            return router.select(ctx.user_message, config)
        except Exception:
            logger.warning("Provider router selection failed", exc_info=True)
            return None

    registry = ctx.metadata.get("provider_registry")
    if registry is None:
        logger.warning("Provider registry is unavailable; using local provider fallback")
        return None

    if ProviderRouter is not None:
        policy = ctx.metadata.get("provider_policy")
        try:
            return ProviderRouter(registry, policy).select(ctx.user_message, config)
        except Exception:
            logger.warning("Provider router selection failed", exc_info=True)

    provider_name = str(ctx.metadata.get("provider_name") or ctx.metadata.get("default_provider") or "openrouter")
    get_provider = getattr(registry, "get", None)
    if callable(get_provider):
        try:
            return get_provider(provider_name)
        except Exception:
            logger.warning("Provider lookup failed for %s", provider_name, exc_info=True)

    return None


def _provider_config(ctx: TurnContext) -> ProviderConfig:
    config = ctx.metadata.get("provider_config")
    if isinstance(config, ProviderConfig):
        return config

    if isinstance(config, Mapping):
        model = str(config.get("model") or "")
        extra = config.get("extra") if isinstance(config.get("extra"), Mapping) else {}
        return ProviderConfig(
            model=model,
            temperature=_optional_float(config.get("temperature")),
            max_tokens=_optional_int(config.get("max_tokens")),
            top_p=_optional_float(config.get("top_p")),
            stop=_optional_stop(config.get("stop")),
            timeout_seconds=_optional_float(config.get("timeout_seconds")),
            extra=dict(extra),
        )

    return ProviderConfig(
        model=str(ctx.metadata.get("model") or ctx.metadata.get("default_model") or ""),
        temperature=_optional_float(ctx.metadata.get("temperature")),
        max_tokens=_optional_int(ctx.metadata.get("max_tokens")),
        top_p=_optional_float(ctx.metadata.get("top_p")),
        stop=_optional_stop(ctx.metadata.get("stop")),
        timeout_seconds=_optional_float(ctx.metadata.get("timeout_seconds")),
    )


def _provider_history(ctx: TurnContext) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    if ctx.compiled_context:
        history.append({"role": "system", "content": ctx.compiled_context})

    for message in ctx.history:
        if not isinstance(message, Mapping):
            continue
        role = message.get("role")
        content = message.get("content")
        if role in {"system", "user", "assistant", "tool"} and content is not None:
            history.append({"role": str(role), "content": str(content)})

    return history


def _fallback_provider_response(ctx: TurnContext) -> str:
    message = ctx.user_message.strip()
    if not message:
        return "I received an empty message."
    return f"I received your message and no provider backend is configured: {message}"


def _emotional_intelligence_ready(ctx: TurnContext) -> bool:
    if not _emotional_intelligence_imports_available():
        return False
    if not _emotional_intelligence_enabled(ctx):
        return False
    for key in (
        "mood_engine",
        "bot_mood_engine",
        "user_state_engine",
        "mode_selector",
        "conversation_mode_selector",
    ):
        value = ctx.metadata.get(key)
        if key in ctx.metadata and (value is None or value is False):
            return False
    return True


def _emotional_intelligence_imports_available() -> bool:
    return (
        MoodEngine is not None
        and MoodState is not None
        and UserStateEngine is not None
        and UserState is not None
        and ModeSelector is not None
        and ConversationMode is not None
    )


def _emotional_intelligence_enabled(ctx: TurnContext) -> bool:
    for key in (
        "emotional_intelligence_enabled",
        "enable_emotional_intelligence",
        "soul_enabled",
    ):
        if key in ctx.metadata:
            return _coerce_bool(ctx.metadata[key], default=True)

    return _coerce_bool(
        os.getenv("CHATBOT_EMOTIONAL_INTELLIGENCE_ENABLED"),
        default=True,
    )


def _configured_component(
    ctx: TurnContext,
    keys: Sequence[str],
    default_cls: Any | None,
) -> Any | None:
    for key in keys:
        if key not in ctx.metadata:
            continue
        configured = ctx.metadata[key]
        if configured is None or configured is False:
            return None
        if isinstance(configured, type):
            try:
                return configured()
            except Exception:
                logger.warning("Configured component %s failed to initialize", key, exc_info=True)
                return None
        return configured

    if default_cls is None:
        return None
    try:
        return default_cls()
    except Exception:
        logger.warning("Default component failed to initialize", exc_info=True)
        return None


def _current_mood_state(ctx: TurnContext, mood_engine: Any | None = None) -> Any | None:
    state = _coerce_mood_state(ctx.metadata.get("mood_state"))
    if state is not None:
        return state

    state = _coerce_mood_state(_read_session_field(ctx, "mood_state"))
    if state is not None:
        ctx.metadata["mood_state"] = state
        return state

    if mood_engine is None and MoodEngine is not None:
        mood_engine = _configured_component(
            ctx,
            ("mood_engine", "bot_mood_engine"),
            MoodEngine,
        )
    default_state = getattr(mood_engine, "default_state", None)
    if callable(default_state):
        try:
            state = default_state()
        except Exception:
            logger.warning("Default mood state creation failed", exc_info=True)
            return None
        ctx.metadata["mood_state"] = state
        return state

    return None


def _current_user_state(ctx: TurnContext) -> Any | None:
    state = _coerce_user_state(ctx.metadata.get("user_state"))
    if state is not None:
        return state

    state = _coerce_user_state(_read_session_field(ctx, "user_state"))
    if state is not None:
        ctx.metadata["user_state"] = state
    return state


def _current_conversation_mode(ctx: TurnContext) -> Any | None:
    mode = _coerce_conversation_mode(ctx.metadata.get("conversation_mode"))
    if mode is not None:
        return mode

    mode = _coerce_conversation_mode(_read_session_field(ctx, "conversation_mode"))
    if mode is not None:
        ctx.metadata["conversation_mode"] = mode
    return mode


def _recent_turn_messages(ctx: TurnContext) -> list[dict]:
    messages: list[dict] = []
    for message in ctx.history:
        coerced = _coerce_message(message)
        if coerced is not None:
            messages.append(coerced)

    if ctx.user_message.strip():
        messages.append({"role": "user", "content": ctx.user_message.strip()})
    return messages[-12:]


def _coerce_message(message: Any) -> dict[str, str] | None:
    if isinstance(message, Mapping):
        role = message.get("role")
        content = message.get("content")
    else:
        role = getattr(message, "role", None)
        content = getattr(message, "content", None)

    if role is None or content is None:
        return None
    role_text = str(role).strip()
    content_text = str(content).strip()
    if not role_text or not content_text:
        return None
    return {"role": role_text, "content": content_text}


def _current_topics(ctx: TurnContext) -> list[str]:
    for key in ("topics", "current_topics", "topic"):
        topics = _coerce_text_list(ctx.metadata.get(key))
        if topics:
            return topics
    return []


def _read_session_field(ctx: TurnContext, field_name: str) -> Any | None:
    if field_name in ctx.metadata and ctx.metadata[field_name] is not None:
        return ctx.metadata[field_name]

    for key in ("session_record", "db_session_record", "session_model", "session"):
        session = ctx.metadata.get(key)
        value = _read_field(session, field_name)
        if value is not None:
            return value

    for key in ("session_store", "sessions_store"):
        session_store = ctx.metadata.get(key)
        if isinstance(session_store, Mapping):
            value = _read_field(session_store.get(ctx.session_id), field_name)
            if value is not None:
                return value
    return None


def _read_field(source: Any, field_name: str) -> Any | None:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(field_name)
    return getattr(source, field_name, None)


def _coerce_mood_state(value: Any) -> Any | None:
    if value is None or MoodState is None:
        return None
    if isinstance(value, MoodState):
        return value
    if not isinstance(value, Mapping):
        return None

    try:
        return MoodState(
            mood=str(value.get("mood") or "warm"),
            energy=_coerce_float(value.get("energy"), 0.5),
            closeness=_coerce_float(value.get("closeness"), 0.0),
            last_updated=_coerce_datetime(value.get("last_updated")),
            recent_emotional_context=_coerce_text_list(
                value.get("recent_emotional_context")
            ),
            turns_since_mood_shift=_coerce_int(
                value.get("turns_since_mood_shift"),
                0,
            ),
        )
    except Exception:
        logger.warning("Stored mood state could not be loaded", exc_info=True)
        return None


def _coerce_user_state(value: Any) -> Any | None:
    if value is None or UserState is None:
        return None
    if isinstance(value, UserState):
        return value
    if not isinstance(value, Mapping):
        return None

    primary_state = value.get("primary_state")
    if primary_state is not None:
        primary_state = str(primary_state).strip() or None

    try:
        return UserState(
            primary_state=primary_state,
            confidence=_coerce_float(value.get("confidence"), 0.0),
            secondary_states=_coerce_secondary_states(value.get("secondary_states")),
            evidence_summary=str(value.get("evidence_summary") or ""),
            last_inferred=_coerce_datetime(value.get("last_inferred")),
            inference_count=_coerce_int(value.get("inference_count"), 0),
            confidence_history=tuple(
                _coerce_float(item, 0.0)
                for item in _coerce_sequence(value.get("confidence_history"))
            ),
        )
    except Exception:
        logger.warning("Stored user state could not be loaded", exc_info=True)
        return None


def _coerce_conversation_mode(value: Any) -> Any | None:
    if value is None or ConversationMode is None:
        return None
    if isinstance(value, ConversationMode):
        return value
    if isinstance(value, Enum):
        value = value.value
    text = str(value).strip()
    if not text:
        return None
    try:
        return ConversationMode(text)
    except Exception:
        logger.warning("Stored conversation mode could not be loaded: %s", text)
        return None


def _coerce_secondary_states(value: Any) -> list[tuple[str, float]]:
    states: list[tuple[str, float]] = []
    for item in _coerce_sequence(value):
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes)):
            continue
        if len(item) < 2:
            continue
        state = str(item[0]).strip()
        if state:
            states.append((state, _coerce_float(item[1], 0.0)))
    return states


def _persist_session_mapping(
    ctx: TurnContext,
    mood_state: Any | None,
    user_state: Any | None,
    mode: Any | None,
) -> None:
    values = _emotional_state_values(mood_state, user_state, mode)

    session = ctx.metadata.get("session")
    if isinstance(session, dict):
        session.update(values)

    for key in ("session_store", "sessions_store"):
        session_store = ctx.metadata.get(key)
        if isinstance(session_store, dict):
            stored_session = session_store.get(ctx.session_id)
            if isinstance(stored_session, dict):
                stored_session.update(values)


async def _persist_db_emotional_state(
    ctx: TurnContext,
    mood_state: Any | None,
    user_state: Any | None,
    mode: Any | None,
) -> None:
    db_session = _db_session(ctx)
    if db_session is None or Session is None:
        return

    coerced_session_id = _coerce_uuid(ctx.session_id)
    if coerced_session_id is None:
        logger.warning(
            "Skipping emotional state DB persistence for non-UUID session %s",
            ctx.session_id,
        )
        return

    try:
        session_record = await _load_db_session_record(
            db_session,
            coerced_session_id,
            ctx,
        )
        if session_record is None:
            logger.warning(
                "Skipping emotional state DB persistence; session %s was not found",
                ctx.session_id,
            )
            return

        values = _emotional_state_values(mood_state, user_state, mode)
        if "mood_state" in values:
            session_record.mood_state = values["mood_state"]
        if "user_state" in values:
            session_record.user_state = values["user_state"]
        if "conversation_mode" in values:
            session_record.conversation_mode = values["conversation_mode"]

        if BotMoodLog is not None and mood_state is not None:
            db_session.add(
                BotMoodLog(
                    session_id=coerced_session_id,
                    mood=str(getattr(mood_state, "mood")),
                    energy=float(getattr(mood_state, "energy")),
                    closeness=float(getattr(mood_state, "closeness")),
                )
            )

        if UserStateLog is not None and user_state is not None:
            db_session.add(
                UserStateLog(
                    session_id=coerced_session_id,
                    primary_state=getattr(user_state, "primary_state", None),
                    confidence=float(getattr(user_state, "confidence", 0.0)),
                    evidence_summary=getattr(user_state, "evidence_summary", None),
                )
            )

        await _flush_or_commit(db_session)
    except Exception:
        logger.warning(
            "Emotional state persistence failed for session %s",
            ctx.session_id,
            exc_info=True,
        )


async def _load_db_session_record(
    db_session: Any,
    session_id: uuid.UUID,
    ctx: TurnContext,
) -> Any | None:
    for key in ("session_record", "db_session_record", "session_model", "session"):
        candidate = ctx.metadata.get(key)
        if Session is not None and isinstance(candidate, Session):
            return candidate

    get_method = getattr(db_session, "get", None)
    if callable(get_method) and Session is not None:
        try:
            record = await _maybe_await(get_method(Session, session_id))
            if record is not None:
                return record
        except Exception:
            logger.warning("Session.get failed for emotional state load", exc_info=True)

    try:
        import sqlalchemy as sa

        result = await _maybe_await(
            db_session.execute(sa.select(Session).where(Session.id == session_id))
        )
        scalar = getattr(result, "scalar_one_or_none", None)
        if callable(scalar):
            return scalar()
    except Exception:
        logger.warning("Session select failed for emotional state load", exc_info=True)
    return None


def _emotional_state_values(
    mood_state: Any | None,
    user_state: Any | None,
    mode: Any | None,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if mood_state is not None:
        values["mood_state"] = _json_safe(mood_state)
    if user_state is not None:
        values["user_state"] = _json_safe(user_state)
    if mode is not None:
        values["conversation_mode"] = _mode_value(mode)
    return values


def _db_session(ctx: TurnContext) -> Any | None:
    for key in (
        "db_session",
        "database_session",
        "database",
        "db",
        "memory_database",
        "working_memory_store",
    ):
        candidate = ctx.metadata.get(key)
        session = _extract_db_session(candidate)
        if session is not None:
            return session
    return None


def _extract_db_session(candidate: Any) -> Any | None:
    if candidate is None:
        return None
    if hasattr(candidate, "execute") and (hasattr(candidate, "add") or hasattr(candidate, "get")):
        return candidate

    session = getattr(candidate, "session", None)
    if session is not None and hasattr(session, "execute"):
        return session
    return None


async def _flush_or_commit(db_session: Any) -> None:
    in_transaction = getattr(db_session, "in_transaction", None)
    if callable(in_transaction) and in_transaction():
        flush = getattr(db_session, "flush", None)
        if callable(flush):
            await _maybe_await(flush())
        return

    commit = getattr(db_session, "commit", None)
    if callable(commit):
        await _maybe_await(commit())
        return

    flush = getattr(db_session, "flush", None)
    if callable(flush):
        await _maybe_await(flush())


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _mode_value(mode: Any | None) -> str | None:
    if mode is None:
        return None
    if isinstance(mode, Enum):
        return str(mode.value)
    return str(mode)


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    return default


def _coerce_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return [text for text in (str(item).strip() for item in _coerce_sequence(value)) if text]


def _coerce_sequence(value: Any) -> list[Any]:
    if value is None or isinstance(value, (str, bytes)):
        return []
    try:
        return list(value)
    except TypeError:
        return []


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            return _coerce_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_stop(value: Any) -> Sequence[str] | None:
    if value is None or isinstance(value, (str, bytes)):
        return None
    if not isinstance(value, Sequence):
        return None
    return [str(item) for item in value]


__all__ = [
    "DEFERRED_STEPS",
    "FAST_PATH_STEPS",
    "TurnPipeline",
    "build_default_pipeline",
]
