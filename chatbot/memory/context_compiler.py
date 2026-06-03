from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from chatbot.continuity.narrative import ThreadCallback, generate_thread_callback
from chatbot.continuity.timeline import (
    TimelineEntry,
    get_active_projects,
    get_pending_promises,
    get_recent_milestones,
)
from chatbot.memory.episodic import EpisodicTurn
from chatbot.memory.retrieval import RetrievalResult
from chatbot.memory.working_memory import WorkingMemoryTurn, WorkingMemoryWindow


BudgetCounter = Callable[[str], int]
_CONTINUITY_CONTEXT_ID = "__continuity_context__"
_CONTINUITY_CONTEXT_SCORE_EPSILON = 1e-6


def _char_count(text: str) -> int:
    return len(text)


def _content_key(role: str, content: str) -> str:
    """Normalized dedup key: collapses whitespace so minor formatting diffs don't create duplicates."""
    return f"{role}\x1f{' '.join(content.split())}"


def _episodic_sort_key(turn: EpisodicTurn) -> tuple[bool, float, str]:
    """Chronological sort: turns with no timestamp sort after those with one."""
    if turn.created_at is None:
        return (True, 0.0, turn.id)
    ts = turn.created_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (False, ts.timestamp(), turn.id)


@dataclass(frozen=True, slots=True)
class PersonaStub:
    """A named block of persona or identity content (typically a system message)."""

    key: str
    content: str
    priority: int = 0

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("key must not be empty")


@dataclass(frozen=True, slots=True)
class ContextSource:
    """Breakdown of what each tier contributed to the compiled context."""

    persona_count: int
    persona_chars: int
    working_count: int
    working_chars: int
    episodic_count: int
    episodic_chars: int


@dataclass(frozen=True, slots=True)
class CompiledContext:
    """Assembled context payload for a single chat turn."""

    persona_stubs: tuple[PersonaStub, ...]
    working_turns: tuple[WorkingMemoryTurn, ...]
    # Episodic turns sorted chronologically for coherent output order.
    episodic_turns: tuple[EpisodicTurn, ...]
    budget_used: int
    budget_total: int
    source: ContextSource

    def as_messages(self) -> list[dict[str, Any]]:
        """Return a flat message list: persona (system), episodic, then working memory."""
        messages: list[dict[str, Any]] = []
        for stub in self.persona_stubs:
            messages.append({"role": "system", "content": stub.content})
        for turn in self.episodic_turns:
            messages.append(turn.as_message())
        for turn in self.working_turns:
            messages.append(turn.as_message())
        return messages


class ContextCompiler:
    """Stateless compiler that assembles a bounded, ranked context payload."""

    def __init__(
        self,
        budget: int,
        *,
        char_counter: BudgetCounter | None = None,
    ) -> None:
        if budget <= 0:
            raise ValueError("budget must be greater than zero")
        self.budget = budget
        self._char_counter: BudgetCounter = char_counter or _char_count

    def compile(
        self,
        *,
        working_memory: WorkingMemoryWindow | None = None,
        retrieval_results: list[RetrievalResult] | None = None,
        persona_stubs: list[PersonaStub] | None = None,
    ) -> CompiledContext:
        return compile_context(
            budget=self.budget,
            working_memory=working_memory,
            retrieval_results=retrieval_results,
            persona_stubs=persona_stubs,
            char_counter=self._char_counter,
        )

    async def compile_with_continuity(
        self,
        *,
        session_id: str,
        current_message: str,
        working_memory: WorkingMemoryWindow | None = None,
        retrieval_results: list[RetrievalResult] | None = None,
        persona_stubs: list[PersonaStub] | None = None,
    ) -> CompiledContext:
        return await compile_context_with_continuity(
            budget=self.budget,
            session_id=session_id,
            current_message=current_message,
            working_memory=working_memory,
            retrieval_results=retrieval_results,
            persona_stubs=persona_stubs,
            char_counter=self._char_counter,
        )


def compile_context(
    *,
    budget: int,
    working_memory: WorkingMemoryWindow | None = None,
    retrieval_results: list[RetrievalResult] | None = None,
    persona_stubs: list[PersonaStub] | None = None,
    char_counter: BudgetCounter | None = None,
) -> CompiledContext:
    """Assemble a context payload using a tiered, budget-aware merge with deduplication.

    Tier 1 — Persona stubs: highest priority; sorted by (priority desc, key asc).
    Tier 2 — Working memory: most-recent turns first; output restored to chronological order.
    Tier 3 — Episodic results: highest-score first; content already in working memory is skipped.
    """
    if budget <= 0:
        raise ValueError("budget must be greater than zero")

    counter = char_counter or _char_count
    remaining = budget

    # --- Tier 1: Persona stubs ---
    # Sort deterministically: higher priority first, then alphabetically by key.
    sorted_persona = sorted(
        persona_stubs or [],
        key=lambda s: (-s.priority, s.key),
    )
    included_persona: list[PersonaStub] = []
    persona_chars = 0
    for stub in sorted_persona:
        cost = counter(stub.content)
        if cost <= remaining:
            included_persona.append(stub)
            persona_chars += cost
            remaining -= cost

    # --- Tier 2: Working memory ---
    # Iterate newest-to-oldest so the most recent turns survive budget exhaustion.
    all_working: list[WorkingMemoryTurn] = (
        list(working_memory.turns) if working_memory is not None else []
    )
    included_working: list[WorkingMemoryTurn] = []
    working_chars = 0
    for turn in reversed(all_working):
        cost = counter(turn.content)
        if cost <= remaining:
            included_working.append(turn)
            working_chars += cost
            remaining -= cost
    included_working.reverse()  # restore chronological (oldest → newest) order

    # Content keys of included working turns; used to deduplicate episodic results.
    working_keys: set[str] = {
        _content_key(t.role, t.content) for t in included_working
    }

    # --- Tier 3: Episodic retrieval results ---
    # Sort deterministically: highest score first, then by turn id to break ties.
    sorted_episodic = sorted(
        retrieval_results or [],
        key=lambda r: (-r.score, r.turn.id),
    )
    included_episodic: list[EpisodicTurn] = []
    seen_episodic_keys: set[str] = set()
    episodic_chars = 0
    for result in sorted_episodic:
        turn = result.turn
        key = _content_key(turn.role, turn.content)
        if key in working_keys or key in seen_episodic_keys:
            continue
        cost = counter(turn.content)
        if cost <= remaining:
            included_episodic.append(turn)
            seen_episodic_keys.add(key)
            episodic_chars += cost
            remaining -= cost

    # Sort episodic turns chronologically for coherent output (no-timestamp entries last).
    included_episodic.sort(key=_episodic_sort_key)

    return CompiledContext(
        persona_stubs=tuple(included_persona),
        working_turns=tuple(included_working),
        episodic_turns=tuple(included_episodic),
        budget_used=budget - remaining,
        budget_total=budget,
        source=ContextSource(
            persona_count=len(included_persona),
            persona_chars=persona_chars,
            working_count=len(included_working),
            working_chars=working_chars,
            episodic_count=len(included_episodic),
            episodic_chars=episodic_chars,
        ),
    )


async def compile_context_with_continuity(
    *,
    budget: int,
    session_id: str,
    current_message: str,
    working_memory: WorkingMemoryWindow | None = None,
    retrieval_results: list[RetrievalResult] | None = None,
    persona_stubs: list[PersonaStub] | None = None,
    char_counter: BudgetCounter | None = None,
) -> CompiledContext:
    """Assemble context with additive continuity injected as a ranked block."""
    continuity_context = await _inject_timeline_context(
        session_id=session_id,
        current_message=current_message,
    )
    if not continuity_context:
        return compile_context(
            budget=budget,
            working_memory=working_memory,
            retrieval_results=retrieval_results,
            persona_stubs=persona_stubs,
            char_counter=char_counter,
        )

    ranked_results = list(retrieval_results or [])
    ranked_results.append(
        _continuity_retrieval_result(
            session_id=session_id,
            content=continuity_context,
            retrieval_results=ranked_results,
        )
    )
    return compile_context(
        budget=budget,
        working_memory=working_memory,
        retrieval_results=ranked_results,
        persona_stubs=persona_stubs,
        char_counter=char_counter,
    )


async def _inject_timeline_context(
    session_id: str,
    current_message: str,
    compiled_context: str | None = None,
) -> str:
    milestones, active_projects, pending_promises, callbacks = await asyncio.gather(
        get_recent_milestones(session_id, limit=10),
        get_active_projects(session_id),
        get_pending_promises(session_id),
        generate_thread_callback(
            session_id,
            current_message,
            relevance_threshold=0.6,
        ),
    )
    _ = compiled_context

    sections: list[str] = []
    milestone_lines = [_timeline_entry_line(entry) for entry in milestones[:10]]
    milestone_lines = [line for line in milestone_lines if line]
    if milestone_lines:
        sections.append("Milestones:\n" + "\n".join(milestone_lines))

    project_lines = [_timeline_entry_line(entry) for entry in active_projects]
    project_lines = [line for line in project_lines if line]
    if project_lines:
        sections.append("Active Projects:\n" + "\n".join(project_lines))

    promise_lines = [_timeline_entry_line(entry) for entry in pending_promises]
    promise_lines = [line for line in promise_lines if line]
    if promise_lines:
        sections.append("Pending Promises:\n" + "\n".join(promise_lines))

    callback_lines = [_thread_callback_line(callback) for callback in callbacks[:3]]
    callback_lines = [line for line in callback_lines if line]
    if callback_lines:
        sections.append("Narrative Callbacks:\n" + "\n".join(callback_lines))

    if not sections:
        return ""
    return "Continuity Context:\n" + "\n\n".join(sections)


def _continuity_retrieval_result(
    *,
    session_id: str,
    content: str,
    retrieval_results: list[RetrievalResult],
) -> RetrievalResult:
    score = _continuity_priority_score(retrieval_results)
    return RetrievalResult(
        turn=EpisodicTurn(
            id=_CONTINUITY_CONTEXT_ID,
            session_id=session_id,
            role="system",
            content=content,
            created_at=datetime.max.replace(tzinfo=timezone.utc),
            metadata={"type": "continuity_context"},
        ),
        score=score,
        vector_score=score,
        rank=0,
        metadata_matches={"type": "continuity_context"},
    )


def _continuity_priority_score(retrieval_results: list[RetrievalResult]) -> float:
    scores = [result.score for result in retrieval_results]
    if not scores:
        return 1.0

    highest = max(scores)
    lower_scores = [score for score in scores if score < highest]
    if lower_scores:
        return (highest + max(lower_scores)) / 2
    return highest - _CONTINUITY_CONTEXT_SCORE_EPSILON


def _timeline_entry_line(entry: TimelineEntry) -> str:
    title = _compact_text(entry.title, max_chars=80)
    detail = _compact_text(_first_present(entry.description, entry.content), max_chars=180)
    created_at = str(entry.created_at or "").strip()
    date_prefix = f"{created_at[:10]}: " if len(created_at) >= 10 else ""
    if title and detail and detail != title:
        return f"- {date_prefix}{title}: {detail}"
    if title:
        return f"- {date_prefix}{title}"
    if detail:
        return f"- {date_prefix}{detail}"
    return ""


def _thread_callback_line(callback: ThreadCallback) -> str:
    label = _compact_text(callback.label, max_chars=80)
    text = _compact_text(callback.callback_text, max_chars=180)
    summary = _compact_text(callback.thread_summary, max_chars=140)
    confidence = f"{callback.confidence:.2f}"
    if label and text:
        line = f"- {label} ({confidence}): {text}"
    elif text:
        line = f"- ({confidence}): {text}"
    elif label:
        line = f"- {label} ({confidence})"
    else:
        return ""
    if summary and summary != text:
        line = f"{line} [{summary}]"
    return line


def _first_present(*values: str | None) -> str:
    for value in values:
        if value and value.strip():
            return value
    return ""


def _compact_text(value: str, *, max_chars: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


__all__ = [
    "BudgetCounter",
    "CompiledContext",
    "ContextCompiler",
    "ContextSource",
    "PersonaStub",
    "compile_context",
    "compile_context_with_continuity",
]
