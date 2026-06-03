from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid
from typing import Any, Literal

ProvenanceOrigin = Literal["explicit", "inferred"]


@dataclass(frozen=True, slots=True)
class ProvenanceAnnotation:
    origin: ProvenanceOrigin
    turn_id: str | None = None
    confidence: float = 1.0
    source_message_id: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "origin": self.origin,
            "confidence": self.confidence,
        }
        if self.turn_id is not None:
            result["turn_id"] = self.turn_id
        if self.source_message_id is not None:
            result["source_message_id"] = self.source_message_id
        if self.created_at is not None:
            result["created_at"] = _normalize_datetime(self.created_at).isoformat()
        if self.metadata:
            result["metadata"] = dict(self.metadata)
        return result


class ProvenanceTracker:
    PROVENANCE_KEY = "provenance"

    def annotate(
        self,
        record: dict[str, Any],
        *,
        origin: ProvenanceOrigin,
        turn_id: str | None = None,
        confidence: float = 1.0,
        source_message_id: str | None = None,
        created_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        annotation = ProvenanceAnnotation(
            origin=origin,
            turn_id=turn_id,
            confidence=confidence,
            source_message_id=source_message_id,
            created_at=created_at or datetime.now(timezone.utc),
            metadata=dict(metadata or {}),
        )
        return _apply_annotation(record, annotation)

    def annotate_explicit(
        self,
        record: dict[str, Any],
        *,
        turn_id: str | None = None,
        source_message_id: str | None = None,
        confidence: float = 0.9,
        created_at: datetime | None = None,
    ) -> dict[str, Any]:
        return self.annotate(
            record,
            origin="explicit",
            turn_id=turn_id,
            confidence=confidence,
            source_message_id=source_message_id,
            created_at=created_at,
        )

    def annotate_inferred(
        self,
        record: dict[str, Any],
        *,
        turn_id: str | None = None,
        source_message_id: str | None = None,
        confidence: float = 0.5,
        created_at: datetime | None = None,
    ) -> dict[str, Any]:
        return self.annotate(
            record,
            origin="inferred",
            turn_id=turn_id,
            confidence=confidence,
            source_message_id=source_message_id,
            created_at=created_at,
        )

    @staticmethod
    def extract_annotation(record: dict[str, Any]) -> ProvenanceAnnotation | None:
        provenance = record.get(ProvenanceTracker.PROVENANCE_KEY)
        if not isinstance(provenance, dict):
            return None

        origin = provenance.get("origin")
        if origin not in ("explicit", "inferred"):
            return None

        created_at = provenance.get("created_at")
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                created_at = None
        elif isinstance(created_at, datetime):
            created_at = _normalize_datetime(created_at)
        else:
            created_at = None

        return ProvenanceAnnotation(
            origin=origin,
            turn_id=provenance.get("turn_id"),
            confidence=float(provenance.get("confidence", 1.0)),
            source_message_id=provenance.get("source_message_id"),
            created_at=created_at,
            metadata=dict(provenance.get("metadata") or {}),
        )


def tag_explicit(
    record: dict[str, Any],
    *,
    turn_id: str | None = None,
    source_message_id: str | None = None,
    confidence: float = 0.9,
) -> dict[str, Any]:
    return default_tracker.annotate_explicit(
        record,
        turn_id=turn_id,
        source_message_id=source_message_id,
        confidence=confidence,
    )


def tag_inferred(
    record: dict[str, Any],
    *,
    turn_id: str | None = None,
    source_message_id: str | None = None,
    confidence: float = 0.5,
) -> dict[str, Any]:
    return default_tracker.annotate_inferred(
        record,
        turn_id=turn_id,
        source_message_id=source_message_id,
        confidence=confidence,
    )


def tag_record(
    record: dict[str, Any],
    *,
    origin: ProvenanceOrigin,
    turn_id: str | None = None,
    source_message_id: str | None = None,
    confidence: float = 1.0,
) -> dict[str, Any]:
    return default_tracker.annotate(
        record,
        origin=origin,
        turn_id=turn_id,
        source_message_id=source_message_id,
        confidence=confidence,
    )


def _apply_annotation(
    record: dict[str, Any],
    annotation: ProvenanceAnnotation,
) -> dict[str, Any]:
    annotated = dict(record)
    annotated[ProvenanceTracker.PROVENANCE_KEY] = annotation.as_dict()
    if annotation.confidence:
        annotated.setdefault("confidence", annotation.confidence)
    if annotation.source_message_id is not None:
        annotated.setdefault("source_message_id", annotation.source_message_id)
    return annotated


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


default_tracker = ProvenanceTracker()


__all__ = [
    "ProvenanceAnnotation",
    "ProvenanceOrigin",
    "ProvenanceTracker",
    "tag_explicit",
    "tag_inferred",
    "tag_record",
]