"""Focused tests for LLM-assisted memory extraction (Task 2.3b).

Every model response is mocked: no network, no real provider. The persistence
test uses a throwaway SQLite database.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator, Mapping, Sequence
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from chatbot.config import Settings
from chatbot.db import models
from chatbot.memory import llm_extraction
from chatbot.memory.extraction import MemoryExtractor, _extract_entries
from chatbot.memory.llm_extraction import (
    LLMExtractionLimits,
    LLMMemoryExtractor,
    parse_candidates,
    resolve_extraction_model,
    validate_candidates,
)
from chatbot.providers.base import (
    Provider,
    ProviderConfig,
    ProviderError,
    ProviderRegistry,
    ProviderResponse,
    ProviderTimeoutError,
)

SESSION = "session-1"
TURN = "turn-1"


class FakeProvider(Provider):
    """Returns a scripted response (or raises) and records every call."""

    def __init__(self, response: Any = None, *, delay: float = 0.0, model: str = "fake-model"):
        self.response = response
        self.delay = delay
        self.model = model
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        prompt: str,
        history: Sequence[Mapping[str, Any]],
        config: ProviderConfig,
    ) -> ProviderResponse:
        self.calls.append({"prompt": prompt, "history": list(history), "config": config})
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.response, BaseException):
            raise self.response
        text = self.response if isinstance(self.response, str) else json.dumps(self.response)
        return ProviderResponse(text=text, finish_reason="stop", model=self.model)


class Recorder:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []

    async def persist(self, session_id: str, entries: Sequence[dict[str, Any]]) -> list[dict]:
        self.calls.append((session_id, list(entries)))
        if self.fail:
            raise RuntimeError("database down")
        return list(entries)


def memory(
    content: str,
    evidence: str,
    *,
    type: str = "personal_fact",
    status: str = "real",
    confidence: Any = 0.9,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "type": type,
        "content": content,
        "evidence": evidence,
        "status": status,
        "confidence": confidence,
        **extra,
    }


def respond(*memories: dict[str, Any]) -> dict[str, Any]:
    return {"memories": list(memories)}


async def outcome_for(user_message: str, response: Any, provider_response: str = "Okay."):
    extractor = LLMMemoryExtractor(storage=Recorder(), provider=FakeProvider(response), model="m")
    return await extractor.extract_entries(
        session_id=SESSION,
        user_message=user_message,
        provider_response=provider_response,
        turn_id=TURN,
    )


def facts(outcome: Any) -> list[dict[str, Any]]:
    return [entry for entry in outcome.entries if entry["type"] == "fact"]


def reasons(outcome: Any) -> list[str]:
    return [rejection.reason for rejection in outcome.rejected]


# --------------------------------------------------------------------------
# Valid structured extraction
# --------------------------------------------------------------------------


async def test_valid_structured_response_becomes_attributed_fact_entries() -> None:
    message = "My name is Dana and I live in Seattle. I love hiking. I have a sister named Mia."
    provider = FakeProvider(
        respond(
            memory("User's name is Dana.", "My name is Dana"),
            memory("User lives in Seattle.", "I live in Seattle", confidence=0.95),
            memory("User loves hiking.", "I love hiking", type="preference"),
            memory("User has a sister named Mia.", "I have a sister named Mia", type="relationship"),
        )
    )
    extractor = LLMMemoryExtractor(storage=Recorder(), provider=provider, model="extract-model")

    outcome = await extractor.extract_entries(
        session_id=SESSION, user_message=message, provider_response="Hi Dana!", turn_id=TURN
    )

    assert outcome.method == "llm"
    assert outcome.fallback_reason is None
    assert outcome.rejected == ()
    assert [entry["content"] for entry in facts(outcome)] == [
        "User's name is Dana.",
        "User lives in Seattle.",
        "User loves hiking.",
        "User has a sister named Mia.",
    ]
    second = facts(outcome)[1]["metadata"]
    assert second == {
        "session_id": SESSION,
        "source": "user_message",
        "source_type": "explicit",
        "confidence": 0.9,  # capped at limits.max_confidence
        "extractor": "llm-v1",
        "kind": "personal_fact",
        "evidence": "I live in Seattle",
        "model": "fake-model",
        "turn_id": TURN,
    }
    assert [facts(outcome)[i]["metadata"]["kind"] for i in (2, 3)] == ["preference", "relationship"]
    # The per-turn episode is still recorded once, as by the heuristic extractor.
    assert [entry["type"] for entry in outcome.entries].count("episode") == 1


@pytest.mark.parametrize(
    ("message", "item"),
    [
        ("I work as a nurse at the county hospital.",
         memory("User works as a nurse at the county hospital.", "I work as a nurse")),
        ("Honestly I can't stand cilantro.",
         memory("User can't stand cilantro.", "I can't stand cilantro", type="preference")),
        ("I want to learn Japanese this year.",
         memory("User wants to learn Japanese.", "I want to learn Japanese", type="goal")),
        ("I'd like to run a marathon.",
         memory("User would like to run a marathon.", "I'd like to run a marathon", type="goal")),
        ("I graduated from nursing school last week!",
         memory("User graduated from nursing school last week.",
                "I graduated from nursing school last week", type="experience")),
        ("My dad passed away in 2019.",
         memory("User's dad passed away in 2019.", "My dad passed away in 2019", type="experience")),
        ("My partner Sam and I just adopted a cat.",
         memory("User's partner is Sam.", "My partner Sam", type="relationship")),
        ("I used to live in Paris.",
         memory("User used to live in Paris.", "I used to live in Paris", temporal="past")),
    ],
)
async def test_each_memory_type_is_accepted_when_grounded(message: str, item: dict) -> None:
    outcome = await outcome_for(message, respond(item))
    assert [entry["content"] for entry in facts(outcome)] == [item["content"]]
    assert facts(outcome)[0]["metadata"]["kind"] == item["type"]
    assert outcome.rejected == ()


async def test_past_temporal_and_explicit_remember_request_are_recorded() -> None:
    outcome = await outcome_for(
        "Please remember that I'm allergic to peanuts. I used to live in Paris.",
        respond(
            memory("User is allergic to peanuts.", "I'm allergic to peanuts"),
            memory("User used to live in Paris.", "I used to live in Paris", temporal="past"),
        ),
    )
    allergy, paris = (entry["metadata"] for entry in facts(outcome))
    assert allergy["explicit_request"] is True
    assert "temporal" not in allergy
    assert paris["temporal"] == "past"


async def test_curly_quotes_and_spacing_in_evidence_still_match() -> None:
    outcome = await outcome_for(
        "I’m  vegetarian.",
        respond(memory("User is vegetarian.", "I'm vegetarian", type="preference")),
    )
    assert [entry["content"] for entry in facts(outcome)] == ["User is vegetarian."]


async def test_code_fenced_json_is_accepted() -> None:
    text = "```json\n" + json.dumps(respond(memory("User lives in Oslo.", "I live in Oslo"))) + "\n```"
    outcome = await outcome_for("I live in Oslo.", text)
    assert outcome.method == "llm"
    assert len(facts(outcome)) == 1


async def test_empty_memory_list_is_trusted_without_fallback() -> None:
    outcome = await outcome_for("I live in Oslo.", respond())
    assert outcome.method == "llm"
    assert facts(outcome) == []  # the model's judgement replaces the heuristic one


# --------------------------------------------------------------------------
# Unsupported or invented information is rejected
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("item", "reason"),
    [
        (memory("User lives in Denver.", "I live in Denver"), "evidence_not_in_user_message"),
        (memory("User lives in Seattle with Jordan.", "I live in Seattle"), "unsupported_content"),
        (memory("User has lived in Seattle for 12 years.", "I live in Seattle"),
         "unsupported_content"),
        (memory("User is a software engineer.", "I live in Seattle"), "unsupported_content"),
        (memory("User lives in Seattle.", ""), "missing_evidence"),
        (memory("User lives in Seattle.", "I live in Seattle", type="opinion"), "unknown_type"),
        (memory("User lives in Seattle.", "I live in Seattle", confidence="high"),
         "bad_confidence"),
        (memory("User lives in Seattle.", "I live in Seattle", confidence=0.2), "low_confidence"),
        (memory("", "I live in Seattle"), "bad_content"),
        ({"type": "personal_fact", "content": "User lives in Seattle.",
          "evidence": "I live in Seattle"}, "missing_status"),
    ],
)
async def test_unsupported_or_invalid_candidates_are_rejected(item: dict, reason: str) -> None:
    outcome = await outcome_for("I live in Seattle.", respond(item))
    assert facts(outcome) == []
    assert reasons(outcome) == [reason]


async def test_assistant_text_cannot_ground_a_user_memory() -> None:
    outcome = await outcome_for(
        "Thanks for the chat.",
        respond(memory("User lives in Seattle.", "you live in Seattle")),
        provider_response="I'll remember that you live in Seattle.",
    )
    assert facts(outcome) == []
    assert reasons(outcome) == ["evidence_not_in_user_message"]


async def test_one_bad_candidate_does_not_discard_the_good_ones() -> None:
    outcome = await outcome_for(
        "I live in Seattle. I love jazz.",
        respond(
            memory("User lives in Seattle.", "I live in Seattle"),
            memory("User plays saxophone in a band.", "I love jazz", type="preference"),
            memory("User loves jazz.", "I love jazz", type="preference"),
        ),
    )
    assert [entry["content"] for entry in facts(outcome)] == [
        "User lives in Seattle.",
        "User loves jazz.",
    ]
    assert reasons(outcome) == ["unsupported_content"]


# --------------------------------------------------------------------------
# Hypotheticals, roleplay, fiction and uncertainty are excluded
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["hypothetical", "roleplay", "fiction", "uncertain", "joke"])
async def test_non_real_status_from_the_model_is_never_stored(status: str) -> None:
    outcome = await outcome_for(
        "I live in Seattle.", respond(memory("User lives in Seattle.", "I live in Seattle", status=status))
    )
    assert facts(outcome) == []
    assert reasons(outcome) == [f"status_{status}"]


@pytest.mark.parametrize(
    ("message", "item", "reason"),
    [
        ("If I had a dog, I would call him Max.",
         memory("User has a dog named Max.", "I had a dog", type="relationship"), "hypothetical"),
        ("Imagine I lived in Tokyo.",
         memory("User lives in Tokyo.", "I lived in Tokyo"), "hypothetical"),
        ("I wish I could learn piano.",
         memory("User wants to learn piano.", "learn piano", type="goal"), "hypothetical"),
        ("I think I'm allergic to shellfish.",
         memory("User is allergic to shellfish.", "I'm allergic to shellfish"), "uncertain"),
        ("Maybe I live in the wrong city.",
         memory("User lives in the wrong city.", "I live in the wrong city"), "uncertain"),
        ("In my novel, I live in a lighthouse.",
         memory("User lives in a lighthouse.", "I live in a lighthouse"), "fiction"),
        ("I dreamed I was a pilot.",
         memory("User was a pilot.", "I was a pilot", type="experience"), "fiction"),
        ("Let's pretend I'm a pirate captain. I have a parrot named Polly.",
         memory("User has a parrot named Polly.", "I have a parrot named Polly",
                type="relationship"), "roleplay"),
        ("*draws my sword* I am the king of this land.",
         memory("User is the king of this land.", "I am the king of this land"), "roleplay"),
        ('She said "I live in Rome" and left.',
         memory("User lives in Rome.", "I live in Rome"), "quoted_or_action"),
        ("I love sushi. Just kidding.",
         memory("User loves sushi.", "I love sushi", type="preference"), "retracted"),
        ("Do I live in Seattle?",
         memory("User lives in Seattle.", "I live in Seattle"), "question"),
        ("Don't remember that I live in Seattle.",
         memory("User lives in Seattle.", "I live in Seattle"), "do_not_remember"),
    ],
)
async def test_deterministic_guard_overrides_a_model_that_says_real(
    message: str, item: dict, reason: str
) -> None:
    outcome = await outcome_for(message, respond(item))
    assert facts(outcome) == []
    assert reasons(outcome) == [reason]


async def test_out_of_character_statement_inside_roleplay_is_kept() -> None:
    outcome = await outcome_for(
        "*bows deeply* Greetings, traveller. OOC: I live in Ohio.",
        respond(
            memory("User is a traveller.", "Greetings, traveller", status="roleplay"),
            memory("User lives in Ohio.", "I live in Ohio"),
        ),
    )
    assert [entry["content"] for entry in facts(outcome)] == ["User lives in Ohio."]
    assert reasons(outcome) == ["status_roleplay"]


# --------------------------------------------------------------------------
# Corrections
# --------------------------------------------------------------------------


async def test_correction_is_extracted_with_the_claim_it_corrects() -> None:
    outcome = await outcome_for(
        "Actually, I moved to Boston last year, not Denver.",
        respond(
            memory(
                "User moved to Boston last year, not Denver.",
                "I moved to Boston last year, not Denver",
                type="correction",
                corrects="User lives in Denver.",
            )
        ),
    )
    [fact] = facts(outcome)
    assert fact["content"] == "User moved to Boston last year, not Denver."
    assert fact["metadata"]["kind"] == "correction"
    assert fact["metadata"]["correction"] is True
    assert fact["metadata"]["corrects"] == "User lives in Denver."


async def test_corrects_is_ignored_on_non_correction_types() -> None:
    outcome = await outcome_for(
        "I live in Boston.",
        respond(memory("User lives in Boston.", "I live in Boston", corrects="User lives in Denver.")),
    )
    assert "corrects" not in facts(outcome)[0]["metadata"]
    assert "correction" not in facts(outcome)[0]["metadata"]


# --------------------------------------------------------------------------
# Fallback to heuristic extraction
# --------------------------------------------------------------------------

FALLBACK_MESSAGE = "My name is Alice. I live in Oslo."


def heuristic_entries(reason: str) -> list[dict[str, Any]]:
    entries = _extract_entries(
        session_id=SESSION, user_message=FALLBACK_MESSAGE, provider_response="Okay.", turn_id=TURN
    )
    for entry in entries:
        entry["metadata"]["llm_fallback"] = reason
    return entries


@pytest.mark.parametrize(
    "text",
    [
        "Sure! Alice lives in Oslo.",
        '{"memories": [{"type": "personal_fact", "content": "User',  # truncated
        '{"facts": []}',
        '{"memories": "none"}',
        "",
        "[" + ",".join(["{}"] * 4000) + "]",  # larger than max_response_chars
    ],
)
async def test_malformed_response_falls_back_to_heuristics(text: str) -> None:
    outcome = await outcome_for(FALLBACK_MESSAGE, text)
    assert outcome.method == "heuristic"
    assert outcome.fallback_reason == "malformed_response"
    assert outcome.entries == heuristic_entries("malformed_response")
    assert [entry["content"] for entry in facts(outcome)] == ["My name is Alice.", "I live in Oslo."]


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ProviderError("502 bad gateway"), "provider_error"),
        (RuntimeError("connection reset"), "provider_error"),
        (ValueError("model must not be empty"), "provider_error"),
        (ProviderTimeoutError("timed out"), "timeout"),
    ],
)
async def test_provider_failure_falls_back_to_heuristics(error: Exception, reason: str) -> None:
    outcome = await outcome_for(FALLBACK_MESSAGE, error)
    assert outcome.method == "heuristic"
    assert outcome.fallback_reason == reason
    assert outcome.entries == heuristic_entries(reason)


async def test_slow_provider_is_cut_off_at_the_timeout() -> None:
    provider = FakeProvider(respond(), delay=5.0)
    extractor = LLMMemoryExtractor(
        storage=Recorder(), provider=provider, limits=LLMExtractionLimits(timeout_seconds=0.05)
    )
    outcome = await asyncio.wait_for(
        extractor.extract_entries(
            session_id=SESSION, user_message=FALLBACK_MESSAGE, provider_response="Okay.", turn_id=TURN
        ),
        timeout=2.0,
    )
    assert outcome.fallback_reason == "timeout"
    assert outcome.entries == heuristic_entries("timeout")


async def test_missing_provider_uses_heuristics_without_a_call() -> None:
    extractor = LLMMemoryExtractor(storage=Recorder(), provider=None)
    outcome = await extractor.extract_entries(
        session_id=SESSION, user_message=FALLBACK_MESSAGE, provider_response="Okay.", turn_id=TURN
    )
    assert outcome.fallback_reason == "no_provider"
    assert outcome.entries == heuristic_entries("no_provider")


async def test_turn_without_user_text_makes_no_model_call() -> None:
    provider = FakeProvider(respond())
    extractor = LLMMemoryExtractor(storage=Recorder(), provider=provider)
    outcome = await extractor.extract_entries(
        session_id=SESSION, user_message="  ", provider_response="Hello there."
    )
    assert provider.calls == []
    assert [entry["type"] for entry in outcome.entries] == ["episode"]


# --------------------------------------------------------------------------
# Bounded input, output and time
# --------------------------------------------------------------------------


async def test_request_is_bounded_and_deterministic() -> None:
    provider = FakeProvider(respond())
    limits = LLMExtractionLimits(max_user_chars=200, max_assistant_chars=50, max_output_tokens=321,
                                 timeout_seconds=7.5)
    extractor = LLMMemoryExtractor(
        storage=Recorder(), provider=provider, model="extract-model", limits=limits
    )
    await extractor.extract_entries(
        session_id=SESSION, user_message="I live in Oslo. " + "x" * 5_000, provider_response="y" * 5_000
    )

    [call] = provider.calls
    config: ProviderConfig = call["config"]
    assert config.model == "extract-model"
    assert config.max_tokens == 321
    assert config.timeout_seconds == 7.5
    assert config.temperature == 0.0
    assert call["history"] == [{"role": "system", "content": llm_extraction.SYSTEM_PROMPT}]
    assert "x" * 200 not in call["prompt"]
    assert "y" * 51 not in call["prompt"]
    assert len(call["prompt"]) < 600


async def test_evidence_beyond_the_bounded_input_is_rejected() -> None:
    limits = LLMExtractionLimits(max_user_chars=40)
    extractor = LLMMemoryExtractor(
        storage=Recorder(),
        provider=FakeProvider(respond(memory("User lives in Oslo.", "I live in Oslo"))),
        limits=limits,
    )
    outcome = await extractor.extract_entries(
        session_id=SESSION, user_message="a" * 60 + ". I live in Oslo.", provider_response=""
    )
    assert reasons(outcome) == ["evidence_not_in_user_message"]


def test_candidate_and_memory_counts_are_capped() -> None:
    message = " ".join(f"I live in City{i}." for i in range(20))
    candidates = [memory(f"User lives in City{i}.", f"I live in City{i}") for i in range(20)]
    limits = LLMExtractionLimits(max_candidates=12, max_memories=8)
    accepted, rejected = validate_candidates(candidates, message, limits)
    assert len(accepted) == 8
    assert [r.reason for r in rejected] == ["over_memory_limit"] * 4 + ["over_candidate_limit"] * 8


def test_duplicate_candidates_are_dropped() -> None:
    candidates = [memory("User lives in Oslo.", "I live in Oslo")] * 2
    accepted, rejected = validate_candidates(candidates, "I live in Oslo.")
    assert len(accepted) == 1
    assert [r.reason for r in rejected] == ["duplicate"]


def test_parse_candidates_skips_non_object_items() -> None:
    assert parse_candidates('{"memories": [1, "x", {"type": "goal"}]}') == [{"type": "goal"}]
    assert parse_candidates("[]") == []
    assert parse_candidates(None) is None


# --------------------------------------------------------------------------
# Independent model selection
# --------------------------------------------------------------------------


def make_settings(**overrides: Any) -> Settings:
    values = {
        "llm_provider": "openrouter",
        "default_model": "google/gemma-4-31b-it",
        "openrouter_api_key": "test-key",
        **overrides,
    }
    return Settings(_env_file=None, **values)


@pytest.fixture
def use_settings(monkeypatch: pytest.MonkeyPatch):
    def apply(settings: Settings) -> Settings:
        # validate_identifier() reads the global settings for the active provider.
        monkeypatch.setattr("chatbot.config.get_settings", lambda: settings)
        return settings

    return apply


def test_extraction_model_comes_from_the_extract_task_class(use_settings: Any) -> None:
    settings = use_settings(make_settings(task_class_extract_model="openrouter/deepseek/deepseek-v3.2"))
    assert resolve_extraction_model(settings) == ("openrouter", "deepseek/deepseek-v3.2")
    assert settings.default_model == "google/gemma-4-31b-it"  # conversation model untouched


def test_default_extract_route_is_used_when_not_overridden(use_settings: Any) -> None:
    settings = use_settings(make_settings())
    assert resolve_extraction_model(settings) == ("openrouter", "qwen/qwen3-coder-next")


def test_extraction_provider_is_independent_of_the_chat_provider(use_settings: Any) -> None:
    settings = use_settings(
        make_settings(task_class_extract_provider="ollama", task_class_extract_model="llama3.1:8b")
    )
    assert settings.default_provider == "openrouter"
    assert resolve_extraction_model(settings) == ("ollama", "llama3.1:8b")


async def test_from_settings_calls_the_extraction_provider_with_the_extraction_model(
    use_settings: Any,
) -> None:
    settings = use_settings(
        make_settings(task_class_extract_provider="ollama", task_class_extract_model="llama3.1:8b")
    )
    chat_provider = FakeProvider(respond())
    extract_provider = FakeProvider(respond(memory("User lives in Oslo.", "I live in Oslo")))
    registry = ProviderRegistry()
    registry.register("openrouter", chat_provider)
    registry.register("ollama", extract_provider)

    extractor = LLMMemoryExtractor.from_settings(
        storage=Recorder(), settings=settings, registry=registry
    )
    outcome = await extractor.extract_entries(
        session_id=SESSION, user_message="I live in Oslo.", provider_response=""
    )

    assert chat_provider.calls == []
    assert [call["config"].model for call in extract_provider.calls] == ["llama3.1:8b"]
    assert outcome.method == "llm"


async def test_unregistered_extraction_provider_falls_back_to_heuristics(use_settings: Any) -> None:
    settings = use_settings(make_settings(task_class_extract_provider="vllm",
                                          task_class_extract_model="local-model"))
    registry = ProviderRegistry()
    registry.register("openrouter", FakeProvider(respond()))
    extractor = LLMMemoryExtractor.from_settings(storage=Recorder(), settings=settings, registry=registry)
    assert extractor.provider is None
    outcome = await extractor.extract_entries(
        session_id=SESSION, user_message=FALLBACK_MESSAGE, provider_response="Okay.", turn_id=TURN
    )
    assert outcome.fallback_reason == "no_provider"


# --------------------------------------------------------------------------
# Callable interface for durable jobs; no interactive-path calls
# --------------------------------------------------------------------------


async def test_extract_persists_through_the_storage_adapter_with_positional_args() -> None:
    storage = Recorder()
    extractor = LLMMemoryExtractor(
        storage=storage, provider=FakeProvider(respond(memory("User lives in Oslo.", "I live in Oslo")))
    )
    written = await extractor.extract("s-9", "I live in Oslo.", "Nice!")

    assert [entry["type"] for entry in written] == ["fact", "episode"]
    assert storage.calls[0][0] == "s-9"
    assert extractor.last_outcome is not None and extractor.last_outcome.method == "llm"


async def test_extract_without_storage_makes_no_model_call() -> None:
    provider = FakeProvider(respond())
    assert await LLMMemoryExtractor(provider=provider).extract("s", "I live in Oslo.", "") == []
    assert provider.calls == []


async def test_persistence_errors_are_swallowed_by_default_and_raised_on_request() -> None:
    response = respond(memory("User lives in Oslo.", "I live in Oslo"))
    quiet = LLMMemoryExtractor(storage=Recorder(fail=True), provider=FakeProvider(response))
    assert await quiet.extract("s", "I live in Oslo.", "") == []

    strict = LLMMemoryExtractor(
        storage=Recorder(fail=True), provider=FakeProvider(response), propagate_errors=True
    )
    with pytest.raises(RuntimeError, match="database down"):
        await strict.extract("s", "I live in Oslo.", "")


def test_llm_extractor_is_a_drop_in_memory_extractor() -> None:
    assert issubclass(LLMMemoryExtractor, MemoryExtractor)


def test_interactive_conversation_path_never_uses_the_llm_extractor() -> None:
    root = Path(llm_extraction.__file__).resolve().parents[1]
    for relative in ("core", "api", "avatar"):
        for path in (root / relative).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "llm_extraction" not in text, path
            assert "LLMMemoryExtractor" not in text, path


async def test_inline_heuristic_extractor_makes_no_provider_call() -> None:
    storage = Recorder()
    written = await MemoryExtractor(storage=storage).extract(SESSION, "I live in Oslo.", "Nice.")
    assert [entry["metadata"]["extractor"] for entry in written] == ["heuristic-v1"] * 2


# --------------------------------------------------------------------------
# Persistence through the existing storage (SQLite)
# --------------------------------------------------------------------------

USER_ID = uuid.UUID("abcdef01-0000-4000-8000-000000000001")
SESSION_ID = uuid.UUID("bcdef001-0000-4000-8000-000000000003")
TURN_ID = "cdef0001-0000-4000-8000-000000000005"


@pytest.fixture
async def factory(tmp_path: Any) -> AsyncIterator[Any]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'llm.db'}")
    async with engine.begin() as connection:
        for model in (models.User, models.Session, models.Message, models.SemanticFact,
                      models.Episode):
            await connection.run_sync(lambda sync, m=model: m.__table__.create(sync))
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        db.add(models.User(id=USER_ID, external_id="a", display_name="a", username="a",
                           email="a@example.com", password_hash="h"))
        db.add(models.Session(id=SESSION_ID, user_id=USER_ID, title="a"))
        await db.commit()
    yield session_factory
    await engine.dispose()


async def test_llm_facts_persist_with_owner_provenance_and_dedup(factory: Any) -> None:
    response = respond(memory("User lives in Oslo.", "I live in Oslo", confidence=0.8))
    for _ in range(2):  # the second turn restates the same fact
        extractor = LLMMemoryExtractor(database=factory, provider=FakeProvider(response))
        await extractor.extract(str(SESSION_ID), "I live in Oslo.", "Nice!", turn_id=TURN_ID)

    async with factory() as db:
        stored = list((await db.execute(select(models.SemanticFact))).scalars().all())
        episodes = list((await db.execute(select(models.Episode))).scalars().all())
    assert [fact.fact_text for fact in stored] == ["User lives in Oslo."]
    assert stored[0].user_id == USER_ID
    assert stored[0].source_type == "explicit"
    assert stored[0].source_turn_id == uuid.UUID(TURN_ID)
    assert stored[0].confidence == pytest.approx(0.8)
    assert len(episodes) == 2
