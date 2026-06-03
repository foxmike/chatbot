"""Integration tests for continuity — timeline, narrative, rituals, and the TimelineUpdateStep."""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.continuity.rituals import CompanionRitualModel, Rituals
from chatbot.continuity.timeline import (
    RelationshipTimeline,
    RelationshipTimelineEntryModel,
    TimelineFilter,
)
from chatbot.core.steps import TurnContext
from chatbot.core.turn import TimelineUpdateStep
from chatbot.db import models


def _load_narrative_module() -> ModuleType:
    saved_memory = sys.modules.get("chatbot.memory")
    saved_pgvector = sys.modules.get("chatbot.memory.pgvector_store")
    fake_memory = ModuleType("chatbot.memory")
    fake_memory.__path__ = []  # type: ignore[attr-defined]
    fake_pgvector = ModuleType("chatbot.memory.pgvector_store")

    class FakePgvectorStore:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def search(self, *_args: object, **_kwargs: object) -> list[Any]:
            return []

    fake_pgvector.PgvectorStore = FakePgvectorStore  # type: ignore[attr-defined]
    sys.modules["chatbot.memory"] = fake_memory
    sys.modules["chatbot.memory.pgvector_store"] = fake_pgvector
    try:
        from chatbot.continuity import narrative as loaded
    finally:
        if saved_memory is None:
            sys.modules.pop("chatbot.memory", None)
        else:
            sys.modules["chatbot.memory"] = saved_memory

        if saved_pgvector is None:
            sys.modules.pop("chatbot.memory.pgvector_store", None)
        else:
            sys.modules["chatbot.memory.pgvector_store"] = saved_pgvector

    return loaded


narrative = _load_narrative_module()


class FakeScalarResult:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def all(self) -> list[Any]:
        return list(self._values)


class FakeExecuteResult:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def scalars(self) -> FakeScalarResult:
        return FakeScalarResult(self._values)

    def scalar_one_or_none(self) -> Any | None:
        if not self._values:
            return None
        return self._values[0]


class FakeDatabase:
    def __init__(self) -> None:
        self.rows: dict[type[Any], list[Any]] = {}

    def session_factory(self) -> "FakeAsyncSession":
        return FakeAsyncSession(self)

    def add(self, model: Any) -> None:
        if isinstance(model, RelationshipTimelineEntryModel):
            if model.status is None:
                model.status = "active"
            if model.tags is None:
                model.tags = []
            if model.metadata_json is None:
                model.metadata_json = {}
            if model.created_at is None:
                model.created_at = _now()
            if model.updated_at is None:
                model.updated_at = _now()
        self.rows.setdefault(type(model), []).append(model)

    def models(self, model_type: type[Any]) -> list[Any]:
        return self.rows.setdefault(model_type, [])


class FakeAsyncSession:
    def __init__(self, database: FakeDatabase) -> None:
        self.database = database
        self.commits = 0
        self.flushes = 0
        self.rollbacks = 0

    async def __aenter__(self) -> "FakeAsyncSession":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def add(self, model: Any) -> None:
        self.database.add(model)

    async def commit(self) -> None:
        self.commits += 1

    async def flush(self) -> None:
        self.flushes += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    def in_transaction(self) -> bool:
        return False

    async def execute(self, statement: Any) -> FakeExecuteResult:
        entity = _statement_entity(statement)
        if entity is None:
            return FakeExecuteResult([])

        rows = [
            row
            for row in self.database.models(entity)
            if _matches_statement(row, statement)
        ]
        rows = _apply_ordering(rows, statement)
        rows = _apply_limit(rows, statement)
        selected_key = _selected_attribute_key(statement)
        if selected_key is not None:
            rows = [getattr(row, selected_key) for row in rows]
        return FakeExecuteResult(rows)


def _statement_entity(statement: Any) -> type[Any] | None:
    descriptions = getattr(statement, "column_descriptions", [])
    if not descriptions:
        return None
    entity = descriptions[0].get("entity")
    return entity if isinstance(entity, type) else None


def _selected_attribute_key(statement: Any) -> str | None:
    descriptions = getattr(statement, "column_descriptions", [])
    if not descriptions:
        return None
    expr = descriptions[0].get("expr")
    entity = descriptions[0].get("entity")
    if expr is entity:
        return None
    key = getattr(expr, "key", None)
    return key if isinstance(key, str) else None


def _matches_statement(row: Any, statement: Any) -> bool:
    return all(_matches_criterion(row, criterion) for criterion in statement._where_criteria)


def _matches_criterion(row: Any, criterion: Any) -> bool:
    if isinstance(criterion, BooleanClauseList):
        if criterion.operator is operators.and_:
            return all(_matches_criterion(row, clause) for clause in criterion.clauses)
        if criterion.operator is operators.or_:
            return any(_matches_criterion(row, clause) for clause in criterion.clauses)

    if isinstance(criterion, BinaryExpression):
        key = getattr(criterion.left, "key", None)
        actual = getattr(row, key, None)
        expected = _expression_value(criterion.right)
        op = criterion.operator

        if op is operators.eq:
            return _same_value(actual, expected)
        if op is operators.in_op:
            return any(_same_value(actual, item) for item in expected)
        if op is operators.ge:
            return actual >= expected
        if op is operators.le:
            return actual <= expected
        if op is operators.is_:
            return actual is expected or actual == expected
        if op is operators.is_not:
            return not (actual is expected or actual == expected)

    return True


def _expression_value(expression: Any) -> Any:
    if hasattr(expression, "effective_value"):
        return expression.effective_value
    if hasattr(expression, "value"):
        return expression.value
    return expression


def _same_value(actual: Any, expected: Any) -> bool:
    try:
        return uuid.UUID(str(actual)) == uuid.UUID(str(expected))
    except (TypeError, ValueError):
        return actual == expected


def _apply_ordering(rows: list[Any], statement: Any) -> list[Any]:
    ordered = list(rows)
    order_by_clauses = tuple(getattr(statement, "_order_by_clauses", ()))
    for order in reversed(order_by_clauses):
        key = _order_key(order)
        if key is None:
            continue
        reverse = " DESC" in str(order).upper()
        ordered.sort(key=lambda row: getattr(row, key, None) or datetime.min, reverse=reverse)
    return ordered


def _order_key(order: Any) -> str | None:
    current = order
    while hasattr(current, "element"):
        current = current.element
    key = getattr(current, "key", None)
    return key if isinstance(key, str) else None


def _apply_limit(rows: list[Any], statement: Any) -> list[Any]:
    limit_clause = getattr(statement, "_limit_clause", None)
    if limit_clause is None:
        return rows
    limit = getattr(limit_clause, "value", None)
    return rows[: int(limit)] if limit is not None else rows


def _uuid() -> str:
    return str(uuid.uuid4())


def _now(offset: timedelta | None = None) -> datetime:
    return datetime.now(timezone.utc) + (offset or timedelta())


def _message(
    *,
    session_id: str,
    content: str,
    created_at: datetime,
    message_id: str | None = None,
) -> models.Message:
    return models.Message(
        id=uuid.UUID(message_id or _uuid()),
        session_id=uuid.UUID(session_id),
        role="user",
        content=content,
        embedding=None,
        token_count=None,
        created_at=created_at,
    )


def _turn_context(**overrides: Any) -> TurnContext:
    values: dict[str, Any] = {
        "session_id": _uuid(),
        "user_message": "",
        "history": [],
        "persona": {},
        "retrieved_memories": [],
        "compiled_context": None,
        "provider_response": None,
        "extracted_memories": [],
        "final_response": None,
        "metadata": {"turn_id": _uuid()},
    }
    values.update(overrides)
    return TurnContext(**values)


class TestTimeline:
    def test_add_timeline_entry(self) -> None:
        async def run() -> None:
            database = FakeDatabase()
            timeline = RelationshipTimeline(database.session_factory)
            session_id = _uuid()
            turn_id = _uuid()

            entry = await timeline.add_timeline_entry(
                session_id=session_id,
                entry_type="milestone",
                title="New job",
                description="The user got a new job.",
                content="User: I got a new job.",
                turn_id=turn_id,
                tags=["career"],
                metadata={"source": "test"},
            )

            assert entry.entry_id
            assert entry.session_id == session_id
            assert entry.entry_type == "milestone"
            assert entry.title == "New job"
            assert entry.description == "The user got a new job."
            assert entry.content == "User: I got a new job."
            assert entry.turn_id == turn_id
            assert entry.status == "active"
            assert entry.tags == ["career"]
            assert entry.metadata == {"source": "test"}

        asyncio.run(run())

    def test_get_timeline_with_filter(self) -> None:
        async def run() -> None:
            database = FakeDatabase()
            timeline = RelationshipTimeline(database.session_factory)
            session_id = _uuid()

            await timeline.add_timeline_entry(session_id, "milestone", "A", "A", "A", _uuid(), tags=["work"])
            promise = await timeline.add_timeline_entry(
                session_id,
                "promise",
                "B",
                "B",
                "B",
                _uuid(),
                tags=["work", "urgent"],
            )
            broken = await timeline.add_timeline_entry(
                session_id,
                "promise",
                "C",
                "C",
                "C",
                _uuid(),
                tags=["urgent"],
            )
            await timeline.update_timeline_entry(broken.entry_id, status="broken")

            filtered = await timeline.get_timeline(
                session_id,
                TimelineFilter(
                    entry_types=["promise"],
                    statuses=["active"],
                    tags=["urgent"],
                ),
            )

            assert [entry.entry_id for entry in filtered] == [promise.entry_id]

        asyncio.run(run())

    def test_get_active_projects(self) -> None:
        async def run() -> None:
            database = FakeDatabase()
            timeline = RelationshipTimeline(database.session_factory)
            session_id = _uuid()

            active = await timeline.add_timeline_entry(
                session_id,
                "running_project",
                "Dashboard",
                "Active dashboard",
                "content",
                _uuid(),
            )
            abandoned = await timeline.add_timeline_entry(
                session_id,
                "running_project",
                "Old app",
                "Old app",
                "content",
                _uuid(),
            )
            await timeline.add_timeline_entry(session_id, "promise", "Promise", "Promise", "content", _uuid())
            await timeline.update_timeline_entry(abandoned.entry_id, status="abandoned")

            projects = await timeline.get_active_projects(session_id)

            assert [entry.entry_id for entry in projects] == [active.entry_id]

        asyncio.run(run())

    def test_get_pending_promises(self) -> None:
        async def run() -> None:
            database = FakeDatabase()
            timeline = RelationshipTimeline(database.session_factory)
            session_id = _uuid()

            pending = await timeline.add_timeline_entry(session_id, "promise", "Send notes", "Send notes", "content", _uuid())
            resolved = await timeline.add_timeline_entry(session_id, "promise", "Done", "Done", "content", _uuid())
            await timeline.add_timeline_entry(session_id, "running_project", "Project", "Project", "content", _uuid())
            await timeline.resolve_entry(resolved.entry_id)

            promises = await timeline.get_pending_promises(session_id)

            assert [entry.entry_id for entry in promises] == [pending.entry_id]

        asyncio.run(run())

    def test_resolve_entry(self) -> None:
        async def run() -> None:
            database = FakeDatabase()
            timeline = RelationshipTimeline(database.session_factory)
            session_id = _uuid()
            promise = await timeline.add_timeline_entry(session_id, "promise", "Send notes", "Send notes", "content", _uuid())

            resolved = await timeline.resolve_entry(promise.entry_id, "Sent the notes")

            assert resolved.status == "resolved"
            assert resolved.metadata["resolution"] == "Sent the notes"

        asyncio.run(run())

    def test_update_not_found(self) -> None:
        async def run() -> None:
            timeline = RelationshipTimeline(FakeDatabase().session_factory)

            with pytest.raises(ValueError, match="not found"):
                await timeline.update_timeline_entry(_uuid(), status="resolved")

        asyncio.run(run())


class TestNarrative:
    def test_build_narrative_threads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def run() -> None:
            database = FakeDatabase()
            session_id = _uuid()
            database.add(
                _message(
                    session_id=session_id,
                    content="The garden project needs a planting calendar.",
                    created_at=_now(timedelta(days=-2)),
                )
            )
            database.add(
                _message(
                    session_id=session_id,
                    content="The garden project calendar also needs irrigation notes.",
                    created_at=_now(timedelta(days=-1)),
                )
            )
            monkeypatch.setattr(narrative, "async_session_factory", database.session_factory)

            threads = await narrative.build_narrative_threads(session_id)

            assert len(threads) == 1
            assert threads[0].session_id == session_id
            assert len(threads[0].turns) == 2
            assert database.models(RelationshipTimelineEntryModel)[0].entry_type == narrative.NARRATIVE_ENTRY_TYPE

        asyncio.run(run())

    def test_generate_callback_with_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def run() -> None:
            database = FakeDatabase()
            session_id = _uuid()
            database.add(
                _message(
                    session_id=session_id,
                    content="The garden project needs a planting calendar.",
                    created_at=_now(timedelta(days=-2)),
                )
            )
            database.add(
                _message(
                    session_id=session_id,
                    content="The garden project calendar also needs irrigation notes.",
                    created_at=_now(timedelta(days=-1)),
                )
            )
            monkeypatch.setattr(narrative, "async_session_factory", database.session_factory)
            await narrative.build_narrative_threads(session_id)

            too_strict = await narrative.generate_thread_callback(
                session_id,
                "garden project calendar",
                relevance_threshold=1.1,
            )
            permissive = await narrative.generate_thread_callback(
                session_id,
                "garden project calendar",
                relevance_threshold=0.05,
            )

            assert too_strict == []
            assert permissive
            assert permissive[0].thread_id

        asyncio.run(run())

    def test_mark_thread_referenced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def run() -> None:
            database = FakeDatabase()
            session_id = _uuid()
            database.add(_message(session_id=session_id, content="Garden calendar planning.", created_at=_now(timedelta(days=-2))))
            database.add(_message(session_id=session_id, content="Garden calendar planting notes.", created_at=_now(timedelta(days=-1))))
            monkeypatch.setattr(narrative, "async_session_factory", database.session_factory)
            threads = await narrative.build_narrative_threads(session_id)
            model = database.models(RelationshipTimelineEntryModel)[0]
            before = model.metadata_json["last_referenced_at"]

            await narrative.mark_thread_referenced(threads[0].thread_id)

            assert model.metadata_json["last_referenced_at"] != before

        asyncio.run(run())


class TestRituals:
    def test_create_and_get_rituals(self) -> None:
        async def run() -> None:
            database = FakeDatabase()
            rituals = Rituals(database.session_factory)
            session_id = _uuid()

            created = await rituals.create_ritual(
                session_id,
                "Morning check",
                "check_in",
                "Ask about the day",
                "daily",
                "How is the morning going?",
            )
            results = await rituals.get_rituals(session_id)

            assert results == [created]
            assert results[0].name == "Morning check"
            assert results[0].ritual_type == "check_in"
            assert results[0].trigger_prompt == "How is the morning going?"

        asyncio.run(run())

    def test_get_due_rituals(self) -> None:
        async def run() -> None:
            database = FakeDatabase()
            rituals = Rituals(database.session_factory)
            session_id = _uuid()
            created = await rituals.create_ritual(session_id, "Weekly", "planning", "Plan", "weekly")
            database.models(CompanionRitualModel)[0].next_trigger_at = _now(timedelta(hours=-1))

            due = await rituals.get_due_rituals(session_id)

            assert [ritual.ritual_id for ritual in due] == [created.ritual_id]

        asyncio.run(run())

    def test_trigger_ritual(self) -> None:
        async def run() -> None:
            database = FakeDatabase()
            rituals = Rituals(database.session_factory)
            session_id = _uuid()
            created = await rituals.create_ritual(session_id, "Daily", "check_in", "Check in", "daily", "Prompt")

            prompt = await rituals.trigger_ritual(created.ritual_id)
            updated = database.models(CompanionRitualModel)[0]

            assert prompt == "Prompt"
            assert updated.last_triggered_at is not None
            assert updated.next_trigger_at is not None

        asyncio.run(run())

    def test_add_inside_joke(self) -> None:
        async def run() -> None:
            rituals = Rituals(FakeDatabase().session_factory)

            joke = await rituals.add_inside_joke(_uuid(), "Spreadsheet bit", "Not the cells again", "Spreadsheet debugging")

            assert joke.ritual_type == "joke"
            assert joke.trigger_prompt == "Not the cells again"
            assert joke.metadata["joke_text"] == "Not the cells again"

        asyncio.run(run())

    def test_deactivate_ritual(self) -> None:
        async def run() -> None:
            database = FakeDatabase()
            rituals = Rituals(database.session_factory)
            created = await rituals.create_ritual(_uuid(), "Daily", "check_in", "Check in", "daily")

            await rituals.deactivate_ritual(created.ritual_id)
            inactive = await rituals.get_rituals(created.session_id, active_only=False)

            assert inactive[0].is_active is False

        asyncio.run(run())


class TestTimelineUpdateStep:
    def test_step_detects_milestone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def run() -> None:
            calls: list[dict[str, Any]] = []

            async def fake_add_timeline_entry(**kwargs: Any) -> SimpleNamespace:
                calls.append(kwargs)
                return SimpleNamespace(entry_id=_uuid())

            monkeypatch.setattr("chatbot.core.turn.add_timeline_entry", fake_add_timeline_entry)
            monkeypatch.setattr("chatbot.core.turn.get_due_rituals", lambda _session_id: asyncio.sleep(0, result=[]))
            ctx = _turn_context(user_message="I got a new job today and I am proud.")

            await TimelineUpdateStep().run(ctx)

            assert any(call["entry_type"] == "milestone" for call in calls)
            assert ctx.metadata["new_timeline_entries"]

        asyncio.run(run())

    def test_step_detects_promise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def run() -> None:
            calls: list[dict[str, Any]] = []

            async def fake_add_timeline_entry(**kwargs: Any) -> SimpleNamespace:
                calls.append(kwargs)
                return SimpleNamespace(entry_id=_uuid())

            monkeypatch.setattr("chatbot.core.turn.add_timeline_entry", fake_add_timeline_entry)
            monkeypatch.setattr("chatbot.core.turn.get_due_rituals", lambda _session_id: asyncio.sleep(0, result=[]))
            ctx = _turn_context(user_message="I will send the notes tomorrow.")

            await TimelineUpdateStep().run(ctx)

            assert any(call["entry_type"] == "promise" for call in calls)

        asyncio.run(run())

    def test_step_triggers_due_ritual(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def run() -> None:
            triggered: list[str] = []

            async def fake_due_rituals(_session_id: str) -> list[SimpleNamespace]:
                return [SimpleNamespace(ritual_id="ritual-1", trigger_prompt="fallback")]

            async def fake_trigger_ritual(ritual_id: str) -> str:
                triggered.append(ritual_id)
                return "Ask about the weekly plan."

            async def fake_add_timeline_entry(**_kwargs: Any) -> SimpleNamespace:
                return SimpleNamespace(entry_id=_uuid())

            monkeypatch.setattr("chatbot.core.turn.get_due_rituals", fake_due_rituals)
            monkeypatch.setattr("chatbot.core.turn.trigger_ritual", fake_trigger_ritual)
            monkeypatch.setattr("chatbot.core.turn.add_timeline_entry", fake_add_timeline_entry)
            ctx = _turn_context(user_message="hello")

            await TimelineUpdateStep().run(ctx)

            assert triggered == ["ritual-1"]
            assert ctx.metadata["ritual_prompts"] == ["Ask about the weekly plan."]

        asyncio.run(run())
