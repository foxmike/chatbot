"""Autobiographical shared experiences, stored as Phase 2 semantic memories.

A *shared experience* is a significant event in the user's real life that the
user told their companion about, or that the two went through together. It is
an ordinary ``semantic_facts`` row with ``extraction_metadata.kind ==
"shared_experience"``, so it reuses the Phase 2 pipeline unchanged: durable
job, receipt replay, scope keys, embeddings, immutable ``memory_evidence``,
lifecycle states, ``memory_relations`` and scoped retrieval. Nothing here adds
a table, queue, worker or model call.

Three responsibilities:

1. **Validation** (``validate_experiences``): checks the experience candidates
   the existing extraction call returns. Only observed, grounded, significant
   events pass; roleplay, fiction, hypotheticals, plans, jokes, invented
   companion backstory and anything not quoted from the user's own message are
   rejected.
2. **Linking** (``link_experience``): after reconciliation, connects the new
   experience to related earlier ones in the same scope (shared topics) and, when
   it revises an earlier experience, supersedes that one through the existing
   ``supersedes`` relation. The earlier row and its evidence are kept.
3. **Reading** (``list_experiences`` / ``get_experience``): chronological
   records with evidence, links and revision history, restricted by the same
   owner/companion/room/session scope as retrieval. This is the interface for
   later reflection.

The durable relationship an experience belongs to is the owned companion
(``companion_relationships.companion_id``); the user and companion ids are in
the row's scope and ``extraction_metadata``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from chatbot.db.models import MemoryEvidence, MemoryRelation, SemanticFact
from chatbot.memory import llm_extraction as _llm
from chatbot.memory.extraction import _ROLEPLAY_FRAME, _normalize_quotes, _rx
from chatbot.memory.pgvector_store import PgvectorStore

EXPERIENCE_KIND = "shared_experience"

MAX_EXPERIENCES_PER_TURN = 2
MAX_RELATED = 5
MAX_LINK_CANDIDATES = 50
MAX_HISTORY_DEPTH = 20
MAX_NARRATIVE_CHARS = 300
MAX_TITLE_CHARS = 80
MAX_TOPICS = 3

# Only these matter enough to become autobiography; "ordinary" is chatter.
SALIENCE = {"significant": 0.7, "major": 0.95}
_PARTICIPANTS = frozenset({"user", "companion"})

_FUTURE = _rx(
    r"\b(?:going to|gonna|will|tomorrow|next (?:week|month|year|time)|someday|"
    r"plan(?:ning)? to|hope to|want to|would like to|thinking of|thinking about)\b"
)
_NARRATIVE_ROLES = re.compile(r"\b(?:Companion|We|Our|Together)\b")
_TOPIC_CHARS = re.compile(r"[^a-z0-9 ]+")


@dataclass(frozen=True, slots=True)
class ExperienceEvidence:
    evidence_id: str
    session_id: str | None
    turn_id: str
    source_message_id: str | None
    source_at: datetime
    excerpt: str


@dataclass(frozen=True, slots=True)
class ExperienceRecord:
    """One shared experience with its chronology, links and (for ``get``) evidence."""

    id: str
    narrative: str
    title: str
    participants: tuple[str, ...]
    significance: str
    salience: float
    topics: tuple[str, ...]
    occurred_at: datetime
    status: str  # semantic_facts.lifecycle_status: active | superseded | historical
    user_id: str
    companion_id: str | None
    room_id: str | None
    session_id: str | None
    turn_id: str | None
    source_message_id: str | None
    reinforcement_count: int
    related_ids: tuple[str, ...] = ()
    revises_id: str | None = None
    superseded_by: str | None = None
    evidence: tuple[ExperienceEvidence, ...] = ()
    revision_history: tuple[str, ...] = field(default_factory=tuple)  # earlier versions, newest first


# --------------------------------------------------------------------------
# Validation (runs inside the existing extraction call's response handling)
# --------------------------------------------------------------------------


def validate_experiences(
    raw_items: Sequence[Any],
    *,
    user_message: str,
    assistant_message: str,
    session_id: str,
    turn_id: str | None,
    model: str | None,
    limits: _llm.LLMExtractionLimits | None = None,
) -> tuple[list[dict[str, Any]], list[_llm.RejectedCandidate]]:
    """Turn model-proposed experiences into extraction entries; reject the rest."""
    limits = limits or _llm.LLMExtractionLimits()
    sentences = _llm._sentence_spans(user_message)
    user_text = " ".join(normalized for _, normalized in sentences)
    turn_text = _llm._normalize(f"{user_message} {assistant_message}")
    turn_words = {_llm._stem(word) for word in _llm._words(turn_text)}
    roleplay_frame = bool(_ROLEPLAY_FRAME.search(_normalize_quotes(user_message)))
    retracted = _llm._retracted_sentences(sentences)

    accepted: list[tuple[float, dict[str, Any]]] = []
    rejected: list[_llm.RejectedCandidate] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            continue
        narrative = _llm._clean_text(str(raw.get("narrative") or raw.get("content") or ""))
        reason, entry = _validate_one(
            raw,
            narrative,
            sentences=sentences,
            user_text=user_text,
            turn_text=turn_text,
            turn_words=turn_words,
            roleplay_frame=roleplay_frame,
            retracted=retracted,
            session_id=session_id,
            turn_id=turn_id,
            model=model,
            limits=limits,
        )
        if entry is None:
            rejected.append(_llm.RejectedCandidate(narrative, reason or "invalid"))
            continue
        key = _llm._dedup_key(narrative)
        if key in seen:
            rejected.append(_llm.RejectedCandidate(narrative, "duplicate"))
            continue
        seen.add(key)
        accepted.append((entry["metadata"]["experience"]["salience"], entry))

    accepted.sort(key=lambda item: -item[0])  # stable: most significant first
    for _, entry in accepted[MAX_EXPERIENCES_PER_TURN:]:
        rejected.append(_llm.RejectedCandidate(entry["content"], "over_experience_limit"))
    return [entry for _, entry in accepted[:MAX_EXPERIENCES_PER_TURN]], rejected


def _validate_one(
    raw: Mapping[str, Any],
    narrative: str,
    *,
    sentences: list[tuple[str, str]],
    user_text: str,
    turn_text: str,
    turn_words: set[str],
    roleplay_frame: bool,
    retracted: set[int],
    session_id: str,
    turn_id: str | None,
    model: str | None,
    limits: _llm.LLMExtractionLimits,
) -> tuple[str | None, dict[str, Any] | None]:
    status = str(raw.get("status") or "").strip().lower()
    if status != "real":
        return (f"status_{status}" if status in _llm.STATUSES else "missing_status"), None

    origin = str(raw.get("origin") or "").strip().lower()
    participants = _participants(raw.get("participants"))
    if origin != "conversation" or "user" not in participants:
        # The companion's own past is configuration (backstory), never observed history.
        return "fictional_backstory", None

    significance = str(raw.get("significance") or "").strip().lower()
    if significance == "ordinary":
        return "not_significant", None
    if significance not in SALIENCE:
        return "bad_significance", None

    if not narrative or len(narrative) > MAX_NARRATIVE_CHARS:
        return "bad_content", None

    evidence = _llm._clean_text(str(raw.get("evidence") or ""))
    evidence_key = _llm._normalize(evidence).rstrip(" .!")
    if not evidence or len(evidence) > limits.max_evidence_chars or len(evidence_key) < 3:
        return "missing_evidence", None
    start = user_text.find(evidence_key)
    if start < 0:
        # Quoted from the user only: assistant text and invented history cannot ground it.
        return "evidence_not_in_user_message", None

    confidence = _llm._confidence(raw.get("confidence"))
    if confidence is None:
        return "bad_confidence", None
    if confidence < limits.min_confidence:
        return "low_confidence", None

    checked = _NARRATIVE_ROLES.sub("", narrative)
    if _llm._unsupported_details(checked, evidence_key, turn_text, turn_words):
        return "unsupported_content", None

    signal = _llm._evidence_signal(
        sentences,
        span=(start, start + len(evidence_key)),
        evidence_key=evidence_key,
        memory_type="experience",
        roleplay_frame=roleplay_frame,
        retracted=retracted,
    )
    if signal:
        return signal, None
    if _FUTURE.search(evidence_key):
        return "not_yet_happened", None

    revises = raw.get("revises")
    revises_text = _llm._clean_text(revises) if isinstance(revises, str) else ""
    title = _llm._clean_text(str(raw.get("title") or "")) or narrative
    metadata: dict[str, Any] = {
        "session_id": session_id,
        "source": "user_message",
        "source_type": "explicit",
        "confidence": min(confidence, limits.max_confidence),
        "extractor": _llm.LLM_EXTRACTOR,
        "kind": EXPERIENCE_KIND,
        "evidence": evidence,
        "experience": {
            "title": title[:MAX_TITLE_CHARS],
            "participants": participants,
            "significance": significance,
            "salience": SALIENCE[significance],
            "topics": _topics(raw.get("topics"), narrative),
            "revises": revises_text[:MAX_NARRATIVE_CHARS] or None,
            "observed": True,
        },
    }
    if model:
        metadata["model"] = model
    if turn_id:
        metadata["turn_id"] = str(turn_id)
    return None, {"type": "fact", "content": narrative, "metadata": metadata}


def _participants(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    found = {str(item).strip().lower() for item in value}
    return [name for name in ("user", "companion") if name in found & _PARTICIPANTS]


def _topics(value: Any, narrative: str) -> list[str]:
    topics: list[str] = []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            topic = " ".join(_TOPIC_CHARS.sub(" ", str(item).casefold()).split())
            if 2 <= len(topic) <= 24 and topic not in topics:
                topics.append(topic)
    if not topics:  # deterministic fallback so linking never depends on model luck
        words = [
            word
            for word in _llm._words(_llm._normalize(_NARRATIVE_ROLES.sub("", narrative)))
            if word not in _llm._STOPWORDS and len(word) >= 4
        ]
        # Longer words carry more subject than short verbs; ties keep narrative order.
        topics = sorted(dict.fromkeys(words), key=lambda word: -len(word))
    return topics[:MAX_TOPICS]


# --------------------------------------------------------------------------
# Linking and revision (runs in the durable job's persistence transaction)
# --------------------------------------------------------------------------


async def link_experience(
    db: Any,
    *,
    canonical_id: uuid.UUID,
    evidence_id: uuid.UUID,
    metadata: Mapping[str, Any],
    source_at: datetime,
) -> tuple[list[str], str | None]:
    """Connect a reconciled experience to related and revised earlier ones.

    Runs after ``reconcile_fact`` inside the same transaction, so the owner lock
    it took is still held and concurrent jobs for this user cannot interleave.
    Idempotent: relation ids are deterministic and link lists only grow by new
    ids, so a replay changes nothing. Candidates come from the same user and
    ``scope_key`` only, so links never cross user, companion or room scope.

    Returns ``(related_ids, revised_id)``.
    """
    canonical = await db.get(SemanticFact, canonical_id)
    if canonical is None:
        return [], None
    incoming = dict(metadata.get("experience") or {})
    source_at = _aware(source_at)

    candidates = list(
        (
            await db.scalars(
                sa.select(SemanticFact)
                .where(
                    SemanticFact.user_id == canonical.user_id,
                    SemanticFact.scope_key == canonical.scope_key,
                    SemanticFact.id != canonical.id,
                    SemanticFact.extraction_metadata["kind"].as_string() == EXPERIENCE_KIND,
                    SemanticFact.lifecycle_status.in_(["active", "historical"]),
                    SemanticFact.confidence > 0,
                )
                .order_by(SemanticFact.last_reinforced_at.desc(), SemanticFact.id)
                .limit(MAX_LINK_CANDIDATES)
            )
        ).all()
    )

    topic_keys = _topic_keys(incoming.get("topics"))
    scored = []
    for candidate in candidates:
        overlap = len(topic_keys & _topic_keys(_experience(candidate).get("topics")))
        if overlap:
            scored.append((overlap, candidate))
    scored.sort(key=lambda item: -item[0])  # stable: recency order is kept within a tie
    related = [candidate for _, candidate in scored[:MAX_RELATED]]

    revised: SemanticFact | None = None
    revises_text = incoming.get("revises")
    if revises_text:
        revised = _best_revision_target(str(revises_text), candidates)
        if revised is not None and not source_at > _fact_time(revised):
            revised = None  # a late job cannot overturn something that happened later

    related_ids = [str(candidate.id) for candidate in related]
    if revised is not None and str(revised.id) not in related_ids:
        related_ids.insert(0, str(revised.id))
    related_ids = related_ids[:MAX_RELATED]

    for candidate in related + ([revised] if revised is not None else []):
        _add_link(candidate, str(canonical.id))
    own = _experience(canonical)
    own["related_ids"] = _merge(own.get("related_ids"), related_ids)
    if revised is not None:
        own["revises_id"] = str(revised.id)
    elif revises_text and "revises_id" not in own:
        own["revision_unmatched"] = True  # nothing earlier to revise: kept as a plain experience
    _set_experience(canonical, own)

    if revised is not None and revised.lifecycle_status == "active":
        revised.lifecycle_status = "superseded"
        revised.superseded_by, revised.superseded_at = canonical.id, source_at
        revised_meta = _experience(revised)
        revised_meta["revised_by"] = str(canonical.id)
        _set_experience(revised, revised_meta)
        await db.execute(
            insert(MemoryRelation)
            .values(
                id=uuid.uuid5(
                    uuid.NAMESPACE_URL, f"experience-revision:{canonical.id}:{revised.id}"
                ),
                source_id=canonical.id,
                target_id=revised.id,
                relation="supersedes",
                evidence_id=evidence_id,
            )
            .on_conflict_do_nothing(index_elements=["source_id", "target_id", "relation"])
        )
    await db.flush()
    return own["related_ids"], str(revised.id) if revised is not None else None


def _best_revision_target(text: str, candidates: Sequence[SemanticFact]) -> SemanticFact | None:
    wanted = _content_stems(text)
    best: tuple[float, SemanticFact] | None = None
    for candidate in candidates:
        if candidate.lifecycle_status != "active":
            continue
        have = _content_stems(candidate.fact_text)
        if not wanted or not have:
            continue
        score = len(wanted & have) / len(wanted | have)
        if score >= 0.5 and (best is None or score > best[0]):
            best = (score, candidate)  # candidates are newest first: ties keep the newest
    return best[1] if best else None


def _content_stems(text: str) -> set[str]:
    return {
        _llm._stem(word)
        for word in _llm._words(_llm._normalize(_NARRATIVE_ROLES.sub("", text)))
        if word not in _llm._STOPWORDS
    }


def _topic_keys(topics: Any) -> set[str]:
    if not isinstance(topics, Sequence) or isinstance(topics, (str, bytes)):
        return set()
    return {" ".join(_llm._stem(word) for word in str(topic).split()) for topic in topics} - {""}


def _experience(fact: SemanticFact) -> dict[str, Any]:
    return dict((fact.extraction_metadata or {}).get("experience") or {})


def _set_experience(fact: SemanticFact, experience: dict[str, Any]) -> None:
    # JSON columns are not mutation-tracked: assign a new dict.
    fact.extraction_metadata = {**(fact.extraction_metadata or {}), "experience": experience}


def _add_link(fact: SemanticFact, other_id: str) -> None:
    experience = _experience(fact)
    experience["related_ids"] = _merge(experience.get("related_ids"), [other_id])
    _set_experience(fact, experience)


def _merge(existing: Any, new: Sequence[str]) -> list[str]:
    merged = [str(item) for item in (existing or [])]
    merged += [item for item in new if item not in merged]
    return merged[-MAX_RELATED:] if len(merged) > MAX_RELATED else merged


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _fact_time(fact: SemanticFact) -> datetime:
    return _aware(fact.last_reinforced_at or fact.created_at)


# --------------------------------------------------------------------------
# Reading (interface for reflection and prompt/retrieval consumers)
# --------------------------------------------------------------------------


async def list_experiences(
    db: Any,
    *,
    session_id: Any,
    include_history: bool = False,
    limit: int = 50,
) -> list[ExperienceRecord]:
    """Experiences visible to a trusted session, oldest first.

    Visibility is exactly the retrieval rule for ``semantic_facts``: the
    session's owner, and only the user-wide, companion, room or session scope
    the session itself belongs to. ``include_history`` adds superseded and
    historical versions.
    """
    states = ["active", "superseded", "historical"] if include_history else ["active"]
    stmt = sa.select(SemanticFact).where(
        SemanticFact.extraction_metadata["kind"].as_string() == EXPERIENCE_KIND,
        SemanticFact.lifecycle_status.in_(states),
        SemanticFact.confidence > 0,
    )
    stmt = PgvectorStore(db)._apply_owner_session_filter(stmt, session_id)
    rows = list(
        (
            await db.scalars(
                stmt.order_by(SemanticFact.last_reinforced_at.desc(), SemanticFact.id).limit(limit)
            )
        ).all()
    )
    records = [_record(row) for row in rows]
    return sorted(records, key=lambda record: (record.occurred_at, record.id))


async def get_experience(
    db: Any,
    *,
    session_id: Any,
    experience_id: Any,
) -> ExperienceRecord | None:
    """One visible experience with its evidence and earlier versions, or ``None``."""
    wanted = experience_id if isinstance(experience_id, uuid.UUID) else uuid.UUID(str(experience_id))
    stmt = sa.select(SemanticFact).where(
        SemanticFact.id == wanted,
        SemanticFact.extraction_metadata["kind"].as_string() == EXPERIENCE_KIND,
        SemanticFact.confidence > 0,
    )
    stmt = PgvectorStore(db)._apply_owner_session_filter(stmt, session_id)
    fact = (await db.scalars(stmt)).one_or_none()
    if fact is None:
        return None

    evidence_rows = list(
        (
            await db.scalars(
                sa.select(MemoryEvidence)
                .where(MemoryEvidence.memory_id == fact.id, MemoryEvidence.user_id == fact.user_id)
                .order_by(MemoryEvidence.source_at, MemoryEvidence.evidence_id)
            )
        ).all()
    )
    history: list[str] = []
    current = fact.id
    for _ in range(MAX_HISTORY_DEPTH):
        earlier = await db.scalar(
            sa.select(MemoryRelation.target_id).where(
                MemoryRelation.source_id == current, MemoryRelation.relation == "supersedes"
            )
        )
        if earlier is None or str(earlier) in history:
            break
        history.append(str(earlier))
        current = earlier
    return _record(
        fact,
        evidence=tuple(
            ExperienceEvidence(
                evidence_id=str(row.evidence_id),
                session_id=str(row.session_id) if row.session_id else None,
                turn_id=row.turn_id,
                source_message_id=str(row.source_message_id) if row.source_message_id else None,
                source_at=_aware(row.source_at),
                excerpt=row.evidence,
            )
            for row in evidence_rows
        ),
        history=tuple(history),
    )


def _record(
    fact: SemanticFact,
    *,
    evidence: tuple[ExperienceEvidence, ...] = (),
    history: tuple[str, ...] = (),
) -> ExperienceRecord:
    meta = fact.extraction_metadata or {}
    experience = _experience(fact)
    occurred = meta.get("source_created_at")
    try:
        occurred_at = _aware(datetime.fromisoformat(str(occurred)))
    except ValueError:
        occurred_at = _aware(fact.created_at)
    return ExperienceRecord(
        id=str(fact.id),
        narrative=fact.fact_text,
        title=str(experience.get("title") or fact.fact_text[:MAX_TITLE_CHARS]),
        participants=tuple(experience.get("participants") or ()),
        significance=str(experience.get("significance") or ""),
        salience=float(experience.get("salience") or 0.0),
        topics=tuple(experience.get("topics") or ()),
        occurred_at=occurred_at,
        status=fact.lifecycle_status,
        user_id=str(fact.user_id),
        companion_id=meta.get("companion_id"),
        room_id=meta.get("room_id"),
        session_id=meta.get("session_id"),
        turn_id=meta.get("turn_id"),
        source_message_id=meta.get("source_message_id"),
        reinforcement_count=fact.reinforcement_count,
        related_ids=tuple(experience.get("related_ids") or ()),
        revises_id=experience.get("revises_id"),
        superseded_by=str(fact.superseded_by) if fact.superseded_by else None,
        evidence=evidence,
        revision_history=history,
    )


__all__ = [
    "EXPERIENCE_KIND",
    "ExperienceEvidence",
    "ExperienceRecord",
    "get_experience",
    "link_experience",
    "list_experiences",
    "validate_experiences",
]
