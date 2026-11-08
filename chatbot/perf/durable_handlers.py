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

import logging

from chatbot.db.models import (
    BackgroundJob,
    CompanionIdentityModel,
    Episode,
    Message,
    Room,
    Session,
    SemanticFact,
)
from chatbot.memory.autobiography import EXPERIENCE_KIND, link_experience, list_experiences
from chatbot.memory.extraction import _ROLEPLAY_FRAME, _normalize_quotes
from chatbot.memory.llm_extraction import (
    LLMMemoryExtractor,
    resolve_extraction_model as validate_extraction_configuration,
)
from chatbot.soul.conversational_threads import (
    ThreadObservation,
    apply_thread_observations,
    known_threads_text,
    load_open_threads,
)
from chatbot.soul.self_model import (
    EvidenceSource,
    SelfModelTooLarge,
    SelfObservation,
    apply_self_observations,
    known_items_text,
)

logger = logging.getLogger(__name__)

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

    @property
    def history_allowed(self) -> bool:
        """Whether this session may build durable autobiography and self-development.

        False for scene rooms (in-character fiction) and non-durable rooms; only
        known once ``persist`` has resolved the session's room.
        """
        return self._experiences_allowed

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
        known_self_items = ""
        known_threads = ""
        known_associations = ""
        association_keys: list[str] = []
        known_experience_ids: list[str] = []
        known_experiences = ""
        if receipt is None and session_row.companion_id:
            # Existing item keys let the one extraction call continue or challenge them;
            # open thread keys let it continue or resolve unfinished business.
            identity = await db.get(CompanionIdentityModel, session_row.companion_id)
            known_self_items = known_items_text(identity.self_model if identity else None)
            known_threads = known_threads_text(
                await load_open_threads(
                    db,
                    user_id=session_row.user_id,
                    companion_id=str(session_row.companion_id),
                    room_id=session_row.room_id,
                    check_ownership=False,  # ownership was verified above
                )
            )
            from chatbot.soul.emotional_associations import (
                known_association_keys,
                known_associations_text,
                list_associations,
            )

            associations = await list_associations(
                db, session_id=job.session_id, include_history=False, limit=12
            )
            known_associations = known_associations_text(associations, limit=12)
            association_keys = sorted(known_association_keys(associations))
            experiences = await list_experiences(db, session_id=job.session_id, limit=20)
            known_experience_ids = [record.id for record in experiences]
            known_experiences = "\n".join(
                f"- {record.id}: {record.title[:80]} — {record.narrative[:100]}"
                for record in experiences
                if record.status == "active"
            )[:700]
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
                known_self_items=known_self_items,
                known_threads=known_threads,
                known_associations=known_associations,
                known_association_keys=association_keys,
                known_experiences=known_experiences,
                known_experience_ids=known_experience_ids,
            )
            outcome = getattr(extractor, "last_outcome", None)
            self_observations = list(getattr(outcome, "self_observations", None) or [])
            thread_updates = list(getattr(outcome, "thread_updates", None) or [])
            emotional_observations = list(
                getattr(outcome, "emotional_observations", None) or []
            )
            extraction_status = {
                "method": getattr(outcome, "method", "unknown"),
                "model": getattr(outcome, "model", None),
                "fallback_reason": getattr(outcome, "fallback_reason", None),
                "output_limit_tokens": int(getattr(outcome, "output_limit_tokens", 700)),
                "finish_reason": getattr(outcome, "response_finish_reason", None),
                "truncated": bool(getattr(outcome, "response_truncated", False)),
                "accepted": {
                    "memory_entries": len(entries),
                    "self_observations": len(self_observations),
                    "thread_updates": len(thread_updates),
                    "emotional_observations": len(emotional_observations),
                },
            }
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
                receipt = {
                    "version": 1,
                    "entries": entries[:MAX_RECEIPT_ENTRIES],
                    "self_observations": self_observations,
                    "thread_updates": thread_updates,
                    "emotional_observations": emotional_observations,
                    "extraction_status": extraction_status,
                }
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
        await _apply_self_reflection(
            db, job, session_row, user_msg, assistant_msg, storage, receipt
        )
        await _apply_thread_updates(
            db, job, session_row, user_msg, assistant_msg, storage, receipt
        )
        await _apply_emotional_observations(
            db, job, session_row, user_msg, assistant_msg, storage, receipt
        )
        locked.payload = {**locked.payload, "effects_applied": True}
        locked.status, locked.completed_at = "succeeded", datetime.now(timezone.utc)
        await db.commit()  # all effects and success are atomic


_OBSERVATION_FIELDS = (
    "kind", "subject", "statement", "excerpt", "reason", "significance", "status",
    "relation", "change_of_mind",
)


async def _apply_self_reflection(
    db: Any,
    job: Any,
    session_row: Any,
    user_msg: Any,
    assistant_msg: Any,
    storage: TurnMemoryStorage,
    receipt: dict[str, Any],
) -> None:
    """Apply the receipt's validated self-observations in the job's own transaction.

    Runs after the memory effects and before the job is marked succeeded, so a
    rollback leaves neither. Replays are idempotent (evidence ids derive from the
    job id and the receipt position). Only eligible turns count: the session is
    companion-bound, the companion's reply is attributed to that companion, the
    room (if any) is durable and not a scene, and the turn is not a roleplay frame.
    """
    raw_items = receipt.get("self_observations") or []
    if not raw_items or not session_row.companion_id or not storage.history_allowed:
        return
    reply_owner = getattr(assistant_msg, "companion_id", None)
    if reply_owner is not None and reply_owner != session_row.companion_id:
        return  # another companion's reply in a shared room
    observations = []
    for raw in raw_items:
        if isinstance(raw, dict):
            try:
                observations.append(
                    SelfObservation(**{k: raw[k] for k in _OBSERVATION_FIELDS if k in raw})
                )
            except TypeError:
                continue
    if not observations:
        return
    source = EvidenceSource(
        job_id=str(job.job_id),
        turn_id=str(job.turn_id),
        session_id=str(job.session_id),
        message_id=str(assistant_msg.id),
        source_at=assistant_msg.created_at,
        in_roleplay=bool(_ROLEPLAY_FRAME.search(_normalize_quotes(user_msg.content or ""))),
    )
    try:
        await apply_self_observations(
            db,
            companion_id=session_row.companion_id,
            user_id=session_row.user_id,
            observations=observations,
            source=source,
        )
    except SelfModelTooLarge:
        # A bound violation is deterministic: retrying cannot help and must not
        # block the turn's memory work. The self-model is left unchanged.
        logger.warning("Companion self-model is full; skipped reflection for job %s", job.job_id)


_THREAD_FIELDS = (
    "ownership", "kind", "subject", "status", "topic", "importance",
    "excerpt", "role", "key", "supersedes",
)


async def _apply_thread_updates(
    db: Any,
    job: Any,
    session_row: Any,
    user_msg: Any,
    assistant_msg: Any,
    storage: TurnMemoryStorage,
    receipt: dict[str, Any],
) -> None:
    """Apply the receipt's validated thread updates in the job's own transaction.

    Runs after the memory and self-model effects and before the job is marked
    succeeded, so a rollback leaves none of them. Replays are idempotent: new
    threads carry deterministic ids from the job id and receipt position. Only
    eligible turns count, with the same rules as self-reflection: the session is
    companion-bound, the companion's reply is attributed to that companion, the
    room (if any) is durable and not a scene, and the turn is not a roleplay
    frame — so roleplay never contaminates real-world threads.
    """
    raw_items = receipt.get("thread_updates") or []
    if not raw_items or not session_row.companion_id or not storage.history_allowed:
        return
    reply_owner = getattr(assistant_msg, "companion_id", None)
    if reply_owner is not None and reply_owner != session_row.companion_id:
        return  # another companion's reply in a shared room
    observations = []
    for raw in raw_items:
        if isinstance(raw, dict):
            try:
                observations.append(
                    ThreadObservation(**{k: raw[k] for k in _THREAD_FIELDS if k in raw})
                )
            except TypeError:
                continue
    if not observations:
        return
    from chatbot.db.models import CompanionRelationshipModel

    relationship = await db.get(CompanionRelationshipModel, session_row.companion_id)
    relationship_context = (
        {
            "depth": relationship.depth,
            "completed_turns": relationship.completed_turns,
        }
        if relationship is not None
        else None
    )
    # Deterministic problems (ownership, bounds) are impossible here: the job
    # verified the session's companion, and scope bounds are rejections, not
    # exceptions. Any storage failure propagates so the job retries cleanly.
    await apply_thread_observations(
        db,
        user_id=session_row.user_id,
        companion_id=str(session_row.companion_id),
        room_id=session_row.room_id,
        observations=observations,
        job_id=str(job.job_id),
        turn_id=str(job.turn_id),
        session_id=str(job.session_id),
        message_id=str(assistant_msg.id),
        source_at=assistant_msg.created_at,
        relationship_context=relationship_context,
    )


_EMOTIONAL_FIELDS = (
    "subject", "emotion", "interpretation", "source", "excerpt", "role",
    "target_kind", "significance", "experience_ref", "unresolved", "revises", "status",
)


async def _apply_emotional_observations(
    db: Any,
    job: Any,
    session_row: Any,
    user_msg: Any,
    assistant_msg: Any,
    storage: TurnMemoryStorage,
    receipt: dict[str, Any],
) -> None:
    """Apply validated emotional associations atomically with the job effects."""
    raw_items = receipt.get("emotional_observations") or []
    if not raw_items or not session_row.companion_id or not storage.history_allowed:
        return
    reply_owner = getattr(assistant_msg, "companion_id", None)
    if reply_owner is not None and reply_owner != session_row.companion_id:
        return
    from chatbot.soul.emotional_associations import (
        AssociationObservation,
        AssociationSource,
        apply_emotional_associations,
    )

    observations: list[AssociationObservation] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        try:
            observations.append(
                AssociationObservation(**{key: raw[key] for key in _EMOTIONAL_FIELDS if key in raw})
            )
        except TypeError:
            continue
    if not observations:
        return
    source = AssociationSource(
        turn_id=str(job.turn_id),
        session_id=str(job.session_id),
        source_at=assistant_msg.created_at,
        job_id=str(job.job_id),
        user_message_id=str(user_msg.id) if user_msg is not None else None,
        assistant_message_id=str(assistant_msg.id) if assistant_msg is not None else None,
        in_roleplay=(
            bool(_ROLEPLAY_FRAME.search(_normalize_quotes(user_msg.content or "")))
            or str(
                getattr(
                    getattr(session_row, "conversation_mode", ""),
                    "value",
                    getattr(session_row, "conversation_mode", ""),
                )
                or ""
            ).casefold() == "roleplay"
        ),
    )
    # Ownership, room eligibility, replay detection and supersession are
    # enforced by the existing writer; this shares the job's transaction.
    await apply_emotional_associations(
        db,
        user_id=session_row.user_id,
        companion_id=str(session_row.companion_id),
        session_id=job.session_id,
        observations=observations,
        source=source,
    )


__all__ = [
    "TurnMemoryStorage",
    "durable_memory_extract_handler",
    "make_durable_memory_extract_handler",
    "validate_extraction_configuration",
]
