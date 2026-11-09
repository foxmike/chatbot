"""Phase 7.2b — measurement harness for performance and cost evaluation.

Reusable, deterministic helpers shared by the measurement script
(``scripts/phase7_measurements.py``) and the focused invariant tests
(``tests/test_phase7_performance_evaluation.py``):

* :func:`estimate_tokens` — characters/4 heuristic (documented; no tokenizer
  dependency is added). Every cost number in the report states this basis.
* :class:`QueryCounter` — counts SQL statements issued by an async engine.
* :func:`measure` — median/min/max wall time of an awaitable factory.
* :func:`seed_scenario` — one fully-populated companion scenario (two users,
  two companions, durable identity/relationship rows, sessions incl. a previous
  one carrying mood state, rooms, open threads, self-model document, semantic
  facts with emotional-association metadata) on any SQLAlchemy async engine.
* :func:`extraction_corpus` — representative scripted extraction responses of
  varying realistic fullness for the 700-token capacity evaluation.

No network, no paid inference: providers are scripted or absent.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import json
import statistics
import time
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from chatbot.db import models

T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
USER_A = "abcdef01-0000-4000-8000-000000000001"
USER_B = "abcdef02-0000-4000-8000-000000000002"
COMPANION_A = "11111111-1111-4111-8111-111111111111"
COMPANION_B = "22222222-2222-4222-8222-222222222222"
ROOM_A = "room-phase7-a"


# ---------------------------------------------------------------------------
# Measurement primitives
# ---------------------------------------------------------------------------


def estimate_tokens(text: Any) -> int:
    """Character/4 token estimate (stated basis for every cost number)."""

    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False, default=str)
    return max(0, len(text) // 4)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


async def measure(
    factory: Callable[[], Awaitable[Any]],
    *,
    repeat: int = 20,
    warmup: int = 3,
) -> dict[str, float]:
    """Run *factory* repeatedly; return wall-time statistics in milliseconds."""

    for _ in range(max(0, warmup)):
        await factory()
    samples: list[float] = []
    for _ in range(max(1, repeat)):
        start = time.perf_counter()
        await factory()
        samples.append((time.perf_counter() - start) * 1000.0)
    return {
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": round(percentile(samples, 0.95), 3),
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
        "samples": repeat,
    }


class QueryCounter:
    """Count SQL statements executed by an async engine during a block."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self.sync_engine = engine.sync_engine
        self.count = 0
        self._handle = None

    async def __aenter__(self) -> "QueryCounter":
        self.count = 0

        def _on_statement(*_args: Any, **_kwargs: Any) -> None:
            self.count += 1

        self._handle = _on_statement
        sa.event.listen(self.sync_engine, "before_cursor_execute", _on_statement)
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._handle is not None:
            sa.event.remove(self.sync_engine, "before_cursor_execute", self._handle)
        return None


def json_safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# ---------------------------------------------------------------------------
# Scenario seeding (SQLite or PostgreSQL; created tables via create_all)
# ---------------------------------------------------------------------------

SCENARIO_TABLES = [
    models.User,
    models.Companion,
    models.BotMoodLog,
    models.IdentityStateModel,
    models.BackgroundJob,
    models.Episode,
    models.MemoryEvidence,
    models.MemoryRelation,
    models.CompanionIdentityModel,
    models.CompanionRelationshipModel,
    models.Room,
    models.Session,
    models.Message,
    models.SemanticFact,
    models.ConversationalThread,
]


async def create_scenario_tables(engine: Any) -> None:
    async with engine.begin() as connection:
        for model in SCENARIO_TABLES:
            await connection.run_sync(lambda sync, m=model: m.__table__.create(sync))


async def seed_scenario(factory: async_sessionmaker, *, with_history: bool = True) -> dict[str, Any]:
    """Seed one full companion scenario and return its identifiers and payloads."""

    user_a = uuid_of(USER_A)
    user_b = uuid_of(USER_B)
    session_id = uuid_of("3a333333-3333-4333-8333-333333333333")
    previous_session_id = uuid_of("4a444444-4444-4444-8444-444444444444")
    now = datetime.now(timezone.utc)

    self_model = {
        "schema": 1,
        "items": {
            "opinion:late night coding": {
                "kind": "opinion", "subject": "late night coding",
                "statement": "I find late-night coding sessions counterproductive.",
                "reason": "I think clearly in the morning",
                "status": "held", "strength": 0.8, "support": 4, "opposition": 0,
                "sessions": {"s1": 2, "s2": 2}, "opposition_sessions": {},
                "challenge": None, "first_at": T0.isoformat(), "updated_at": T0.isoformat(),
                "evidence": [{"id": "e1", "excerpt": "I think clearly in the morning"}],
                "history": [],
            },
            "interest:modal jazz": {
                "kind": "interest", "subject": "modal jazz",
                "statement": "I am drawn to modal jazz.",
                "reason": "", "status": "held", "strength": 0.7, "support": 3,
                "opposition": 0, "sessions": {"s1": 1, "s3": 2}, "opposition_sessions": {},
                "challenge": None, "first_at": T0.isoformat(), "updated_at": T0.isoformat(),
                "evidence": [{"id": "e2", "excerpt": "modal jazz rewards patience"}],
                "history": [],
            },
        },
        "log": [], "applied": [],
    }

    threads = [
        {
            "thread_id": "t1", "user_id": str(user_a), "companion_id": COMPANION_A,
            "room_id": None, "ownership": "shared", "kind": "subject",
            "subject_key": "finishing treehouse app", "summary": "Finishing the treehouse app",
            "topic": "treehouse", "status": "open", "importance": "significant",
            "source_evidence": [], "relationship_context": {"depth": 0.4, "completed_turns": 8},
            "last_discussed_at": (now - timedelta(hours=2)).isoformat(),
            "resolved_at": None, "superseded_by": None,
        },
        {
            "thread_id": "t2", "user_id": str(user_a), "companion_id": COMPANION_A,
            "room_id": None, "ownership": "user", "kind": "interest",
            "subject_key": "learning watercolor painting", "summary": "Learning watercolor painting",
            "topic": "watercolor", "status": "open", "importance": "ordinary",
            "source_evidence": [], "relationship_context": None,
            "last_discussed_at": (now - timedelta(days=3)).isoformat(),
            "resolved_at": None, "superseded_by": None,
        },
    ]

    mood_state = {
        "mood": "upbeat", "energy": 0.78, "closeness": 0.35,
        "last_updated": (now - timedelta(minutes=25)).isoformat(),
        "recent_emotional_context": ["positive engagement", "shared excitement"],
        "turns_since_mood_shift": 2,
    }

    async with factory() as db:
        for user, tag in ((user_a, "a"), (user_b, "b")):
            db.add(models.User(id=user, external_id=tag, display_name=tag, username=tag,
                               email=f"{tag}@example.com", password_hash="h"))
        for cid, user in ((COMPANION_A, user_a), (COMPANION_B, user_a)):
            db.add(models.Companion(
                companion_id=cid, user_id=user, name=cid, persona="A warm, curious friend.",
                description="d", backstory="b",
                personality_sliders={"openness": 0.7, "extraversion": 0.8, "curiosity": 0.75,
                                     "conscientiousness": 0.5, "agreeableness": 0.6,
                                     "neuroticism": 0.3, "creativity": 0.7},
                favorite_topics=["jazz", "coding"],
            ))
        await db.flush()  # parents first: children reference these rows
        db.add(models.CompanionIdentityModel(
            companion_id=COMPANION_A, trait_adjustments={}, self_model=self_model,
        ))
        db.add(models.CompanionRelationshipModel(
            companion_id=COMPANION_A, depth=0.42, completed_turns=36,
            first_interaction_at=now - timedelta(days=30),
            last_interaction_at=now - timedelta(minutes=25),
        ))
        db.add(models.Room(room_id=ROOM_A, user_id=str(user_a), room_type="one_on_one_chat",
                           display_name="Perf", persist_to_durable=True, memory_scope="room"))
        db.add(models.Session(id=session_id, user_id=user_a, companion_id=COMPANION_A,
                              title="current", mood_state=mood_state, updated_at=now))
        db.add(models.Session(id=previous_session_id, user_id=user_a, companion_id=COMPANION_A,
                              title="previous", mood_state=mood_state,
                              updated_at=now - timedelta(hours=3)))
        await db.flush()

        if with_history:
            for index in range(6):  # noqa: B007
                db.add(models.Message(
                    session_id=session_id, user_id=user_a, role="user",
                    content=f"Message {index}: I have been working on the treehouse app again.",
                    turn_id=f"turn-{index}", created_at=now - timedelta(minutes=30 - index),
                ))
                db.add(models.Message(
                    session_id=session_id, user_id=user_a, role="assistant",
                    content=f"Reply {index}: that project sounds rewarding.",
                    turn_id=f"turn-{index}", companion_id=COMPANION_A,
                    created_at=now - timedelta(minutes=29 - index),
                ))

        # Semantic facts, some carrying emotional-association metadata (phase 5).
        # Visible associations carry kind+companion_id in the extraction metadata
        # (the same shape the durable extraction job writes).
        association = {
            "subject": "the treehouse app", "emotion": "pride",
            "source": "user", "weight": 0.8, "significance": "significant",
            "first_at": (now - timedelta(days=5)).isoformat(),
            "last_at": (now - timedelta(days=1)).isoformat(),
            "unresolved": True, "interpretation": "", "history": [],
            "evidence": [{"id": "ev1", "excerpt": "proud of the app", "at": (now - timedelta(days=1)).isoformat()}],
        }
        for index in range(8):
            db.add(models.SemanticFact(
                id=uuid_of(f"5a555555-0000-4a00-8a00-{index:012d}"),
                user_id=user_a,
                fact_text=("The user said they felt pride about the treehouse app: proud of finishing it."
                           if index % 2 == 0
                           else f"Unrelated fact number {index} about cooking."),
                source_type="inferred", confidence=0.9,
                extraction_metadata=(
                    {
                        "kind": "emotional_association",
                        "companion_id": COMPANION_A,
                        "user_id": str(user_a),
                        "session_id": str(session_id),
                        "association": dict(association),
                    }
                    if index % 2 == 0
                    else {"kind": "personal_fact", "companion_id": COMPANION_A}
                ),
                last_reinforced_at=now - timedelta(days=1),
            ))
        await db.commit()

    return {
        "user_id": user_a,
        "other_user_id": user_b,
        "companion_id": COMPANION_A,
        "other_companion_id": COMPANION_B,
        "session_id": str(session_id),
        "previous_session_id": str(previous_session_id),
        "room_id": ROOM_A,
        "self_model": self_model,
        "open_threads": threads,
        "mood_state": mood_state,
    }


def uuid_of(value: str) -> Any:
    import uuid

    return uuid.UUID(value)


# ---------------------------------------------------------------------------
# Extraction-capacity corpus: representative scripted responses
# ---------------------------------------------------------------------------


def _memory(content: str, evidence: str, *, type_: str = "personal_fact") -> dict[str, Any]:
    return {
        "type": type_, "content": content, "evidence": evidence, "status": "real",
        "confidence": 0.85, "temporal": "current", "corrects": None,
    }


def _experience(title: str, narrative: str, evidence: str) -> dict[str, Any]:
    return {
        "title": title, "narrative": narrative, "participants": ["user", "companion"],
        "significance": "significant", "evidence": evidence, "status": "real",
        "origin": "conversation", "topics": ["work", "project"], "revises": None,
        "confidence": 0.8,
    }


def _self(statement: str, evidence: str, key: str | None = None) -> dict[str, Any]:
    return {
        "kind": "opinion", "key": key, "subject": "work-life balance",
        "statement": statement, "reason": "I have seen it help before",
        "evidence": evidence, "status": "real", "significance": "ordinary",
        "relation": "consistent", "change_of_mind": False,
    }


def _thread(subject: str, evidence: str, key: str | None = None) -> dict[str, Any]:
    return {
        "ownership": "shared", "kind": "subject", "key": key, "subject": subject,
        "topic": "project", "status": "open", "importance": "significant",
        "evidence": evidence, "supersedes": None,
    }


def _emotion(subject: str, evidence: str, key: str | None = None) -> dict[str, Any]:
    return {
        "kind": "emotional_association", "key": key, "subject": subject, "emotion": "pride",
        "interpretation": "The user is proud of finishing hard things.",
        "evidence": evidence, "status": "real", "significance": "significant",
        "unresolved": False, "revises": None, "confidence": 0.8,
    }


def extraction_corpus() -> list[dict[str, Any]]:
    """Representative scripted extraction responses, from quiet to maximal turns.

    ``expected_full`` records which categories a realistic model would emit for
    that kind of turn; the capacity evaluation measures the token cost of each
    and how the 700-token output budget treats it.
    """

    user = "I finally shipped the treehouse app this weekend. I am proud but also exhausted; honestly I think late nights were a mistake, though the jazz playlists kept me sane."
    assistant = "Congratulations! I told you the persistence would pay off. I do find late-night coding overrated myself; mornings are kinder. Shall we celebrate properly?"
    return [
        {
            "name": "quiet_smalltalk",
            "description": "Chatter turn: nothing worth remembering",
            "response": {"memories": [], "experiences": [], "self": [], "threads": [], "emotions": []},
            "expected_full": False,
        },
        {
            "name": "typical_fact_only",
            "description": "One or two facts, no auxiliary categories",
            "response": {"memories": [_memory("User lives in Seattle.", "I live in Seattle"),
                                      _memory("User started a new job.", "I got a new job")],
                         "experiences": [], "self": [], "threads": [], "emotions": []},
            "expected_full": True,
        },
        {
            "name": "typical_mixed",
            "description": "Two facts, one experience, one self-observation",
            "response": {
                "memories": [_memory("User lives in Seattle.", "I live in Seattle"),
                             _memory("User finds late nights a mistake.", "late nights were a mistake")],
                "experiences": [_experience("Shipped the treehouse app",
                                            "User shipped the treehouse app this weekend.",
                                            "I finally shipped the treehouse app")],
                "self": [_self("I find late-night coding counterproductive.",
                               "I do find late-night coding overrated myself")],
                "threads": [], "emotions": [],
            },
            "expected_full": True,
        },
        {
            "name": "typical_with_threads_and_emotions",
            "description": "Typical rich turn: facts + experience + self + threads + emotions",
            "response": {
                "memories": [_memory("User shipped the treehouse app.", "I finally shipped the treehouse app"),
                             _memory("User finds late nights a mistake.", "late nights were a mistake")],
                "experiences": [_experience("Shipped the treehouse app",
                                            "User shipped the treehouse app this weekend.",
                                            "I finally shipped the treehouse app")],
                "self": [_self("I find late-night coding counterproductive.",
                               "I do find late-night coding overrated myself")],
                "threads": [_thread("Celebrating the shipped treehouse app",
                                    "Shall we celebrate properly?")],
                "emotions": [_emotion("the treehouse app", "I am proud but also exhausted")],
            },
            "expected_full": True,
        },
        {
            "name": "busy_maximal",
            "description": "Maximal legitimate turn: 8 facts, 2 experiences, 3 self, 2 threads, 2 emotions",
            "response": {
                "memories": [
                    _memory(f"User detail number {index} about their new job.",
                            "my new job", type_=("personal_fact" if index % 2 else "preference"))
                    for index in range(8)
                ],
                "experiences": [_experience("Shipped the treehouse app",
                                            "User shipped the treehouse app this weekend.",
                                            "I finally shipped the treehouse app"),
                                _experience("Jazz kept the late nights sane",
                                            "The jazz playlists kept the user sane during crunch.",
                                            "the jazz playlists kept me sane")],
                "self": [_self("I find late-night coding counterproductive.",
                               "I do find late-night coding overrated myself"),
                         _self("I value persistence in long projects.",
                               "the persistence would pay off"),
                         _self("I prefer celebrating real milestones.",
                               "Shall we celebrate properly?")],
                "threads": [_thread("Celebrating the shipped treehouse app",
                                    "Shall we celebrate properly?"),
                            _thread("Whether the next project uses late nights",
                                    "late nights were a mistake")],
                "emotions": [_emotion("the treehouse app", "I am proud but also exhausted"),
                             _emotion("late-night work", "exhausted")],
            },
            "expected_full": True,
        },
        {
            "name": "resolution_turn",
            "description": "Turn that resolves known items (keys referencing known state)",
            "response": {
                "memories": [_memory("User shipped the treehouse app.",
                                     "I finally shipped the treehouse app")],
                "experiences": [_experience("Shipped the treehouse app",
                                            "User shipped the treehouse app this weekend.",
                                            "I finally shipped the treehouse app")],
                "self": [_self("I think celebrating milestones matters.", "Congratulations!",
                               key="opinion:celebrating milestones")],
                "threads": [_thread("Finishing the treehouse app", "I finally shipped the treehouse app",
                                    key="finishing treehouse app")],
                "emotions": [_emotion("the treehouse app", "I am proud", key="the treehouse app:pride:user")],
            },
            "expected_full": True,
        },
    ]


def corpus_statistics() -> dict[str, Any]:
    """Per-corpus-item serialized size, token estimate, and 700-token verdict."""

    results = []
    for item in extraction_corpus():
        serialized = json.dumps(item["response"], ensure_ascii=False)
        tokens = estimate_tokens(serialized)
        results.append(
            {
                "name": item["name"],
                "description": item["description"],
                "chars": len(serialized),
                "estimated_tokens": tokens,
                "fits_700": tokens <= 700,
                "expected_full": item["expected_full"],
            }
        )
    full = [entry for entry in results if entry["expected_full"]]
    over = [entry for entry in full if not entry["fits_700"]]
    return {
        "items": results,
        "estimated_output_limit_tokens": 700,
        "full_category_items": len(full),
        "over_budget_items": len(over),
        "max_estimated_tokens": max(entry["estimated_tokens"] for entry in results),
        "truncation_mode": (
            "provider truncation (finish_reason=length) makes the JSON unparseable; "
            "parse_candidates returns None and the whole response falls back to the "
            "regex heuristic, which keeps only user facts and loses experiences, "
            "self-observations, threads and emotional associations"
        ),
    }


__all__ = [
    "COMPANION_A",
    "COMPANION_B",
    "ROOM_A",
    "SCENARIO_TABLES",
    "T0",
    "QueryCounter",
    "corpus_statistics",
    "create_scenario_tables",
    "estimate_tokens",
    "extraction_corpus",
    "json_safe",
    "measure",
    "percentile",
    "seed_scenario",
    "uuid_of",
]
