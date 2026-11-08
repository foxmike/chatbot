"""Bounded, transactional reconciliation of extracted facts, without model calls.

Immutable evidence is the replay key. Canonical facts may gain evidence or
become historical/superseded; neither evidence nor messages are auto-deleted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from chatbot.consolidation.duplicates import identify_duplicates, normalize_assertion
from chatbot.consolidation.contradiction import ContradictionResolver
from chatbot.db.models import MemoryEvidence, MemoryRelation, SemanticFact, User

MAX_RECONCILE_CANDIDATES = 50


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class Assertion:
    key: str
    slot: str | None
    value: str
    transition: bool
    historical: bool


def assertion(text: str, metadata: dict[str, Any], source_at: datetime) -> Assertion:
    """Only explicit scalar patterns get property semantics; other text is exact.

    Unknown paraphrases remain distinct rather than guessing subject or context.
    Significant events include their occurrence date and never lose episode data.
    """
    clean = normalize_assertion(text)
    clean = re.sub(
        r"^(?:as i (?:mentioned|said)(?: earlier| before)?[, ]*|remember (?:that )?|actually[, ]*|correction[: ,]*|to clarify[, ]*)",
        "",
        clean,
    ).strip()
    # Only match a complete assertion, so a second predicate is not discarded.
    patterns = (
        (
            "residence",
            r"(?:i|user) (?:live|lives|reside|resides|used to live|lived) in (.+)",
            False,
        ),
        ("residence", r"(?:i|user) (?:(?:have|has) )?(?:(?:just|recently) )?moved to (.+)", True),
        ("occupation", r"(?:i|user) (?:work|works|used to work|worked) as (.+)", False),
        ("name", r"(?:my name is|user's name is|user is named) (.+)", False),
    )
    historical = metadata.get("temporal") == "past" or bool(
        re.search(r"\b(?:used to|previously|in the past|lived|worked)\b", clean)
    )
    if metadata.get("kind") == "shared_experience":
        # An event, not a property: never a slot, so it cannot supersede a fact.
        # Its date keeps the same wording on another day a different event.
        return Assertion(
            _digest([clean, str(metadata.get("context") or ""), historical, source_at.date().isoformat()]),
            None,
            clean,
            False,
            historical,
        )
    state_change = bool(re.search(r"\bnow\b", clean))
    clean = re.sub(r"\b(i|user) (?:now|currently) ", r"\1 ", clean)
    clean = re.sub(r" now$", "", clean)
    context = str(metadata.get("context") or "")
    for predicate, pattern, transition in patterns:
        match = re.fullmatch(pattern, clean)
        if not match:
            continue
        value = match.group(1)
        if re.search(r"\b(?:and|but|or|if|maybe)\b|[;?]", value):
            break
        qualifier = re.search(
            r"\s+(during|since|until|when|on weekends|at work|in (?:winter|summer|spring|autumn|fall))\b.*$",
            value,
        )
        if qualifier:
            context += qualifier.group(0)
            value = value[: qualifier.start()].strip()
        slot = _digest(["user", predicate, context])
        return Assertion(
            _digest([slot, value, historical]), slot, value, transition or state_change, historical
        )
    preference = re.fullmatch(
        r"(?:i|user) (like|likes|love|loves|prefer|prefers|dislike|dislikes|hate|hates|do not like|does not like) (.+)",
        clean,
    )
    if preference and not re.search(r"\b(?:and|or|but|if|over)\b", preference.group(2)):
        target = preference.group(2)
        slot = _digest(["user", "preference", target, context])
        value = (
            "negative"
            if preference.group(1)
            in {"dislike", "dislikes", "hate", "hates", "do not like", "does not like"}
            else "positive"
        )
        return Assertion(_digest([slot, value, historical]), slot, value, False, historical)
    # Quoted polarity, numbers, dates and internal punctuation stay meaningful.
    occurrence = source_at.date().isoformat() if metadata.get("kind") == "experience" else None
    return Assertion(
        _digest([clean, context, historical, occurrence]), None, clean, False, historical
    )


def scope_identity(user_id: Any, metadata: dict[str, Any]) -> str:
    scope = metadata["scope"]
    return _digest([str(user_id), scope])


def _duplicate_record(id_: Any, value: str, scope: str) -> dict:
    return {"id": str(id_), "user_id": scope, "fact_text": value}


async def reconcile_fact(db: Any, row: dict[str, Any]) -> uuid.UUID:
    """Apply at most 50 comparisons and one evidence event under an owner lock.

    The handler owns commit/rollback. Concurrent jobs for this owner serialize
    only during their bounded persistence work, never during inference.
    """
    evidence_id = row["id"]
    owner = row["user_id"]
    await db.execute(sa.select(User.id).where(User.id == owner).with_for_update())
    prior = await db.get(MemoryEvidence, evidence_id)
    if prior is not None:
        if prior.user_id != owner:
            raise PermissionError("Memory evidence owner mismatch")
        return prior.memory_id
    metadata = dict(row["extraction_metadata"])
    source_at = datetime.fromisoformat(metadata["source_created_at"])
    scope = scope_identity(owner, metadata)
    claim = assertion(row["fact_text"], metadata, source_at)
    matching = list(
        (
            await db.scalars(
                sa.select(SemanticFact)
                .where(
                    SemanticFact.user_id == owner,
                    SemanticFact.scope_key == scope,
                    SemanticFact.assertion_key == claim.key,
                    SemanticFact.lifecycle_status.in_(["active", "historical"]),
                    SemanticFact.confidence > 0,
                )
                .order_by(SemanticFact.created_at, SemanticFact.id)
                .limit(1)
                .with_for_update()
            )
        ).all()
    )
    canonical = matching[0] if matching else None
    if canonical is not None:
        # Reuse the checkpoint's deterministic duplicate decision over the
        # normalized factual assertion, including proven scope and time context.
        equivalent = identify_duplicates(
            [
                _duplicate_record(canonical.id, claim.key, scope),
                _duplicate_record(evidence_id, claim.key, scope),
            ]
        )
        if not equivalent.groups:
            canonical = None
    if canonical is None:
        row.update(
            scope_key=scope,
            assertion_key=claim.key,
            slot_key=claim.slot,
            lifecycle_status="historical" if claim.historical else "active",
            reinforcement_count=1,
            last_reinforced_at=source_at,
        )
        await db.execute(
            insert(SemanticFact).values(**row).on_conflict_do_nothing(index_elements=["id"])
        )
        canonical = await db.get(SemanticFact, evidence_id)
        if canonical is None or canonical.user_id != owner or canonical.scope_key != scope:
            raise PermissionError("Memory canonical identity collision")
    else:
        canonical.reinforcement_count += 1
        if canonical.last_reinforced_at is None or source_at > canonical.last_reinforced_at:
            canonical.last_reinforced_at = source_at
        # Retain a previously missing embedding without another provider call.
        if canonical.embedding is None and row.get("embedding") is not None:
            canonical.embedding = row["embedding"]
    db.add(
        MemoryEvidence(
            evidence_id=evidence_id,
            memory_id=canonical.id,
            user_id=owner,
            source_message_id=row["source_message_id"],
            session_id=uuid.UUID(metadata["session_id"]),
            turn_id=metadata["turn_id"],
            assertion_text=row["fact_text"],
            evidence=metadata["evidence"],
            provenance=metadata,
            source_at=source_at,
        )
    )
    await db.flush()
    correction_signal = bool(
        re.search(
            r"\b(?:actually|correction|no longer|to clarify|i meant|i was wrong|i misspoke)\b",
            metadata.get("evidence", ""),
            re.I,
        )
    ) and (metadata.get("correction") is True or metadata.get("kind") == "correction")
    corrected_text = metadata.get("corrects")
    corrected = (
        assertion(corrected_text, {}, source_at)
        if correction_signal and isinstance(corrected_text, str) and corrected_text.strip()
        else None
    )
    if (claim.slot or corrected) and canonical.lifecycle_status == "active":
        candidates = list(
            (
                await db.scalars(
                    sa.select(SemanticFact)
                    .where(
                        SemanticFact.user_id == owner,
                        SemanticFact.scope_key == scope,
                        sa.or_(
                            SemanticFact.slot_key == claim.slot if claim.slot else sa.false(),
                            SemanticFact.assertion_key == corrected.key
                            if corrected
                            else sa.false(),
                        ),
                        SemanticFact.id != canonical.id,
                        SemanticFact.lifecycle_status == "active",
                        SemanticFact.confidence > 0,
                    )
                    .order_by(SemanticFact.last_reinforced_at.desc(), SemanticFact.id)
                    .limit(MAX_RECONCILE_CANDIDATES + 1)
                    .with_for_update()
                )
            ).all()
        )
        if len(candidates) > MAX_RECONCILE_CANDIDATES:
            raise ValueError("Memory slot exceeds bounded reconciliation budget; sources retained")
        resolver = ContradictionResolver(provider=None)
        for old in candidates:
            old_meta = old.extraction_metadata or {}
            old_claim = assertion(old.fact_text, old_meta, old.last_reinforced_at or old.created_at)
            result = await resolver.resolve(
                {"content": canonical.fact_text, "subject": claim.slot, "value": claim.value},
                {"content": old.fact_text, "subject": old_claim.slot, "value": old_claim.value},
            )
            explicit_target = corrected is not None and corrected.key == old.assertion_key
            if not result.has_contradiction and not explicit_target:
                continue
            correction = correction_signal
            old_change = old_claim.transition or old_meta.get("correction") is True
            newer = source_at > (old.last_reinforced_at or old.created_at)
            if (correction or claim.transition) and newer:
                old.lifecycle_status = "superseded"
                old.superseded_by, old.superseded_at = canonical.id, source_at
                relation, source, target = (
                    ("supersedes" if correction else "changed_from"),
                    canonical,
                    old,
                )
            elif old_change and not newer:
                canonical.lifecycle_status = "superseded"
                canonical.superseded_by, canonical.superseded_at = old.id, old.last_reinforced_at
                relation, source, target = "changed_from", old, canonical
            else:
                relation, source, target = "conflicts_with", canonical, old
                for fact in (canonical, old):
                    fact.extraction_metadata = {
                        **(fact.extraction_metadata or {}),
                        "unresolved_conflict": True,
                    }
            relation_id = uuid.uuid5(
                uuid.NAMESPACE_URL, f"memory-relation:{source.id}:{target.id}:{relation}"
            )
            await db.execute(
                insert(MemoryRelation)
                .values(
                    id=relation_id,
                    source_id=source.id,
                    target_id=target.id,
                    relation=relation,
                    evidence_id=evidence_id,
                )
                .on_conflict_do_nothing(index_elements=["source_id", "target_id", "relation"])
            )
    await db.flush()
    return canonical.id


async def correct_memory(db: Any, fact: SemanticFact, correction: str) -> SemanticFact:
    """Explicit user edit creates a linked replacement, without rewriting history."""
    await db.execute(sa.select(User.id).where(User.id == fact.user_id).with_for_update())
    memory_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"manual-correction:{fact.user_id}:{fact.id}:{correction}"
    )
    existing = await db.get(SemanticFact, memory_id)
    if existing is not None:
        return existing
    now = datetime.now(timezone.utc)
    old_meta = dict(fact.extraction_metadata or {})
    session_id = old_meta.get("session_id")
    if not session_id and fact.source_message_id:
        from chatbot.db.models import Message

        message = await db.get(Message, fact.source_message_id)
        session_id = str(message.session_id) if message else None
    new_scope_key = fact.scope_key
    if not new_scope_key and fact.source_message_id:
        from chatbot.db.models import Message, Session

        message = await db.get(Message, fact.source_message_id)
        source_session = await db.get(Session, message.session_id) if message else None
        if source_session and source_session.user_id == fact.user_id:
            scope = {"level": "user", "user_id": str(fact.user_id)}
            if source_session.companion_id:
                scope.update(level="companion", companion_id=source_session.companion_id)
            if source_session.room_id:
                scope.update(
                    level="room",
                    room_id=source_session.room_id,
                    companion_id=source_session.companion_id,
                )
            old_meta["scope"] = scope
            new_scope_key = scope_identity(fact.user_id, old_meta)
    if await db.get(MemoryEvidence, fact.id) is None:
        db.add(
            MemoryEvidence(
                evidence_id=fact.id,
                memory_id=fact.id,
                user_id=fact.user_id,
                source_message_id=fact.source_message_id,
                session_id=uuid.UUID(session_id) if session_id else None,
                turn_id=str(old_meta.get("turn_id") or fact.source_turn_id or "legacy"),
                assertion_text=fact.fact_text,
                evidence=str(old_meta.get("evidence") or fact.fact_text),
                provenance=old_meta,
                source_at=fact.created_at,
            )
        )
    new_meta = {
        **old_meta,
        "correction": True,
        "origin": "user_memory_edit",
        "evidence": correction,
        "turn_id": f"edit:{memory_id}",
        "source_created_at": now.isoformat(),
        "original_memory_id": str(fact.id),
    }
    # Retain the original trusted scope. A direct edit has no new chat message;
    # its origin and evidence are recorded explicitly rather than fabricated.
    new_meta["source_message_id"] = None
    new = SemanticFact(
        id=memory_id,
        user_id=fact.user_id,
        fact_text=correction,
        source_message_id=None,
        source_type="explicit",
        confidence=fact.confidence,
        pinned=fact.pinned,
        privacy_level=fact.privacy_level,
        scope_key=new_scope_key,
        assertion_key=_digest(normalize_assertion(correction)),
        slot_key=fact.slot_key,
        last_reinforced_at=now,
        extraction_metadata=new_meta,
    )
    db.add(new)
    await db.flush()
    db.add(
        MemoryEvidence(
            evidence_id=memory_id,
            memory_id=memory_id,
            user_id=fact.user_id,
            session_id=uuid.UUID(session_id) if session_id else None,
            source_message_id=None,
            turn_id=new_meta["turn_id"],
            assertion_text=correction,
            evidence=correction,
            provenance=new_meta,
            source_at=now,
        )
    )
    await db.flush()
    fact.lifecycle_status, fact.superseded_by, fact.superseded_at = "superseded", new.id, now
    db.add(
        MemoryRelation(
            id=uuid.uuid5(uuid.NAMESPACE_URL, f"manual-relation:{memory_id}:{fact.id}"),
            source_id=new.id,
            target_id=fact.id,
            relation="supersedes",
            evidence_id=memory_id,
        )
    )
    await db.flush()
    return new
