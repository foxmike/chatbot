"""Durable emotional associations: why something matters to a companion.

An association records the *emotional significance* a companion has developed
around a subject, person, event or shared experience, together with the reason
("remember why it matters, not only that it happened"). It is not a mood and
not a score: one subject can carry several associations at once (excited *and*
anxious about the same project), and a later experience can revise one of them.

Storage reuses the Phase 2 memory tables and adds nothing to the schema. An
association is a ``semantic_facts`` row with ``extraction_metadata.kind ==
"emotional_association"``; every observation that supports it is an immutable
``memory_evidence`` row; a revision supersedes the earlier row through the
existing ``supersedes`` relation, so the earlier row and its evidence stay
attributable. Rows carry the same ``scope_key``/``scope`` as every other memory,
so the existing owner, companion, room and session visibility rules apply, and
the companion id is additionally part of each row's identity (emotions are
character-specific).

Three responsibilities, none of which calls a model or an embedding service:

1. **Validation** (:func:`validate_emotional_observations`): checks the
   observations the existing extraction call may propose. Only observed,
   grounded and significant ones pass; roleplay, fiction, hypotheticals, jokes,
   questions, invented experiences, ordinary chatter, and a user's feelings
   passed off as the companion's are rejected.
2. **Writing** (:func:`apply_emotional_associations`): the one database
   operation for the durable job's transaction. It verifies ownership, locks the
   owner (the lock every memory write already takes), creates / reinforces /
   revises rows, and leaves commit to the caller. Retries change nothing.
3. **Reactivation** (:func:`retrieve_emotional_associations`,
   :func:`render_associations`): a bounded, lexical lookup of the associations
   relevant to the current message, shared experiences and open threads. It
   returns compact evidence-grounded context, never dialogue and never an
   instruction about how to react.

Source semantics. ``companion_expressed``: the companion itself voiced the
emotion in its own reply. ``user_reported``: the user said how *they* felt; the
holder stays the user and the record never becomes the companion's emotion.
``inferred``: significance inferred for the companion about a recorded shared
experience; it must reference that experience.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import math
import re
from typing import Any
import uuid

import sqlalchemy as sa

from chatbot.consolidation.lifecycle import scope_identity
from chatbot.db.models import (
    Companion,
    MemoryEvidence,
    MemoryRelation,
    Room,
    SemanticFact,
    Session,
    User,
)
from chatbot.memory import llm_extraction as _llm
from chatbot.memory.pgvector_store import PgvectorStore
from chatbot.memory.self_observation import _signal

ASSOCIATION_KIND = "emotional_association"
EXTRACTOR = "emotional_association_v1"

SOURCES = ("companion_expressed", "inferred", "user_reported")  # primary-source order
TARGET_KINDS = frozenset({"subject", "experience", "person", "event", "situation"})
# Only these matter enough to be durable; "ordinary" is chatter and never stored.
SALIENCE = {"significant": 0.7, "major": 0.95}
HALF_LIFE_DAYS = {"significant": 120.0, "major": 365.0}
DECAY_FLOOR = 0.4          # time alone never erases more than 60% of an association's weight

# --- bounds (constants, not configuration) ---------------------------------
MAX_OBSERVATIONS_PER_TURN = 3
MAX_SUBJECT_CHARS = 80
MAX_EMOTION_CHARS = 24
MAX_EMOTION_WORDS = 3
MAX_INTERPRETATION_CHARS = 200
MAX_EXCERPT_CHARS = 160
MAX_FACT_CHARS = 360
MAX_ACTIVE_PER_SCOPE = 40  # beyond this the weakest active association becomes historical
MAX_ROWS_PER_SCOPE = 200   # hard cap on all rows (active, superseded, historical) per scope
MAX_EVIDENCE_PER_ASSOCIATION = 12
MAX_SEEN_EVIDENCE = 16     # recent evidence ids kept for replay detection after the evidence cap
MAX_INTERPRETATION_HISTORY = 5
MAX_HISTORY_DEPTH = 20
MAX_CANDIDATES = 60        # rows examined by one reactivation lookup
MAX_RETRIEVED = 3
MAX_RETRIEVED_CEILING = 5
MAX_IMPLICIT_UNRESOLVED = 1
MAX_CONTEXT_CHARS = 700
MAX_LINE_CHARS = 220

CONTEXT_HEADER = (
    "EMOTIONAL ASSOCIATIONS (what these have meant to you before; unresolved items "
    "may inform current tone when relevant, even when unnamed, but need not become "
    "the topic or dictate a response):"
)

_EMOTION_RE = re.compile(r"^[a-z][a-z' -]*$")
_STOP = _llm._STOPWORDS


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AssociationObservation:
    """One validated observation from a single completed turn.

    ``role`` says whose words ground it (``user`` or ``assistant``). ``revises``
    is the key (see :func:`association_key`) of a known association this
    observation changes. ``unresolved`` is tri-state: ``None`` leaves an
    existing flag alone.
    """

    subject: str
    emotion: str
    interpretation: str
    source: str
    excerpt: str
    role: str = "assistant"
    target_kind: str = "subject"
    significance: str = "significant"
    experience_ref: str = ""
    unresolved: bool | None = None
    revises: str = ""
    status: str = "real"


@dataclass(frozen=True, slots=True)
class AssociationSource:
    """Where a batch of observations came from (one persisted turn)."""

    turn_id: str
    session_id: str
    source_at: datetime
    job_id: str = ""
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    in_roleplay: bool = False


@dataclass(slots=True)
class AssociationChange:
    """What one apply did; ``applied`` entries are ``{"op", "id", "key"}``."""

    applied: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (subject, reason)

    @property
    def changed(self) -> bool:
        return any(item["op"] != "replay" for item in self.applied)


@dataclass(frozen=True, slots=True)
class AssociationEvidence:
    evidence_id: str
    session_id: str | None
    turn_id: str
    source_message_id: str | None
    source_at: datetime
    excerpt: str
    emotion: str = ""
    interpretation: str = ""
    source: str = ""


@dataclass(frozen=True, slots=True)
class EmotionalAssociation:
    id: str
    key: str
    subject: str
    target_kind: str
    emotion: str
    interpretation: str
    source: str            # primary source: companion_expressed | inferred | user_reported
    holder: str            # companion | user
    significance: str
    salience: float
    status: str            # semantic_facts.lifecycle_status
    unresolved: bool
    reinforcement_count: int
    first_at: datetime
    last_at: datetime
    user_id: str
    companion_id: str | None
    room_id: str | None
    session_id: str | None
    turn_id: str | None
    source_message_id: str | None
    experience_ref: str | None
    weight: float = 0.0
    matched: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    revises_id: str | None = None
    superseded_by: str | None = None
    evidence_count: int = 0
    latest_excerpt: str = ""
    evidence: tuple[AssociationEvidence, ...] = ()
    revision_history: tuple[str, ...] = ()  # earlier association ids, newest first


# --------------------------------------------------------------------------
# Keys and small helpers (pure)
# --------------------------------------------------------------------------


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return _aware(value).isoformat()


def _stems(text: str) -> set[str]:
    return {_llm._stem(word) for word in _llm._words(_llm._normalize(text)) if word not in _STOP}


def subject_key(subject: str) -> str:
    """Stemmed content words of a subject, order kept (``Projects`` == ``project``)."""
    words = [_llm._stem(w) for w in _llm._words(_llm._normalize(subject)) if w not in _STOP]
    return " ".join(dict.fromkeys(words))[:64]


def emotion_key(emotion: str) -> str:
    return " ".join(str(emotion).casefold().split())[:MAX_EMOTION_CHARS]


def holder_of(source: str) -> str:
    """Whose emotion this is. A user's reported feeling is never the companion's."""
    return "user" if source == "user_reported" else "companion"


def association_key(subject: str, emotion: str, source: str) -> str:
    """Human-readable identity of an association within its scope."""
    return f"{subject_key(subject)}|{emotion_key(emotion)}|{holder_of(source)}"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _row_id(scope_key: str, companion_id: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"emotional-association-v1:{scope_key}:{companion_id}:{key}")


def _evidence_id(
    user_id: Any, companion_id: str, source: AssociationSource, key: str
) -> uuid.UUID:
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"emotional-association-evidence-v1:{user_id}:{companion_id}:{source.session_id}:{source.turn_id}:{key}",
    )


def effective_weight(
    *, salience: float, significance: str, reinforcement_count: int, last_at: datetime, now: datetime
) -> float:
    """Present weight in ``[0, 1]``: salience, softened by age (never below a floor), plus repetition."""
    half_life = HALF_LIFE_DAYS.get(significance, HALF_LIFE_DAYS["significant"])
    age_days = max(0.0, (_aware(now) - _aware(last_at)).total_seconds() / 86400.0)
    decay = 0.5 ** (age_days / half_life)
    base = salience * (DECAY_FLOOR + (1.0 - DECAY_FLOOR) * decay)
    return round(min(1.0, base + min(0.15, 0.05 * max(0, reinforcement_count - 1))), 4)


def known_associations_text(records: Sequence[EmotionalAssociation], *, limit: int = 12) -> str:
    """Lines an extraction prompt can show so a model can revise a known association by key."""
    lines = [
        f"- {record.key}: {record.subject} ({record.emotion}; {record.holder})"
        for record in records
        if record.status == "active"
    ][:limit]
    return "\n".join(lines)


def known_association_keys(records: Sequence[EmotionalAssociation]) -> set[str]:
    return {record.key for record in records if record.status == "active"}


# --------------------------------------------------------------------------
# Validation (pure; runs where the extraction response is handled)
# --------------------------------------------------------------------------


def validate_emotional_observations(
    raw_items: Sequence[Any],
    *,
    user_message: str,
    assistant_message: str,
    known_keys: Sequence[str] = (),
    known_experience_ids: Sequence[str] = (),
    limits: _llm.LLMExtractionLimits | None = None,
) -> tuple[list[AssociationObservation], list[_llm.RejectedCandidate]]:
    """Accepted observations and rejections. Adds no model call."""
    limits = limits or _llm.LLMExtractionLimits()
    user_sentences = _llm._sentence_spans(user_message)
    assistant_sentences = _llm._sentence_spans(assistant_message)
    user_text = " ".join(normalized for _, normalized in user_sentences)
    assistant_text = " ".join(normalized for _, normalized in assistant_sentences)
    turn_text = _llm._normalize(f"{user_message} {assistant_message}")
    turn_words = {_llm._stem(word) for word in _llm._words(turn_text)}
    turn_stems = _stems(turn_text)
    roleplay = bool(_llm._ROLEPLAY_FRAME.search(_llm._normalize_quotes(user_message)))
    keys, experiences = set(known_keys), set(known_experience_ids)

    accepted: list[tuple[float, AssociationObservation]] = []
    rejected: list[_llm.RejectedCandidate] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            continue
        label = _clean(raw.get("subject"))
        reason, observation = _validate_one(
            raw,
            user_sentences=user_sentences,
            assistant_sentences=assistant_sentences,
            user_text=user_text,
            assistant_text=assistant_text,
            turn_text=turn_text,
            turn_words=turn_words,
            turn_stems=turn_stems,
            roleplay=roleplay,
            keys=keys,
            experiences=experiences,
            limits=limits,
        )
        if observation is None:
            rejected.append(_llm.RejectedCandidate(label, reason or "invalid"))
            continue
        identity = association_key(observation.subject, observation.emotion, observation.source)
        if identity in seen:
            rejected.append(_llm.RejectedCandidate(label, "duplicate"))
            continue
        seen.add(identity)
        accepted.append((SALIENCE[observation.significance], observation))

    accepted.sort(key=lambda item: -item[0])  # stable: most significant first
    for _, observation in accepted[MAX_OBSERVATIONS_PER_TURN:]:
        rejected.append(_llm.RejectedCandidate(observation.subject, "over_observation_limit"))
    return [item for _, item in accepted[:MAX_OBSERVATIONS_PER_TURN]], rejected


def _validate_one(
    raw: Mapping[str, Any],
    *,
    user_sentences: list[tuple[str, str]],
    assistant_sentences: list[tuple[str, str]],
    user_text: str,
    assistant_text: str,
    turn_text: str,
    turn_words: set[str],
    turn_stems: set[str],
    roleplay: bool,
    keys: set[str],
    experiences: set[str],
    limits: _llm.LLMExtractionLimits,
) -> tuple[str | None, AssociationObservation | None]:
    status = str(raw.get("status") or "").strip().lower()
    if status != "real":
        return (f"status_{status}" if status in _llm.STATUSES else "missing_status"), None
    if roleplay:
        return "roleplay", None

    source = str(raw.get("source") or "").strip().lower()
    if source not in SOURCES:
        return "bad_source", None
    significance = str(raw.get("significance") or "").strip().lower()
    if significance == "ordinary":
        return "not_significant", None
    if significance not in SALIENCE:
        return "bad_significance", None

    subject = _clean(raw.get("subject"))
    if not subject_key(subject) or len(subject) > MAX_SUBJECT_CHARS:
        return "bad_subject", None
    emotion = emotion_key(_clean(raw.get("emotion")))
    if (
        len(emotion) < 3
        or len(emotion.split()) > MAX_EMOTION_WORDS
        or not _EMOTION_RE.match(emotion)
    ):
        return "bad_emotion", None
    interpretation = _clean(raw.get("interpretation") or raw.get("reason"))
    if not interpretation or len(interpretation) > MAX_INTERPRETATION_CHARS:
        return "bad_interpretation", None
    target_kind = str(raw.get("target_kind") or "subject").strip().lower()
    if target_kind not in TARGET_KINDS:
        return "bad_target_kind", None

    experience_ref = _clean(raw.get("experience_ref"))
    if experience_ref and experience_ref not in experiences:
        return "unknown_experience", None  # an experience the system never recorded is invented
    if source == "inferred" and not experience_ref:
        return "inferred_without_experience", None

    evidence = _clean(raw.get("evidence"))
    evidence_key = _llm._normalize(evidence).rstrip(" .!")
    if not evidence or len(evidence) > limits.max_evidence_chars or len(evidence_key) < 3:
        return "missing_evidence", None
    if source == "companion_expressed":
        roles = (("assistant", assistant_text, assistant_sentences),)
    elif source == "user_reported":
        roles = (("user", user_text, user_sentences),)
    else:
        roles = (
            ("user", user_text, user_sentences),
            ("assistant", assistant_text, assistant_sentences),
        )
    role, start, sentences = "", -1, []
    for name, text, spans in roles:
        start = text.find(evidence_key)
        if start >= 0:
            role, sentences = name, spans
            break
    if start < 0:
        # A user's words cannot ground the companion's own feeling, nor the reverse.
        return ("evidence_not_in_reply" if source == "companion_expressed" else "evidence_not_in_message"), None

    if _invented_details(f"{subject}. {interpretation}", turn_text, turn_words):
        return "unsupported_content", None
    subject_stems = _stems(subject)
    if not experience_ref and not subject_stems <= turn_stems:
        return "subject_not_in_turn", None
    signal = _signal(sentences, (start, start + len(evidence_key)), evidence_key)
    if signal:
        return signal, None

    revises = _clean(raw.get("revises"))
    unresolved = raw.get("unresolved")
    return None, AssociationObservation(
        subject=subject,
        emotion=emotion,
        interpretation=interpretation,
        source=source,
        excerpt=evidence[:MAX_EXCERPT_CHARS],
        role=role,
        target_kind=target_kind,
        significance=significance,
        experience_ref=experience_ref,
        unresolved=unresolved if isinstance(unresolved, bool) else None,
        revises=revises if revises in keys else "",
    )


def _invented_details(text: str, turn_text: str, turn_words: set[str]) -> bool:
    """True when the text names a number or a proper name the turn never mentioned.

    An interpretation is a paraphrase of why something matters, so it is not held
    to the shared-wording rule used for factual claims; it may not, however,
    smuggle in events, people or figures that the conversation did not contain.
    """
    if any(number not in turn_text for number in _llm._NUMBER.findall(text)):
        return True
    return any(
        _llm._stem(word.casefold()) not in turn_words
        for word in _llm._CAPITALISED.findall(text)
        if word not in _llm._NON_ENTITY_CAPITALS
    )


# --------------------------------------------------------------------------
# Scope (same derivation as the durable memory writer)
# --------------------------------------------------------------------------


def scope_for_session(session_row: Any, room: Any | None) -> dict[str, Any]:
    """The memory scope a session's durable memories are written under."""
    level = "companion" if session_row.companion_id else "user"
    if session_row.room_id:
        level = {"global": "user", "companion": "companion", "room": "room"}.get(
            room.memory_scope, "session"
        )
    if level == "companion" and not session_row.companion_id:
        level = "session"
    scope: dict[str, Any] = {"level": level, "user_id": str(session_row.user_id)}
    if level in {"companion", "room", "session"}:
        scope["companion_id"] = session_row.companion_id
    if level in {"room", "session"}:
        scope["room_id"] = session_row.room_id
    if level == "session":
        scope["session_id"] = str(session_row.id)
    return scope


# --------------------------------------------------------------------------
# Writing (the durable job's persistence transaction)
# --------------------------------------------------------------------------


def _assoc(fact: SemanticFact) -> dict[str, Any]:
    return dict((fact.extraction_metadata or {}).get("association") or {})


def _set_assoc(fact: SemanticFact, association: dict[str, Any], **top: Any) -> None:
    # JSON columns are not mutation-tracked: assign a new dict.
    fact.extraction_metadata = {**(fact.extraction_metadata or {}), **top, "association": association}


def _fact_text(obs: AssociationObservation, interpretation: str | None = None) -> str:
    holder = "The user said they felt" if holder_of(obs.source) == "user" else "Companion felt"
    text = f"{holder} {obs.emotion} about {obs.subject}: {interpretation or obs.interpretation}"
    return text[:MAX_FACT_CHARS]


def _last_at(fact: SemanticFact) -> datetime:
    return _aware(fact.last_reinforced_at or fact.created_at)


def _first_at(fact: SemanticFact) -> datetime:
    raw = _assoc(fact).get("first_at")
    try:
        return _aware(datetime.fromisoformat(str(raw)))
    except ValueError:
        return _aware(fact.created_at)


def _weight_of(fact: SemanticFact, now: datetime) -> float:
    data = _assoc(fact)
    weight = effective_weight(
        salience=float(data.get("salience") or SALIENCE["significant"]),
        significance=str(data.get("significance") or "significant"),
        reinforcement_count=fact.reinforcement_count,
        last_at=_last_at(fact),
        now=now,
    )
    return weight + (0.2 if data.get("unresolved") else 0.0)  # unfinished business is kept longest


def _primary_source(sources: Sequence[str]) -> str:
    return next((name for name in SOURCES if name in sources), SOURCES[0])


async def apply_emotional_associations(
    db: Any,
    *,
    user_id: Any,
    companion_id: str,
    session_id: Any,
    observations: Sequence[AssociationObservation],
    source: AssociationSource,
) -> AssociationChange:
    """Create, reinforce or revise associations inside the caller's transaction.

    The caller owns commit and rollback. Raises ``PermissionError`` if the
    session, companion or room does not belong to the user. Observations from a
    roleplay turn, a scene room or a non-durable room are refused (not errors).
    Replays are idempotent: evidence ids derive from the session, turn and
    association key, and an evidence id that was already applied changes nothing.
    """
    change = AssociationChange()
    if not observations:
        return change
    if source.in_roleplay:
        change.rejected = [(item.subject, "roleplay") for item in observations]
        return change
    owner = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    session_row = await db.get(Session, session_id)
    if (
        session_row is None
        or session_row.user_id != owner
        or str(session_row.companion_id or "") != str(companion_id)
    ):
        raise PermissionError("Emotional association session does not belong to this user/companion")
    companion = await db.get(Companion, companion_id)
    if companion is None or str(companion.user_id) != str(owner):
        raise PermissionError("Companion not found for this user")
    room = None
    if session_row.room_id:
        room = await db.get(Room, session_row.room_id)
        if room is None or str(room.user_id) != str(owner):
            raise PermissionError("Room not found for this user")
        if room.scene_id is not None or not room.persist_to_durable:
            change.rejected = [(item.subject, "room_not_durable") for item in observations]
            return change

    scope = scope_for_session(session_row, room)
    scope_key = scope_identity(owner, {"scope": scope})
    source_at = _aware(source.source_at)
    # The lock every memory write takes: concurrent jobs of this owner serialize here.
    await db.execute(sa.select(User.id).where(User.id == owner).with_for_update())
    rows = await _scope_rows(db, owner, scope_key, str(companion_id), lock=True)
    by_key = {_assoc(row).get("key"): row for row in rows}

    touched: list[SemanticFact] = []
    for observation in list(observations)[:MAX_OBSERVATIONS_PER_TURN]:
        reason = _unacceptable(observation)
        if reason:
            change.rejected.append((observation.subject, reason))
            continue
        key = association_key(observation.subject, observation.emotion, observation.source)
        evidence_id = _evidence_id(owner, str(companion_id), source, key)
        existing = by_key.get(key)
        if await db.get(MemoryEvidence, evidence_id) is not None or (
            existing is not None and str(evidence_id) in (_assoc(existing).get("seen") or [])
        ):
            change.applied.append(
                {"op": "replay", "id": str(existing.id) if existing else None, "key": key}
            )
            continue
        if existing is None:
            if len(rows) >= MAX_ROWS_PER_SCOPE:
                change.rejected.append((observation.subject, "scope_full"))
                continue
            fact = _create(
                owner, str(companion_id), session_row, scope, scope_key, key, observation, source, evidence_id
            )
            db.add(fact)
            rows.append(fact)
            by_key[key] = fact
            op = "create"
        else:
            fact = existing
            op = _reinforce(fact, observation, source, evidence_id)
        await db.flush()
        await _add_evidence(db, fact, observation, source, evidence_id, owner)
        if op == "create":
            await _revise_earlier(db, fact, observation, by_key, source_at, evidence_id)
        touched.append(fact)
        change.applied.append({"op": op, "id": str(fact.id), "key": key})

    await _supersede_for_late_revisions(db, rows, touched, source_at)
    _enforce_active_cap(rows, protect={fact.id for fact in touched}, now=source_at)
    await db.flush()
    return change


def _unacceptable(obs: AssociationObservation) -> str | None:
    """Defence in depth: the writer does not trust that validation ran."""
    if obs.status != "real":
        return "not_real"
    if obs.significance == "ordinary":
        return "not_significant"
    if obs.significance not in SALIENCE:
        return "bad_significance"
    if obs.source not in SOURCES or obs.target_kind not in TARGET_KINDS:
        return "invalid"
    if obs.source == "inferred" and not obs.experience_ref:
        return "inferred_without_experience"
    emotion = emotion_key(obs.emotion)
    if not subject_key(obs.subject) or len(_clean(obs.subject)) > MAX_SUBJECT_CHARS:
        return "bad_subject"
    if len(emotion) < 3 or not _EMOTION_RE.match(emotion) or len(emotion.split()) > MAX_EMOTION_WORDS:
        return "bad_emotion"
    if not _clean(obs.interpretation) or not _clean(obs.excerpt):
        return "invalid"
    return None


def _bounded(obs: AssociationObservation) -> AssociationObservation:
    return AssociationObservation(
        subject=_clean(obs.subject)[:MAX_SUBJECT_CHARS],
        emotion=emotion_key(obs.emotion),
        interpretation=_clean(obs.interpretation)[:MAX_INTERPRETATION_CHARS],
        source=obs.source,
        excerpt=_clean(obs.excerpt)[:MAX_EXCERPT_CHARS],
        role=obs.role,
        target_kind=obs.target_kind,
        significance=obs.significance,
        experience_ref=_clean(obs.experience_ref)[:64],
        unresolved=obs.unresolved,
        revises=_clean(obs.revises)[:160],
        status=obs.status,
    )


def _create(
    owner: uuid.UUID,
    companion_id: str,
    session_row: Any,
    scope: dict[str, Any],
    scope_key: str,
    key: str,
    raw_obs: AssociationObservation,
    source: AssociationSource,
    evidence_id: uuid.UUID,
) -> SemanticFact:
    obs = _bounded(raw_obs)
    source_at = _aware(source.source_at)
    message_id = source.user_message_id if obs.role == "user" else source.assistant_message_id
    metadata = {
        "kind": ASSOCIATION_KIND,
        "extractor": EXTRACTOR,
        "source_type": "inferred" if obs.source == "inferred" else "explicit",
        "user_id": str(owner),
        "companion_id": companion_id,
        "room_id": session_row.room_id,
        "session_id": str(source.session_id),
        "turn_id": str(source.turn_id),
        "source_message_id": message_id,
        "evidence": obs.excerpt,
        "scope": scope,
        "source_created_at": _iso(source_at),
        "association": {
            "key": key,
            "subject": obs.subject,
            "subject_key": subject_key(obs.subject),
            "target_kind": obs.target_kind,
            "emotion": obs.emotion,
            "interpretation": obs.interpretation,
            "interpreted_at": _iso(source_at),
            "sources": [obs.source],
            "holder": holder_of(obs.source),
            "significance": obs.significance,
            "salience": SALIENCE[obs.significance],
            "unresolved": bool(obs.unresolved),
            "experience_ref": obs.experience_ref or None,
            "first_at": _iso(source_at),
            "evidence_count": 0,
            "first_evidence_id": str(evidence_id),
            "seen": [],
            "revision_count": 0,
            "interpretation_history": [],
            "revises_key": obs.revises or None,
            "revises_id": None,
            "revised_by": None,
        },
    }
    return SemanticFact(
        id=_row_id(scope_key, companion_id, key),
        user_id=owner,
        fact_text=_fact_text(obs),
        source_message_id=uuid.UUID(message_id) if message_id else None,
        source_turn_id=_maybe_uuid(source.turn_id),
        source_type=metadata["source_type"],
        confidence=SALIENCE[obs.significance],
        extraction_metadata=metadata,
        lifecycle_status="active",
        scope_key=scope_key,
        assertion_key=_digest(f"{companion_id}:{key}"),
        reinforcement_count=1,
        last_reinforced_at=source_at,
        embedding=None,  # no embedding request: reactivation is lexical
    )


def _reinforce(
    fact: SemanticFact, raw_obs: AssociationObservation, source: AssociationSource, evidence_id: uuid.UUID
) -> str:
    obs = _bounded(raw_obs)
    source_at = _aware(source.source_at)
    data = _assoc(fact)
    fact.reinforcement_count += 1
    newest = source_at > _last_at(fact)
    if newest:
        fact.last_reinforced_at = source_at
    sources = list(data.get("sources") or [])
    if obs.source not in sources:
        sources.append(obs.source)
    data["sources"] = [name for name in SOURCES if name in sources]
    if SALIENCE[obs.significance] > float(data.get("salience") or 0):
        data["significance"], data["salience"] = obs.significance, SALIENCE[obs.significance]
        fact.confidence = max(fact.confidence, SALIENCE[obs.significance])
    if obs.experience_ref and not data.get("experience_ref"):
        data["experience_ref"] = obs.experience_ref

    # A late job may add support but cannot rewrite what a later observation concluded.
    interpreted_at = _aware(datetime.fromisoformat(str(data.get("interpreted_at") or _iso(source_at))))
    op = "reinforce"
    if source_at >= interpreted_at:
        if _stems(obs.interpretation) != _stems(str(data.get("interpretation") or "")):
            history = list(data.get("interpretation_history") or [])
            history.append(
                {
                    "interpretation": data.get("interpretation"),
                    "at": data.get("interpreted_at"),
                    "evidence_id": data.get("latest_evidence", {}).get("evidence_id"),
                }
            )
            data["interpretation_history"] = history[-MAX_INTERPRETATION_HISTORY:]
            data["interpretation"] = obs.interpretation
            data["revision_count"] = int(data.get("revision_count") or 0) + 1
            fact.fact_text = _fact_text(obs)
            op = "reinterpret"
        data["interpreted_at"] = _iso(source_at)
        if obs.unresolved is not None:
            data["unresolved"] = obs.unresolved
    # A superseded/historical association comes back only if the new evidence is later.
    if fact.lifecycle_status in {"superseded", "historical"}:
        ended = _aware(fact.superseded_at) if fact.superseded_at else _last_at(fact)
        if source_at > ended:
            fact.lifecycle_status = "active"
            fact.superseded_by, fact.superseded_at = None, None
            data["revised_by"] = None
            op = "revive"
    _set_assoc(fact, data)
    return op


async def _add_evidence(
    db: Any,
    fact: SemanticFact,
    raw_obs: AssociationObservation,
    source: AssociationSource,
    evidence_id: uuid.UUID,
    owner: uuid.UUID,
) -> None:
    obs = _bounded(raw_obs)
    source_at = _aware(source.source_at)
    data = _assoc(fact)
    message_id = source.user_message_id if obs.role == "user" else source.assistant_message_id
    seen = [*list(data.get("seen") or []), str(evidence_id)][-MAX_SEEN_EVIDENCE:]
    data["seen"] = seen
    if int(data.get("evidence_count") or 0) < MAX_EVIDENCE_PER_ASSOCIATION:
        provenance = {
            "kind": ASSOCIATION_KIND,
            "association_key": data.get("key"),
            "emotion": obs.emotion,
            "interpretation": obs.interpretation,
            "source": obs.source,
            "holder": holder_of(obs.source),
            "significance": obs.significance,
            "role": obs.role,
            "experience_ref": obs.experience_ref or None,
            "job_id": source.job_id or None,
            "companion_id": (fact.extraction_metadata or {}).get("companion_id"),
            "room_id": (fact.extraction_metadata or {}).get("room_id"),
        }
        db.add(
            MemoryEvidence(
                evidence_id=evidence_id,
                memory_id=fact.id,
                user_id=owner,
                source_message_id=uuid.UUID(message_id) if message_id else None,
                session_id=_maybe_uuid(source.session_id),
                turn_id=str(source.turn_id),
                assertion_text=fact.fact_text,
                evidence=obs.excerpt,
                provenance=provenance,
                source_at=source_at,
            )
        )
        data["evidence_count"] = int(data.get("evidence_count") or 0) + 1
    latest = data.get("latest_evidence") or {}
    if not latest or source_at >= _aware(datetime.fromisoformat(latest["at"])):
        data["latest_evidence"] = {
            "evidence_id": str(evidence_id),
            "session_id": str(source.session_id),
            "turn_id": str(source.turn_id),
            "message_id": message_id,
            "at": _iso(source_at),
            "excerpt": obs.excerpt,
        }
    _set_assoc(fact, data)
    await db.flush()


async def _revise_earlier(
    db: Any,
    new: SemanticFact,
    obs: AssociationObservation,
    by_key: dict[Any, SemanticFact],
    source_at: datetime,
    evidence_id: uuid.UUID,
) -> None:
    """A new association that names an earlier one it changes supersedes it (if it is earlier)."""
    revises = _clean(obs.revises)
    target = by_key.get(revises) if revises else None
    data = _assoc(new)
    if (
        target is None
        or target.id == new.id
        or target.lifecycle_status != "active"
        or _assoc(target).get("holder") != data.get("holder")
        or not source_at > _last_at(target)
    ):
        return
    await _link_supersession(db, new, target, source_at, evidence_id)


async def _link_supersession(
    db: Any, new: SemanticFact, old: SemanticFact, at: datetime, evidence_id: uuid.UUID
) -> None:
    old.lifecycle_status = "superseded"
    old.superseded_by, old.superseded_at = new.id, at
    old_data = _assoc(old)
    old_data["revised_by"] = str(new.id)
    _set_assoc(old, old_data)
    new_data = _assoc(new)
    new_data["revises_id"] = str(old.id)
    _set_assoc(new, new_data)
    relation_id = uuid.uuid5(uuid.NAMESPACE_URL, f"emotional-association-revision:{new.id}:{old.id}")
    if await db.get(MemoryRelation, relation_id) is None:  # the owner lock serializes writers
        db.add(
            MemoryRelation(
                id=relation_id,
                source_id=new.id,
                target_id=old.id,
                relation="supersedes",
                evidence_id=evidence_id,
            )
        )
    await db.flush()


async def _supersede_for_late_revisions(
    db: Any, rows: Sequence[SemanticFact], touched: Sequence[SemanticFact], source_at: datetime
) -> None:
    """Order independence: an earlier observation arriving after the one that revises it.

    If a row ``R`` names ``X`` as the association it revises and ``X`` was last
    supported before ``R`` first appeared, ``X`` is superseded by ``R`` however
    the jobs were ordered.
    """
    for target in touched:
        if target.lifecycle_status != "active":
            continue
        key = _assoc(target).get("key")
        revisers = sorted(
            (
                row
                for row in rows
                if row.id != target.id
                and _assoc(row).get("revises_key") == key
                and _assoc(row).get("holder") == _assoc(target).get("holder")
                and _first_at(row) > _last_at(target)
                and _assoc(row).get("revises_id") in (None, str(target.id))
            ),
            key=lambda row: (_first_at(row), str(row.id)),
        )
        if revisers:
            first = _assoc(revisers[0]).get("first_evidence_id")
            if first:
                await _link_supersession(
                    db, revisers[0], target, _first_at(revisers[0]), uuid.UUID(first)
                )


def _enforce_active_cap(rows: Sequence[SemanticFact], *, protect: set[Any], now: datetime) -> None:
    active = [row for row in rows if row.lifecycle_status == "active"]
    overflow = len(active) - MAX_ACTIVE_PER_SCOPE
    if overflow <= 0:
        return
    candidates = sorted(
        (row for row in active if row.id not in protect),
        key=lambda row: (_weight_of(row, now), _last_at(row), str(row.id)),
    )
    for row in candidates[:overflow]:
        row.lifecycle_status = "historical"  # kept with its evidence; no longer presented
        data = _assoc(row)
        data["demoted"] = "capacity"
        _set_assoc(row, data)


def _maybe_uuid(value: Any) -> uuid.UUID | None:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


async def _scope_rows(
    db: Any, owner: uuid.UUID, scope_key: str, companion_id: str, *, lock: bool
) -> list[SemanticFact]:
    stmt = (
        sa.select(SemanticFact)
        .where(
            SemanticFact.user_id == owner,
            SemanticFact.scope_key == scope_key,
            SemanticFact.extraction_metadata["kind"].as_string() == ASSOCIATION_KIND,
            SemanticFact.extraction_metadata["companion_id"].as_string() == companion_id,
        )
        .order_by(SemanticFact.id)
        .limit(MAX_ROWS_PER_SCOPE + 1)
    )
    if lock:
        stmt = stmt.with_for_update()
    return list((await db.scalars(stmt)).all())


# --------------------------------------------------------------------------
# Reading and reactivation
# --------------------------------------------------------------------------


async def _session_companion(db: Any, session_id: Any) -> str | None:
    wanted = session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))
    row = await db.get(Session, wanted)
    return row.companion_id if row is not None else None


async def _visible_rows(
    db: Any,
    *,
    session_id: Any,
    states: Sequence[str],
    limit: int,
    association_id: Any = None,
) -> list[SemanticFact]:
    companion_id = await _session_companion(db, session_id)
    if not companion_id:
        return []  # associations belong to a companion
    stmt = sa.select(SemanticFact).where(
        SemanticFact.extraction_metadata["kind"].as_string() == ASSOCIATION_KIND,
        SemanticFact.extraction_metadata["companion_id"].as_string() == companion_id,
        SemanticFact.lifecycle_status.in_(list(states)),
        SemanticFact.confidence > 0,
    )
    if association_id is not None:
        wanted = association_id if isinstance(association_id, uuid.UUID) else uuid.UUID(str(association_id))
        stmt = stmt.where(SemanticFact.id == wanted)
    stmt = PgvectorStore(db)._apply_owner_session_filter(stmt, session_id)
    return list(
        (
            await db.scalars(
                stmt.order_by(SemanticFact.last_reinforced_at.desc(), SemanticFact.id).limit(limit)
            )
        ).all()
    )


async def retrieve_emotional_associations(
    db: Any,
    *,
    session_id: Any,
    message: str,
    experience_ids: Sequence[str] = (),
    thread_subjects: Sequence[str] = (),
    limit: int = MAX_RETRIEVED,
    include_unresolved: bool = False,
    max_unresolved: int = MAX_IMPLICIT_UNRESOLVED,
    now: datetime | None = None,
) -> list[EmotionalAssociation]:
    """Associations relevant to this moment, most relevant first (at most ``limit``).

    One bounded query (at most ``MAX_CANDIDATES`` rows), no embedding, no model
    call. An association is relevant when its subject's words appear in the
    message, when the shared experience it is tied to is being recalled
    (``experience_ids``), or when it is unfinished and one of the companion's
    open thread subjects (``thread_subjects``) concerns it. When requested,
    one recent unresolved association may also surface without a lexical match;
    this implicit reminder expires after two significance half-lives. Direct
    message, experience and thread matches remain available for older history.
    Visibility is the
    session's owner/companion/room/session scope, so nothing crosses users,
    companions or rooms. Nothing relevant means an empty list.
    """
    limit = max(0, min(int(limit), MAX_RETRIEVED_CEILING))
    if not limit:
        return []
    now = _aware(now or datetime.now(timezone.utc))
    rows = await _visible_rows(db, session_id=session_id, states=["active"], limit=MAX_CANDIDATES)
    message_stems = _stems(message or "")
    thread_stems = [_stems(subject) for subject in thread_subjects]
    experiences = {str(item) for item in experience_ids}
    max_unresolved = max(0, min(int(max_unresolved), MAX_IMPLICIT_UNRESOLVED))

    scored: list[tuple[float, str, EmotionalAssociation, bool]] = []
    for row in rows:
        data = _assoc(row)
        subject_stems = _stems(str(data.get("subject") or ""))
        reasons: list[str] = []
        strength = 0.0
        matched: set[str] = set()
        if subject_stems:
            overlap = subject_stems & message_stems
            if len(overlap) >= math.ceil(len(subject_stems) / 2):
                reasons.append("subject")
                strength = max(strength, len(overlap) / len(subject_stems))
                matched |= overlap
        if data.get("experience_ref") and str(data["experience_ref"]) in experiences:
            reasons.append("experience")
            strength = max(strength, 1.0)
        if data.get("unresolved") and subject_stems:
            for stems in thread_stems:
                overlap = subject_stems & stems
                if stems and len(overlap) >= math.ceil(len(subject_stems) / 2):
                    reasons.append("unfinished")
                    strength = max(strength, 0.8)
                    matched |= overlap
                    break
        implicit_unresolved = False
        if not reasons and include_unresolved and max_unresolved and data.get("unresolved"):
            age_days = max(0.0, (now - _last_at(row)).total_seconds() / 86400.0)
            significance = str(data.get("significance") or "significant")
            if age_days <= 2.0 * HALF_LIFE_DAYS.get(significance, HALF_LIFE_DAYS["significant"]):
                reasons.append("unfinished")
                strength = 0.35
                implicit_unresolved = True
        if not reasons:
            continue
        record = _record(row, now=now, matched=tuple(sorted(matched)), reasons=tuple(reasons))
        relevance = 0.3 * strength + 0.5 * record.weight + (0.2 if implicit_unresolved else 0.2 * strength)
        scored.append((relevance, str(row.id), record, implicit_unresolved))
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected: list[EmotionalAssociation] = []
    implicit_count = 0
    for _, _, record, implicit in scored:
        if implicit and implicit_count >= max_unresolved:
            continue
        selected.append(record)
        implicit_count += int(implicit)
        if len(selected) >= limit:
            break
    return selected


def render_associations(
    records: Sequence[EmotionalAssociation], *, max_chars: int = MAX_CONTEXT_CHARS
) -> str:
    """Compact, bounded prompt text. Empty when there is nothing to say.

    Holder is explicit: a user's reported feeling is labelled as theirs, and an
    inferred significance as inferred. The text states what the association is;
    it never says how to react.
    """
    lines: list[str] = []
    for record in records:
        when = record.last_at.date().isoformat()
        count = f"{record.reinforcement_count}x, " if record.reinforcement_count > 1 else ""
        if record.holder == "user":
            line = (
                f"- {record.subject}: the user said they felt {record.emotion} — "
                f"{record.interpretation} (the user's feeling, not yours; {count}last {when})"
            )
        else:
            origin = "inferred" if record.source == "inferred" else "your own"
            line = (
                f"- {record.subject}: you associate {record.emotion} — {record.interpretation} "
                f"({origin}; {count}last {when}{'; unresolved' if record.unresolved else ''})"
            )
        lines.append(line[:MAX_LINE_CHARS])
    kept: list[str] = []
    for line in lines:
        candidate = "\n".join([CONTEXT_HEADER, *kept, line])
        if len(candidate) > max_chars:
            break
        kept.append(line)
    return "\n".join([CONTEXT_HEADER, *kept]) if kept else ""


async def list_associations(
    db: Any, *, session_id: Any, include_history: bool = False, limit: int = 50, now: datetime | None = None
) -> list[EmotionalAssociation]:
    """Visible associations, strongest first; ``include_history`` adds superseded and historical."""
    now = _aware(now or datetime.now(timezone.utc))
    states = ["active", "superseded", "historical"] if include_history else ["active"]
    rows = await _visible_rows(db, session_id=session_id, states=states, limit=max(1, limit))
    records = [_record(row, now=now) for row in rows]
    return sorted(records, key=lambda record: (-record.weight, record.id))


async def get_association(
    db: Any, *, session_id: Any, association_id: Any, now: datetime | None = None
) -> EmotionalAssociation | None:
    """One visible association with all retained evidence and earlier versions, or ``None``."""
    now = _aware(now or datetime.now(timezone.utc))
    rows = await _visible_rows(
        db,
        session_id=session_id,
        states=["active", "superseded", "historical"],
        limit=1,
        association_id=association_id,
    )
    if not rows:
        return None
    fact = rows[0]
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
    evidence = tuple(
        AssociationEvidence(
            evidence_id=str(row.evidence_id),
            session_id=str(row.session_id) if row.session_id else None,
            turn_id=row.turn_id,
            source_message_id=str(row.source_message_id) if row.source_message_id else None,
            source_at=_aware(row.source_at),
            excerpt=row.evidence,
            emotion=str((row.provenance or {}).get("emotion") or ""),
            interpretation=str((row.provenance or {}).get("interpretation") or ""),
            source=str((row.provenance or {}).get("source") or ""),
        )
        for row in evidence_rows
    )
    return _record(fact, now=now, evidence=evidence, history=tuple(history))


def _record(
    fact: SemanticFact,
    *,
    now: datetime,
    matched: tuple[str, ...] = (),
    reasons: tuple[str, ...] = (),
    evidence: tuple[AssociationEvidence, ...] = (),
    history: tuple[str, ...] = (),
) -> EmotionalAssociation:
    meta = fact.extraction_metadata or {}
    data = _assoc(fact)
    sources = list(data.get("sources") or [])
    latest = data.get("latest_evidence") or {}
    return EmotionalAssociation(
        id=str(fact.id),
        key=str(data.get("key") or ""),
        subject=str(data.get("subject") or ""),
        target_kind=str(data.get("target_kind") or "subject"),
        emotion=str(data.get("emotion") or ""),
        interpretation=str(data.get("interpretation") or ""),
        source=_primary_source(sources),
        holder=str(data.get("holder") or "companion"),
        significance=str(data.get("significance") or ""),
        salience=float(data.get("salience") or 0.0),
        status=fact.lifecycle_status,
        unresolved=bool(data.get("unresolved")),
        reinforcement_count=fact.reinforcement_count,
        first_at=_first_at(fact),
        last_at=_last_at(fact),
        user_id=str(fact.user_id),
        companion_id=meta.get("companion_id"),
        room_id=meta.get("room_id"),
        session_id=meta.get("session_id"),
        turn_id=meta.get("turn_id"),
        source_message_id=meta.get("source_message_id"),
        experience_ref=data.get("experience_ref"),
        weight=_weight_of(fact, now),
        matched=matched,
        reasons=reasons,
        revises_id=data.get("revises_id"),
        superseded_by=str(fact.superseded_by) if fact.superseded_by else None,
        evidence_count=int(data.get("evidence_count") or 0),
        latest_excerpt=str(latest.get("excerpt") or ""),
        evidence=evidence,
        revision_history=history,
    )


__all__ = [
    "ASSOCIATION_KIND",
    "AssociationChange",
    "AssociationEvidence",
    "AssociationObservation",
    "AssociationSource",
    "EmotionalAssociation",
    "MAX_IMPLICIT_UNRESOLVED",
    "apply_emotional_associations",
    "association_key",
    "effective_weight",
    "get_association",
    "known_association_keys",
    "known_associations_text",
    "list_associations",
    "render_associations",
    "retrieve_emotional_associations",
    "scope_for_session",
    "validate_emotional_observations",
]
