"""Durable extraction: cache validated output, then atomically apply effects.

The bounded job receipt fixes the candidate set across retries. Memory identity
uses owned source messages, evidence, memory kind and a stable receipt slot,
never generated wording. A job-row lock serializes receipt/effect commits.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from chatbot.db.models import BackgroundJob, Episode, Message, Room, Session, SemanticFact
from chatbot.memory.autobiography import EXPERIENCE_KIND, link_experience
from chatbot.memory.llm_extraction import (
    LLMMemoryExtractor,
    resolve_extraction_model as validate_extraction_configuration,
)

# Up to 8 facts, 2 shared experiences and the turn episode.
MAX_RECEIPT_ENTRIES = 11


def _source_message_id(source: Any, user_msg: Any, assistant_msg: Any) -> Any | None:
    return getattr(
        user_msg
        if source == "user_message"
        else assistant_msg
        if source == "provider_response"
        else None,
        "id",
        None,
    )


class TurnMemoryStorage:
    """Existing storage-adapter interface, with deterministic primary keys."""

    def __init__(
        self,
        db: Any,
        *,
        session_row: Any,
        session_id: Any,
        turn_id: str,
        user_msg: Any,
        assistant_msg: Any,
        vectors: dict[int, list[float]] | None = None,
    ) -> None:
        self.db, self.session_row, self.session_id = db, session_row, session_id
        self.turn_id, self.user_msg, self.assistant_msg = turn_id, user_msg, assistant_msg
        self.vectors = vectors or {}
        self.persistence_error: Exception | None = None
        self._scope = None
        self._experiences_allowed = True

    async def persist(self, session_id: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if str(session_id) != str(self.session_id):
            raise PermissionError("Extraction storage session mismatch")
        try:
            return await self._persist(entries)
        except Exception as exc:
            self.persistence_error = exc
            raise

    async def _persist(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        written = []
        if self._scope is None:
            level = "companion" if self.session_row.companion_id else "user"
            room_id = self.session_row.room_id
            if room_id:
                room = await self.db.get(Room, room_id)
                if room is None or str(room.user_id) != str(self.session_row.user_id):
                    raise PermissionError("Memory room ownership mismatch")
                level = {"global": "user", "companion": "companion", "room": "room"}.get(room.memory_scope, "session")
                # Scene rooms are in-character fiction and non-durable rooms do not
                # keep history: neither produces autobiographical experiences.
                self._experiences_allowed = room.scene_id is None and bool(room.persist_to_durable)
            if level == "companion" and not self.session_row.companion_id:
                level = "session"
            self._scope = {"level": level, "user_id": str(self.session_row.user_id)}
            if level in {"companion", "room", "session"}:
                self._scope["companion_id"] = self.session_row.companion_id
            if level in {"room", "session"}:
                self._scope["room_id"] = room_id
            if level == "session":
                self._scope["session_id"] = str(self.session_id)
        slots: dict[str, int] = defaultdict(int)
        for index, entry in enumerate(entries[:MAX_RECEIPT_ENTRIES]):
            content = str(entry.get("content") or "").strip()
            if not content:
                continue
            metadata = dict(entry.get("metadata") or {})
            kind = entry.get("type")
            source_id = _source_message_id(
                metadata.get("source"), self.user_msg, self.assistant_msg
            )
            if kind == "fact" and source_id is None:
                raise ValueError("Extracted fact is missing source-message attribution")
            if kind not in {"fact", "episode"}:
                raise ValueError("Unsupported extracted memory kind")
            if metadata.get("kind") == EXPERIENCE_KIND and not self._experiences_allowed:
                continue
            evidence = metadata.get("evidence") or metadata.get("source_text")
            # Heuristics carry the source wording in content, not evidence.
            evidence = str(evidence or content) if kind == "fact" else "turn_episode"
            identity = json.dumps(
                [
                    str(self.session_row.user_id),
                    self.session_row.companion_id,
                    str(self.session_id),
                    self.turn_id,
                    str(source_id),
                    kind,
                    metadata.get("kind", kind),
                    evidence,
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            slot = slots[identity]
            slots[identity] += 1
            memory_id = uuid.uuid5(uuid.NAMESPACE_URL, f"chatbot-memory-v1:{identity}:{slot}")
            metadata.update(
                {
                    "user_id": str(self.session_row.user_id),
                    "companion_id": self.session_row.companion_id,
                    "room_id": self.session_row.room_id,
                    "session_id": str(self.session_id),
                    "turn_id": self.turn_id,
                    "source_message_id": str(source_id) if source_id else None,
                    "evidence": evidence,
                    "receipt_slot": slot,
                    "scope": self._scope,
                    "source_created_at": self.user_msg.created_at.isoformat()
                    if self.user_msg
                    else None,
                }
            )
            if kind == "fact":
                source_type = metadata.get("source_type", "inferred")
                if source_type not in {"explicit", "inferred"}:
                    raise ValueError("Invalid source classification")
                try:
                    source_turn_id = uuid.UUID(self.turn_id)
                except ValueError:
                    source_turn_id = None
                model = SemanticFact
                row = dict(
                    id=memory_id,
                    user_id=self.session_row.user_id,
                    fact_text=content,
                    source_message_id=source_id,
                    embedding=self.vectors.get(index),
                    source_turn_id=source_turn_id,
                    source_type=source_type,
                    confidence=float(metadata.get("confidence", 1.0)),
                    extraction_metadata=metadata,
                )
            else:
                model = Episode
                row = dict(
                    id=memory_id,
                    session_id=self.session_id,
                    title=str(metadata.get("title") or "Conversation turn")[:160],
                    summary=content,
                    start_message_id=getattr(self.user_msg, "id", None),
                    end_message_id=getattr(self.assistant_msg, "id", None),
                    extraction_metadata=metadata,
                )
            if kind == "fact":
                from chatbot.consolidation.lifecycle import reconcile_fact
                canonical_id = await reconcile_fact(self.db, row)
                if metadata.get("kind") == EXPERIENCE_KIND:
                    await link_experience(
                        self.db,
                        canonical_id=canonical_id,
                        evidence_id=memory_id,
                        metadata=metadata,
                        source_at=datetime.fromisoformat(metadata["source_created_at"]),
                    )
                written.append(entry)
                continue
            result = await self.db.execute(
                insert(model).values(**row).on_conflict_do_nothing(index_elements=["id"])
            )
            if result.rowcount:
                written.append(entry)
        return written


class _CaptureStorage:
    async def persist(self, session_id: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return entries[:MAX_RECEIPT_ENTRIES]


def make_durable_memory_extract_handler(
    session_factory: Any, *, registry: Any = None, settings: Any = None, embeddings: Any = None
) -> Any:
    async def handler(job: Any) -> None:
        await _process_memory_extract_job(
            session_factory, job, registry=registry, settings=settings, embeddings=embeddings
        )

    return handler


async def durable_memory_extract_handler(job: Any) -> None:
    from chatbot.db.session import async_session_factory

    await _process_memory_extract_job(async_session_factory, job)


async def _process_memory_extract_job(
    session_factory: Any,
    job: Any,
    *,
    registry: Any = None,
    settings: Any = None,
    embeddings: Any = None,
) -> None:
    if not all((job.session_id, job.user_id, job.turn_id)):
        raise ValueError("memory_extract job is missing required identifiers")
    async with session_factory() as db:
        session_row = await db.get(Session, job.session_id)
        if session_row is None:
            raise ValueError("memory_extract session does not exist")
        if session_row.user_id != job.user_id or session_row.companion_id != job.companion_id:
            raise PermissionError(
                "memory_extract session does not belong to the job's user/companion"
            )
        stored_job = await db.get(BackgroundJob, job.job_id)
        if stored_job is None:
            raise ValueError("memory_extract job no longer exists")
        if stored_job.status == "succeeded":
            return
        if stored_job.payload.get("effects_applied"):
            stored_job.status, stored_job.completed_at = "succeeded", datetime.now(timezone.utc)
            await db.commit()
            return
        receipt = stored_job.payload.get("extraction_result")
        messages = list(
            (
                await db.scalars(
                    sa.select(Message)
                    .where(
                        Message.session_id == job.session_id,
                        Message.turn_id == job.turn_id,
                    )
                    .order_by(Message.created_at, Message.id)
                )
            ).all()
        )
        user_msg = next((m for m in messages if m.role == "user"), None)
        assistant_msg = next((m for m in messages if m.role == "assistant"), None)
        await db.commit()  # release the read transaction before inference
        if not messages:
            return
        if not user_msg or not assistant_msg:
            raise ValueError("memory_extract requires a persisted completed message pair")
        if receipt is None:
            extractor = LLMMemoryExtractor.from_settings(
                storage=_CaptureStorage(),
                registry=registry,
                settings=settings,
                propagate_errors=True,
            )
            entries = await extractor.extract(
                session_id=str(job.session_id),
                user_message=user_msg.content,
                provider_response=assistant_msg.content,
                turn_id=job.turn_id,
            )
            locked = await db.scalar(
                sa.select(BackgroundJob)
                .where(BackgroundJob.job_id == job.job_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if locked is None:
                raise ValueError("memory_extract job was deleted")
            if locked.status == "succeeded" or locked.attempt_count != job.attempt_count:
                return
            receipt = locked.payload.get("extraction_result")
            if receipt is None:
                receipt = {"version": 1, "entries": entries[:MAX_RECEIPT_ENTRIES]}
                locked.payload = {**locked.payload, "extraction_result": receipt}
            await db.commit()  # durable replay input, including an empty result
        vectors = {}
        if embeddings is not None:
            facts = [
                (index, entry["content"])
                for index, entry in enumerate(receipt["entries"])
                if entry.get("type") == "fact"
            ]
            if facts:
                batch = await embeddings.embed([content for _, content in facts])
                if len(batch) != len(facts):
                    raise ValueError("Embedding batch size mismatch")
                vectors = {index: vector for (index, _), vector in zip(facts, batch, strict=True)}
        locked = await db.scalar(
            sa.select(BackgroundJob)
            .where(BackgroundJob.job_id == job.job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked is None:
            raise ValueError("memory_extract job was deleted")
        if locked.status == "succeeded" or locked.attempt_count != job.attempt_count:
            return
        storage = TurnMemoryStorage(
            db,
            session_row=session_row,
            session_id=job.session_id,
            turn_id=job.turn_id,
            user_msg=user_msg,
            assistant_msg=assistant_msg,
            vectors=vectors,
        )
        await storage.persist(str(job.session_id), receipt["entries"])
        locked.payload = {**locked.payload, "effects_applied": True}
        locked.status, locked.completed_at = "succeeded", datetime.now(timezone.utc)
        await db.commit()  # all effects and success are atomic


__all__ = [
    "TurnMemoryStorage",
    "durable_memory_extract_handler",
    "make_durable_memory_extract_handler",
    "validate_extraction_configuration",
]
