"""Deterministic duplicate identification for provenance-bearing memories.

This module identifies equivalent assertions only. It does not merge or mutate
records; callers remain responsible for persistence and evidence retention.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import re
import unicodedata
from typing import Any


_TOKEN_RE = re.compile(r"[^\w]+", re.UNICODE)
_SCOPE_KEYS = (
    "user_id",
    "companion_id",
    "room_id",
    "memory_type",
    "start_at",
    "end_at",
    "valid_from",
    "valid_until",
    "attribution",
)


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    """A deterministic set of equivalent records and their preserved evidence IDs."""

    fingerprint: str
    canonical_id: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DuplicateDecision:
    """Result for one input group; singleton groups are omitted."""

    groups: tuple[DuplicateGroup, ...]


def identify_duplicates(entries: Sequence[Any]) -> DuplicateDecision:
    """Group exact normalized assertions with proven compatible scope.

    Only punctuation, whitespace, and case are normalized. Missing owner or
    companion/room provenance is not guessed; such records are not grouped.
    Original entries are never modified or discarded.
    """
    candidates: dict[tuple[str, tuple[str, ...]], list[str]] = defaultdict(list)
    for entry in entries:
        entry_id = _text(_value(entry, "id"))
        assertion = _assertion(entry)
        scope = _scope(entry)
        if not entry_id or not assertion or scope is None:
            continue
        candidates[(assertion, scope)].append(entry_id)

    groups: list[DuplicateGroup] = []
    for (assertion, scope), ids in candidates.items():
        source_ids = tuple(sorted(set(ids)))
        if len(source_ids) < 2:
            continue
        fingerprint = hashlib.sha256(
            "\x1f".join((assertion, *scope)).encode("utf-8")
        ).hexdigest()
        groups.append(
            DuplicateGroup(
                fingerprint=fingerprint,
                canonical_id=source_ids[0],
                source_ids=source_ids,
            )
        )

    groups.sort(key=lambda group: (group.fingerprint, group.source_ids))
    return DuplicateDecision(groups=tuple(groups))


def _assertion(entry: Any) -> str:
    value = _value(entry, "fact_text")
    if value is None:
        value = _value(entry, "summary")
    if value is None:
        value = _value(entry, "content")
    text = unicodedata.normalize("NFKC", _text(value)).casefold()
    return " ".join(_TOKEN_RE.sub(" ", text).split())


def _scope(entry: Any) -> tuple[str, ...] | None:
    metadata = _value(entry, "metadata")
    if not isinstance(metadata, Mapping):
        metadata = {}
    values: dict[str, str] = {}
    for key in _SCOPE_KEYS:
        value = _value(entry, key)
        if value is None:
            value = metadata.get(key)
        if value is not None:
            values[key] = _text(value)

    # Source relationships can disambiguate legacy facts without new columns.
    message = _value(entry, "source_message")
    session = _value(message, "session") or _value(entry, "session")
    if "companion_id" not in values:
        value = _value(message, "companion_id") or _value(session, "companion_id")
        if value is not None:
            values["companion_id"] = _text(value)
    if "room_id" not in values:
        value = _value(session, "room_id")
        if value is not None:
            values["room_id"] = _text(value)

    # User ownership is mandatory. Companion and room values are included when
    # known; their absence means the existing user-shared scope.
    owner = values.get("user_id")
    if not owner:
        return None
    return tuple(f"{key}={values.get(key, '')}" for key in _SCOPE_KEYS)


def _value(entry: Any, key: str) -> Any:
    if isinstance(entry, Mapping):
        return entry.get(key)
    return getattr(entry, key, None)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


__all__ = ["DuplicateDecision", "DuplicateGroup", "identify_duplicates"]
