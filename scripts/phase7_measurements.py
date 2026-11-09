#!/usr/bin/env python3
"""Phase 7.2b — controlled performance and cost measurement pass.

Runs the reproducible measurement campaign over the completed companion
evolution system and writes ``docs/verification/phase7_performance_metrics.json``.

Rules honoured by this script:

* deterministic application overhead, provider inference and background
  extraction are measured separately and never conflated;
* providers are scripted (no network, no paid inference) — costs are reported
  as token counts (characters/4 heuristic) and call counts, never as dollars;
* controlled synthetic workloads only (small N, throwaway SQLite by default,
  and a disposable Alembic-built PostgreSQL when ``PHASE0_PG_URL`` is set);
  the development database is never touched;
* one measurement pass per invocation; no repeated campaigns.

Usage::

    python scripts/phase7_measurements.py                     # SQLite campaign
    PHASE0_PG_URL=... python scripts/phase7_measurements.py   # + PostgreSQL
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import statistics
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from chatbot.db import models
from chatbot.api.conversation import load_companion_inputs
from chatbot.soul.behavior import build_character_behavior, build_open_thread_context
from chatbot.soul.character import CharacterProfile
from chatbot.soul.evolution import IdentityState
from chatbot.soul.linguistic_fingerprint import format_linguistic_fingerprint
from chatbot.soul.pacing import PacingContext, full_pacing_guidance
from chatbot.soul.persona_prompt import PersonaPromptAssembler
from chatbot.soul.relationship import RelationshipConfig
from chatbot.soul.self_model import developed_self_text
from chatbot.soul.mood import MoodEngine
from chatbot.soul.mood_continuity import (
    load_restorable_mood,
    restore_mood,
    temperament_from_character,
)
from chatbot.soul.emotional_associations import (
    render_associations,
    retrieve_emotional_associations,
)
from phase7_helpers import (
    COMPANION_A,
    QueryCounter,
    corpus_statistics,
    create_scenario_tables,
    estimate_tokens,
    json_safe,
    measure,
    seed_scenario,
)

REPORT_PATH = ROOT / "docs" / "verification" / "phase7_performance_metrics.json"
NOW = datetime.now(timezone.utc)
USER_MESSAGE = "I finally shipped the treehouse app this weekend! So happy with how it turned out."


# ---------------------------------------------------------------------------
# Scripted providers (no network, no paid inference)
# ---------------------------------------------------------------------------


class ScriptedProvider:
    """Records every call; returns one fixed scripted response."""

    def __init__(self, response: str = "") -> None:
        self.response = response
        self.calls: list[dict] = []

    async def complete(self, prompt, history, config):
        self.calls.append({"prompt": prompt, "config": config})
        from chatbot.providers.base import ProviderResponse

        return ProviderResponse(text=self.response, finish_reason="stop", model="scripted")


class ScriptedEmbeddings:
    """Deterministic embedding provider: hashed text, tiny dimension."""

    dimension = 8

    async def embed(self, texts):
        import hashlib

        return [
            [byte / 255.0 for byte in hashlib.sha256(text.encode("utf-8")).digest()[:8]]
            for text in texts
        ]


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


# ---------------------------------------------------------------------------
# Deterministic overhead (category A) — scripted providers, one database
# ---------------------------------------------------------------------------


async def deterministic_overhead(engine, factory, scenario) -> dict:
    results: dict[str, object] = {}

    async def load_inputs():
        async with factory() as db:
            session_row = await db.get(models.Session, uuid.UUID(scenario["session_id"]))
            await load_companion_inputs(db, session_row, str(scenario["user_id"]), durable_state={})

    async with QueryCounter(engine) as counter:
        await load_inputs()
    results["load_companion_inputs"] = {
        **await measure(load_inputs, repeat=30),
        "queries_per_call": counter.count,
    }

    temperament = temperament_from_character({"personality_sliders": {"extraversion": 0.8}})

    async def restore():
        async with factory() as db:
            restorable = await load_restorable_mood(
                db, user_id=scenario["user_id"], companion_id=COMPANION_A,
                room_id=None, exclude_session_id=scenario["session_id"], now=NOW,
            )
            if restorable is not None:
                return restore_mood(restorable.state, now=NOW, temperament=temperament)
            return None

    async with QueryCounter(engine) as counter:
        await restore()
    results["cross_session_mood_restoration"] = {
        **await measure(restore, repeat=30),
        "queries_per_call": counter.count,
    }

    async def associations():
        async with factory() as db:
            records = await retrieve_emotional_associations(
                db, session_id=uuid.UUID(scenario["session_id"]), message=USER_MESSAGE,
                limit=3, include_unresolved=True,
            )
            return render_associations(records, max_chars=700)

    async with QueryCounter(engine) as counter:
        await associations()
    results["emotional_association_retrieval"] = {
        **await measure(associations, repeat=30),
        "queries_per_call": counter.count,
    }
    return results


def synchronous_overheads(scenario) -> dict:
    """Pure in-process assemblies: behavioral context, fingerprint, pacing, mood."""

    character = CharacterProfile.defaults()
    results: dict[str, object] = {}

    behavior, behavior_meta = build_character_behavior(
        personality={"personality_sliders": dict(character.personality_sliders),
                     "favorite_topics": list(character.favorite_topics),
                     "humor_style": character.humor_style},
        conversation_context=USER_MESSAGE,
        effective_traits=dict(character.personality_sliders),
        self_model=scenario["self_model"],
        relationship={"warmth": 0.7, "depth": 0.42, "completed_turns": 36},
        mood={"mood": "upbeat", "energy": 0.78},
    )
    results["character_behavior_block"] = {
        "chars": behavior_meta["chars"],
        "est_tokens": behavior_meta["est_tokens"],
        **sync_timing(lambda: build_character_behavior(
            personality={"personality_sliders": dict(character.personality_sliders),
                         "favorite_topics": list(character.favorite_topics),
                         "humor_style": character.humor_style},
            conversation_context=USER_MESSAGE,
            effective_traits=dict(character.personality_sliders),
            self_model=scenario["self_model"],
            relationship={"warmth": 0.7, "depth": 0.42, "completed_turns": 36},
            mood={"mood": "upbeat", "energy": 0.78},
        ), repeat=50),
    }

    thread_text, thread_meta = build_open_thread_context(
        scenario["open_threads"], conversation_context=USER_MESSAGE)
    results["open_thread_context"] = {
        "chars": len(thread_text or ""),
        "included": thread_meta.get("included"),
        **sync_timing(lambda: build_open_thread_context(
            scenario["open_threads"], conversation_context=USER_MESSAGE), repeat=50),
    }

    fingerprint = format_linguistic_fingerprint(
        {"punctuation_habits": {"exclamation_rate": 0.2, "ellipsis_rate": 0.1},
         "emoji_style": {"per_message": 0.3}, "slang_profile": {"rate": 0.1}})
    results["linguistic_fingerprint"] = {
        "chars": len(fingerprint),
        **sync_timing(lambda: format_linguistic_fingerprint(
            {"punctuation_habits": {"exclamation_rate": 0.2, "ellipsis_rate": 0.1},
             "emoji_style": {"per_message": 0.3}}), repeat=50),
    }

    pacing = full_pacing_guidance(PacingContext(
        personality_sliders=dict(character.personality_sliders),
        humor_style=character.humor_style, communication_style=character.communication_style,
        user_message=USER_MESSAGE, interests=("modal jazz",),
        open_threads=tuple(scenario["open_threads"]),
        relationship={"completed_turns": 36, "warmth": 0.7},
        mood_label="upbeat", mood_energy=0.78,
    ))
    results["pacing_guidance"] = {
        "chars": len(pacing or ""),
        **sync_timing(lambda: full_pacing_guidance(PacingContext(
            personality_sliders=dict(character.personality_sliders),
            humor_style=character.humor_style,
            communication_style=character.communication_style,
            user_message=USER_MESSAGE, interests=("modal jazz",),
            open_threads=tuple(scenario["open_threads"]),
            relationship={"completed_turns": 36, "warmth": 0.7},
            mood_label="upbeat", mood_energy=0.78,
        )), repeat=50),
    }

    engine = MoodEngine()

    def hundred_updates():
        state = engine.default_state()
        for _ in range(100):
            state = engine.update(
                {"user_message": USER_MESSAGE, "provider_response": "Congratulations!"},
                state, now=NOW,
            )

    timing = sync_timing(hundred_updates, repeat=10)
    results["mood_updates"] = {
        "median_ms_per_100_updates": timing["median_ms"],
        "median_ms_per_update": round(timing["median_ms"] / 100.0, 4),
    }
    return results


def sync_timing(fn, *, repeat: int, warmup: int = 2) -> dict:
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    return {
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": round(max(samples), 3),
        "samples": repeat,
    }


# ---------------------------------------------------------------------------
# Prompt composition: phases 3-6 token growth, measured on real renderers
# ---------------------------------------------------------------------------


async def prompt_composition_measurements(factory, scenario) -> dict:
    assembler = PersonaPromptAssembler()
    character = CharacterProfile.defaults()
    identity_state = IdentityState.defaults()
    relationship = RelationshipConfig.defaults()
    mood = MoodEngine().default_state()
    persona = {"name": character.name, "persona": "A warm, curious friend.", "description": "d"}

    # Phase 0/1 baseline: identity and relationship only.
    base = assembler.assemble(persona, mood=mood, mode=None, relationship_config=None,
                              character_profile=character, identity_state=identity_state)
    with_rel = assembler.assemble(
        persona, mood=mood, mode=None, relationship_config=relationship,
        character_profile=character, identity_state=identity_state)
    developed_self = developed_self_text(scenario["self_model"]) or ""
    # Phase 3: developed self-model section.
    with_self = assembler.assemble(
        persona, mood=mood, mode=None, relationship_config=relationship,
        character_profile=character, identity_state=identity_state,
        developed_self=developed_self)

    # Phase 4/6 expression section: behavioral-informed fingerprint + pacing rhythm,
    # exactly as CompileContextStep assembles it (expression_context).
    from chatbot.soul.expression_context import expression_context

    expression_ctx = type("Ctx", (), {})()
    expression_ctx.metadata = {
        "character_profile": {
            "name": character.name, "personality_sliders": dict(character.personality_sliders),
            "humor_style": character.humor_style,
            "communication_style": character.communication_style,
            "favorite_topics": list(character.favorite_topics),
        },
        "companion_relationship": {"warmth": 0.7, "depth": 0.42, "completed_turns": 36},
        "companion_self_model": scenario["self_model"],
        "companion_open_threads": list(scenario["open_threads"]),
        "mood_state": MoodEngine().baseline_state(),
        "intent": "casual",
    }
    expression_ctx.persona = persona
    expression_ctx.user_message = USER_MESSAGE
    expression_text, expression_meta = expression_context(expression_ctx)

    # Phase 4 open threads + phase 5 emotional associations as compiled by the step.
    thread_text, thread_meta = build_open_thread_context(
        scenario["open_threads"], conversation_context=USER_MESSAGE)
    async with factory() as db:
        records = await retrieve_emotional_associations(
            db, session_id=uuid.UUID(scenario["session_id"]), message=USER_MESSAGE,
            limit=3, include_unresolved=True)
    association_text = render_associations(records, max_chars=700) or ""

    full_prompt = "\n\n".join(
        part for part in (with_self, expression_text, thread_text or "", association_text)
        if part
    )
    timing = await measure(lambda: asyncio.to_thread(
        assembler.assemble,
        persona, mood=mood, mode=None, relationship_config=relationship,
        character_profile=character, identity_state=identity_state,
        developed_self=developed_self,
    ), repeat=30)
    expression_timing = await measure(lambda: asyncio.to_thread(
        expression_context, expression_ctx), repeat=50)

    return {
        "prompt_growth_chars": {
            "phase1_persona_baseline": len(base),
            "relationship": len(with_rel) - len(base),
            "phase3_developed_self": len(with_self) - len(with_rel),
            "phase4_6_expression_fingerprint_behavior_pacing": len(expression_text),
            "phase4_open_threads": len(thread_text or ""),
            "phase5_emotional_associations": len(association_text),
        },
        "prompt_growth_estimated_tokens": {
            "phase1_persona_baseline": estimate_tokens(base),
            "phase3_developed_self": estimate_tokens(developed_self),
            "phase4_6_expression_fingerprint_behavior_pacing": estimate_tokens(expression_text),
            "phase4_open_threads": estimate_tokens(thread_text or ""),
            "phase5_emotional_associations": estimate_tokens(association_text),
        },
        "full_persona_prompt_estimated_tokens": estimate_tokens(full_prompt),
        "full_persona_prompt_chars": len(full_prompt),
        "persona_assembly_median_ms": timing["median_ms"],
        "expression_context_median_ms": expression_timing["median_ms"],
        "open_threads_included": thread_meta.get("included"),
    }


# ---------------------------------------------------------------------------
# Streaming parser overhead
# ---------------------------------------------------------------------------


def streaming_measurements() -> dict:
    from chatbot.core.performance import DialogueStreamParser
    from chatbot.core import performance_runtime

    segment = (
        "<segment><text>Hello there! </text>"
        "<performance><emotion>joy</emotion><pace>fast</pace></performance></segment>"
    )
    stream = (segment * 400) + "Plain trailing text."

    def parse_all():
        parser = DialogueStreamParser()
        return "".join(
            event.text for event in (*parser.feed(stream), *parser.finish())
            if hasattr(event, "text")
        )

    def parse_completed():
        performance_runtime.parse_completed_response(type("Ctx", (), {"metadata": {}})(), stream)

    parse_timing = sync_timing(parse_all, repeat=20)
    completed_timing = sync_timing(parse_completed, repeat=10)
    return {
        "stream_bytes": len(stream.encode("utf-8")),
        "parser_only_median_ms": parse_timing["median_ms"],
        "parser_throughput_mb_per_s": round(
            (len(stream.encode("utf-8")) / 1e6) / (parse_timing["median_ms"] / 1000.0), 2),
        "completed_response_pipeline_median_ms": completed_timing["median_ms"],
    }


# ---------------------------------------------------------------------------
# Background extraction (category C) and throughput
# ---------------------------------------------------------------------------


async def background_extraction_measurements(engine, factory, scenario) -> dict:
    from chatbot.config import Settings
    from chatbot.perf.durable_handlers import make_durable_memory_extract_handler

    provider = ScriptedProvider(EXTRACTION_RESPONSE)

    class _Registry:
        def get(self, _name):
            return provider

    handler = make_durable_memory_extract_handler(
        factory, registry=_Registry(), settings=Settings(_env_file=None),
    )

    session_id = uuid.UUID(scenario["session_id"])

    async def seed_job(index: int):
        turn_id = f"perf-turn-{index}"
        async with factory() as db:
            user_id = await db.scalar(
                sa.select(models.Session.user_id).where(models.Session.id == session_id))
            db.add(models.Message(session_id=session_id, user_id=user_id, role="user",
                                  content=USER_MESSAGE, turn_id=turn_id))
            db.add(models.Message(session_id=session_id, user_id=user_id, role="assistant",
                                  content="Congratulations! Shall we celebrate properly?",
                                  turn_id=turn_id, companion_id=COMPANION_A))
            job = models.BackgroundJob(
                session_id=session_id, user_id=user_id, companion_id=COMPANION_A,
                task_type="memory_extract", turn_id=turn_id,
                payload={"session_id": str(session_id), "user_id": str(user_id),
                         "companion_id": COMPANION_A, "turn_id": turn_id},
            )
            db.add(job)
            await db.commit()
            return job.job_id

    async def run_job(job_id) -> None:
        class _Job:
            pass

        async with factory() as db:
            stored = await db.get(models.BackgroundJob, job_id)
            job = _Job()
            for field in ("job_id", "session_id", "user_id", "companion_id",
                          "turn_id", "attempt_count", "payload"):
                setattr(job, field, getattr(stored, field, None))
        await handler(job)

    async def one_job(index: int):
        job_id = await seed_job(index)
        await run_job(job_id)

    await one_job(0)  # warm-up
    provider.calls.clear()
    await one_job(0 - 1)  # measured probe job: establishes the extraction prompt
    calls_per_job = len(provider.calls)
    extraction_prompt = provider.calls[0]["prompt"] if provider.calls else ""

    job_counter = {"next": 101}

    def unique_job():
        index = job_counter["next"]
        job_counter["next"] += 1
        return one_job(index)

    single = await measure(unique_job, repeat=10)

    start = time.perf_counter()
    for index in range(1, 21):
        await one_job(index)
    batch_seconds = time.perf_counter() - start

    # Retry safety: replay the same job — the durable receipt must prevent any
    # additional provider call and any duplicate effects.
    replay_job = await seed_job(1000)
    await run_job(replay_job)
    provider.calls.clear()
    await run_job(replay_job)
    replay_extra_calls = len(provider.calls)

    return {
        "provider_calls_per_job": calls_per_job,
        "extraction_input_chars": len(extraction_prompt),
        "extraction_input_estimated_tokens": estimate_tokens(extraction_prompt),
        "extraction_output_chars": len(EXTRACTION_RESPONSE),
        "extraction_output_estimated_tokens": estimate_tokens(EXTRACTION_RESPONSE),
        "extraction_system_prompt_estimated_tokens": estimate_tokens(_system_prompt()),
        "single_job_handler_median_ms": single["median_ms"],
        "sequential_jobs": 20,
        "sequential_wall_seconds": round(batch_seconds, 3),
        "jobs_per_second": round(20 / batch_seconds, 2),
        "replay_extra_provider_calls": replay_extra_calls,
    }


def _system_prompt() -> str:
    from chatbot.memory.llm_extraction import SYSTEM_PROMPT

    return SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Cost summary
# ---------------------------------------------------------------------------


def cost_summary(results: dict) -> dict:
    extraction = results["background_extraction"]
    prompt = results["prompt_composition"]
    sync = results["synchronous_overheads"]
    return {
        "basis": (
            "token counts are characters/4 estimates from scripted providers; "
            "call counts are measured; no dollar estimates (no verified pricing applied)"
        ),
        "per_conversation_turn": {
            "generation_provider_calls": 1,
            "embedding_provider_calls": 1,
            "background_extraction_provider_calls": extraction["provider_calls_per_job"],
            "extraction_input_estimated_tokens": extraction["extraction_input_estimated_tokens"],
            "extraction_output_estimated_tokens": extraction["extraction_output_estimated_tokens"],
            "extraction_system_prompt_estimated_tokens":
                extraction["extraction_system_prompt_estimated_tokens"],
            "deterministic_overhead_median_ms": round(
                results["deterministic"]["load_companion_inputs"]["median_ms"]
                + results["deterministic"]["cross_session_mood_restoration"]["median_ms"]
                + results["deterministic"]["emotional_association_retrieval"]["median_ms"]
                + prompt["persona_assembly_median_ms"]
                + sync["pacing_guidance"]["median_ms"]
                + sync["linguistic_fingerprint"]["median_ms"]
                + sync["character_behavior_block"]["median_ms"],
                3,
            ),
        },
        "prompt_growth_phases_3_to_6_estimated_tokens":
            prompt["prompt_growth_estimated_tokens"],
        "retry_duplicate_costs": {
            "extraction_replay_extra_provider_calls": extraction["replay_extra_provider_calls"],
            "note": "the durable job receipt prevents repeated model calls and repeated effects",
        },
        "dollar_estimates": "intentionally omitted — no verified provider pricing was applied",
    }


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------


async def sqlite_campaign() -> dict:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="phase7-perf-"))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp / 'perf.db'}")
    await create_scenario_tables(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    scenario = await seed_scenario(factory)

    results: dict[str, object] = {}
    results["deterministic"] = await deterministic_overhead(engine, factory, scenario)
    results["synchronous_overheads"] = synchronous_overheads(scenario)
    results["prompt_composition"] = await prompt_composition_measurements(factory, scenario)
    results["streaming_parser"] = streaming_measurements()
    results["background_extraction"] = await background_extraction_measurements(
        engine, factory, scenario)

    async def load_inputs():
        async with factory() as db:
            session_row = await db.get(models.Session, uuid.UUID(scenario["session_id"]))
            await load_companion_inputs(db, session_row, str(scenario["user_id"]), durable_state={})

    start = time.perf_counter()
    await asyncio.gather(*(load_inputs() for _ in range(10)))
    results["concurrent_preparation_10_wall_ms"] = round((time.perf_counter() - start) * 1000, 1)

    results["corpus"] = corpus_statistics()
    await engine.dispose()
    return results


async def postgres_campaign() -> dict | None:
    url = os.environ.get("PHASE0_PG_URL")
    if not url:
        return None
    from test_timeline_migration import alembic as run_alembic  # type: ignore
    from sqlalchemy import make_url

    admin_url = make_url(url).set(database="postgres").render_as_string(hide_password=False)
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    name = f"phase7_{uuid.uuid4().hex[:10]}"
    async with admin_engine.connect() as connection:
        await connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    await admin_engine.dispose()

    database_url = make_url(url).set(database=name).render_as_string(hide_password=False)
    done = run_alembic(database_url, "upgrade", "head")
    if done.returncode != 0:
        raise RuntimeError(done.stderr[-1500:])

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    scenario = await seed_scenario(factory)

    async with factory() as db:
        facts = (await db.scalars(sa.select(models.SemanticFact))).all()
        vectors = await ScriptedEmbeddings().embed([fact.fact_text for fact in facts])
        for fact, vector in zip(facts, vectors, strict=True):
            await db.execute(
                sa.update(models.SemanticFact)
                .where(models.SemanticFact.id == fact.id)
                .values(embedding=vector)
            )
        await db.commit()

    results: dict[str, object] = {}
    results["deterministic"] = await deterministic_overhead(engine, factory, scenario)
    results["synchronous_overheads"] = synchronous_overheads(scenario)
    results["prompt_composition"] = await prompt_composition_measurements(factory, scenario)
    results["background_extraction"] = await background_extraction_measurements(
        engine, factory, scenario)

    async def load_inputs():
        async with factory() as db:
            session_row = await db.get(models.Session, uuid.UUID(scenario["session_id"]))
            await load_companion_inputs(db, session_row, str(scenario["user_id"]), durable_state={})

    start = time.perf_counter()
    await asyncio.gather(*(load_inputs() for _ in range(10)))
    results["concurrent_preparation_10_wall_ms"] = round((time.perf_counter() - start) * 1000, 1)

    from chatbot.memory.retrieval import RetrievalEngine, SessionAwareVectorStore

    retrieval = RetrievalEngine(SessionAwareVectorStore(factory), ScriptedEmbeddings())

    async def semantic_retrieval():
        return await retrieval.retrieve(
            query="the treehouse app this weekend", session_id=uuid.UUID(scenario["session_id"]))

    async with QueryCounter(engine) as counter:
        found = await semantic_retrieval()
    results["semantic_retrieval_engine"] = {
        **await measure(semantic_retrieval, repeat=20),
        "queries_per_call": counter.count,
        "results_for_seeded_facts": len(found),
    }

    await engine.dispose()
    cleanup = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with cleanup.connect() as connection:
        await connection.execute(sa.text(f'DROP DATABASE "{name}"'))
    await cleanup.dispose()
    return results


async def main() -> int:
    report: dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "basis": {
            "token_estimate": "characters/4 heuristic (no tokenizer dependency added)",
            "providers": "scripted; no network, no paid inference",
            "databases": (
                "throwaway SQLite always; disposable Alembic-built PostgreSQL when "
                "PHASE0_PG_URL is set"
            ),
        },
    }
    report["sqlite"] = await sqlite_campaign()
    postgres = await postgres_campaign()
    if postgres is not None:
        report["postgres"] = postgres
    report["cost"] = cost_summary(report["sqlite"])

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(json_safe(report), indent=2, ensure_ascii=False))
    print(f"wrote {REPORT_PATH}")
    print(json.dumps(json_safe(report["cost"])["per_conversation_turn"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
