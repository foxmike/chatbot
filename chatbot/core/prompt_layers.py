"""Deterministic system prompt layer composition."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from chatbot.core.token_budget import TokenBudget, estimate_tokens
from chatbot.memory.timescale import (
    ACTIVE_GOAL,
    LONG_TERM_NARRATIVE_ARC,
    MEMORY_TIMESCALES,
    RECENT_CONVERSATIONAL_STATE,
    STABLE_IDENTITY_FACT,
    MemoryTimescale,
)


class PromptLayerName(str, Enum):
    """Canonical prompt layer identifiers in priority order."""

    SAFETY_FLOOR = "safety_floor"
    PLATFORM_BEHAVIOR = "platform_behavior"
    COMPANION_IDENTITY = "companion_identity"
    OPEN_THREADS = "open_threads"
    RELATIONSHIP_STAGE = "relationship_stage"
    ROOM_SCENE = "room_scene"
    MOOD_STATE = "mood_state"
    CHARACTER_BEHAVIOR = "character_behavior"
    MEMORY = "memory"
    SESSION_GOALS = "session_goals"


PROMPT_LAYER_ORDER: tuple[PromptLayerName, ...] = (
    PromptLayerName.SAFETY_FLOOR,
    PromptLayerName.PLATFORM_BEHAVIOR,
    PromptLayerName.COMPANION_IDENTITY,
    PromptLayerName.OPEN_THREADS,
    PromptLayerName.RELATIONSHIP_STAGE,
    PromptLayerName.ROOM_SCENE,
    PromptLayerName.MOOD_STATE,
    PromptLayerName.CHARACTER_BEHAVIOR,
    PromptLayerName.MEMORY,
    PromptLayerName.SESSION_GOALS,
)

MEMORY_REALISM_DIRECTIVES: dict[MemoryTimescale, str] = {
    RECENT_CONVERSATIONAL_STATE: (
        "Recent conversational state represents fragments of the last 24 hours. "
        "Details may be incomplete, compressed, or slightly misordered. "
        "Treat this as continuity context, not as verbatim transcript. "
        "If a detail feels off, acknowledge uncertainty rather than fabricating precision."
    ),
    STABLE_IDENTITY_FACT: (
        "Stable identity facts are durable user details and preferences stored over time. "
        "Treat these as reliable unless explicitly contradicted by the user. "
        "If a fact seems outdated, implausible, or conflicts with recent conversation, "
        "gently verify with the user rather than silently correcting. "
        "When multiple identity facts contradict each other, defer to the most recent."
    ),
    LONG_TERM_NARRATIVE_ARC: (
        "Long-term narrative arcs track ongoing themes, milestones, and recurring "
        "patterns across sessions. These may contain gaps, merged events, or fuzzy "
        "timelines. If a narrative reference feels incomplete or ambiguous, "
        "acknowledge what you know and invite the user to fill in missing context. "
        "Do not invent narrative connections that were not retrieved from memory."
    ),
    ACTIVE_GOAL: (
        "Active goals represent current user intentions, projects, promises, and "
        "open tasks. Treat these as active priorities unless the user indicates "
        "otherwise. If a goal appears to be resolved, superseded, or abandoned "
        "based on recent conversation, confirm before assuming it is no longer "
        "relevant. Do not fabricate completion status for goals lacking resolution."
    ),
}

_ASSISTANTISM_PATTERNS: tuple[str, ...] = (
    "overly formal or corporate phrasing",
    "preambles like 'Certainly!', 'Of course!', 'I'd be happy to', 'Great question!'",
    "closing formulas like 'Let me know if you need anything else'",
    "faux-empathy like 'I understand how frustrating that must be'",
    "hedging boilerplate like 'As an AI', 'It's important to note that'",
    "unsolicited disclaimers or safety warnings in casual conversation",
    "bullet-point summaries and numbered lists when the user is chatting naturally",
    "over-explaining simple concepts the user clearly already understands",
)

ASSISTANTISM_SCRUB_DIRECTIVE: str = (
    "You are a companion, not an assistant. Speak like a person, not a product.\n"
    "Avoid 'assistantisms' — the verbal tics of AI chatbots. Specifically:\n"
    + "\n".join(f"- Do not use {pattern}." for pattern in _ASSISTANTISM_PATTERNS)
    + "\n\n"
    "Instead: use natural, conversational language. Be direct. Be warm but not saccharine. "
    "Let your personality show through word choice, rhythm, and tone — not through "
    "performed helpfulness. If you don't know something, just say so. If you disagree, "
    "say so respectfully. The goal is authentic human-like interaction, not customer-service polish."
)


PROMPT_LAYER_TITLES: dict[PromptLayerName, str] = {
    PromptLayerName.SAFETY_FLOOR: "Safety Floor",
    PromptLayerName.PLATFORM_BEHAVIOR: "Platform Behavior",
    PromptLayerName.COMPANION_IDENTITY: "Companion Identity",
    PromptLayerName.OPEN_THREADS: "Open Threads",
    PromptLayerName.RELATIONSHIP_STAGE: "Relationship Stage",
    PromptLayerName.ROOM_SCENE: "Room/Scene",
    PromptLayerName.MOOD_STATE: "Mood/State",
    PromptLayerName.CHARACTER_BEHAVIOR: "Character Behavior",
    PromptLayerName.MEMORY: "Memory",
    PromptLayerName.SESSION_GOALS: "Session Goals",
}

_PROMPT_LAYER_ALIASES: dict[str, PromptLayerName] = {
    layer.value: layer for layer in PROMPT_LAYER_ORDER
}
_PROMPT_LAYER_ALIASES.update(
    {
        "safety": PromptLayerName.SAFETY_FLOOR,
        "safety floor": PromptLayerName.SAFETY_FLOOR,
        "platform": PromptLayerName.PLATFORM_BEHAVIOR,
        "platform behavior": PromptLayerName.PLATFORM_BEHAVIOR,
        "companion": PromptLayerName.COMPANION_IDENTITY,
        "companion identity": PromptLayerName.COMPANION_IDENTITY,
        "identity": PromptLayerName.COMPANION_IDENTITY,
        "open threads": PromptLayerName.OPEN_THREADS,
        "open_threads": PromptLayerName.OPEN_THREADS,
        "relationship": PromptLayerName.RELATIONSHIP_STAGE,
        "relationship stage": PromptLayerName.RELATIONSHIP_STAGE,
        "room": PromptLayerName.ROOM_SCENE,
        "scene": PromptLayerName.ROOM_SCENE,
        "room/scene": PromptLayerName.ROOM_SCENE,
        "room scene": PromptLayerName.ROOM_SCENE,
        "mood": PromptLayerName.MOOD_STATE,
        "state": PromptLayerName.MOOD_STATE,
        "mood/state": PromptLayerName.MOOD_STATE,
        "mood state": PromptLayerName.MOOD_STATE,
        "memory": PromptLayerName.MEMORY,
        "memories": PromptLayerName.MEMORY,
        "goals": PromptLayerName.SESSION_GOALS,
        "session goals": PromptLayerName.SESSION_GOALS,
        "session_goals": PromptLayerName.SESSION_GOALS,
    }
)

_PROMPT_LAYER_PRIORITY: dict[PromptLayerName, int] = {
    layer: index for index, layer in enumerate(PROMPT_LAYER_ORDER)
}


@dataclass(frozen=True, slots=True)
class PromptLayer:
    """A named system prompt block."""

    name: PromptLayerName
    content: str

    @property
    def priority(self) -> int:
        """Return the deterministic composition priority for this layer."""
        return _PROMPT_LAYER_PRIORITY[self.name]

    @property
    def title(self) -> str:
        """Return the human-readable layer title."""
        return PROMPT_LAYER_TITLES[self.name]


def normalize_layer_name(name: str | PromptLayerName) -> PromptLayerName:
    """Return the canonical prompt layer name for a label or slug."""
    if isinstance(name, PromptLayerName):
        return name

    normalized = str(name).strip().lower().replace("-", "_")
    layer = _PROMPT_LAYER_ALIASES.get(normalized)
    if layer is not None:
        return layer

    normalized = normalized.replace("_", " ")
    layer = _PROMPT_LAYER_ALIASES.get(normalized)
    if layer is not None:
        return layer

    raise ValueError(f"Unknown prompt layer: {name!r}")


def make_prompt_layer(
    name: str | PromptLayerName,
    content: Any,
) -> PromptLayer | None:
    """Create a prompt layer, returning ``None`` for blank content."""
    text = _coerce_layer_content(content)
    if not text:
        return None
    return PromptLayer(name=normalize_layer_name(name), content=text)


def compose_memory_realism_directives(
    timescales: Iterable[MemoryTimescale] | None = None,
    *,
    header: str = "Memory realism directives:",
) -> str:
    """Compose memory realism directives for the given timescales.

    When ``timescales`` is ``None``, directives for all known timescales are
    included.  Each directive instructs the model how to handle uncertainty,
    absence, or incompleteness in memories of that timescale.
    """
    selected = list(timescales) if timescales is not None else list(MEMORY_TIMESCALES)
    parts = [header]
    for ts in selected:
        directive = MEMORY_REALISM_DIRECTIVES.get(ts)
        if directive:
            parts.append(f"- [{ts}]: {directive}")
    return "\n".join(parts)


def compose_assistantism_scrub_directive(
    *,
    header: str = "",
) -> str:
    """Return the assistantism scrubbing directive.

    This directive instructs the model to avoid 'assistantisms' — verbal tics
    common in AI chatbot responses — and instead use natural, human-like
    conversational language.

    When ``header`` is provided, it is prepended as a label (e.g.
    ``"Voice guidelines:"``).
    """
    if header:
        return f"{header}\n{ASSISTANTISM_SCRUB_DIRECTIVE}"
    return ASSISTANTISM_SCRUB_DIRECTIVE


_LAYER_BUDGET_SECTION: dict[PromptLayerName, str] = {
    PromptLayerName.SAFETY_FLOOR: "persona",
    PromptLayerName.PLATFORM_BEHAVIOR: "persona",
    PromptLayerName.COMPANION_IDENTITY: "persona",
    PromptLayerName.OPEN_THREADS: "memory",
    PromptLayerName.RELATIONSHIP_STAGE: "persona",
    PromptLayerName.MEMORY: "memory",
    PromptLayerName.ROOM_SCENE: "scene",
    PromptLayerName.MOOD_STATE: "scene",
    PromptLayerName.CHARACTER_BEHAVIOR: "persona",
    PromptLayerName.SESSION_GOALS: "scene",
}


def _truncate_to_budget(content: str, max_tokens: int) -> str:
    """Truncate text to approximately fit within max_tokens."""
    if max_tokens <= 0:
        return ""
    if estimate_tokens(content) <= max_tokens:
        return content
    char_limit = max_tokens * 4
    truncated = content[:char_limit]
    for sep in ("\n\n", "\n", ". ", " "):
        idx = truncated.rfind(sep)
        if idx > char_limit // 2:
            return truncated[: idx + len(sep)].rstrip()
    return truncated.rstrip()


def _apply_layer_budgets(layers: list[PromptLayer], budget: TokenBudget) -> list[PromptLayer]:
    """Trim layers proportionally to respect per-section token budgets."""
    section_layers: dict[str, list[PromptLayer]] = {}
    for layer in layers:
        section = _LAYER_BUDGET_SECTION.get(layer.name)
        if section is not None:
            section_layers.setdefault(section, []).append(layer)

    by_name: dict[PromptLayerName, PromptLayer] = {layer.name: layer for layer in layers}

    for section, section_layer_list in section_layers.items():
        section_budget = budget.sections.get(section)
        if section_budget is None or section_budget.budget_tokens <= 0:
            continue
        max_tokens = section_budget.budget_tokens
        token_counts = {
            layer.name: estimate_tokens(layer.content) for layer in section_layer_list
        }
        total = sum(token_counts.values())
        if total <= max_tokens:
            continue
        for layer in section_layer_list:
            layer_tokens = token_counts[layer.name]
            if layer_tokens == 0:
                continue
            layer_budget = max(64, int(max_tokens * layer_tokens / total))
            if layer_tokens > layer_budget:
                trimmed = _truncate_to_budget(layer.content, layer_budget)
                by_name[layer.name] = PromptLayer(name=layer.name, content=trimmed)

    return [by_name[layer.name] for layer in layers]


def build_prompt_layers(
    *,
    safety_floor: Any = None,
    platform_behavior: Any = None,
    companion_identity: Any = None,
    open_threads: Any = None,
    relationship_stage: Any = None,
    room_scene: Any = None,
    mood_state: Any = None,
    character_behavior: Any = None,
    memory: Any = None,
    session_goals: Any = None,
    attention_anchors: Any = None,
    memory_timescales: Iterable[MemoryTimescale] | None = None,
    inject_memory_realism: bool = True,
    inject_assistantism_scrub: bool = True,
    token_budget: TokenBudget | None = None,
) -> list[PromptLayer]:
    """Build prompt layers from named blocks in canonical priority order.

    When ``inject_memory_realism`` is ``True`` (the default) and memory
    content is present, imperfection-handling directives segmented by
    timescale are appended to the memory layer.  Pass a subset of
    ``memory_timescales`` to restrict which timescale directives are emitted.

    When ``inject_assistantism_scrub`` is ``True`` (the default), the
    assistantism scrubbing directive is appended to the companion identity
    layer to suppress AI chatbot verbal tics.

    When ``attention_anchors`` is provided, the pre-formatted anchor content
    (unresolved questions, companion promises, active goals) is appended to
    the session goals layer.

    When ``token_budget`` is provided, layer content is trimmed proportionally
    to respect the per-section token allocations.
    """
    memory_layer = make_prompt_layer(PromptLayerName.MEMORY, memory)
    if inject_memory_realism and memory_layer is not None:
        directives = compose_memory_realism_directives(timescales=memory_timescales)
        memory_layer = PromptLayer(
            name=PromptLayerName.MEMORY,
            content=f"{memory_layer.content}\n\n{directives}",
        )

    identity_layer = make_prompt_layer(PromptLayerName.COMPANION_IDENTITY, companion_identity)
    if inject_assistantism_scrub:
        scrub = compose_assistantism_scrub_directive()
        if identity_layer is not None:
            identity_layer = PromptLayer(
                name=PromptLayerName.COMPANION_IDENTITY,
                content=f"{identity_layer.content}\n\n{scrub}",
            )
        else:
            identity_layer = PromptLayer(
                name=PromptLayerName.COMPANION_IDENTITY,
                content=scrub,
            )

    goals_layer = make_prompt_layer(PromptLayerName.SESSION_GOALS, session_goals)
    if attention_anchors is not None:
        anchor_text = _coerce_layer_content(attention_anchors)
        if anchor_text:
            if goals_layer is not None:
                goals_layer = PromptLayer(
                    name=PromptLayerName.SESSION_GOALS,
                    content=f"{goals_layer.content}\n\n{anchor_text}",
                )
            else:
                goals_layer = PromptLayer(
                    name=PromptLayerName.SESSION_GOALS,
                    content=anchor_text,
                )

    assembled = sorted_prompt_layers(
        (
            make_prompt_layer(PromptLayerName.SAFETY_FLOOR, safety_floor),
            make_prompt_layer(PromptLayerName.PLATFORM_BEHAVIOR, platform_behavior),
            identity_layer,
            make_prompt_layer(PromptLayerName.OPEN_THREADS, open_threads),
            make_prompt_layer(PromptLayerName.RELATIONSHIP_STAGE, relationship_stage),
            make_prompt_layer(PromptLayerName.ROOM_SCENE, room_scene),
            make_prompt_layer(PromptLayerName.MOOD_STATE, mood_state),
            make_prompt_layer(PromptLayerName.CHARACTER_BEHAVIOR, character_behavior),
            memory_layer,
            goals_layer,
        )
    )

    if token_budget is not None:
        assembled = _apply_layer_budgets(assembled, token_budget)

    return assembled


def sorted_prompt_layers(
    layers: Iterable[PromptLayer | None],
) -> list[PromptLayer]:
    """Return non-empty layers sorted by deterministic prompt priority."""
    merged: dict[PromptLayerName, list[str]] = {}
    for layer in layers:
        if layer is None:
            continue
        text = _coerce_layer_content(layer.content)
        if not text:
            continue
        merged.setdefault(layer.name, []).append(text)

    return [
        PromptLayer(name=layer_name, content="\n\n".join(merged[layer_name]))
        for layer_name in PROMPT_LAYER_ORDER
        if layer_name in merged
    ]


def compose_prompt_layers(
    layers: Mapping[str | PromptLayerName, Any]
    | Iterable[PromptLayer | Mapping[str, Any] | Sequence[Any] | None],
    *,
    include_headers: bool = False,
    separator: str = "\n\n",
) -> str:
    """Compose system prompt layers in canonical priority order.

    ``layers`` may be a mapping of layer names to content or an iterable of
    ``PromptLayer`` objects, ``{"name": ..., "content": ...}`` mappings, or
    two-item ``(name, content)`` sequences. Empty content is skipped.
    """
    prompt_layers = sorted_prompt_layers(_iter_prompt_layers(layers))
    if include_headers:
        blocks = [f"{layer.title}:\n{layer.content}" for layer in prompt_layers]
    else:
        blocks = [layer.content for layer in prompt_layers]
    return separator.join(blocks)


def compile_system_prompt(
    layers: Mapping[str | PromptLayerName, Any]
    | Iterable[PromptLayer | Mapping[str, Any] | Sequence[Any] | None],
    *,
    include_headers: bool = False,
    separator: str = "\n\n",
) -> str:
    """Alias for composing system prompt layers."""
    return compose_prompt_layers(
        layers,
        include_headers=include_headers,
        separator=separator,
    )


def _iter_prompt_layers(
    layers: Mapping[str | PromptLayerName, Any]
    | Iterable[PromptLayer | Mapping[str, Any] | Sequence[Any] | None],
) -> Iterable[PromptLayer | None]:
    if isinstance(layers, Mapping):
        for name, content in layers.items():
            yield make_prompt_layer(name, content)
        return

    for layer in layers:
        if layer is None or isinstance(layer, PromptLayer):
            yield layer
            continue

        if isinstance(layer, Mapping):
            name = layer.get("name", layer.get("layer", layer.get("type")))
            if name is None:
                raise ValueError(f"Prompt layer mapping is missing a name: {layer!r}")
            yield make_prompt_layer(str(name), layer.get("content", layer.get("text")))
            continue

        if isinstance(layer, Sequence) and not isinstance(layer, (str, bytes)):
            if len(layer) != 2:
                raise ValueError(f"Prompt layer sequence must have 2 items: {layer!r}")
            name, content = layer
            yield make_prompt_layer(str(name), content)
            continue

        raise TypeError(f"Unsupported prompt layer: {layer!r}")


def _coerce_layer_content(content: Any) -> str:
    if content is None:
        return ""

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, Mapping):
        lines: list[str] = []
        for key in sorted(content):
            value = _coerce_layer_content(content[key])
            if value:
                lines.append(f"{key}: {value}")
        return "\n".join(lines)

    if isinstance(content, Iterable) and not isinstance(content, (str, bytes)):
        parts = [_coerce_layer_content(item) for item in content]
        return "\n".join(part for part in parts if part)

    return str(content).strip()


__all__ = [
    "ASSISTANTISM_SCRUB_DIRECTIVE",
"MEMORY_REALISM_DIRECTIVES",
    "PROMPT_LAYER_ORDER",
    "PROMPT_LAYER_TITLES",
    "PromptLayer",
    "PromptLayerName",
    "build_prompt_layers",
    "compile_system_prompt",
    "compose_assistantism_scrub_directive",
    "compose_memory_realism_directives",
    "compose_prompt_layers",
    "make_prompt_layer",
    "normalize_layer_name",
    "sorted_prompt_layers",
]
