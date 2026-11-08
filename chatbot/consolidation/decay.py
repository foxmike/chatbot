from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
import math
from typing import Any, Protocol


class _MemoryEntry(Protocol):
    metadata: Mapping[str, Any]


_DEFAULT_HALF_LIFE_SECONDS = 7 * 24 * 60 * 60
_RELEVANCE_KEYS = ("relevance", "relevance_score", "importance", "confidence")
_ACCESS_COUNT_KEYS = ("access_count", "accesses", "retrieval_count", "use_count")
_ACCESS_TIME_KEYS = (
    "last_accessed_at",
    "last_access_at",
    "accessed_at",
    "last_retrieved_at",
)
_CREATED_TIME_KEYS = ("created_at", "updated_at", "timestamp", "time")


class MemoryDecayEngine:
    """Apply deterministic age, access, and relevance decay to memory entries."""

    def __init__(
        self,
        base_half_life_seconds: float = _DEFAULT_HALF_LIFE_SECONDS,
        access_count_weight: float = 0.25,
        relevance_threshold: float = 0.1,
        reference_time: datetime | None = None,
    ) -> None:
        if not math.isfinite(base_half_life_seconds) or base_half_life_seconds <= 0:
            raise ValueError("base_half_life_seconds must be greater than zero")
        if not math.isfinite(access_count_weight) or access_count_weight < 0:
            raise ValueError("access_count_weight must be non-negative")
        if not math.isfinite(relevance_threshold):
            raise ValueError("relevance_threshold must be finite")

        self.base_half_life_seconds = float(base_half_life_seconds)
        self.access_count_weight = float(access_count_weight)
        self.relevance_threshold = float(relevance_threshold)
        self.reference_time = _datetime_value(reference_time) or datetime.now(timezone.utc)

    def retrieval_weight(self, entry: Mapping[str, Any], *, reference_time: datetime | None = None) -> float:
        """Reversible prominence reduction; unknown importance remains neutral.

        No retention deadline, deletion, or manufactured importance is implied.
        Reinforcement is the actual count of distinct persisted source events.
        """
        meta = entry.get("metadata") or {}
        if entry.get("pinned") or entry.get("privacy_level") == "permanent" or meta.get("explicit_request"):
            return 1.0
        importance = meta.get("importance")
        if isinstance(importance, bool) or not isinstance(importance, (int, float)) or not math.isfinite(importance):
            return 1.0
        if not 0 <= importance < 0.75:
            return 1.0
        count = max(0, int(entry.get("reinforcement_count") or 1) - 1)
        source = {"created_at": entry.get("last_reinforced_at") or entry.get("created_at"),
                  "metadata": {"importance": importance, "access_count": count}}
        score = self.decay_score(source, reference_time=reference_time)
        # Preserve an observable reinforcement contribution even when an old
        # age component is smaller than floating-point precision at the floor.
        reinforcement = importance * self.access_count_weight * (count / (1 + count))
        return 0.75 + 0.25 * min(1.0, score + reinforcement)

    def prune(self, entries: list) -> list:
        if not entries:
            return []

        reference_time = self.reference_time
        return [
            entry
            for entry in entries
            if self.decay_score(entry, reference_time=reference_time) >= self.relevance_threshold
        ]

    def decay_score(
        self,
        entry: _MemoryEntry | Mapping[str, Any],
        *,
        reference_time: datetime | None = None,
    ) -> float:
        normalized = _normalize_entry(entry)
        as_of = _datetime_value(reference_time) or self.reference_time

        age_seconds = _age_seconds(normalized.created_at, as_of)
        access_boost = _access_boost(
            normalized.access_count,
            normalized.last_accessed_at,
            as_of,
            self.base_half_life_seconds,
        )
        effective_half_life = self.base_half_life_seconds * (
            1.0 + (self.access_count_weight * access_boost)
        )
        age_component = _exponential_decay(age_seconds, effective_half_life)

        return _clamp(normalized.intrinsic_relevance * age_component)


class _NormalizedEntry:
    def __init__(
        self,
        *,
        metadata: Mapping[str, Any],
        created_at: datetime | None,
        last_accessed_at: datetime | None,
        access_count: float,
        intrinsic_relevance: float,
    ) -> None:
        self.metadata = metadata
        self.created_at = created_at
        self.last_accessed_at = last_accessed_at
        self.access_count = access_count
        self.intrinsic_relevance = intrinsic_relevance


def _normalize_entry(entry: _MemoryEntry | Mapping[str, Any]) -> _NormalizedEntry:
    source = _entry_mapping(entry)
    metadata = _expanded_metadata(source.get("metadata", getattr(entry, "metadata", None)))
    for key in ("metadata_json", "extra"):
        nested = source.get(key, getattr(entry, key, None))
        if isinstance(nested, Mapping):
            metadata.update(_expanded_metadata(nested))

    created_at = _first_datetime(source, entry, metadata, _CREATED_TIME_KEYS)
    access_times = _access_times(source, entry, metadata)
    last_accessed_at = max(access_times) if access_times else _first_datetime(
        source,
        entry,
        metadata,
        _ACCESS_TIME_KEYS,
    )
    access_count = _access_count(source, entry, metadata, access_times)
    intrinsic_relevance = _intrinsic_relevance(source, entry, metadata)

    return _NormalizedEntry(
        metadata=metadata,
        created_at=created_at,
        last_accessed_at=last_accessed_at,
        access_count=access_count,
        intrinsic_relevance=intrinsic_relevance,
    )


def _entry_mapping(entry: _MemoryEntry | Mapping[str, Any]) -> Mapping[str, Any]:
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


def _expanded_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}

    metadata = {str(key): item for key, item in value.items()}
    for key in ("metadata", "metadata_json", "extra"):
        nested = metadata.get(key)
        if isinstance(nested, Mapping):
            for nested_key, nested_value in nested.items():
                metadata.setdefault(str(nested_key), nested_value)
    return metadata


def _first_datetime(
    source: Mapping[str, Any],
    entry: _MemoryEntry | Mapping[str, Any],
    metadata: Mapping[str, Any],
    keys: tuple[str, ...],
) -> datetime | None:
    for values in (source, metadata):
        for key in keys:
            value = _datetime_value(values.get(key))
            if value is not None:
                return value

    for key in keys:
        value = _datetime_value(getattr(entry, key, None))
        if value is not None:
            return value

    return None


def _access_times(
    source: Mapping[str, Any],
    entry: _MemoryEntry | Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> list[datetime]:
    values = (
        source.get("access_timestamps"),
        source.get("accessed_at_times"),
        getattr(entry, "access_timestamps", None),
        getattr(entry, "accessed_at_times", None),
        metadata.get("access_timestamps"),
        metadata.get("accessed_at_times"),
    )

    timestamps: list[datetime] = []
    for value in values:
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            timestamps.extend(
                timestamp
                for timestamp in (_datetime_value(item) for item in value)
                if timestamp is not None
            )
    return timestamps


def _access_count(
    source: Mapping[str, Any],
    entry: _MemoryEntry | Mapping[str, Any],
    metadata: Mapping[str, Any],
    access_times: Sequence[datetime],
) -> float:
    for value in _candidate_values(source, entry, metadata, _ACCESS_COUNT_KEYS):
        count = _float_value(value)
        if count is not None:
            return max(0.0, count)
    return float(len(access_times))


def _intrinsic_relevance(
    source: Mapping[str, Any],
    entry: _MemoryEntry | Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> float:
    for value in _candidate_values(source, entry, metadata, _RELEVANCE_KEYS):
        relevance = _float_value(value)
        if relevance is not None:
            return _clamp(relevance)
    return 1.0


def _candidate_values(
    source: Mapping[str, Any],
    entry: _MemoryEntry | Mapping[str, Any],
    metadata: Mapping[str, Any],
    keys: tuple[str, ...],
) -> list[Any]:
    values = []
    for mapping in (source, metadata):
        values.extend(mapping.get(key) for key in keys if key in mapping)
    values.extend(getattr(entry, key, None) for key in keys)
    return values


def _datetime_value(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _normalize_datetime(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc)
        except (OverflowError, OSError):
            return None
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            return _normalize_datetime(datetime.fromisoformat(text.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_seconds(created_at: datetime | None, reference_time: datetime) -> float:
    if created_at is None:
        return 0.0
    return max(0.0, (reference_time - created_at).total_seconds())


def _access_boost(
    access_count: float,
    last_accessed_at: datetime | None,
    reference_time: datetime,
    base_half_life_seconds: float,
) -> float:
    if access_count <= 0:
        return 0.0

    if last_accessed_at is None:
        recency = 1.0
    else:
        recency = _exponential_decay(
            _age_seconds(last_accessed_at, reference_time),
            base_half_life_seconds,
        )

    return math.log1p(access_count) * recency


def _exponential_decay(age_seconds: float, half_life_seconds: float) -> float:
    if age_seconds <= 0:
        return 1.0
    return 0.5 ** (age_seconds / half_life_seconds)


def _float_value(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


__all__ = ["MemoryDecayEngine"]
