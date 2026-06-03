from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.db import models
from chatbot.memory.controls import (
    correct_misunderstanding,
    delete_memory,
    do_not_remember,
    edit_memory,
    pin_memory,
    remember_forever,
    set_privacy_level,
    unpin_memory,
)
from chatbot.memory.export import (
    ExportBundle,
    ExportFilter,
    MemoryEntry,
    TurnEntry,
    export_memories,
    export_to_json,
    export_to_text,
)
from chatbot.memory.provenance import (
    ProvenanceAnnotation,
    ProvenanceTracker,
    tag_explicit,
    tag_inferred,
    tag_record,
)
from chatbot.memory.search import (
    SearchFilter,
    SearchPage,
    SearchResult,
    search,
)


def _uid() -> uuid.UUID:
    return uuid.uuid4()


def _fact(**overrides: Any) -> models.SemanticFact:
    kwargs: dict[str, Any] = {
        "id": _uid(),
        "user_id": _uid(),
        "fact_text": "test fact",
        "confidence": 0.9,
        "source_message_id": None,
        "source_type": "explicit",
        "source_turn_id": None,
        "privacy_level": "temporary",
        "pinned": False,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    kwargs.update(overrides)
    return models.SemanticFact(**kwargs)


def _message(**overrides: Any) -> models.Message:
    kwargs: dict[str, Any] = {
        "id": _uid(),
        "session_id": _uid(),
        "role": "user",
        "content": "hello world",
        "token_count": 5,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    kwargs.update(overrides)
    return models.Message(**kwargs)


def _scalar_result(value: Any | None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    return result


def _scalars_result(values: list[Any]) -> MagicMock:
    result = MagicMock()
    result.scalars = MagicMock(return_value=MagicMock())
    result.scalars().all = MagicMock(return_value=values)
    return result


def _mappings_result(rows: list[dict[str, Any]]) -> MagicMock:
    result = MagicMock()
    result.mappings = MagicMock(return_value=MagicMock())
    result.mappings().all = MagicMock(return_value=rows)
    return result


def _mk_db(execute_return: Any | None = None) -> AsyncMock:
    db = AsyncMock()
    if execute_return is not None:
        db.execute = AsyncMock(return_value=execute_return)
    return db


def _mk_db_fact(fact: models.SemanticFact | None) -> AsyncMock:
    return _mk_db(_scalar_result(fact))


def _mk_db_message(message: models.Message | None) -> AsyncMock:
    return _mk_db(_scalar_result(message))


def _mk_db_search(count: int, rows: list[dict[str, Any]]) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_scalar_result(count), _mappings_result(rows)])
    return db


def _mk_db_export_facts(facts: list[models.SemanticFact]) -> AsyncMock:
    return _mk_db(_scalars_result(facts))


def _mk_db_export_messages(messages: list[models.Message]) -> AsyncMock:
    return _mk_db(_scalars_result(messages))


_SECONDS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _search_row(
    source: str,
    id_val: str,
    content: str,
    *,
    role: str | None = None,
    token_count: int | None = None,
    created_at: datetime | None = None,
    session_id: str | None = None,
    confidence: float | None = None,
    relevance: float = 1.0,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "source": source,
        "id": uuid.UUID(id_val),
        "content": content,
        "role": role,
        "token_count": token_count,
        "created_at": created_at or _SECONDS,
        "session_id": uuid.UUID(session_id) if session_id else None,
        "relevance": relevance,
    }
    if confidence is not None:
        row["confidence"] = confidence
    return row


# ---------------------------------------------------------------------------
# provenance tests
# ---------------------------------------------------------------------------


class TestProvenanceAnnotation:
    def test_default_construction(self) -> None:
        ann = ProvenanceAnnotation(origin="explicit")
        assert ann.origin == "explicit"
        assert ann.turn_id is None
        assert ann.confidence == 1.0
        assert ann.source_message_id is None
        assert ann.created_at is None
        assert ann.metadata == {}

    def test_full_construction(self) -> None:
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        ann = ProvenanceAnnotation(
            origin="inferred",
            turn_id="turn-1",
            confidence=0.75,
            source_message_id="msg-1",
            created_at=now,
            metadata={"key": "val"},
        )
        assert ann.turn_id == "turn-1"
        assert ann.confidence == 0.75
        assert ann.source_message_id == "msg-1"
        assert ann.created_at == now
        assert ann.metadata == {"key": "val"}

    def test_is_frozen(self) -> None:
        ann = ProvenanceAnnotation(origin="explicit")
        with pytest.raises(Exception):
            ann.origin = "inferred"  # type: ignore[misc]

    def test_as_dict_minimal(self) -> None:
        ann = ProvenanceAnnotation(origin="explicit")
        d = ann.as_dict()
        assert d == {"origin": "explicit", "confidence": 1.0}

    def test_as_dict_full(self) -> None:
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        ann = ProvenanceAnnotation(
            origin="explicit",
            turn_id="turn-1",
            confidence=0.5,
            source_message_id="msg-1",
            created_at=now,
            metadata={"k": "v"},
        )
        d = ann.as_dict()
        assert d["origin"] == "explicit"
        assert d["turn_id"] == "turn-1"
        assert d["confidence"] == 0.5
        assert d["source_message_id"] == "msg-1"
        assert d["created_at"] == now.isoformat()
        assert d["metadata"] == {"k": "v"}

    def test_null_fields_omitted_from_as_dict(self) -> None:
        ann = ProvenanceAnnotation(origin="explicit", turn_id=None, source_message_id=None)
        d = ann.as_dict()
        assert "turn_id" not in d
        assert "source_message_id" not in d


class TestProvenanceTracker:
    def test_annotate_explicit_defaults(self) -> None:
        tracker = ProvenanceTracker()
        record: dict[str, Any] = {"fact_text": "hello"}
        result = tracker.annotate_explicit(record)
        prov = result["provenance"]
        assert prov["origin"] == "explicit"
        assert prov["confidence"] == 0.9
        assert result["fact_text"] == "hello"

    def test_annotate_inferred_defaults(self) -> None:
        tracker = ProvenanceTracker()
        record: dict[str, Any] = {"fact_text": "hello"}
        result = tracker.annotate_inferred(record)
        prov = result["provenance"]
        assert prov["origin"] == "inferred"
        assert prov["confidence"] == 0.5

    def test_annotate_with_all_fields(self) -> None:
        tracker = ProvenanceTracker()
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        record: dict[str, Any] = {"fact_text": "hello"}
        result = tracker.annotate(
            record,
            origin="explicit",
            turn_id="t1",
            confidence=0.6,
            source_message_id="m1",
            created_at=now,
            metadata={"tag": "a"},
        )
        prov = result["provenance"]
        assert prov["turn_id"] == "t1"
        assert prov["source_message_id"] == "m1"
        assert prov["metadata"] == {"tag": "a"}
        assert result["source_message_id"] == "m1"

    def test_annotate_sets_created_at_when_none(self) -> None:
        tracker = ProvenanceTracker()
        record: dict[str, Any] = {"fact_text": "hello"}
        result = tracker.annotate(record, origin="explicit", created_at=None)
        assert result["provenance"]["created_at"] is not None

    def test_annotate_does_not_overwrite_existing_keys(self) -> None:
        tracker = ProvenanceTracker()
        record: dict[str, Any] = {"fact_text": "hello", "confidence": 0.99}
        result = tracker.annotate(record, origin="explicit", confidence=0.5)
        assert result["confidence"] == 0.99

    def test_extract_annotation_valid(self) -> None:
        tracker = ProvenanceTracker()
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        record: dict[str, Any] = {"fact_text": "x", "provenance": {"origin": "explicit", "confidence": 0.8, "turn_id": "t1", "source_message_id": "m1", "created_at": now.isoformat()}}
        ann = tracker.extract_annotation(record)
        assert ann is not None
        assert ann.origin == "explicit"
        assert ann.confidence == 0.8
        assert ann.turn_id == "t1"
        assert ann.source_message_id == "m1"

    def test_extract_annotation_null_provenance(self) -> None:
        tracker = ProvenanceTracker()
        assert tracker.extract_annotation({}) is None
        assert tracker.extract_annotation({"provenance": None}) is None

    def test_extract_annotation_invalid_origin(self) -> None:
        tracker = ProvenanceTracker()
        record: dict[str, Any] = {"provenance": {"origin": "unknown"}}
        assert tracker.extract_annotation(record) is None

    def test_extract_annotation_not_a_dict(self) -> None:
        tracker = ProvenanceTracker()
        record: dict[str, Any] = {"provenance": "not-a-dict"}
        assert tracker.extract_annotation(record) is None

    def test_extract_annotation_created_at_string_parse(self) -> None:
        tracker = ProvenanceTracker()
        record: dict[str, Any] = {"provenance": {"origin": "explicit", "created_at": "2026-06-01T12:00:00+00:00"}}
        ann = tracker.extract_annotation(record)
        assert ann is not None
        assert ann.created_at == datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

    def test_extract_annotation_created_at_datetime(self) -> None:
        tracker = ProvenanceTracker()
        dt = datetime(2026, 6, 1, tzinfo=timezone.utc)
        record: dict[str, Any] = {"provenance": {"origin": "explicit", "created_at": dt}}
        ann = tracker.extract_annotation(record)
        assert ann is not None
        assert ann.created_at == dt

    def test_extract_annotation_invalid_created_at_string(self) -> None:
        tracker = ProvenanceTracker()
        record: dict[str, Any] = {"provenance": {"origin": "explicit", "created_at": "not-a-date"}}
        ann = tracker.extract_annotation(record)
        assert ann is not None
        assert ann.created_at is None

    def test_extract_annotation_missing_confidence(self) -> None:
        tracker = ProvenanceTracker()
        record: dict[str, Any] = {"provenance": {"origin": "inferred"}}
        ann = tracker.extract_annotation(record)
        assert ann is not None
        assert ann.confidence == 1.0

    def test_provenance_key_constant(self) -> None:
        assert ProvenanceTracker.PROVENANCE_KEY == "provenance"


class TestTagHelpers:
    def test_tag_explicit(self) -> None:
        record: dict[str, Any] = {"fact_text": "x"}
        result = tag_explicit(record, turn_id="t1")
        assert result["provenance"]["origin"] == "explicit"
        assert result["provenance"]["turn_id"] == "t1"

    def test_tag_inferred(self) -> None:
        record: dict[str, Any] = {"fact_text": "x"}
        result = tag_inferred(record, turn_id="t1")
        assert result["provenance"]["origin"] == "inferred"
        assert result["confidence"] == 0.5

    def test_tag_record(self) -> None:
        record: dict[str, Any] = {"fact_text": "x"}
        result = tag_record(record, origin="explicit", confidence=0.7)
        assert result["provenance"]["origin"] == "explicit"
        assert result["provenance"]["confidence"] == 0.7


# ---------------------------------------------------------------------------
# controls tests
# ---------------------------------------------------------------------------


class TestPinUnpin:
    def test_pin_memory_found(self) -> None:
        fact = _fact(pinned=False)
        db = _mk_db_fact(fact)
        result = run(pin_memory(db, fact.id))
        assert result["id"] == str(fact.id)
        assert result["pinned"] is True

    def test_pin_memory_not_found(self) -> None:
        mem_id = str(_uid())
        db = _mk_db_fact(None)
        result = run(pin_memory(db, mem_id))
        assert result["id"] == mem_id
        assert result["pinned"] is True

    def test_unpin_memory_found(self) -> None:
        fact = _fact(pinned=True)
        db = _mk_db_fact(fact)
        result = run(unpin_memory(db, fact.id))
        assert result["id"] == str(fact.id)
        assert result["pinned"] is False

    def test_unpin_memory_not_found(self) -> None:
        mem_id = str(_uid())
        db = _mk_db_fact(None)
        result = run(unpin_memory(db, mem_id))
        assert result["id"] == mem_id
        assert result["pinned"] is False

    def test_pin_invalid_id(self) -> None:
        db = _mk_db_fact(None)
        with pytest.raises(ValueError):
            run(pin_memory(db, "not-a-uuid"))

    def test_unpin_invalid_id(self) -> None:
        db = _mk_db_fact(None)
        with pytest.raises(ValueError):
            run(unpin_memory(db, "not-a-uuid"))


class TestDeleteMemory:
    def test_soft_delete_found(self) -> None:
        fact = _fact(confidence=0.9)
        db = _mk_db_fact(fact)
        result = run(delete_memory(db, fact.id))
        assert result["id"] == str(fact.id)
        assert result["deleted"] is True
        assert result["confidence"] == 0.0

    def test_hard_delete_found(self) -> None:
        fact = _fact(confidence=0.9)
        db = _mk_db_fact(fact)
        db.execute.reset_mock()
        result = run(delete_memory(db, fact.id, hard=True))
        assert result["id"] == str(fact.id)
        assert result["deleted"] is True
        assert result["confidence"] == 0.0
        assert result["fact_text"] == ""

    def test_delete_not_found(self) -> None:
        mem_id = str(_uid())
        db = _mk_db_fact(None)
        result = run(delete_memory(db, mem_id))
        assert result["id"] == mem_id
        assert result["deleted"] is True

    def test_hard_delete_not_found(self) -> None:
        mem_id = str(_uid())
        db = _mk_db_fact(None)
        result = run(delete_memory(db, mem_id, hard=True))
        assert result["id"] == mem_id
        assert result["deleted"] is True

    def test_delete_invalid_id(self) -> None:
        db = _mk_db_fact(None)
        with pytest.raises(ValueError):
            run(delete_memory(db, "not-a-uuid"))


class TestEditMemory:
    def test_edit_content_found(self) -> None:
        fact = _fact(fact_text="original")
        db = _mk_db_fact(fact)
        result = run(edit_memory(db, fact.id, content="updated"))
        assert result["id"] == str(fact.id)
        assert fact.fact_text == "updated"

    def test_edit_metadata_found(self) -> None:
        fact = _fact()
        db = _mk_db_fact(fact)
        result = run(edit_memory(db, fact.id, metadata={"topic": "science"}))
        assert result["metadata"] == {"topic": "science"}

    def test_edit_not_found(self) -> None:
        mem_id = str(_uid())
        db = _mk_db_fact(None)
        result = run(edit_memory(db, mem_id, content="updated"))
        assert result["id"] == mem_id
        assert result["deleted"] is False

    def test_edit_invalid_id(self) -> None:
        db = _mk_db_fact(None)
        with pytest.raises(ValueError):
            run(edit_memory(db, "not-a-uuid"))


class TestPrivacyLevel:
    def test_set_privacy_valid(self) -> None:
        fact = _fact()
        db = _mk_db_fact(fact)
        result = run(set_privacy_level(db, fact.id, "private"))
        assert result["privacy_level"] == "private"

    @pytest.mark.parametrize("level", ["permanent", "temporary"])
    def test_set_privacy_levels(self, level: str) -> None:
        fact = _fact()
        db = _mk_db_fact(fact)
        result = run(set_privacy_level(db, fact.id, level))
        assert result["privacy_level"] == level

    def test_set_privacy_invalid_raises(self) -> None:
        db = _mk_db(_scalar_result(_fact()))
        with pytest.raises(ValueError, match="Invalid privacy level"):
            run(set_privacy_level(db, str(_uid()), "public"))

    def test_set_privacy_not_found(self) -> None:
        mem_id = str(_uid())
        db = _mk_db_fact(None)
        result = run(set_privacy_level(db, mem_id, "private"))
        assert result["id"] == mem_id
        assert result["privacy_level"] == "private"

    def test_set_privacy_invalid_id(self) -> None:
        db = _mk_db_fact(None)
        with pytest.raises(ValueError):
            run(set_privacy_level(db, "not-a-uuid", "private"))


class TestDoNotRemember:
    def test_do_not_remember_found(self) -> None:
        msg = _message()
        db = _mk_db_message(msg)
        result = run(do_not_remember(db, msg.id))
        assert result["id"] == str(msg.id)
        assert result["do_not_remember"] is True
        assert result["role"] == msg.role
        assert result["content"] == msg.content

    def test_do_not_remember_not_found(self) -> None:
        turn_id = str(_uid())
        db = _mk_db_message(None)
        result = run(do_not_remember(db, turn_id, session_id="s1"))
        assert result["id"] == turn_id
        assert result["do_not_remember"] is True
        assert result["session_id"] == "s1"

    def test_do_not_remember_invalid_id(self) -> None:
        db = _mk_db_message(None)
        with pytest.raises(ValueError):
            run(do_not_remember(db, "not-a-uuid"))


class TestRememberForever:
    def test_remember_forever_found(self) -> None:
        fact = _fact()
        db = _mk_db_fact(fact)
        result = run(remember_forever(db, fact.id))
        assert result["id"] == str(fact.id)
        assert result["retention"] == "forever"

    def test_remember_forever_not_found(self) -> None:
        mem_id = str(_uid())
        db = _mk_db_fact(None)
        result = run(remember_forever(db, mem_id))
        assert result["id"] == mem_id
        assert result["retention"] == "forever"

    def test_remember_forever_invalid_id(self) -> None:
        db = _mk_db_fact(None)
        with pytest.raises(ValueError):
            run(remember_forever(db, "not-a-uuid"))


class TestCorrectMisunderstanding:
    def test_correct_found(self) -> None:
        fact = _fact(fact_text="wrong", confidence=0.8)
        db = _mk_db_fact(fact)
        result = run(correct_misunderstanding(db, fact.id, "correct"))
        assert result["id"] == str(fact.id)
        assert result["correction"] == "correct"
        assert result["fact_text"] == "correct"
        assert result["original_text"] == "wrong"
        assert fact.confidence == pytest.approx(0.4)

    def test_correct_not_found(self) -> None:
        mem_id = str(_uid())
        db = _mk_db_fact(None)
        result = run(correct_misunderstanding(db, mem_id, "correct"))
        assert result["id"] == mem_id
        assert result["correction"] == "correct"
        assert result["confidence"] == 0.5

    def test_correct_empty_correction_raises(self) -> None:
        db = _mk_db_fact(None)
        with pytest.raises(ValueError, match="correction must not be empty"):
            run(correct_misunderstanding(db, str(_uid()), ""))

    def test_correct_whitespace_correction_raises(self) -> None:
        db = _mk_db_fact(None)
        with pytest.raises(ValueError, match="correction must not be empty"):
            run(correct_misunderstanding(db, str(_uid()), "   "))

    def test_correct_invalid_id(self) -> None:
        db = _mk_db_fact(None)
        with pytest.raises(ValueError):
            run(correct_misunderstanding(db, "not-a-uuid", "fixed"))


# ---------------------------------------------------------------------------
# search tests
# ---------------------------------------------------------------------------


class TestSearchDatatypes:
    def test_search_filter_defaults(self) -> None:
        sf = SearchFilter()
        assert sf.query is None
        assert sf.start is None
        assert sf.end is None
        assert sf.topics == ()
        assert sf.tags == ()
        assert sf.session_id is None
        assert sf.user_id is None
        assert sf.domain is None
        assert sf.semantic is False

    def test_search_filter_with_values(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 6, 1, tzinfo=timezone.utc)
        sf = SearchFilter(query="hello", start=start, end=end, topics=("a", "b"), domain="messages")
        assert sf.query == "hello"
        assert sf.start == start
        assert sf.topics == ("a", "b")

    def test_search_result_creation(self) -> None:
        sr = SearchResult(domain="messages", id="id-1", content="hello", score=0.8)
        assert sr.domain == "messages"
        assert sr.id == "id-1"
        assert sr.content == "hello"
        assert sr.score == 0.8
        assert sr.created_at is None
        assert sr.metadata == {}
        assert sr.provenance is None

    def test_search_page_creation(self) -> None:
        results = [SearchResult(domain="messages", id="a", content="x", score=1.0)]
        page = SearchPage(results=results, total=1, offset=0, limit=20)
        assert page.results == results
        assert page.total == 1
        assert page.offset == 0
        assert page.limit == 20


class TestSearchValidation:
    def test_validate_pagination_raises_negative_offset(self) -> None:
        with pytest.raises(ValueError, match="offset must be non-negative"):
            run(search(_mk_db_search(0, []), offset=-1))

    def test_validate_pagination_raises_zero_limit(self) -> None:
        with pytest.raises(ValueError, match="limit must be greater than zero"):
            run(search(_mk_db_search(0, []), limit=0))

    def test_validate_time_range_raises_inverted(self) -> None:
        start = datetime(2026, 6, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match="start must be before or equal to end"):
            run(search(_mk_db_search(0, []), start=start, end=end))


class TestSearchResults:
    def test_search_empty_results(self) -> None:
        db = _mk_db_search(0, [])
        page = run(search(db))
        assert page.total == 0
        assert page.results == []

    def test_search_message_results(self) -> None:
        rows = [
            _search_row("messages", "00000000-0000-0000-0000-000000000001", "hello", role="user", token_count=3, relevance=0.9),
            _search_row("messages", "00000000-0000-0000-0000-000000000002", "world", role="assistant", token_count=5, relevance=0.7),
        ]
        db = _mk_db_search(2, rows)
        page = run(search(db))
        assert page.total == 2
        assert len(page.results) == 2
        assert page.results[0].domain == "messages"
        assert page.results[0].id == "00000000-0000-0000-0000-000000000001"
        assert page.results[0].content == "hello"
        assert page.results[0].score == 0.9

    def test_search_includes_provenance(self) -> None:
        rows = [_search_row("messages", "00000000-0000-0000-0000-000000000001", "hello", role="user")]
        db = _mk_db_search(1, rows)
        page = run(search(db))
        assert page.results[0].provenance is not None
        assert page.results[0].provenance["origin"] == "explicit"

    def test_search_fact_results_have_inferred_provenance(self) -> None:
        rows = [_search_row("semantic_facts", "00000000-0000-0000-0000-000000000001", "a memory", confidence=0.7)]
        db = _mk_db_search(1, rows)
        page = run(search(db))
        assert page.results[0].provenance["origin"] == "inferred"

    def test_search_pagination_offset(self) -> None:
        rows: list[dict[str, Any]] = []
        for i in range(5):
            id_str = f"00000000-0000-0000-0000-{i:012d}"
            rows.append(_search_row("messages", id_str, f"msg-{i}", relevance=0.9))
        db = _mk_db_search(5, rows)
        page = run(search(db, offset=2, limit=2))
        assert page.offset == 2
        assert page.limit == 2
        assert page.total == 5

    def test_search_domain_filter_messages_only(self) -> None:
        db = _mk_db_search(0, [])
        page = run(search(db, domain="messages"))
        assert page.total == 0

    def test_search_domain_filter_facts_only(self) -> None:
        db = _mk_db_search(0, [])
        page = run(search(db, domain="semantic_facts"))
        assert page.total == 0

    def test_search_with_filters_object(self) -> None:
        db = _mk_db_search(0, [])
        sf = SearchFilter(query="test", domain="messages")
        page = run(search(db, filters=sf))
        assert page.total == 0

    def test_search_null_content_in_result(self) -> None:
        rows = [_search_row("messages", "00000000-0000-0000-0000-000000000001", "test")]
        rows[0]["content"] = None
        db = _mk_db_search(1, rows)
        page = run(search(db))
        assert page.results[0].content == ""


# ---------------------------------------------------------------------------
# export tests
# ---------------------------------------------------------------------------


class TestExportFilter:
    def test_valid_defaults(self) -> None:
        ef = ExportFilter()
        assert ef.start is None
        assert ef.end is None
        assert ef.topics == ()
        assert ef.privacy_level is None
        assert ef.include_turns is True
        assert ef.include_memories is True

    def test_valid_with_all_fields(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 6, 1, tzinfo=timezone.utc)
        ef = ExportFilter(start=start, end=end, topics=("ai",), privacy_level="private", session_id="s1", user_id="u1", include_turns=False, include_memories=True)
        assert ef.start == start
        assert ef.topics == ("ai",)
        assert ef.include_turns is False

    def test_invalid_privacy_level_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid privacy level"):
            ExportFilter(privacy_level="public")

    def test_inverted_time_range_raises(self) -> None:
        start = datetime(2026, 6, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match="start must be before or equal to end"):
            ExportFilter(start=start, end=end)


class TestMemoryEntry:
    def test_as_dict_minimal(self) -> None:
        entry = MemoryEntry(id="id-1", user_id=None, fact_text="hello", confidence=0.9, source_message_id=None, created_at=None, updated_at=None, provenance=None, privacy_level="temporary")
        d = entry.as_dict()
        assert d["id"] == "id-1"
        assert d["fact_text"] == "hello"
        assert d["confidence"] == 0.9
        assert d["privacy_level"] == "temporary"
        assert "user_id" not in d
        assert "source_message_id" not in d
        assert "provenance" not in d

    def test_as_dict_full(self) -> None:
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        entry = MemoryEntry(
            id="id-1",
            user_id="u1",
            fact_text="full test",
            confidence=0.8,
            source_message_id="m1",
            created_at=now,
            updated_at=now,
            provenance={"origin": "explicit", "confidence": 0.9},
            privacy_level="permanent",
            topics=("ai", "ml"),
        )
        d = entry.as_dict()
        assert d["user_id"] == "u1"
        assert d["source_message_id"] == "m1"
        assert d["created_at"] is not None
        assert d["updated_at"] is not None
        assert d["provenance"] == {"origin": "explicit", "confidence": 0.9}
        assert d["topics"] == ["ai", "ml"]


class TestTurnEntry:
    def test_as_dict_minimal(self) -> None:
        entry = TurnEntry(id="t1", session_id="s1", role="user", content="hello", token_count=None, created_at=None, provenance=None, topics=())
        d = entry.as_dict()
        assert d["id"] == "t1"
        assert d["session_id"] == "s1"
        assert d["role"] == "user"
        assert d["content"] == "hello"
        assert "token_count" not in d
        assert "provenance" not in d

    def test_as_dict_full(self) -> None:
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        entry = TurnEntry(
            id="t1",
            session_id="s1",
            role="assistant",
            content="reply",
            token_count=10,
            created_at=now,
            provenance={"origin": "explicit"},
            topics=("greeting",),
        )
        d = entry.as_dict()
        assert d["token_count"] == 10
        assert d["provenance"] == {"origin": "explicit"}
        assert d["topics"] == ["greeting"]


class TestExportBundle:
    def _make_bundle(self) -> ExportBundle:
        return ExportBundle(
            export_id="exp-1",
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            filter=ExportFilter(),
            memories=[
                MemoryEntry(id="m1", user_id=None, fact_text="fact", confidence=0.9, source_message_id=None, created_at=None, updated_at=None, provenance=None, privacy_level="temporary"),
            ],
            turns=[
                TurnEntry(id="t1", session_id="s1", role="user", content="msg", token_count=None, created_at=None, provenance=None),
            ],
            summary={"total_memories": 1, "total_turns": 1, "total_entries": 2},
        )

    def test_as_dict_structure(self) -> None:
        bundle = self._make_bundle()
        d = bundle.as_dict()
        assert d["export_id"] == "exp-1"
        assert d["created_at"] is not None
        assert d["filter"] is not None
        assert len(d["memories"]) == 1
        assert len(d["turns"]) == 1
        assert d["summary"]["total_memories"] == 1

    def test_export_to_json(self) -> None:
        bundle = self._make_bundle()
        result = export_to_json(bundle)
        parsed = json.loads(result)
        assert parsed["export_id"] == "exp-1"
        assert len(parsed["memories"]) == 1

    def test_export_to_json_no_indent(self) -> None:
        bundle = self._make_bundle()
        result = export_to_json(bundle, indent=None)
        assert "\n" not in result

    def test_export_to_text_structure(self) -> None:
        bundle = self._make_bundle()
        result = export_to_text(bundle)
        assert "Export: exp-1" in result
        assert "MEMORIES" in result
        assert "CONVERSATION TURNS" in result
        assert "fact" in result
        assert "msg" in result

    def test_export_to_text_includes_provenance(self) -> None:
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        bundle = ExportBundle(
            export_id="exp-2",
            created_at=now,
            filter=ExportFilter(),
            memories=[
                MemoryEntry(id="m1", user_id=None, fact_text="fact", confidence=0.9, source_message_id=None, created_at=now, updated_at=now, provenance={"origin": "inferred", "confidence": 0.5}, privacy_level="temporary"),
            ],
            turns=[],
            summary={"total_memories": 1, "total_turns": 0, "total_entries": 1},
        )
        result = export_to_text(bundle)
        assert "Provenance:" in result
        assert '"origin": "inferred"' in result

    def test_export_to_text_includes_filters(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 6, 1, tzinfo=timezone.utc)
        bundle = ExportBundle(
            export_id="exp-3",
            created_at=end,
            filter=ExportFilter(start=start, end=end, topics=("ai",), privacy_level="permanent"),
            memories=[],
            turns=[],
            summary={"total_memories": 0, "total_turns": 0, "total_entries": 0},
        )
        result = export_to_text(bundle)
        assert "Filters:" in result
        assert "Topics: ai" in result
        assert "Privacy Level: permanent" in result

    def test_export_to_text_empty_bundle(self) -> None:
        bundle = ExportBundle(
            export_id="exp-4",
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            filter=ExportFilter(),
            memories=[],
            turns=[],
            summary={"total_memories": 0, "total_turns": 0, "total_entries": 0},
        )
        result = export_to_text(bundle)
        assert "MEMORIES" not in result
        assert "CONVERSATION TURNS" not in result
        assert "Total Memories: 0" in result


class TestExportMemories:
    def test_empty_result(self) -> None:
        db = _mk_db_export_facts([])
        bundle = run(export_memories(db, include_turns=False))
        assert bundle.summary["total_memories"] == 0
        assert bundle.summary["total_turns"] == 0

    def test_with_memories(self) -> None:
        fact_a = _fact(fact_text="fact a", confidence=0.95)
        fact_b = _fact(fact_text="fact b", confidence=0.3)
        db = _mk_db_export_facts([fact_a, fact_b])
        bundle = run(export_memories(db, include_turns=False))
        assert bundle.summary["total_memories"] == 2
        assert len(bundle.memories) == 2

    def test_privacy_level_filter(self) -> None:
        fact_perm = _fact(fact_text="perm", confidence=0.95)
        fact_temp = _fact(fact_text="temp", confidence=0.5)
        facts = [fact_temp, fact_perm]
        db = _mk_db_export_facts(facts)
        bundle = run(export_memories(db, privacy_level="permanent", include_turns=False))
        assert bundle.summary["total_memories"] == 1
        assert bundle.memories[0].privacy_level == "permanent"

    def test_with_turns(self) -> None:
        msg = _message()
        db = _mk_db_export_messages([msg])
        bundle = run(export_memories(db, include_memories=False))
        assert bundle.summary["total_turns"] == 1
        assert bundle.turns[0].id == str(msg.id)

    def test_both_memories_and_turns(self) -> None:
        fact = _fact()
        msg = _message()
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_scalars_result([fact]), _scalars_result([msg])])
        bundle = run(export_memories(db))
        assert bundle.summary["total_memories"] == 1
        assert bundle.summary["total_turns"] == 1
        assert bundle.summary["total_entries"] == 2

    def test_topic_filter(self) -> None:
        fact = _fact(fact_text="discussing AI topics")
        db = _mk_db_export_facts([fact])
        bundle = run(export_memories(db, topics=("AI",), include_turns=False))
        assert bundle.summary["total_memories"] == 1
        assert "AI" in bundle.memories[0].topics

    def test_zero_limit_raises(self) -> None:
        db = _mk_db_export_facts([])
        with pytest.raises(ValueError, match="limit must be greater than zero"):
            run(export_memories(db, include_turns=False, limit=0))

    def test_negative_limit_raises(self) -> None:
        db = _mk_db_export_facts([])
        with pytest.raises(ValueError, match="limit must be greater than zero"):
            run(export_memories(db, include_turns=False, limit=-1))

    def test_export_filter_object(self) -> None:
        fact = _fact(fact_text="hello", confidence=0.95)
        db = _mk_db_export_facts([fact])
        ef = ExportFilter(privacy_level="permanent", include_turns=False)
        bundle = run(export_memories(db, filters=ef))
        assert bundle.summary["total_memories"] == 1

    def test_provenance_included_in_entries(self) -> None:
        fact = _fact(fact_text="with provenance", confidence=0.8, source_message_id=_uid())
        db = _mk_db_export_facts([fact])
        bundle = run(export_memories(db, include_turns=False))
        assert bundle.memories[0].provenance is not None
        assert bundle.memories[0].provenance["origin"] == "inferred"

    def test_date_filter(self) -> None:
        recent = datetime(2026, 6, 1, tzinfo=timezone.utc)
        old = datetime(2025, 1, 1, tzinfo=timezone.utc)
        fact_new = _fact(fact_text="new", created_at=recent)
        fact_old = _fact(fact_text="old", created_at=old)
        db = _mk_db_export_facts([fact_old, fact_new])
        bundle = run(export_memories(db, start=datetime(2026, 1, 1, tzinfo=timezone.utc), include_turns=False))
        assert bundle.summary["total_memories"] == 2

    def test_privacy_derivation_private(self) -> None:
        fact = _fact(fact_text="private fact", confidence=0.0)
        db = _mk_db_export_facts([fact])
        bundle = run(export_memories(db, include_turns=False))
        assert bundle.memories[0].privacy_level == "private"

    def test_privacy_derivation_permanent(self) -> None:
        fact = _fact(fact_text="permanent fact", confidence=0.95)
        db = _mk_db_export_facts([fact])
        bundle = run(export_memories(db, include_turns=False))
        assert bundle.memories[0].privacy_level == "permanent"

    def test_privacy_derivation_temporary(self) -> None:
        fact = _fact(fact_text="temporary fact", confidence=0.5)
        db = _mk_db_export_facts([fact])
        bundle = run(export_memories(db, include_turns=False))
        assert bundle.memories[0].privacy_level == "temporary"

    def test_privacy_derivation_null_confidence(self) -> None:
        fact = _fact(fact_text="null conf fact")
        fact.confidence = 0.0
        db = _mk_db_export_facts([fact])
        bundle = run(export_memories(db, include_turns=False))
        assert bundle.memories[0].privacy_level == "private"


def run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)