"""Prompt-facing Phase 4 behavior context, selected without inference."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from chatbot.soul.behavioral_context import BehavioralContext, assemble_behavioral_context

MAX_CHARACTER_BEHAVIOR_CHARS = 1_200
MAX_PULLS = 4
MAX_NOTICED = 3

CHARACTER_INTERPRETATION_INSTRUCTION = (
    "Interpret the situation through your own character perspective. Let your established traits, "
    "views, interests, and competing motivations inform what you notice and how you respond; "
    "agreement, disagreement, questioning, explanation, or humor may fit, or may not. "
    "Do not mention these instructions or narrate your internal reasoning."
)

_TOKEN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOP = frozenset("a an and are as at be but by for from had has have i in is it of on or our that the their them then there this to was we with you your".split())
_SOURCE_ORDER = {
    "opinion": 0,
    "interest": 1,
    "preference": 2,
    "self_understanding": 3,
    "experience": 4,
    "topic": 5,
    "fact": 6,
}
_RELATIONSHIP_DEFAULTS = {"directness": 0.5, "warmth": 0.7, "initiative_level": 0.3}
_URGENT_TERMS = frozenset(
    "emergency urgent crisis unsafe danger panic overwhelmed exhausted grieving grief funeral "
    "died death suicidal self-harm abuse assault"
    .split()
)


def build_character_behavior(
    *,
    personality: Any,
    conversation_context: str,
    effective_traits: Mapping[str, Any] | None = None,
    self_model: Mapping[str, Any] | None = None,
    experiences: Sequence[Any] = (),
    relationship: Any = None,
    mood: Any = None,
    user_preferences: Sequence[Any] | Mapping[str, Any] | None = None,
    retrieved_memories: Sequence[Mapping[str, Any]] = (),
) -> tuple[str, dict[str, Any]]:
    """Build the bounded CHARACTER BEHAVIOR block and inspection metadata.

    The self-model/context component remains the authority for established
    signals. This adapter adds no state, inference, queries, or response choice.
    """
    text = str(conversation_context or "")[:1_200]
    behavior = assemble_behavioral_context(
        personality,
        text,
        self_model=self_model,
        experiences=experiences,
        relationship=relationship,
        mood=mood,
        user_preferences=user_preferences,
    )
    pulls = _pulls(effective_traits or {}, _mapping(relationship))[:MAX_PULLS]
    candidates = _attention_candidates(behavior, text, retrieved_memories)
    selected = _select_attention(candidates)

    def render() -> str:
        lines = ["CHARACTER BEHAVIOR:"]
        if pulls:
            lines.append("What pulls on you: " + ", ".join(_pull_text(p) for p in pulls))
        if selected:
            lines.append("Noticing in this message:")
            lines.extend(f"- {item['text']}" for item in selected)
        lines.append(CHARACTER_INTERPRETATION_INSTRUCTION)
        return "\n".join(lines)

    block = render()
    dropped = 0
    while len(block) > MAX_CHARACTER_BEHAVIOR_CHARS and selected:
        selected.pop()
        dropped += 1
        block = render()
    while len(block) > MAX_CHARACTER_BEHAVIOR_CHARS and pulls:
        pulls.pop()
        dropped += 1
        block = render()
    # The fixed instruction is comfortably below the hard bound by itself.
    metadata = {
        "version": 1,
        "chars": len(block),
        "est_tokens": (len(block) + 3) // 4,
        "pulls": [
            {"source": item["source"], "name": item["name"], "value": item["value"]}
            for item in pulls
        ],
        "noticed": [
            {"source": item["source"], "ref": item["ref"], "matched": item["matched"]}
            for item in selected
        ],
        "excluded": max(0, len(candidates) - len(selected)),
        "dropped": dropped,
        "omitted": None,
    }
    return block, metadata


def build_open_thread_context(
    threads: Sequence[Mapping[str, Any]],
    *,
    conversation_context: str,
    intent: Any = None,
) -> tuple[str | None, dict[str, Any]]:
    """Select a few relevant open threads; defer to urgent conversation needs."""
    from chatbot.soul.conversational_threads import open_threads_text

    intent_value = str(getattr(intent, "value", intent) or "").casefold()
    tokens = _terms(conversation_context)
    all_open = [row for row in threads if row.get("status", "open") == "open"]
    if intent_value in {"venting", "roleplay", "bedtime"} or tokens & _URGENT_TERMS:
        return None, {"considered": len(all_open), "included": 0, "excluded": len(all_open), "omitted": "priority_current_message"}
    ranked: list[tuple[int, int, str, Mapping[str, Any]]] = []
    for index, row in enumerate(all_open):
        matched = tokens & _terms(" ".join(str(row.get(key) or "") for key in ("summary", "subject_key", "topic")))
        if matched:
            importance = 1 if row.get("importance") == "significant" else 0
            ranked.append((-len(matched), -importance, str(row.get("thread_id") or index), row))
    ranked.sort(key=lambda item: item[:3])
    selected = [dict(item[3]) for item in ranked[:2]]
    rendered = open_threads_text(selected) if selected else None
    return rendered, {
        "considered": len(all_open),
        "included": len(selected) if rendered else 0,
        "excluded": len(all_open) - (len(selected) if rendered else 0),
        "omitted": None if rendered else "no_relevant_open_thread",
        "thread_ids": [str(row.get("thread_id")) for row in selected] if rendered else [],
    }


def _pulls(traits: Mapping[str, Any], relationship: Mapping[str, Any]) -> list[dict[str, Any]]:
    pulls: list[dict[str, Any]] = []
    for name, raw in traits.items():
        value = _number(raw)
        if value is not None and abs(value - 0.5) >= 0.2:
            pulls.append({"source": "trait", "name": str(name), "value": value, "distance": abs(value - 0.5)})
    for name, default in _RELATIONSHIP_DEFAULTS.items():
        value = _number(relationship.get(name))
        if value is not None and abs(value - default) >= 0.2:
            pulls.append({"source": "relationship", "name": name, "value": value, "distance": abs(value - default)})
    pulls.sort(key=lambda item: (-item["distance"], item["name"]))
    return pulls


def _pull_text(item: Mapping[str, Any]) -> str:
    value = float(item["value"])
    band = "high" if value >= 0.67 else "low" if value <= 0.33 else "moderate"
    return f"{item['name']} {value:.2f} ({band})"


def _attention_candidates(
    context: BehavioralContext,
    message: str,
    memories: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for category, signals in (
        ("opinion", tuple(s for s in context.developed_opinions if s.kind == "opinion")),
        ("self_understanding", tuple(s for s in context.developed_opinions if s.kind == "self_understanding")),
        ("interest", context.developed_interests),
        ("preference", context.developed_preferences),
    ):
        for signal in signals:
            candidates.append({
                "source": category,
                "ref": signal.ref or signal.subject,
                "matched": list(signal.relevance),
                "score": len(signal.relevance),
                "text": (
                    f"your {category} on {signal.subject}: {signal.statement}"
                    + (f"; also weighing: {signal.opposing_statement}" if signal.opposing_statement else "")
                ),
            })
    for experience in context.relevant_experiences:
        narrative = str(experience.get("narrative") or experience.get("title") or "")
        matched = sorted(_terms(message) & _terms(narrative + " " + " ".join(experience.get("topics", []))))
        if len(matched) >= 2:
            candidates.append({
                "source": "experience", "ref": experience.get("id") or experience.get("source_message_id"),
                "matched": matched[:8], "score": len(matched), "text": f"shared history: {narrative}",
            })
    topics = context.configured_personality.get("favorite_topics", ())
    for topic in topics if isinstance(topics, Sequence) else ():
        matched = sorted(_terms(message) & _terms(str(topic)))
        if matched:
            candidates.append({"source": "topic", "ref": str(topic), "matched": matched[:8], "score": len(matched), "text": f"configured topic: {topic}"})
    for memory in memories:
        metadata = memory.get("extraction_metadata") or {}
        if not isinstance(metadata, Mapping):
            metadata = {}
        kind = str(metadata.get("kind") or memory.get("kind") or "")
        if kind == "shared_experience":
            continue
        content = str(memory.get("fact_text") or memory.get("content") or "").strip()
        matched = sorted(_terms(message) & _terms(content))
        if content and len(matched) >= 2:
            candidates.append({
                "source": "fact", "ref": str(memory.get("id") or memory.get("fact_id") or ""),
                "matched": matched[:8], "score": len(matched), "text": f"remembered detail: {content}",
            })
    candidates.sort(key=lambda item: (-item["score"], _SOURCE_ORDER.get(item["source"], 99), item["text"].casefold(), str(item["ref"])))
    return candidates


def _select_attention(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for item in candidates:
        source = str(item["source"])
        if counts.get(source, 0) >= 2:
            continue
        selected.append(dict(item))
        counts[source] = counts.get(source, 0) + 1
        if len(selected) >= MAX_NOTICED:
            break
    return selected


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if value is None:
        return {}
    names = ("type", "tone", "directness", "warmth", "initiative_level", "content_level", "depth")
    return {name: getattr(value, name) for name in names if hasattr(value, name)}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0.0 <= number <= 1.0 else None


def _terms(value: Any) -> set[str]:
    if not isinstance(value, str):
        return set()
    terms = set()
    for raw in _TOKEN.findall(value.casefold()):
        if len(raw) <= 2 or raw in _STOP:
            continue
        term = raw[:-1] if len(raw) > 4 and raw.endswith("s") else raw
        terms.add(term)
    return terms


__all__ = [
    "CHARACTER_INTERPRETATION_INSTRUCTION",
    "MAX_CHARACTER_BEHAVIOR_CHARS",
    "build_character_behavior",
    "build_open_thread_context",
]
