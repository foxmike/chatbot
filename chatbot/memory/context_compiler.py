from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from typing import Any, Callable

from chatbot.memory.episodic import EpisodicTurn
from chatbot.memory.retrieval import RetrievalResult
from chatbot.memory.working_memory import WorkingMemoryTurn, WorkingMemoryWindow


BudgetCounter = Callable[[str], int]


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


__all__ = [
    "BudgetCounter",
    "CompiledContext",
    "ContextCompiler",
    "ContextSource",
    "PersonaStub",
    "compile_context",
]
