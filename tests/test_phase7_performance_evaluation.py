"""Phase 7.2b — performance and cost evaluation invariants.

The *measurements* live in ``scripts/phase7_measurements.py`` (one controlled
pass writes ``docs/verification/phase7_performance_metrics.json``); this file
asserts the structural and cost invariants those measurements must keep
holding, with generous timing ceilings so they catch gross regressions without
being flaky. Providers are scripted; the database is throwaway SQLite (a
disposable PostgreSQL is used only by the script when ``PHASE0_PG_URL`` is set).
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chatbot.db import models
from chatbot.api.conversation import load_companion_inputs
from chatbot.memory.llm_extraction import LLMExtractionLimits, LLMMemoryExtractor
from chatbot.soul.mood import MoodEngine
from chatbot.soul.mood_continuity import load_restorable_mood, restore_mood
from chatbot.soul.emotional_associations import (
    render_associations,
    retrieve_emotional_associations,
)
from chatbot.soul.pacing import calculate_momentum, get_pacing_directive
from phase7_helpers import (
    COMPANION_A,
    QueryCounter,
    corpus_statistics,
    create_scenario_tables,
    estimate_tokens,
    seed_scenario,
)

USER_MESSAGE = "I finally shipped the treehouse app this weekend! So happy with how it turned out."


class ScriptedProvider:
    def __init__(self, response: str, finish_reason: str = "stop") -> None:
        self.response = response
        self.finish_reason = finish_reason
        self.calls: list[str] = []

    async def complete(self, prompt, history, config):
        self.calls.append(prompt)
        from chatbot.providers.base import ProviderResponse

        return ProviderResponse(text=self.response, finish_reason=self.finish_reason,
                                model="scripted")


class _Registry:
    def __init__(self, provider) -> None:
        self._provider = provider

    def get(self, _name):
        return self._provider


EXTRACTION_RESPONSE = json.dumps(
    {
        "memories": [
            {"type": "personal_fact", "content": "User shipped the treehouse app.",
             "evidence": "I finally shipped the treehouse app", "status": "real",
             "confidence": 0.9, "temporal": "current", "corrects": None}
        ],
        "experiences": [
            {"title": "Shipped the treehouse app",
             "narrative": "User shipped the treehouse app this weekend.",
             "participants": ["user", "companion"], "significance": "significant",
             "evidence": "I finally shipped the treehouse app", "status": "real",
             "origin": "conversation", "topics": ["work"], "revises": None, "confidence": 0.8}
        ],
        "self": [
            {"kind": "opinion", "key": None, "subject": "late nights",
             "statement": "I find late-night coding counterproductive.",
             "reason": "I think clearly in the morning",
             "evidence": "I do find late-night coding overrated myself", "status": "real",
             "significance": "ordinary", "relation": "consistent", "change_of_mind": False}
        ],
        "threads": [
            {"ownership": "shared", "kind": "subject", "key": None,
             "subject": "The shipped treehouse app", "topic": "project",
             "status": "open", "importance": "significant",
             "evidence": "I finally shipped the treehouse app", "supersedes": None}
        ],
        "emotions": [
            {"kind": "emotional_association", "key": None, "subject": "the treehouse app",
             "emotion": "pride", "source": "user_reported",
             "interpretation": "Happy about finishing the app.",
             "evidence": "I finally shipped the treehouse app this weekend", "status": "real",
             "significance": "significant", "unresolved": False, "revises": None,
             "confidence": 0.8}
        ],
    },
    ensure_ascii=False,
)

_TABLES = [
    models.User, models.Companion, models.CompanionIdentityModel,
    models.CompanionRelationshipModel, models.Room, models.Session, models.Message,
    models.SemanticFact, models.ConversationalThread, models.BotMoodLog,
    models.IdentityStateModel, models.BackgroundJob, models.Episode,
    models.MemoryEvidence, models.MemoryRelation,
]


@pytest.fixture
async def env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'perf.db'}")
    await create_scenario_tables(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    scenario = await seed_scenario(factory)
    yield engine, factory, scenario
    await engine.dispose()


# --------------------------------------------------------------------------
# Provider call counts and retry cost (A18 / A10: no duplicate processing)
# --------------------------------------------------------------------------


async def test_one_extraction_job_costs_exactly_one_provider_call(env):
    engine, factory, scenario = env
    from chatbot.config import Settings
    from chatbot.perf.durable_handlers import make_durable_memory_extract_handler

    provider = ScriptedProvider(EXTRACTION_RESPONSE)
    handler = make_durable_memory_extract_handler(
        factory, registry=_Registry(provider), settings=Settings(_env_file=None))
    session_id = uuid.UUID(scenario["session_id"])

    async def seed_and_run(turn_id: str):
        async with factory() as db:
            user_id = await db.scalar(
                sa.select(models.Session.user_id).where(models.Session.id == session_id))
            db.add(models.Message(session_id=session_id, user_id=user_id, role="user",
                                  content=USER_MESSAGE, turn_id=turn_id))
            db.add(models.Message(session_id=session_id, user_id=user_id, role="assistant",
                                  content="Congratulations!", turn_id=turn_id,
                                  companion_id=COMPANION_A))
            job = models.BackgroundJob(
                session_id=session_id, user_id=user_id, companion_id=COMPANION_A,
                task_type="memory_extract", turn_id=turn_id,
                payload={"session_id": str(session_id), "user_id": str(user_id),
                         "companion_id": COMPANION_A, "turn_id": turn_id})
            db.add(job)
            await db.commit()
            job_id = job.job_id

        class _Job:
            pass

        async with factory() as db:
            stored = await db.get(models.BackgroundJob, job_id)
            job = _Job()
            for field in ("job_id", "session_id", "user_id", "companion_id",
                          "turn_id", "attempt_count", "payload"):
                setattr(job, field, getattr(stored, field, None))
        await handler(job)
        return job_id

    await seed_and_run("cost-turn-1")
    provider.calls.clear()
    job_id = await seed_and_run("cost-turn-2")
    assert len(provider.calls) == 1  # one model call per completed turn's job

    # Retrying the same job adds no provider call and no duplicate effects.
    async with factory() as db:
        stored = await db.get(models.BackgroundJob, job_id)
        assert stored.status == "succeeded"
        job = stored
        payload = dict(stored.payload)
        turn_id = stored.turn_id
    class _Job:
        pass
    replay = _Job()
    for field in ("job_id", "session_id", "user_id", "companion_id", "turn_id",
                  "attempt_count", "payload"):
        setattr(replay, field, getattr(job, field, None))
    async with factory() as db:
        facts_before = (await db.scalars(sa.select(models.SemanticFact))).all()
    provider.calls.clear()
    await handler(replay)
    assert len(provider.calls) == 0
    async with factory() as db:
        facts_after = (await db.scalars(sa.select(models.SemanticFact))).all()
    assert len(facts_after) == len(facts_before)  # no duplicate effects from the replay


# --------------------------------------------------------------------------
# Deterministic per-turn overhead: bounded queries and generous time ceilings
# --------------------------------------------------------------------------


async def test_preparation_database_work_is_bounded_per_turn(env):
    engine, factory, scenario = env

    async def load_inputs():
        async with factory() as db:
            session_row = await db.get(models.Session, uuid.UUID(scenario["session_id"]))
            await load_companion_inputs(db, session_row, str(scenario["user_id"]), durable_state={})

    async with QueryCounter(engine) as counter:
        await load_inputs()
    assert counter.count <= 5  # one joined companion load + open threads

    timing = await _measure(load_inputs, repeat=15)
    assert timing["median_ms"] < 100  # generous ceiling; medians are ~1 ms

    async def restore():
        async with factory() as db:
            restorable = await load_restorable_mood(
                db, user_id=scenario["user_id"], companion_id=COMPANION_A,
                room_id=None, exclude_session_id=scenario["session_id"],
                now=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            )
            if restorable is not None:
                return restore_mood(restorable.state, now=__import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc))

    async with QueryCounter(engine) as counter:
        await restore()
    assert counter.count <= 6  # candidates + mood log/tier lookups


async def test_emotional_association_retrieval_is_one_bounded_query(env):
    engine, factory, scenario = env

    async def retrieve():
        async with factory() as db:
            records = await retrieve_emotional_associations(
                db, session_id=uuid.UUID(scenario["session_id"]), message=USER_MESSAGE,
                limit=3, include_unresolved=True)
            return render_associations(records, max_chars=700)

    async with QueryCounter(engine) as counter:
        rendered = await retrieve()
    assert counter.count <= 3  # session scope + bounded candidate query
    assert rendered is not None and len(rendered) <= 700


async def _measure(factory, *, repeat: int = 15, warmup: int = 3):
    for _ in range(warmup):
        await factory()
    import statistics
    import time as time_module

    samples = []
    for _ in range(repeat):
        start = time_module.perf_counter()
        await factory()
        samples.append((time_module.perf_counter() - start) * 1000.0)
    return {"median_ms": statistics.median(samples), "samples": repeat}


# --------------------------------------------------------------------------
# Prompt growth: phases 3-6 additions are individually bounded
# --------------------------------------------------------------------------


def test_phase_3_to_6_prompt_additions_stay_within_their_bounds(env):
    engine, factory, scenario = env
    from chatbot.soul.behavior import build_character_behavior, build_open_thread_context
    from chatbot.soul.self_model import developed_self_text
    from chatbot.soul.pacing import PacingContext, conversational_rhythm_directive
    from chatbot.soul.character import CharacterProfile

    character = CharacterProfile.defaults()
    self_text = developed_self_text(scenario["self_model"]) or ""
    assert len(self_text) <= 900  # self-model prompt bound (Phase 3)

    behavior_text, behavior_meta = build_character_behavior(
        personality={"personality_sliders": dict(character.personality_sliders),
                     "favorite_topics": list(character.favorite_topics),
                     "humor_style": character.humor_style},
        conversation_context=USER_MESSAGE,
        effective_traits=dict(character.personality_sliders),
        self_model=scenario["self_model"],
        relationship={"warmth": 0.7, "depth": 0.42, "completed_turns": 36},
        mood={"mood": "upbeat", "energy": 0.78},
    )
    assert len(behavior_text) <= 1200  # behavior block hard bound (Phase 4)

    thread_text, thread_meta = build_open_thread_context(
        scenario["open_threads"], conversation_context=USER_MESSAGE)
    assert thread_text is None or len(thread_text) <= 700  # thread bound (Phase 4.3a)

    pacing_text = conversational_rhythm_directive(PacingContext(
        personality_sliders=dict(character.personality_sliders),
        humor_style=character.humor_style, communication_style=character.communication_style,
        user_message=USER_MESSAGE, interests=("modal jazz",),
        open_threads=tuple(scenario["open_threads"]),
        relationship={"completed_turns": 36, "warmth": 0.7},
    ))
    from chatbot.soul.pacing import MAX_GUIDANCE_CHARS
    assert pacing_text is None or len(pacing_text) <= MAX_GUIDANCE_CHARS  # Phase 6.1b


# --------------------------------------------------------------------------
# Extraction capacity: the scripted evidence is reproducible
# --------------------------------------------------------------------------


def test_typical_full_category_extractions_fit_the_700_token_budget():
    corpus = corpus_statistics()
    by_name = {item["name"]: item for item in corpus["items"]}
    # Quiet and typical turns are comfortably inside the budget.
    assert by_name["typical_fact_only"]["fits_700"]
    assert by_name["typical_mixed"]["fits_700"]
    assert by_name["typical_with_threads_and_emotions"]["fits_700"]
    assert by_name["resolution_turn"]["fits_700"]
    # The maximal legitimate turn (8 facts + 2 experiences + 3 self + 2 threads
    # + 2 emotions) does NOT fit — this is the recorded evidence behind the
    # recommendation to widen the output budget.
    assert not by_name["busy_maximal"]["fits_700"]
    assert by_name["busy_maximal"]["estimated_tokens"] > 700


def test_an_output_truncation_falls_back_to_heuristics_and_loses_auxiliary_categories():
    """A response cut off by the output limit becomes unparseable JSON."""

    truncated = EXTRACTION_RESPONSE[: int(len(EXTRACTION_RESPONSE) * 0.7)]  # simulates length cut
    limits = LLMExtractionLimits()
    provider = ScriptedProvider(truncated, finish_reason="length")  # provider-level cut
    extractor = LLMMemoryExtractor(storage=_Recorder(), provider=provider, model="scripted")

    async def run():
        return await extractor.extract_entries(
            session_id="s", user_message=USER_MESSAGE,
            provider_response="Congratulations! Shall we celebrate properly?", turn_id="t")

    outcome = asyncio.run(run())
    # The heuristic fallback kept plain facts but every auxiliary category is lost.
    assert outcome.method == "heuristic"
    assert outcome.fallback_reason == "malformed_response"
    assert outcome.response_truncated is True
    assert outcome.entries and all(entry.get("type") in {"fact", "episode"} for entry in outcome.entries)
    assert outcome.self_observations == []
    assert outcome.thread_updates == []
    assert outcome.emotional_observations == []


class _Recorder:
    async def persist(self, session_id, entries):
        return list(entries)


def test_scripted_full_category_output_fits_when_the_budget_allows_it():
    """Sanity: a complete in-budget response keeps every category."""

    provider = ScriptedProvider(EXTRACTION_RESPONSE)
    extractor = LLMMemoryExtractor(storage=_Recorder(), provider=provider, model="scripted")

    async def run():
        return await extractor.extract_entries(
            session_id="s", user_message=USER_MESSAGE,
            provider_response="Congratulations! Shall we celebrate properly? "
                              "I do find late-night coding overrated myself.",
            turn_id="t")

    outcome = asyncio.run(run())
    assert outcome.method == "llm"
    assert outcome.entries  # facts and/or experiences persisted
    assert len(outcome.self_observations) == 1
    assert len(outcome.thread_updates) == 1
    assert len(outcome.emotional_observations) == 1


# --------------------------------------------------------------------------
# Original interfaces and regression guards
# --------------------------------------------------------------------------


def test_original_pacing_interfaces_are_unchanged():
    assert calculate_momentum() == "steady"
    assert calculate_momentum(5.0, 20) == "fast"
    assert (
        get_pacing_directive("action", "fast")
        == "Pacing: Keep responses short, punchy, and highly reactive to match the fast pace."
    )
    assert (
        get_pacing_directive("casual", "steady")
        == "Pacing: Maintain a natural, comfortable conversational pacing."
    )


def test_extraction_system_prompt_cost_is_recorded_and_bounded():
    from chatbot.memory.llm_extraction import SYSTEM_PROMPT

    tokens = estimate_tokens(SYSTEM_PROMPT)
    # The single shared extraction instruction stays under ~2k estimated tokens;
    # growth beyond that would signal unbounded prompt accretion.
    assert tokens < 2_500
    assert tokens > 0


def test_mood_continuity_is_cheap_per_turn():
    engine = MoodEngine()
    state = engine.default_state()
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)

    def hundred():
        current = engine.default_state()
        for _ in range(100):
            current = engine.update(
                {"user_message": USER_MESSAGE, "provider_response": "Great!"}, current, now=now)

    import statistics
    import time as time_module

    samples = []
    for _ in range(5):
        start = time_module.perf_counter()
        hundred()
        samples.append((time_module.perf_counter() - start) * 1000)
    assert statistics.median(samples) / 100.0 < 1.0  # per-update cost well under 1 ms


def test_corpus_statistics_are_deterministic():
    assert corpus_statistics() == corpus_statistics()
