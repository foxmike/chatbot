"""Deterministic rules of ``chatbot.memory.extraction`` (Task 2.3a).

The classification tests need no database. The persistence tests use a real
SQLAlchemy ``AsyncSession`` on a temporary SQLite file: they verify the rows the
extractor writes (ownership, provenance columns, duplicate suppression), not
PostgreSQL behavior.
"""

from __future__ import annotations

import uuid
from typing import Any, AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from chatbot.db import models
from chatbot.memory import extraction
from chatbot.memory.extraction import MemoryExtractor, _extract_entries


def facts_of(message: str, provider_response: str = "") -> list[dict[str, Any]]:
    entries = _extract_entries(
        session_id="s-rules", user_message=message, provider_response=provider_response
    )
    return [entry for entry in entries if entry["type"] == "fact"]


def fact_texts(message: str) -> list[str]:
    return [fact["content"] for fact in facts_of(message)]


# --------------------------------------------------------------------------
# Real-world facts are captured, with their kind
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "content", "kind"),
    [
        ("My name is Alice.", "My name is Alice.", "identity"),
        ("Call me Sam.", "Call me Sam.", "identity"),
        ("I'm 34 years old.", "I'm 34 years old.", "attribute"),
        ("I live in Lisbon.", "I live in Lisbon.", "location"),
        ("I’m from Brazil.", "I’m from Brazil.", "location"),
        ("I work as a nurse at the city hospital.", "I work as a nurse at the city hospital.", "occupation"),
        ("I'm a nurse.", "I'm a nurse.", "attribute"),
        ("I'm Canadian.", "I'm Canadian.", "attribute"),
        ("I'm vegetarian.", "I'm vegetarian.", "preference"),
        ("My favorite band is Radiohead.", "My favorite band is Radiohead.", "preference"),
        ("I can't stand cilantro.", "I can't stand cilantro.", "preference"),
        ("I don't eat meat.", "I don't eat meat.", "preference"),
        ("I'm allergic to peanuts.", "I'm allergic to peanuts.", "preference"),
        ("I usually run every morning before work.", "I usually run every morning before work.", "habit"),
        ("I have two cats.", "I have two cats.", "relationship"),
        ("My sister lives in Rome.", "My sister lives in Rome.", "relationship"),
        ("I believe in second chances.", "I believe in second chances.", "belief"),
        ("I want to become a doctor.", "I want to become a doctor.", "goal"),
        ("I'm learning Japanese.", "I'm learning Japanese.", "activity"),
    ],
)
def test_explicit_personal_facts_and_preferences_are_captured(
    message: str, content: str, kind: str
) -> None:
    facts = facts_of(message)
    assert [fact["content"] for fact in facts] == [content]
    assert facts[0]["metadata"]["kind"] == kind


@pytest.mark.parametrize(
    "message",
    [
        "I graduated from MIT in 2019.",
        "I got a new job today!",
        "My dog died last week.",
        "I just got back from Japan.",
        "I was diagnosed with diabetes last year.",
        "I broke up with my boyfriend.",
    ],
)
def test_meaningful_experiences_are_captured(message: str) -> None:
    facts = facts_of(message)
    assert [fact["content"] for fact in facts] == [message]
    assert facts[0]["metadata"]["kind"] == "experience"


def test_each_sentence_is_judged_on_its_own() -> None:
    assert fact_texts(
        "Can you help me plan my schedule? I live in Seattle and I have two golden retrievers."
    ) == ["I live in Seattle and I have two golden retrievers."]
    assert fact_texts("I got a new job today! I live in Seattle and I am so thrilled.") == [
        "I got a new job today!",
        "I live in Seattle and I am so thrilled.",
    ]


def test_adverbs_between_subject_and_verb_do_not_hide_a_fact() -> None:
    assert fact_texts("I actually live in Ohio.") == ["I actually live in Ohio."]
    assert fact_texts("I really love jazz.") == ["I really love jazz."]


def test_a_quoted_line_inside_a_stated_preference_is_kept_with_it() -> None:
    message = 'My favorite quote is "I am the master of my fate".'
    assert fact_texts(message) == [message]


# --------------------------------------------------------------------------
# Non-facts are not promoted into real-world user facts
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        # questions and requests
        "Do you think I should quit my job?",
        "Do you think I should quit my job.",
        "What is my name.",
        "Can you remember my favorite color",
        "Please tell me about my horoscope.",
        "I want a summary of this article.",
        "I need help with my code.",
        "I have a question about Python.",
        # hypotheticals, wishes, plans
        "If I lived in Paris I would eat croissants every day.",
        "What if I moved to Japan.",
        "Imagine I'm a dragon who lives in a cave.",
        "Suppose I have three kids.",
        "I wish I had a dog.",
        "I'd love to live by the sea.",
        "I'll move to Berlin next year.",
        "Someday I want to become a pilot.",
        # uncertain
        "I think I might be allergic to cats.",
        "Maybe I live too far from work.",
        "I'm not sure I like jazz.",
        "I kind of like opera.",
        # fiction
        "In my novel, I am a detective who lives in London.",
        "I dreamed I was flying over Tokyo.",
        "My character is a wizard who lives in a tower.",
        'She said "I live in Rome" to me.',
        # jokes
        "I'm a billionaire, just kidding.",
        # temporary states
        "I am feeling wonderful and excited today!",
        "I'm tired.",
        "I'm a bit stressed.",
        "I'm going to Paris next week.",
        # about the conversation
        "I love that idea!",
        "I like your answer.",
        # do-not-remember requests
        "Forget that I said I like jazz.",
        "Don't remember that I live in Ohio.",
    ],
)
def test_non_factual_statements_are_not_extracted(message: str) -> None:
    assert fact_texts(message) == []


@pytest.mark.parametrize(
    "message",
    [
        "Let's roleplay. I am the queen of Arendelle. I live in an ice castle.",
        "Let's pretend I'm a pirate. I have a parrot.",
        "*walks into the tavern* I am a wandering knight.",
        "Stay in character. I live in the northern woods.",
    ],
)
def test_roleplay_messages_produce_no_user_facts(message: str) -> None:
    assert fact_texts(message) == []


def test_out_of_character_statement_inside_roleplay_is_a_real_fact() -> None:
    facts = facts_of("Let's pretend I'm a pirate. OOC: I actually live in Ohio.")
    assert [fact["content"] for fact in facts] == ["I actually live in Ohio."]
    assert facts[0]["metadata"]["kind"] == "location"


def test_a_retraction_removes_the_statement_it_follows() -> None:
    assert fact_texts("I love sushi. Just kidding.") == []
    assert fact_texts("I live in Oslo. I love sushi. jk") == ["I live in Oslo."]


def test_assistant_text_never_becomes_a_user_fact() -> None:
    facts = facts_of(
        "Hello there.",
        provider_response="I'll remember that you live in Paris and your name is Bob.",
    )
    assert facts == []
    facts = facts_of(
        "My name is Bob.", provider_response="I'll remember that your name is Bob."
    )
    assert [(f["content"], f["metadata"]["source"]) for f in facts] == [
        ("My name is Bob.", "user_message")
    ]


# --------------------------------------------------------------------------
# Explicit memory requests, corrections and temporal markers
# --------------------------------------------------------------------------


def test_explicit_remember_request_is_kept_with_higher_confidence() -> None:
    (fact,) = facts_of("Please remember that I'm allergic to peanuts.")
    assert fact["content"] == "Please remember that I'm allergic to peanuts."
    assert fact["metadata"]["explicit_request"] is True
    assert fact["metadata"]["confidence"] == pytest.approx(0.85)
    (fact,) = facts_of("Don't forget that my birthday is March 3.")
    assert fact["metadata"]["explicit_request"] is True


def test_corrections_and_past_facts_are_marked_not_dropped() -> None:
    (correction,) = facts_of("Actually, I live in Boston now.")
    assert correction["metadata"]["correction"] is True
    (past,) = facts_of("When I was a kid, I lived in Ohio.")
    assert past["metadata"]["temporal"] == "past"
    (plain,) = facts_of("I live in Boston.")
    assert "correction" not in plain["metadata"]
    assert "temporal" not in plain["metadata"]


# --------------------------------------------------------------------------
# Entry contract (unchanged for existing consumers)
# --------------------------------------------------------------------------


def test_entry_shape_and_attribution() -> None:
    entries = _extract_entries(
        session_id="s-shape",
        user_message="Hi, my name is Bob and I work at Google. I live in Mountain View.",
        provider_response="Nice to meet you, Bob.",
        turn_id="turn-1",
    )
    facts = [entry for entry in entries if entry["type"] == "fact"]
    episodes = [entry for entry in entries if entry["type"] == "episode"]

    assert [fact["content"] for fact in facts] == [
        "Hi, my name is Bob and I work at Google.",
        "I live in Mountain View.",
    ]
    for fact in facts:
        metadata = fact["metadata"]
        assert metadata["session_id"] == "s-shape"
        assert metadata["source"] == "user_message"
        assert metadata["source_type"] == "explicit"
        assert metadata["extractor"] == "heuristic-v1"
        assert metadata["rules_version"] == 2
        assert metadata["turn_id"] == "turn-1"
        assert metadata["confidence"] == pytest.approx(0.72)
    assert len(episodes) == 1
    assert episodes[0]["metadata"]["source"] == "turn"
    assert episodes[0]["metadata"]["turn_id"] == "turn-1"


def test_repeated_statement_in_one_message_is_extracted_once() -> None:
    assert fact_texts("I live in Oslo. I live in Oslo!") == ["I live in Oslo."]


def test_fact_count_per_turn_stays_bounded() -> None:
    message = " ".join(f"I have {n} cats in house {n}." for n in range(1, 13))
    assert len(fact_texts(message)) == 8


def test_looks_like_user_fact_still_answers_for_one_sentence() -> None:
    assert extraction._looks_like_user_fact("I live in Oslo.")
    assert not extraction._looks_like_user_fact("If I lived in Oslo I would ski.")


async def test_extract_keeps_its_positional_signature() -> None:
    class Recorder:
        def __init__(self) -> None:
            self.entries: list[dict[str, Any]] = []

        async def persist(self, session_id: str, entries: Any) -> list[dict[str, Any]]:
            self.entries = list(entries)
            return self.entries

    storage = Recorder()
    written = await MemoryExtractor(storage=storage).extract("s-1", "I live in Oslo.", "Nice.")
    assert [entry["type"] for entry in written] == ["fact", "episode"]
    assert "turn_id" not in written[0]["metadata"]


# --------------------------------------------------------------------------
# Persistence: ownership, provenance columns, duplicate suppression
# --------------------------------------------------------------------------

# SQLite gives UUID columns numeric affinity; ids starting with a letter stay text.
USER_ID = uuid.UUID("abcdef01-0000-4000-8000-000000000001")
OTHER_USER_ID = uuid.UUID("abcdef02-0000-4000-8000-000000000002")
SESSION_ID = uuid.UUID("bcdef001-0000-4000-8000-000000000003")
OTHER_SESSION_ID = uuid.UUID("bcdef002-0000-4000-8000-000000000004")
TURN_ID = "cdef0001-0000-4000-8000-000000000005"

_TABLES = [models.User, models.Session, models.Message, models.SemanticFact, models.Episode]


@pytest.fixture
async def factory(tmp_path: Any) -> AsyncIterator[Any]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'extraction.db'}")
    async with engine.begin() as connection:
        for model in _TABLES:
            await connection.run_sync(lambda sync, m=model: m.__table__.create(sync))
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        for user_id, session_id, name in (
            (USER_ID, SESSION_ID, "a"),
            (OTHER_USER_ID, OTHER_SESSION_ID, "b"),
        ):
            db.add(
                models.User(
                    id=user_id, external_id=name, display_name=name, username=name,
                    email=f"{name}@example.com", password_hash="h",
                )
            )
            db.add(models.Session(id=session_id, user_id=user_id, title=name))
        await db.commit()
    yield session_factory
    await engine.dispose()


async def stored_facts(session_factory: Any) -> list[models.SemanticFact]:
    async with session_factory() as db:
        result = await db.execute(select(models.SemanticFact).order_by(models.SemanticFact.created_at))
        return list(result.scalars().all())


async def test_persisted_facts_carry_owner_and_provenance(factory: Any) -> None:
    written = await MemoryExtractor(database=factory).extract(
        str(SESSION_ID),
        "My name is Alice. Maybe I'll move to Rome. I live in Oslo.",
        "Hi Alice!",
        turn_id=TURN_ID,
    )

    assert [entry["type"] for entry in written] == ["fact", "fact", "episode"]
    facts = await stored_facts(factory)
    assert sorted(fact.fact_text for fact in facts) == ["I live in Oslo.", "My name is Alice."]
    for fact in facts:
        assert fact.user_id == USER_ID  # owner resolved from the session, as before
        assert fact.source_type == "explicit"
        assert fact.source_turn_id == uuid.UUID(TURN_ID)
        assert fact.source_message_id is None  # messages are not yet saved at extraction time
        assert fact.confidence == pytest.approx(0.72)


async def test_a_non_uuid_turn_id_is_not_forced_into_the_uuid_column(factory: Any) -> None:
    await MemoryExtractor(database=factory).extract(
        str(SESSION_ID), "I live in Oslo.", "", turn_id="not-a-uuid"
    )
    (fact,) = await stored_facts(factory)
    assert fact.source_turn_id is None


async def test_restating_an_active_fact_in_a_later_turn_adds_no_duplicate(factory: Any) -> None:
    extractor = MemoryExtractor(database=factory)
    await extractor.extract(str(SESSION_ID), "I live in Oslo.", "")
    second = await extractor.extract(str(SESSION_ID), "i live in oslo! I have two cats.", "")

    assert [entry["content"] for entry in second if entry["type"] == "fact"] == ["I have two cats."]
    assert [fact.fact_text for fact in await stored_facts(factory)] == [
        "I live in Oslo.",
        "I have two cats.",
    ]
    async with factory() as db:
        episodes = (await db.execute(select(models.Episode))).scalars().all()
    assert len(episodes) == 2  # one episode per turn, unchanged


async def test_duplicate_check_is_scoped_to_the_owner_and_ignores_deleted_facts(
    factory: Any,
) -> None:
    async with factory() as db:
        db.add(models.SemanticFact(user_id=OTHER_USER_ID, fact_text="I live in Oslo.", confidence=0.72))
        db.add(models.SemanticFact(user_id=USER_ID, fact_text="I have two cats.", confidence=0.0))
        await db.commit()

    written = await MemoryExtractor(database=factory).extract(
        str(SESSION_ID), "I live in Oslo. I have two cats.", ""
    )

    assert [entry["content"] for entry in written if entry["type"] == "fact"] == [
        "I live in Oslo.",
        "I have two cats.",
    ]
    owned = [fact for fact in await stored_facts(factory) if fact.user_id == USER_ID]
    assert sorted((fact.fact_text, fact.confidence > 0) for fact in owned) == [
        ("I have two cats.", False),
        ("I have two cats.", True),
        ("I live in Oslo.", True),
    ]


async def test_roleplay_turn_writes_only_its_episode(factory: Any) -> None:
    written = await MemoryExtractor(database=factory).extract(
        str(SESSION_ID), "Let's roleplay. I am the queen. I live in an ice castle.", "As you wish."
    )
    assert [entry["type"] for entry in written] == ["episode"]
    assert await stored_facts(factory) == []
