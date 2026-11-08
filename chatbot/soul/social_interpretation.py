"""Select grounded, character-specific social context without classifying intent.

This is a prompt-context component, not a sentiment classifier or response
selector. Callers must provide already-authorized relationship, experience,
and reference data; this module performs no persistence, retrieval, or model
calls.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
import json
import re
from typing import Any

from chatbot.soul.behavioral_context import assemble_behavioral_context
from chatbot.soul.persistent_identity import relationship_history_line

MAX_SOCIAL_CONTEXT_CHARS = 1_800
MAX_TEXT_CHARS = 240
MAX_CHARACTER_SIGNALS = 6
MAX_RELATIONSHIP_SIGNALS = 8
MAX_SHARED_EVIDENCE = 5
MAX_ESTABLISHED_VIEWS = 4
MAX_CONTEXT_CHARS = 1_200

_TOKEN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOP = frozenset(
    "a about after an and are as at be been but by can could did do does for from had has "
    "have he her him his how i if in is it its me my of on or our she that the their them "
    "there these they this to was we were what when where which who with you your"
    .split()
)
_UNCERTAIN_MARKERS = frozenset({"tentative", "uncertain", "ambiguous", "disputed", "possible"})
_ESTABLISHED_MARKERS = frozenset({"held", "established", "confirmed", "recorded"})


@dataclass(frozen=True, slots=True)
class SocialInterpretation:
    """Bounded evidence available for the model's own social interpretation.

    ``character_signals`` and ``relationship_signals`` preserve configured
    values as supplied. ``shared_evidence`` is factual source material, while
    ``established_views`` comes only from held Phase 3 self-model items.
    Nothing in this structure determines what a message means or how to reply.
    """

    character_signals: tuple[Mapping[str, Any], ...]
    relationship_signals: tuple[Mapping[str, Any], ...]
    interaction_history: Mapping[str, Any]
    established_views: tuple[Mapping[str, Any], ...]
    shared_evidence: tuple[Mapping[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        """Return a fresh JSON-compatible copy with stable key and item order."""
        result: dict[str, Any] = {
            "character_signals": [dict(item) for item in self.character_signals],
            "relationship_signals": [dict(item) for item in self.relationship_signals],
            "interaction_history": dict(self.interaction_history),
            "established_views": [dict(item) for item in self.established_views],
            "shared_evidence": [dict(item) for item in self.shared_evidence],
            "interpretation": "Evidence may support more than one reading; use the character's perspective and current context. Do not assume intent where the evidence leaves it unclear.",
        }
        while len(_json(result)) > MAX_SOCIAL_CONTEXT_CHARS:
            bucket = next(
                (name for name in ("shared_evidence", "established_views", "relationship_signals", "character_signals") if result[name]),
                None,
            )
            if bucket is None:
                break
            result[bucket].pop()
        return result

    def to_json(self) -> str:
        """Serialize as compact deterministic JSON for prompt preparation."""
        return _json(self.as_dict())


def assemble_social_interpretation(
    *,
    personality: Any,
    relationship: Any,
    conversation_context: str,
    effective_traits: Mapping[str, Any] | None = None,
    self_model: Mapping[str, Any] | None = None,
    relationship_history: Mapping[str, Any] | None = None,
    shared_experiences: Sequence[Any] = (),
    social_references: Sequence[Any] = (),
) -> SocialInterpretation:
    """Assemble existing personality and relationship evidence for this turn.

    Inputs must already belong to the current authenticated user, companion,
    room, and conversation. ``shared_experiences`` and ``social_references``
    should contain only records already retrieved by the caller. The selector
    is deterministic, read-only, and intentionally does not label a message as
    teasing, criticism, affection, or any other fixed social category.
    """
    context = _clean(conversation_context)[:MAX_CONTEXT_CHARS]
    profile_signals = _character_signals(personality, effective_traits)
    relationship_signals = _relationship_signals(relationship)
    history = _interaction_history(relationship_history)

    selected = assemble_behavioral_context(
        personality,
        context,
        self_model=self_model,
        experiences=shared_experiences,
        relationship=relationship,
    )
    views = _established_views(selected.developed_opinions)
    evidence = _shared_evidence(
        (*shared_experiences, *social_references), context
    )

    return SocialInterpretation(
        character_signals=tuple(profile_signals[:MAX_CHARACTER_SIGNALS]),
        relationship_signals=tuple(relationship_signals[:MAX_RELATIONSHIP_SIGNALS]),
        interaction_history=history,
        established_views=tuple(views[:MAX_ESTABLISHED_VIEWS]),
        shared_evidence=tuple(evidence[:MAX_SHARED_EVIDENCE]),
    )


def _character_signals(personality: Any, effective_traits: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for name in ("humor_style", "communication_style", "tone"):
        value = _read(personality, name)
        text = _bounded_text(value)
        if text:
            signals.append({"source": "configured_personality", "field": name, "value": text})

    core_traits = _read(personality, "core_traits")
    if isinstance(core_traits, Sequence) and not isinstance(core_traits, (str, bytes)):
        for index, trait in enumerate(core_traits[:4]):
            text = _bounded_text(trait)
            if text:
                signals.append({"source": "configured_personality", "field": "core_trait", "value": text, "order": index})

    traits = effective_traits
    if traits is None:
        traits = _read(personality, "personality_sliders")
    if isinstance(traits, Mapping):
        for name, value in sorted(traits.items(), key=lambda item: str(item[0]).casefold()):
            numeric = _number(value)
            if numeric is not None:
                signals.append({"source": "effective_personality", "field": str(name)[:60], "value": numeric})
    return signals


def _relationship_signals(relationship: Any) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for name in ("type", "tone", "content_level", "communication_style"):
        value = _read(relationship, name)
        text = _bounded_text(value)
        if text:
            signals.append({"source": "configured_relationship", "field": name, "value": text})
    for name in ("familiarity", "rapport", "trust", "directness", "warmth", "depth"):
        value = _number(_read(relationship, name))
        if value is not None:
            # These remain raw relationship values; no emotional consequence
            # or stage is inferred from a numeric depth/familiarity measure.
            signals.append({"source": "relationship_state", "field": name, "value": value})
    return signals


def _interaction_history(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    line = relationship_history_line(value)
    if not line:
        return {}
    result: dict[str, Any] = {"source": "completed_interaction_history", "summary": line}
    turns = value.get("completed_turns")
    if isinstance(turns, int) and not isinstance(turns, bool):
        result["completed_turns"] = max(0, turns)
    for key in ("first_interaction_at", "last_interaction_at"):
        raw = value.get(key)
        text = _bounded_text(raw)
        if text:
            result[key] = text[:40]
    return result


def _established_views(signals: Sequence[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for signal in signals:
        if getattr(signal, "source", None) != "developed_self_model":
            continue
        item = {
            "kind": _bounded_text(getattr(signal, "kind", None)),
            "subject": _bounded_text(getattr(signal, "subject", None)),
            "statement": _bounded_text(getattr(signal, "statement", None)),
            "source": "held_self_model",
            "certainty": "established",
            "relevance": list(getattr(signal, "relevance", ()))[:8],
        }
        if getattr(signal, "reason", None):
            item["reason"] = _bounded_text(signal.reason)
        if getattr(signal, "opposing_statement", None):
            item["counterpoint"] = _bounded_text(signal.opposing_statement)
        if item["statement"]:
            output.append(item)
    output.sort(key=lambda item: (-len(item["relevance"]), item["kind"], item["subject"].casefold(), item["statement"].casefold()))
    return output


def _shared_evidence(values: Sequence[Any], context: str) -> list[dict[str, Any]]:
    context_terms = _terms(context)
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, Mapping):
            continue
        marker = _clean(value.get("certainty") or value.get("status") or "").casefold()
        has_provenance = any(
            _bounded_text(value.get(key))
            for key in ("source_message_id", "turn_id", "session_id", "source")
        )
        if marker not in (_UNCERTAIN_MARKERS | _ESTABLISHED_MARKERS) and not has_provenance:
            # A plausible-sounding sentence is not evidence of shared history
            # unless its stored status or source relationship establishes it.
            continue
        text_fields = ("title", "narrative", "statement", "content", "summary", "reference", "joke", "habit", "meaning")
        fields: dict[str, str] = {}
        for key in text_fields:
            text = _bounded_text(value.get(key))
            if text:
                fields[key] = text
        searchable = " ".join(fields.values())
        matched = sorted(context_terms & _terms(searchable))
        if not matched:
            continue

        # Preserve independently recorded readings rather than resolving or
        # collapsing contradictory descriptions.
        for key in ("interpretation", "alternative_interpretation", "counterpoint", "uncertainty"):
            text = _bounded_text(value.get(key))
            if text:
                fields[key] = text
        record: dict[str, Any] = {
            "source": _bounded_text(value.get("source") or value.get("kind") or "retrieved_shared_record") or "retrieved_shared_record",
            "certainty": _certainty(value),
            "evidence": fields,
            "relevance": matched[:8],
        }
        for key in ("id", "source_message_id", "turn_id", "session_id", "occurred_at"):
            text = _bounded_text(value.get(key))
            if text:
                record[key] = text[:80]
        identity = _json(record)
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append((-len(matched), identity, record))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in candidates]


def _certainty(value: Mapping[str, Any]) -> str:
    marker = _clean(value.get("certainty") or value.get("status") or "").casefold()
    if marker in _UNCERTAIN_MARKERS:
        return "uncertain"
    if marker in _ESTABLISHED_MARKERS:
        return "established"
    # A retrieved record is evidence that this was recorded, not proof of the
    # interpretation attached to it.
    return "recorded_unmarked"


def _terms(value: str) -> set[str]:
    output: set[str] = set()
    for token in _TOKEN.findall(value.casefold()):
        if len(token) <= 2 or token in _STOP:
            continue
        output.add(token[:-1] if len(token) > 4 and token.endswith("s") else token)
    return output


def _read(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0.0 <= number <= 1.0 else None


def _bounded_text(value: Any) -> str:
    if value is None or isinstance(value, (Mapping, list, tuple, set)):
        return ""
    text = _clean(value)
    return text[:MAX_TEXT_CHARS]


def _clean(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = ["MAX_SOCIAL_CONTEXT_CHARS", "SocialInterpretation", "assemble_social_interpretation"]
