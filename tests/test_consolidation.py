from __future__ import annotations

import asyncio
import copy
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.consolidation.contradiction import ContradictionResolver, ContradictionResult
from chatbot.consolidation.decay import MemoryDecayEngine
from chatbot.consolidation.summarizer import Summarizer
from chatbot.consolidation.worker import ConsolidationResult, ConsolidationWorker
from chatbot.providers import Provider, ProviderConfig, ProviderResponse


class MockProvider(Provider):
    def __init__(self, responses: Sequence[str] | None = None) -> None:
        self.responses = list(responses or ["mock summary"])
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        prompt: str,
        history: Sequence[Mapping[str, Any]],
        config: ProviderConfig,
    ) -> ProviderResponse:
        self.calls.append(
            {
                "prompt": prompt,
                "history": list(history),
                "config": config,
            }
        )
        if len(self.calls) <= len(self.responses):
            text = self.responses[len(self.calls) - 1]
        else:
            text = self.responses[-1]
        return ProviderResponse(text=text, finish_reason="stop", model=config.model)


class MockSummarizer:
    def __init__(
        self,
        *,
        turn_summary: str = "turn summary",
        session_summary: str = "session summary",
        long_term_summary: str = "long-term summary",
    ) -> None:
        self.turn_summary = turn_summary
        self.session_summary = session_summary
        self.long_term_summary = long_term_summary
        self.turn_calls: list[list[Any]] = []
        self.session_calls: list[list[str]] = []
        self.long_term_calls: list[list[str]] = []

    async def summarize_turn(self, messages: list[Any]) -> str:
        self.turn_calls.append(list(messages))
        return self.turn_summary

    async def summarize_session(self, turn_summaries: list[str]) -> str:
        self.session_calls.append(list(turn_summaries))
        return self.session_summary

    async def summarize_long_term(self, session_summaries: list[str]) -> str:
        self.long_term_calls.append(list(session_summaries))
        return self.long_term_summary


class MockContradictionResolver:
    def __init__(self, result: ContradictionResult | None = None) -> None:
        self.result = result or ContradictionResult(False, None, 1.0, [])
        self.calls: list[tuple[Any, Any]] = []

    async def resolve(self, entry_a: Any, entry_b: Any) -> ContradictionResult:
        self.calls.append((entry_a, entry_b))
        return self.result


class MockDecayEngine:
    def __init__(self, kept_entries: Sequence[Any]) -> None:
        self.kept_entries = list(kept_entries)
        self.calls: list[list[Any]] = []

    def prune(self, entries: list[Any]) -> list[Any]:
        self.calls.append(list(entries))
        return list(self.kept_entries)


class MockTransaction:
    def __init__(self, store: MockMemoryStore) -> None:
        self.store = store
        self.snapshot: dict[str, Any] | None = None

    async def __aenter__(self) -> MockTransaction:
        self.snapshot = self.store.snapshot()
        self.store.transaction_entries += 1
        return self

    async def __aexit__(self, exc_type: object, *_exc_info: object) -> None:
        if exc_type is not None and self.snapshot is not None:
            self.store.restore(self.snapshot)
        self.store.transaction_exits += 1
        return None


class MockMemoryStore:
    def __init__(
        self,
        entries: Sequence[dict[str, Any]],
        *,
        fail_on_prune: bool = False,
    ) -> None:
        self.unsummarized_entries = list(entries)
        self.recent_entries = list(entries)
        self.all_entries = list(entries)
        self.fail_on_prune = fail_on_prune
        self.applied_resolutions: list[dict[str, Any]] = []
        self.saved_summaries: list[dict[str, Any]] = []
        self.summarized_ids: list[str] = []
        self.pruned_entries: list[dict[str, Any]] = []
        self.last_run: datetime | None = None
        self.transaction_entries = 0
        self.transaction_exits = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "applied_resolutions": copy.deepcopy(self.applied_resolutions),
            "saved_summaries": copy.deepcopy(self.saved_summaries),
            "summarized_ids": list(self.summarized_ids),
            "pruned_entries": copy.deepcopy(self.pruned_entries),
            "last_run": self.last_run,
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        self.applied_resolutions = snapshot["applied_resolutions"]
        self.saved_summaries = snapshot["saved_summaries"]
        self.summarized_ids = snapshot["summarized_ids"]
        self.pruned_entries = snapshot["pruned_entries"]
        self.last_run = snapshot["last_run"]

    async def transaction(self, session_id: str) -> MockTransaction:
        return MockTransaction(self)

    async def fetch_unsummarized_memory_entries(self, session_id: str) -> list[dict[str, Any]]:
        return list(self.unsummarized_entries)

    async def fetch_recent_memory_entries(
        self,
        session_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        return list(self.recent_entries[:limit])

    async def fetch_all_memory_entries(self, session_id: str) -> list[dict[str, Any]]:
        return list(self.all_entries)

    async def apply_contradiction_resolution(
        self,
        session_id: str,
        entry_a: dict[str, Any],
        entry_b: dict[str, Any],
        result: ContradictionResult,
    ) -> None:
        self.applied_resolutions.append(
            {
                "session_id": session_id,
                "entry_ids": [entry_a["id"], entry_b["id"]],
                "resolution": result.resolution,
            }
        )

    async def persist_consolidation_summaries(
        self,
        session_id: str,
        entries: Sequence[dict[str, Any]],
        turn_summaries: list[str],
        session_summary: str,
        long_term_summary: str,
    ) -> None:
        self.saved_summaries.append(
            {
                "session_id": session_id,
                "entry_ids": [entry["id"] for entry in entries],
                "turn_summaries": list(turn_summaries),
                "session_summary": session_summary,
                "long_term_summary": long_term_summary,
            }
        )

    async def mark_entries_summarized(
        self,
        session_id: str,
        entries: Sequence[dict[str, Any]],
    ) -> None:
        self.summarized_ids.extend(entry["id"] for entry in entries)

    async def persist_pruned_set(
        self,
        session_id: str,
        kept_entries: Sequence[dict[str, Any]],
        pruned_entries: Sequence[dict[str, Any]],
    ) -> bool:
        if self.fail_on_prune:
            raise RuntimeError("simulated db failure")
        self.pruned_entries.extend(dict(entry) for entry in pruned_entries)
        self.all_entries = list(kept_entries)
        return True

    async def get_last_consolidation_run(self, session_id: str) -> datetime | None:
        return self.last_run

    async def set_last_consolidation_run(
        self,
        session_id: str,
        run_timestamp: datetime,
    ) -> None:
        self.last_run = run_timestamp


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def memory_entries(fixed_now: datetime) -> list[dict[str, Any]]:
    return [
        {
            "id": "entry-1",
            "role": "user",
            "content": "User timezone is UTC",
            "created_at": fixed_now,
            "metadata": {"relevance": 1.0},
        },
        {
            "id": "entry-2",
            "role": "assistant",
            "content": "User timezone is PST",
            "created_at": fixed_now - timedelta(days=30),
            "metadata": {"relevance": 0.2},
        },
    ]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_summarize_turn_returns_non_empty_string_for_valid_messages() -> None:
    provider = MockProvider(["User prefers concise answers."])
    summarizer = Summarizer(provider)

    summary = run(
        summarizer.summarize_turn(
            [
                {"role": "user", "content": "Please remember I like concise answers."},
                {"role": "assistant", "content": "I will keep responses concise."},
            ]
        )
    )

    assert summary == "User prefers concise answers."
    assert summary.strip()
    assert len(provider.calls) == 1


def test_summarize_session_rolls_up_multiple_turn_summaries() -> None:
    provider = MockProvider(["Session summary"])
    summarizer = Summarizer(provider)

    summary = run(
        summarizer.summarize_session(
            [
                "User prefers concise answers.",
                "User is building consolidation tests.",
            ]
        )
    )

    assert summary == "Session summary"
    prompt = provider.calls[0]["prompt"]
    assert "User prefers concise answers." in prompt
    assert "User is building consolidation tests." in prompt


def test_summarize_long_term_handles_empty_input_gracefully() -> None:
    provider = MockProvider(["should not be used"])
    summarizer = Summarizer(provider)

    assert run(summarizer.summarize_long_term([])) == ""
    assert provider.calls == []


def test_summarizer_is_idempotent_for_same_input() -> None:
    provider = MockProvider(["stable summary", "different summary"])
    summarizer = Summarizer(provider)
    messages = [{"role": "user", "content": "Remember that my timezone is UTC."}]

    first = run(summarizer.summarize_turn(messages))
    second = run(summarizer.summarize_turn(messages))

    assert first == second == "stable summary"
    assert len(provider.calls) == 1


def test_rule_based_subject_value_contradiction_is_detected() -> None:
    resolver = ContradictionResolver()

    result = run(
        resolver.resolve(
            {"content": "User timezone is UTC", "subject": "user timezone", "value": "UTC"},
            {"content": "User timezone is PST", "subject": "user timezone", "value": "PST"},
        )
    )

    assert result.has_contradiction is True
    assert result.confidence == pytest.approx(0.95)
    assert result.contradictory_facts == ["User timezone is PST", "User timezone is UTC"]


@pytest.mark.parametrize(
    ("entry_a", "entry_b"),
    [
        (
            {"content": "User timezone is UTC", "subject": "user timezone", "value": "UTC"},
            {"content": "User city is Boston", "subject": "user city", "value": "Boston"},
        ),
        (
            {"content": "User timezone is UTC", "subject": "user timezone", "value": "UTC"},
            {"content": "Timezone: UTC", "subject": "user timezone", "value": "UTC"},
        ),
    ],
)
def test_rule_based_non_contradictions_are_not_detected(
    entry_a: dict[str, Any],
    entry_b: dict[str, Any],
) -> None:
    resolver = ContradictionResolver()

    result = run(resolver.resolve(entry_a, entry_b))

    assert result.has_contradiction is False
    assert result.resolution is None
    assert result.contradictory_facts == []


def test_temporal_contradiction_with_incorrect_ordering_is_detected() -> None:
    resolver = ContradictionResolver()

    result = run(
        resolver.resolve(
            {"content": "Migration happened before launch."},
            {"content": "Launch happened before migration."},
        )
    )

    assert result.has_contradiction is True
    assert result.confidence == pytest.approx(0.95)


def test_preference_reversal_is_detected() -> None:
    resolver = ContradictionResolver()

    result = run(
        resolver.resolve(
            {"content": "I like dark mode."},
            {"content": "I do not like dark mode."},
        )
    )

    assert result.has_contradiction is True
    assert result.confidence == pytest.approx(0.9)


@pytest.mark.parametrize(
    ("entry_a", "entry_b"),
    [
        (None, {"content": "User timezone is UTC"}),
        ({"content": ""}, {"content": "User timezone is UTC"}),
        ({}, {"content": "User timezone is UTC"}),
    ],
)
def test_empty_or_none_contradiction_entries_are_handled_without_exception(
    entry_a: dict[str, Any] | None,
    entry_b: dict[str, Any],
) -> None:
    resolver = ContradictionResolver()

    result = run(resolver.resolve(entry_a, entry_b))

    assert result == ContradictionResult(False, None, 1.0, [])


def test_contradiction_resolver_is_idempotent_for_same_pair() -> None:
    resolver = ContradictionResolver()
    entry_a = {"content": "User timezone is UTC", "subject": "user timezone", "value": "UTC"}
    entry_b = {"content": "User timezone is PST", "subject": "user timezone", "value": "PST"}

    first = run(resolver.resolve(entry_a, entry_b))
    second = run(resolver.resolve(entry_a, entry_b))

    assert first == second


def test_llm_fallback_is_called_only_for_ambiguous_cases() -> None:
    provider = MockProvider(
        [
            (
                '{"has_contradiction": false, "resolution": null, '
                '"confidence": 0.4, "contradictory_facts": []}'
            )
        ]
    )
    resolver = ContradictionResolver(provider)

    clear_result = run(
        resolver.resolve(
            {"content": "User timezone is UTC", "subject": "user timezone", "value": "UTC"},
            {"content": "User timezone is PST", "subject": "user timezone", "value": "PST"},
        )
    )
    unrelated_result = run(
        resolver.resolve(
            {"content": "User likes coffee."},
            {"content": "The deployment finished."},
        )
    )
    ambiguous_result = run(
        resolver.resolve(
            {"content": "Project Apollo uses fast tests.", "subject": "project apollo"},
            {"content": "Project Apollo should keep tests focused.", "subject": "project apollo"},
        )
    )

    assert clear_result.has_contradiction is True
    assert unrelated_result.has_contradiction is False
    assert ambiguous_result.has_contradiction is False
    assert ambiguous_result.confidence == pytest.approx(0.4)
    assert len(provider.calls) == 1


def test_entries_older_than_half_life_receive_lower_decay_scores(fixed_now: datetime) -> None:
    engine = MemoryDecayEngine(
        base_half_life_seconds=100.0,
        relevance_threshold=0.0,
        reference_time=fixed_now,
    )
    recent = {"metadata": {"created_at": fixed_now, "relevance": 1.0}}
    old = {"metadata": {"created_at": fixed_now - timedelta(seconds=200), "relevance": 1.0}}

    assert engine.decay_score(old) < engine.decay_score(recent)


def test_recently_accessed_entries_are_boosted(fixed_now: datetime) -> None:
    engine = MemoryDecayEngine(
        base_half_life_seconds=100.0,
        access_count_weight=1.0,
        relevance_threshold=0.0,
        reference_time=fixed_now,
    )
    base_entry = {
        "metadata": {
            "created_at": fixed_now - timedelta(seconds=200),
            "relevance": 1.0,
        }
    }
    accessed_entry = {
        "metadata": {
            "created_at": fixed_now - timedelta(seconds=200),
            "last_accessed_at": fixed_now,
            "access_count": 10,
            "relevance": 1.0,
        }
    }

    assert engine.decay_score(accessed_entry) > engine.decay_score(base_entry)


def test_entries_below_relevance_threshold_are_pruned(fixed_now: datetime) -> None:
    engine = MemoryDecayEngine(relevance_threshold=0.5, reference_time=fixed_now)
    keep = {"id": "keep", "metadata": {"created_at": fixed_now, "relevance": 0.8}}
    prune = {"id": "prune", "metadata": {"created_at": fixed_now, "relevance": 0.2}}

    assert engine.prune([keep, prune]) == [keep]


def test_memory_decay_empty_input_returns_empty_list(fixed_now: datetime) -> None:
    engine = MemoryDecayEngine(reference_time=fixed_now)

    assert engine.prune([]) == []


def test_all_entries_above_threshold_are_not_pruned(fixed_now: datetime) -> None:
    entries = [
        {"id": "one", "metadata": {"created_at": fixed_now, "relevance": 0.9}},
        {"id": "two", "metadata": {"created_at": fixed_now, "relevance": 0.8}},
    ]
    engine = MemoryDecayEngine(relevance_threshold=0.5, reference_time=fixed_now)

    assert engine.prune(entries) == entries


def test_all_entries_below_threshold_are_pruned(fixed_now: datetime) -> None:
    entries = [
        {"id": "one", "metadata": {"created_at": fixed_now, "relevance": 0.2}},
        {"id": "two", "metadata": {"created_at": fixed_now, "relevance": 0.1}},
    ]
    engine = MemoryDecayEngine(relevance_threshold=0.5, reference_time=fixed_now)

    assert engine.prune(entries) == []


def test_memory_decay_is_idempotent_for_same_inputs(fixed_now: datetime) -> None:
    entries = [
        {"id": "keep", "metadata": {"created_at": fixed_now, "relevance": 0.8}},
        {"id": "prune", "metadata": {"created_at": fixed_now, "relevance": 0.2}},
    ]
    engine = MemoryDecayEngine(relevance_threshold=0.5, reference_time=fixed_now)

    first = engine.prune(entries)
    second = engine.prune(entries)

    assert first == second == [entries[0]]


def test_run_consolidation_runs_full_pipeline_when_work_exists(
    memory_entries: list[dict[str, Any]],
) -> None:
    store = MockMemoryStore(memory_entries)
    summarizer = MockSummarizer()
    contradiction = ContradictionResult(True, "User timezone is UTC", 0.95, [])
    resolver = MockContradictionResolver(contradiction)
    decay = MockDecayEngine([memory_entries[0]])
    worker = ConsolidationWorker(
        MockProvider(),
        store,
        summarizer=summarizer,
        contradiction_resolver=resolver,
        decay_engine=decay,
    )

    result = run(worker.run_consolidation("session-a"))

    assert result.summaries_produced == 3
    assert result.contradictions_resolved == 1
    assert result.entries_pruned == 1
    assert isinstance(result.run_timestamp, datetime)
    assert result.run_timestamp.tzinfo is not None
    assert len(resolver.calls) == 1
    assert len(summarizer.turn_calls) == 1
    assert decay.calls == [memory_entries]
    assert store.applied_resolutions == [
        {
            "session_id": "session-a",
            "entry_ids": ["entry-1", "entry-2"],
            "resolution": "User timezone is UTC",
        }
    ]
    assert store.saved_summaries == [
        {
            "session_id": "session-a",
            "entry_ids": ["entry-1", "entry-2"],
            "turn_summaries": ["turn summary"],
            "session_summary": "session summary",
            "long_term_summary": "long-term summary",
        }
    ]
    assert store.summarized_ids == ["entry-1", "entry-2"]
    assert store.pruned_entries == [memory_entries[1]]
    assert store.last_run == result.run_timestamp


def test_coalescing_window_second_call_returns_empty_result(
    memory_entries: list[dict[str, Any]],
) -> None:
    store = MockMemoryStore(memory_entries)
    resolver = MockContradictionResolver()
    worker = ConsolidationWorker(
        MockProvider(),
        store,
        coalescing_window_seconds=3600,
        summarizer=MockSummarizer(),
        contradiction_resolver=resolver,
        decay_engine=MockDecayEngine(memory_entries),
    )

    first = run(worker.run_consolidation("session-a"))
    second = run(worker.run_consolidation("session-a"))

    assert first.summaries_produced == 3
    assert second == ConsolidationResult(0, 0, 0, second.run_timestamp)
    assert len(resolver.calls) == 1
    assert store.transaction_entries == 1


def test_transactional_rollback_on_db_failure_does_not_corrupt_state(
    memory_entries: list[dict[str, Any]],
) -> None:
    store = MockMemoryStore(memory_entries, fail_on_prune=True)
    worker = ConsolidationWorker(
        MockProvider(),
        store,
        summarizer=MockSummarizer(),
        contradiction_resolver=MockContradictionResolver(
            ContradictionResult(True, "User timezone is UTC", 0.95, [])
        ),
        decay_engine=MockDecayEngine([memory_entries[0]]),
    )

    with pytest.raises(RuntimeError, match="simulated db failure"):
        run(worker.run_consolidation("session-a"))

    assert store.applied_resolutions == []
    assert store.saved_summaries == []
    assert store.summarized_ids == []
    assert store.pruned_entries == []
    assert store.all_entries == memory_entries
    assert store.last_run is None
    assert store.transaction_entries == 1
    assert store.transaction_exits == 1


def test_consolidation_result_fields_are_populated_correctly(
    memory_entries: list[dict[str, Any]],
) -> None:
    store = MockMemoryStore(memory_entries)
    worker = ConsolidationWorker(
        MockProvider(),
        store,
        summarizer=MockSummarizer(turn_summary="turn", session_summary="", long_term_summary=""),
        contradiction_resolver=MockContradictionResolver(),
        decay_engine=MockDecayEngine(memory_entries),
    )

    result = run(worker.run_consolidation("session-a"))

    assert result == ConsolidationResult(
        summaries_produced=1,
        contradictions_resolved=0,
        entries_pruned=0,
        run_timestamp=result.run_timestamp,
    )
    assert isinstance(result.summaries_produced, int)
    assert isinstance(result.contradictions_resolved, int)
    assert isinstance(result.entries_pruned, int)
    assert isinstance(result.run_timestamp, datetime)
