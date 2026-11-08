"""Assemble bounded, evidence-grounded behavioral context.

This module selects personality evidence that may be relevant to a conversation.
It does not choose a response, infer new beliefs, mutate identity, or call a
model. Inputs such as experiences and user preferences must already be scoped
and authorized by their caller.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
import json
import re
from typing import Any


MAX_OUTPUT_CHARS = 6_000
MAX_ITEMS = 6
MAX_ITEM_CHARS = 220
MAX_CONTEXT_CHARS = 1_200
MAX_CORE_TRAITS = 8
MAX_USER_PREFERENCES = 4
MAX_EXPERIENCES = 3

_TOKEN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOP_WORDS = frozenset(
    "a about after all also an and any are as at be because been before being between both "
    "but by can could did do does doing down during each few for from further had has have "
    "he her here hers herself him himself his how i if in into is it its itself just me "
    "more most my myself no nor not of off on once only or other our ours ourselves out over "
    "own same she should so some such than that the their theirs them themselves then there "
    "these they this those through to too under until up very was we were what when where "
    "which while who whom why will with would you your yours yourself yourselves"
    .split()
)

# Context labels identify what part of a situation a configured trait may help
# interpret. They never prescribe behavior or alter the trait's meaning.
_TRAIT_CUES: dict[str, frozenset[str]] = {
    "agreeableness": frozenset("argument argue conflict disagreement criticism hurt upset rude".split()),
    "conscientiousness": frozenset("plan deadline responsibility promise task organize mistake".split()),
    "creativity": frozenset("idea imagine create design story possibility alternative".split()),
    "curiosity": frozenset("question learn explore discover explain why how".split()),
    "extraversion": frozenset("friend family people group party meet social".split()),
    "neuroticism": frozenset("worry worried anxious risk uncertain uncertainty fear".split()),
    "openness": frozenset("new unfamiliar change perspective idea possibility".split()),
}


@dataclass(frozen=True, slots=True)
class BehavioralSignal:
    """One existing personality or self-model item selected for relevance."""

    kind: str
    subject: str
    statement: str
    source: str
    relevance: tuple[str, ...] = ()
    strength: float | None = None
    reason: str | None = None
    opposing_statement: str | None = None
    opposing_reason: str | None = None


@dataclass(frozen=True, slots=True)
class BehavioralContext:
    """Structured context with separate identity, development, mood and user data."""

    configured_personality: Mapping[str, Any]
    relevant_traits: tuple[BehavioralSignal, ...]
    developed_opinions: tuple[BehavioralSignal, ...]
    developed_preferences: tuple[BehavioralSignal, ...]
    developed_interests: tuple[BehavioralSignal, ...]
    relevant_experiences: tuple[Mapping[str, Any], ...]
    tensions: tuple[BehavioralSignal, ...]
    temporary_mood: Mapping[str, Any]
    user_preferences: tuple[Mapping[str, Any] | str, ...]
    relationship: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-ready, deterministic bounded representation."""
        result: dict[str, Any] = {
            "configured_personality": dict(self.configured_personality),
            "relevant_traits": [asdict(item) for item in self.relevant_traits],
            "developed_opinions": [asdict(item) for item in self.developed_opinions],
            "developed_preferences": [asdict(item) for item in self.developed_preferences],
            "developed_interests": [asdict(item) for item in self.developed_interests],
            "relevant_experiences": [dict(item) for item in self.relevant_experiences],
            "tensions": [asdict(item) for item in self.tensions],
            "temporary_mood": dict(self.temporary_mood),
            "user_preferences": [dict(item) if isinstance(item, Mapping) else item for item in self.user_preferences],
            "relationship": dict(self.relationship),
        }
        # All fields have individual bounds. This final guard drops lowest-ranked
        # items deterministically if unusually long caller-provided values remain.
        buckets = (
            "relevant_experiences", "developed_interests", "developed_preferences",
            "developed_opinions", "tensions", "relevant_traits", "user_preferences",
        )
        while len(_json(result)) > MAX_OUTPUT_CHARS:
            bucket = next((name for name in buckets if result[name]), None)
            if bucket is None:
                break
            result[bucket].pop()
        return result

    def to_json(self) -> str:
        """Compact deterministic JSON suitable for a future prompt adapter."""
        return _json(self.as_dict())


def assemble_behavioral_context(
    personality: Any,
    conversation_context: str,
    *,
    self_model: Mapping[str, Any] | None = None,
    experiences: Sequence[Any] = (),
    relationship: Any = None,
    mood: Any = None,
    user_preferences: Sequence[Any] | Mapping[str, Any] | None = None,
) -> BehavioralContext:
    """Select stored behavioral evidence relevant to ``conversation_context``.

    ``self_model`` contributes only items marked ``held``. Configured
    ``favorite_topics`` remain part of configured personality and never become
    developed interests. Tensions are emitted only for a held item with an
    explicit, persisted opposing challenge.
    """
    context_text = _clean(conversation_context)[:MAX_CONTEXT_CHARS]
    context_terms = _terms(context_text)
    profile = _configured_personality(personality)
    traits = _relevant_traits(profile, context_terms)

    opinions: list[BehavioralSignal] = []
    preferences: list[BehavioralSignal] = []
    interests: list[BehavioralSignal] = []
    tensions: list[BehavioralSignal] = []
    raw_items = (self_model or {}).get("items", {})
    if isinstance(raw_items, Mapping):
        for key, raw in raw_items.items():
            if not isinstance(raw, Mapping) or raw.get("status") != "held":
                continue
            kind = raw.get("kind")
            if kind not in {"opinion", "preference", "interest", "self_understanding"}:
                continue
            subject = _clean(raw.get("subject") or str(key).partition(":")[2])[:MAX_ITEM_CHARS]
            statement = _clean(raw.get("statement"))[:MAX_ITEM_CHARS]
            if not statement:
                continue
            relevance = _relevance(context_terms, subject, statement, raw.get("reason"))
            if not relevance:
                continue
            try:
                strength = float(raw.get("strength"))
            except (TypeError, ValueError):
                strength = None
            challenge = raw.get("challenge")
            signal = BehavioralSignal(
                kind=str(kind), subject=subject, statement=statement,
                source="developed_self_model", relevance=relevance,
                strength=strength, reason=_optional_text(raw.get("reason")),
                opposing_statement=(
                    _clean(challenge.get("statement"))[:MAX_ITEM_CHARS]
                    if isinstance(challenge, Mapping) and challenge.get("statement") else None
                ),
                opposing_reason=(
                    _clean(challenge.get("reason"))[:MAX_ITEM_CHARS]
                    if isinstance(challenge, Mapping) and challenge.get("reason") else None
                ),
            )
            if kind == "opinion" or kind == "self_understanding":
                opinions.append(signal)
            elif kind == "preference":
                preferences.append(signal)
            elif kind == "interest":
                interests.append(signal)
            if signal.opposing_statement:
                tensions.append(signal)

    experiences_out = _relevant_records(experiences, context_terms, "experience")
    user_prefs = _relevant_user_preferences(user_preferences, context_terms)
    return BehavioralContext(
        configured_personality=profile,
        relevant_traits=tuple(_sort_signals(traits)[:MAX_ITEMS]),
        developed_opinions=tuple(_sort_signals(opinions)[:MAX_ITEMS]),
        developed_preferences=tuple(_sort_signals(preferences)[:MAX_ITEMS]),
        developed_interests=tuple(_sort_signals(interests)[:MAX_ITEMS]),
        relevant_experiences=tuple(experiences_out[:MAX_EXPERIENCES]),
        tensions=tuple(_sort_signals(tensions)[:MAX_ITEMS]),
        temporary_mood=_small_mapping(mood, ("mood", "energy", "playfulness", "interest", "short_term_tone")),
        user_preferences=tuple(user_prefs[:MAX_USER_PREFERENCES]),
        relationship=_small_mapping(
            relationship,
            ("type", "tone", "directness", "warmth", "initiative_level", "content_level", "depth"),
        ),
    )


def _configured_personality(value: Any) -> dict[str, Any]:
    fields = (
        "name", "description", "backstory", "core_traits", "tone", "verbosity",
        "humor_style", "communication_style", "favorite_topics", "personality_sliders",
    )
    result: dict[str, Any] = {}
    for name in fields:
        raw = _read(value, name)
        if raw is None:
            continue
        if name in {"core_traits", "favorite_topics"}:
            if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
                result[name] = [_clean(item)[:MAX_ITEM_CHARS] for item in raw[:MAX_CORE_TRAITS] if _clean(item)]
        elif name == "personality_sliders" and isinstance(raw, Mapping):
            result[name] = {
                str(key)[:60]: _bounded_scalar(item)
                for key, item in sorted(raw.items(), key=lambda pair: str(pair[0]))[:16]
                if _bounded_scalar(item) is not None
            }
        elif isinstance(raw, (str, int, float, bool)):
            result[name] = _clean(raw)[:360] if isinstance(raw, str) else raw
    return result


def _relevant_traits(profile: Mapping[str, Any], context_terms: set[str]) -> list[BehavioralSignal]:
    output: list[BehavioralSignal] = []
    sliders = profile.get("personality_sliders", {})
    if isinstance(sliders, Mapping):
        for name, value in sliders.items():
            cues = _TRAIT_CUES.get(str(name).casefold())
            matched = sorted(context_terms & cues) if cues else []
            if matched:
                output.append(BehavioralSignal(
                    kind="configured_trait", subject=str(name), statement=f"{name}={value}",
                    source="configured_personality", relevance=tuple(matched),
                ))
    for raw in profile.get("core_traits", ()):
        trait = _clean(raw)
        matched = sorted(context_terms & _terms(trait))
        if matched:
            output.append(BehavioralSignal(
                kind="configured_trait", subject=trait[:MAX_ITEM_CHARS], statement=trait[:MAX_ITEM_CHARS],
                source="configured_personality", relevance=tuple(matched),
            ))
    # The profile stays intact above; this list is only the context-matched view.
    return output


def _relevant_records(values: Sequence[Any], context_terms: set[str], source: str) -> list[dict[str, Any]]:
    records: list[tuple[int, str, dict[str, Any]]] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        record = {}
        for key in ("title", "narrative", "statement", "content", "topics", "significance", "occurred_at", "source_at", "source_message_id", "turn_id", "session_id"):
            raw = value.get(key)
            if isinstance(raw, str):
                record[key] = _clean(raw)[:MAX_ITEM_CHARS]
            elif key == "topics" and isinstance(raw, Sequence):
                record[key] = [_clean(item)[:80] for item in raw[:6] if _clean(item)]
        text = " ".join(str(record.get(k, "")) for k in ("title", "narrative", "statement", "content", "topics"))
        matched = context_terms & _terms(text)
        if matched:
            record["source"] = source
            record["relevance"] = sorted(matched)[:8]
            records.append((-len(matched), _clean(record.get("title") or record.get("narrative") or "").casefold(), record))
    records.sort(key=lambda item: (item[0], item[1], _json(item[2])))
    return [item[2] for item in records]


def _relevant_user_preferences(values: Sequence[Any] | Mapping[str, Any] | None, terms: set[str]) -> list[Mapping[str, Any] | str]:
    if isinstance(values, Mapping):
        candidates: Sequence[Any] = [
            {"preference": key, "value": value} for key, value in sorted(values.items(), key=lambda pair: str(pair[0]))
        ]
    elif isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        candidates = values
    else:
        candidates = ()
    result: list[tuple[int, str, Mapping[str, Any] | str]] = []
    for value in candidates:
        if isinstance(value, str):
            text = _clean(value)[:MAX_ITEM_CHARS]
            matched = terms & _terms(text)
            if matched:
                result.append((-len(matched), text.casefold(), text))
        elif isinstance(value, Mapping):
            record = {str(k)[:60]: _clean(v)[:MAX_ITEM_CHARS] for k, v in sorted(value.items(), key=lambda pair: str(pair[0])) if isinstance(v, (str, int, float, bool))}
            matched = terms & _terms(" ".join(record.values()))
            if matched:
                result.append((-len(matched), _json(record), record))
    result.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in result]


def _relevance(terms: set[str], *values: Any) -> tuple[str, ...]:
    words: set[str] = set()
    for value in values:
        if isinstance(value, str):
            words.update(_terms(value))
    return tuple(sorted(terms & words)[:8])


def _sort_signals(values: Sequence[BehavioralSignal]) -> list[BehavioralSignal]:
    return sorted(values, key=lambda item: (-len(item.relevance), item.kind, item.subject.casefold(), item.statement.casefold(), item.source))


def _small_mapping(value: Any, allowed: Sequence[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in allowed:
        raw = _read(value, name)
        bounded = _bounded_scalar(raw)
        if bounded is not None:
            result[name] = bounded
    return result


def _read(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _bounded_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return _clean(value)[:MAX_ITEM_CHARS]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return None


def _optional_text(value: Any) -> str | None:
    text = _clean(value)
    return text[:MAX_ITEM_CHARS] if text else None


def _terms(value: Any) -> set[str]:
    if not isinstance(value, str):
        return set()
    return {token.casefold() for token in _TOKEN.findall(value) if len(token) > 2 and token.casefold() not in _STOP_WORDS}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "BehavioralContext",
    "BehavioralSignal",
    "MAX_OUTPUT_CHARS",
    "assemble_behavioral_context",
]
