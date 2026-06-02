from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, timezone
from typing import Any, Protocol

from chatbot.providers import Provider, ProviderConfig

try:
    from chatbot.memory.models import MemoryEntry
except ModuleNotFoundError:

    class MemoryEntry(Protocol):
        content: str


_PROMPT_VERSION = "contradiction-resolver-v1"
_SYSTEM_PROMPT = (
    "You are a deterministic memory contradiction resolver. Return only JSON. "
    "Use only the supplied entries. Do not invent facts."
)

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "the",
    "to",
    "user",
    "was",
    "were",
}

_NEGATIVE_PREFERENCE_RE = re.compile(
    r"\b(?:i|user)\s+"
    r"(?:(?:do not|don't|does not|doesn't)\s+(?:like|prefer|want)|"
    r"dislike|dislikes|hate|hates|no longer\s+(?:like|likes|prefer|prefers))\s+"
    r"(?P<object>[^.!?;]+)",
    re.IGNORECASE,
)
_POSITIVE_PREFERENCE_RE = re.compile(
    r"\b(?:i|user)\s+(?:like|likes|love|loves|prefer|prefers|want|wants)\s+"
    r"(?P<object>[^.!?;]+)",
    re.IGNORECASE,
)
_PREFER_OVER_RE = re.compile(
    r"\b(?:i|user)\s+(?:prefer|prefers|like|likes)\s+"
    r"(?P<preferred>[^.!?;]+?)\s+over\s+(?P<other>[^.!?;]+)",
    re.IGNORECASE,
)
_BEFORE_RE = re.compile(
    r"(?P<first>[^.!?;]+?)\s+(?:happened\s+|occurred\s+|was\s+|is\s+)?before\s+"
    r"(?P<second>[^.!?;]+)",
    re.IGNORECASE,
)
_AFTER_RE = re.compile(
    r"(?P<first>[^.!?;]+?)\s+(?:happened\s+|occurred\s+|was\s+|is\s+)?after\s+"
    r"(?P<second>[^.!?;]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ContradictionResult:
    has_contradiction: bool
    resolution: str | None
    confidence: float
    contradictory_facts: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _NormalizedEntry:
    content: str
    role: str
    metadata: dict[str, Any]
    subject: str | None
    value: str | None
    confidence: float | None
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class _Preference:
    target: str
    polarity: int


@dataclass(frozen=True, slots=True)
class _PreferenceOrder:
    preferred: str
    other: str


@dataclass(frozen=True, slots=True)
class _TemporalOrder:
    first: str
    second: str


class ContradictionResolver:
    """Detect and resolve contradictions between two memory entries."""

    def __init__(
        self,
        provider: Provider | None = None,
        config: ProviderConfig | None = None,
    ) -> None:
        self.provider = provider
        self.config = config or ProviderConfig(
            model="",
            temperature=0.0,
            max_tokens=256,
        )
        self._cache: dict[str, ContradictionResult] = {}
        self._cache_lock = asyncio.Lock()

    async def resolve(
        self,
        entry_a: MemoryEntry | Mapping[str, Any] | None,
        entry_b: MemoryEntry | Mapping[str, Any] | None,
    ) -> ContradictionResult:
        normalized_a = _normalize_entry(entry_a)
        normalized_b = _normalize_entry(entry_b)
        cache_key = _cache_key(normalized_a, normalized_b)

        async with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        result = await self._resolve_uncached(normalized_a, normalized_b)

        async with self._cache_lock:
            self._cache[cache_key] = result
        return result

    async def _resolve_uncached(
        self,
        entry_a: _NormalizedEntry | None,
        entry_b: _NormalizedEntry | None,
    ) -> ContradictionResult:
        if entry_a is None or entry_b is None:
            return _no_contradiction()
        if not entry_a.content or not entry_b.content:
            return _no_contradiction()
        if _canonical_entry(entry_a) == _canonical_entry(entry_b):
            return _no_contradiction()

        rule_result = _strict_compare(entry_a, entry_b)
        if rule_result is not None:
            return rule_result

        if self.provider is None or not _is_ambiguous_pair(entry_a, entry_b):
            return _no_contradiction()

        return await self._resolve_with_provider(entry_a, entry_b)

    async def _resolve_with_provider(
        self,
        entry_a: _NormalizedEntry,
        entry_b: _NormalizedEntry,
    ) -> ContradictionResult:
        payload = {
            "prompt_version": _PROMPT_VERSION,
            "entry_a": _entry_payload(entry_a),
            "entry_b": _entry_payload(entry_b),
            "schema": {
                "has_contradiction": "boolean",
                "resolution": "string or null",
                "confidence": "number from 0 to 1",
                "contradictory_facts": "array of conflicting statement strings",
            },
        }
        prompt = (
            "Decide whether these two memory entries contradict each other.\n"
            "Return a single JSON object matching the schema. If there is no contradiction, "
            "use has_contradiction=false, resolution=null, confidence between 0 and 1, "
            "and contradictory_facts=[].\n\n"
            f"Input JSON:\n{_canonical_json(payload)}"
        )
        response = await self.provider.complete(
            prompt,
            [{"role": "system", "content": _SYSTEM_PROMPT}],
            self.config,
        )
        return _parse_provider_result(response.text, entry_a, entry_b)


def _strict_compare(
    entry_a: _NormalizedEntry,
    entry_b: _NormalizedEntry,
) -> ContradictionResult | None:
    subject_result = _compare_subject_values(entry_a, entry_b)
    if subject_result is not None:
        return subject_result

    temporal_result = _compare_temporal_order(entry_a, entry_b)
    if temporal_result is not None:
        return temporal_result

    preference_result = _compare_preferences(entry_a, entry_b)
    if preference_result is not None:
        return preference_result

    return None


def _compare_subject_values(
    entry_a: _NormalizedEntry,
    entry_b: _NormalizedEntry,
) -> ContradictionResult | None:
    if not entry_a.subject or not entry_b.subject:
        return None
    if not entry_a.value or not entry_b.value:
        return None
    if entry_a.subject != entry_b.subject or entry_a.value == entry_b.value:
        return None

    return _contradiction(
        entry_a,
        entry_b,
        resolution=_choose_resolution(entry_a, entry_b),
        confidence=0.95,
    )


def _compare_temporal_order(
    entry_a: _NormalizedEntry,
    entry_b: _NormalizedEntry,
) -> ContradictionResult | None:
    orders_a = _temporal_orders(entry_a)
    orders_b = _temporal_orders(entry_b)

    for order_a in orders_a:
        for order_b in orders_b:
            if order_a.first == order_b.second and order_a.second == order_b.first:
                return _contradiction(
                    entry_a,
                    entry_b,
                    resolution=_choose_resolution(entry_a, entry_b),
                    confidence=0.95,
                )

    timestamp_result = _compare_temporal_claims_to_timestamps(entry_a, entry_b, orders_a)
    if timestamp_result is not None:
        return timestamp_result

    return _compare_temporal_claims_to_timestamps(entry_b, entry_a, orders_b)


def _compare_temporal_claims_to_timestamps(
    claimant: _NormalizedEntry,
    other: _NormalizedEntry,
    orders: list[_TemporalOrder],
) -> ContradictionResult | None:
    if claimant.created_at is None or other.created_at is None:
        return None
    if claimant.subject is None or other.subject is None:
        return None

    for order in orders:
        claimant_is_first = claimant.subject == order.first and other.subject == order.second
        claimant_is_second = claimant.subject == order.second and other.subject == order.first

        if claimant_is_first and claimant.created_at > other.created_at:
            return _contradiction(
                claimant,
                other,
                resolution=_choose_resolution(claimant, other),
                confidence=0.95,
            )
        if claimant_is_second and other.created_at > claimant.created_at:
            return _contradiction(
                claimant,
                other,
                resolution=_choose_resolution(claimant, other),
                confidence=0.95,
            )

    return None


def _compare_preferences(
    entry_a: _NormalizedEntry,
    entry_b: _NormalizedEntry,
) -> ContradictionResult | None:
    for order_a in _preference_orders(entry_a.content):
        for order_b in _preference_orders(entry_b.content):
            if order_a.preferred == order_b.other and order_a.other == order_b.preferred:
                return _contradiction(
                    entry_a,
                    entry_b,
                    resolution=_choose_resolution(entry_a, entry_b),
                    confidence=0.9,
                )

    preferences_a = _preferences(entry_a.content)
    preferences_b = _preferences(entry_b.content)
    for preference_a in preferences_a:
        for preference_b in preferences_b:
            same_target = preference_a.target == preference_b.target
            reversed_polarity = preference_a.polarity != preference_b.polarity
            if same_target and reversed_polarity:
                return _contradiction(
                    entry_a,
                    entry_b,
                    resolution=_choose_resolution(entry_a, entry_b),
                    confidence=0.9,
                )

    return None


def _normalize_entry(entry: MemoryEntry | Mapping[str, Any] | None) -> _NormalizedEntry | None:
    if entry is None:
        return None

    source = _entry_mapping(entry)
    content = _first_text(
        source,
        entry,
        ("content", "fact_text", "text", "summary", "value"),
    )
    metadata = _normalized_metadata(source.get("metadata", getattr(entry, "metadata", None)))
    for key in ("metadata_json", "extra"):
        nested = source.get(key, getattr(entry, key, None))
        if isinstance(nested, Mapping):
            metadata.update(_normalized_metadata(nested))

    role = _text_value(source.get("role", getattr(entry, "role", "")))
    confidence = _confidence(source, entry, metadata)
    created_at = _datetime_value(
        source.get("created_at", getattr(entry, "created_at", metadata.get("created_at", None)))
    )

    subject, value = _subject_value(source, entry, metadata, content)
    return _NormalizedEntry(
        content=content.strip(),
        role=role.strip(),
        metadata=metadata,
        subject=subject,
        value=value,
        confidence=confidence,
        created_at=created_at,
    )


def _entry_mapping(entry: MemoryEntry | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(entry, Mapping):
        return entry

    as_message = getattr(entry, "as_message", None)
    if callable(as_message):
        mapped = as_message()
        if isinstance(mapped, Mapping):
            return mapped

    if is_dataclass(entry):
        mapped = asdict(entry)
        if isinstance(mapped, Mapping):
            return mapped

    return {}


def _subject_value(
    source: Mapping[str, Any],
    entry: MemoryEntry | Mapping[str, Any],
    metadata: Mapping[str, Any],
    content: str,
) -> tuple[str | None, str | None]:
    subject = _first_text(
        source,
        entry,
        ("subject", "entity", "key", "name", "event"),
        metadata,
    )
    predicate = _first_text(
        source,
        entry,
        ("predicate", "property", "attribute", "field"),
        metadata,
    )
    value = _first_text(source, entry, ("value", "object"), metadata)

    if subject and predicate:
        subject = f"{subject} {predicate}"
    if subject and value:
        return _normalize_phrase(subject), _normalize_phrase(value)

    parsed = _parse_subject_value_content(content)
    if parsed is not None:
        return parsed

    return (_normalize_phrase(subject) if subject else None, None)


def _parse_subject_value_content(content: str) -> tuple[str, str] | None:
    compact = content.strip()
    if not compact:
        return None

    patterns = (
        r"^(?P<subject>[^.!?;:=]{1,120}?)\s*(?:=|:)\s*(?P<value>[^.!?;]+)$",
        r"^(?P<subject>[^.!?;]{1,120}?)\s+(?:is|are|was|were)\s+(?P<value>[^.!?;]+)$",
        r"^(?P<subject>[^.!?;]{1,80}?)'s\s+(?P<property>[^.!?;]{1,80}?)\s+"
        r"(?:is|are|was|were)\s+(?P<value>[^.!?;]+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, compact, re.IGNORECASE)
        if not match:
            continue
        groupdict = match.groupdict()
        subject = groupdict["subject"]
        if groupdict.get("property"):
            subject = f"{subject} {groupdict['property']}"
        return _normalize_phrase(subject), _normalize_phrase(groupdict["value"])

    return None


def _temporal_orders(entry: _NormalizedEntry) -> list[_TemporalOrder]:
    orders: list[_TemporalOrder] = []
    if entry.subject:
        for key in ("before", "precedes"):
            target = _metadata_phrase(entry.metadata.get(key))
            if target:
                orders.append(_TemporalOrder(entry.subject, target))
        for key in ("after", "follows"):
            target = _metadata_phrase(entry.metadata.get(key))
            if target:
                orders.append(_TemporalOrder(target, entry.subject))

    for match in _BEFORE_RE.finditer(entry.content):
        orders.append(
            _TemporalOrder(
                _normalize_phrase(match.group("first")),
                _normalize_phrase(match.group("second")),
            )
        )
    for match in _AFTER_RE.finditer(entry.content):
        orders.append(
            _TemporalOrder(
                _normalize_phrase(match.group("second")),
                _normalize_phrase(match.group("first")),
            )
        )

    return _unique_temporal_orders(orders)


def _preferences(content: str) -> list[_Preference]:
    preferences = [
        _Preference(_normalize_preference(match.group("object")), -1)
        for match in _NEGATIVE_PREFERENCE_RE.finditer(content)
    ]
    negative_targets = {preference.target for preference in preferences}

    for match in _POSITIVE_PREFERENCE_RE.finditer(content):
        target = _normalize_preference(match.group("object"))
        if target and target not in negative_targets:
            preferences.append(_Preference(target, 1))

    return [preference for preference in preferences if preference.target]


def _preference_orders(content: str) -> list[_PreferenceOrder]:
    orders = []
    for match in _PREFER_OVER_RE.finditer(content):
        orders.append(
            _PreferenceOrder(
                _normalize_preference(match.group("preferred")),
                _normalize_preference(match.group("other")),
            )
        )
    return [order for order in orders if order.preferred and order.other]


def _is_ambiguous_pair(entry_a: _NormalizedEntry, entry_b: _NormalizedEntry) -> bool:
    if entry_a.subject and entry_b.subject and entry_a.subject == entry_b.subject:
        return True

    tokens_a = _tokens(entry_a.content)
    tokens_b = _tokens(entry_b.content)
    if not tokens_a or not tokens_b:
        return False

    shared = tokens_a & tokens_b
    if len(shared) < 2:
        return False

    overlap = len(shared) / min(len(tokens_a), len(tokens_b))
    return overlap >= 0.4


def _parse_provider_result(
    text: str,
    entry_a: _NormalizedEntry,
    entry_b: _NormalizedEntry,
) -> ContradictionResult:
    raw = _json_object(text)
    if not isinstance(raw, Mapping):
        return _no_contradiction(confidence=0.0)

    has_contradiction = bool(raw.get("has_contradiction"))
    confidence = _clamp_confidence(raw.get("confidence"), default=0.5)
    if not has_contradiction:
        return _no_contradiction(confidence=confidence)

    resolution = raw.get("resolution")
    facts = raw.get("contradictory_facts")
    contradictory_facts = [
        _text_value(fact).strip()
        for fact in facts
        if _text_value(fact).strip()
    ] if isinstance(facts, Sequence) and not isinstance(facts, (str, bytes)) else []

    if not contradictory_facts:
        contradictory_facts = _facts(entry_a, entry_b)

    return ContradictionResult(
        has_contradiction=True,
        resolution=_text_value(resolution).strip() or _choose_resolution(entry_a, entry_b),
        confidence=confidence,
        contradictory_facts=sorted(dict.fromkeys(contradictory_facts)),
    )


def _json_object(text: str) -> Mapping[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match is None:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    return value if isinstance(value, Mapping) else None


def _contradiction(
    entry_a: _NormalizedEntry,
    entry_b: _NormalizedEntry,
    *,
    resolution: str | None,
    confidence: float,
) -> ContradictionResult:
    return ContradictionResult(
        has_contradiction=True,
        resolution=resolution,
        confidence=_clamp_confidence(confidence),
        contradictory_facts=_facts(entry_a, entry_b),
    )


def _no_contradiction(confidence: float = 1.0) -> ContradictionResult:
    return ContradictionResult(
        has_contradiction=False,
        resolution=None,
        confidence=_clamp_confidence(confidence),
        contradictory_facts=[],
    )


def _choose_resolution(entry_a: _NormalizedEntry, entry_b: _NormalizedEntry) -> str | None:
    winner = _choose_winner(entry_a, entry_b)
    if not winner.content:
        return None
    return winner.content


def _choose_winner(entry_a: _NormalizedEntry, entry_b: _NormalizedEntry) -> _NormalizedEntry:
    confidence_a = entry_a.confidence if entry_a.confidence is not None else -1.0
    confidence_b = entry_b.confidence if entry_b.confidence is not None else -1.0
    if confidence_a != confidence_b:
        return entry_a if confidence_a > confidence_b else entry_b

    if entry_a.created_at is not None and entry_b.created_at is not None:
        if entry_a.created_at != entry_b.created_at:
            return entry_a if entry_a.created_at > entry_b.created_at else entry_b
    elif entry_a.created_at is not None:
        return entry_a
    elif entry_b.created_at is not None:
        return entry_b

    return entry_a if _canonical_entry(entry_a) >= _canonical_entry(entry_b) else entry_b


def _facts(entry_a: _NormalizedEntry, entry_b: _NormalizedEntry) -> list[str]:
    return sorted(dict.fromkeys([entry_a.content, entry_b.content]))


def _first_text(
    source: Mapping[str, Any],
    entry: MemoryEntry | Mapping[str, Any],
    keys: tuple[str, ...],
    fallback: Mapping[str, Any] | None = None,
) -> str:
    for values in (source, fallback or {}):
        for key in keys:
            if key in values and values[key] is not None:
                return _text_value(values[key])
    for key in keys:
        value = getattr(entry, key, None)
        if value is not None:
            return _text_value(value)
    return ""


def _normalized_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): _jsonable(item) for key, item in value.items()}


def _confidence(
    source: Mapping[str, Any],
    entry: MemoryEntry | Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> float | None:
    for value in (
        source.get("confidence"),
        getattr(entry, "confidence", None),
        metadata.get("confidence"),
    ):
        if value is None:
            continue
        return _clamp_confidence(value)
    return None


def _datetime_value(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _normalize_datetime(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            return _normalize_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _metadata_phrase(value: Any) -> str | None:
    if value is None:
        return None
    text = _normalize_phrase(_text_value(value))
    return text or None


def _normalize_phrase(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"^[\"'`]+|[\"'`]+$", "", text)
    text = re.sub(r"\b(?:the|a|an|my|user's|users)\b", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .,!?:;")


def _normalize_preference(value: str) -> str:
    text = _normalize_phrase(value)
    text = re.sub(r"\s+over\s+.*$", "", text)
    text = re.sub(r"\b(?:anymore|now|today|please)\b", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]*", text.lower())
        if token not in _STOPWORDS and len(token) > 1
    }


def _unique_temporal_orders(orders: list[_TemporalOrder]) -> list[_TemporalOrder]:
    unique: dict[tuple[str, str], _TemporalOrder] = {}
    for order in orders:
        if order.first and order.second and order.first != order.second:
            unique.setdefault((order.first, order.second), order)
    return list(unique.values())


def _entry_payload(entry: _NormalizedEntry) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "content": entry.content,
        "role": entry.role,
        "metadata": entry.metadata,
        "subject": entry.subject,
        "value": entry.value,
        "confidence": entry.confidence,
    }
    if entry.created_at is not None:
        payload["created_at"] = entry.created_at.isoformat()
    return payload


def _canonical_entry(entry: _NormalizedEntry) -> str:
    return _canonical_json(_entry_payload(entry))


def _cache_key(
    entry_a: _NormalizedEntry | None,
    entry_b: _NormalizedEntry | None,
) -> str:
    payloads = [
        _canonical_json(None if entry is None else _entry_payload(entry))
        for entry in (entry_a, entry_b)
    ]
    return _canonical_json(sorted(payloads))


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if is_dataclass(value):
        return _jsonable(asdict(value))

    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    return str(value)


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return _canonical_json(value)


def _clamp_confidence(value: Any, default: float = 1.0) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = default
    return min(1.0, max(0.0, confidence))


__all__ = ["ContradictionResolver", "ContradictionResult"]
