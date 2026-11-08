"""Persistent companion self-model: evidence-gated opinions, preferences, interests.

The self-model is one bounded JSON document on the companion's existing durable
identity row (``companion_identities.self_model``). It records what the
companion has *come to think and like through its own experiences*, each item
with the evidence behind it. The companion's configuration (persona, sliders,
favourite topics, values) is the anchor and is never written here; there are
no stages and no automatic numeric trait changes (deferred).

Two layers, deliberately separate:

* ``next_self_model`` is a pure, deterministic accumulator: current document +
  validated observations + evidence source -> next document and a change report.
  All thresholds, caps and inertia live here.
* ``apply_self_observations`` is the one database operation: it verifies
  ownership, locks the identity row, applies the accumulator and lets the ORM
  version check reject any stale write. It never commits; the caller (the
  durable job's effects transaction, in Phase 3.3) owns the transaction.

Inertia comes from evidence rules, not from decay or drift:

* an explicitly stated, *reasoned* opinion is established at once, but weakly;
* an unreasoned opinion, a preference, an interest or a self-understanding
  needs repeated evidence, across sessions, before it counts as held;
* a held opinion changes only through a reasoned change of mind, or through
  reasoned opposing evidence from several sessions that outweighs its support;
  the earlier position is kept in the item's history;
* one session can contribute at most two weight to an item, so one long
  conversation cannot establish or overturn anything alone;
* chatter, roleplay, fiction, jokes and hypotheticals never count.

Time uses the evidence's ``source_at``, never processing time, so jobs that
finish out of order give the same result and a late job cannot overturn a
newer position. Replaying the same observations changes nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
from typing import Any
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from chatbot.db.models import Companion, CompanionIdentityModel

SCHEMA_VERSION = 1

KINDS = ("opinion", "preference", "interest", "self_understanding")
REVISABLE_KINDS = frozenset({"opinion", "preference", "self_understanding"})
WEIGHTS = {"ordinary": 1, "significant": 2}
RELATIONS = frozenset({"consistent", "opposing"})

# Bounds (constants, not configuration).
MAX_ITEMS = {"opinion": 24, "preference": 16, "interest": 16, "self_understanding": 8}
MAX_EVIDENCE = 8
MAX_HISTORY = 5
MAX_SESSIONS = 10
MAX_LOG = 50
MAX_APPLIED = 128
MAX_DOCUMENT_BYTES = 64 * 1024
MAX_STATEMENT_CHARS = 240
MAX_EXCERPT_CHARS = 160
MAX_SUBJECT_KEY_CHARS = 64

SESSION_WEIGHT_CAP = 2  # weight one session may add to one item (support or opposition)
FIRST_STRENGTH = 0.4
STRENGTH_STEP = 0.1
MAX_STRENGTH = 0.9
MIN_SESSIONS = 2
# Support needed (with MIN_SESSIONS sessions, except self-understanding) to hold an
# item that was not stated with its own reason.
HOLD_SUPPORT = {"opinion": 2, "preference": 3, "interest": 3, "self_understanding": 2}

PROMPT_LIMITS = {"opinion": 6, "preference": 4, "interest": 4, "self_understanding": 3}
MAX_PROMPT_CHARS = 900
MAX_KNOWN_ITEM_CHARS = 1_200


class SelfModelTooLarge(ValueError):
    """The updated document would exceed its bound; nothing is truncated silently."""


@dataclass(frozen=True, slots=True)
class SelfObservation:
    """One validated observation about the companion's own views, from its own words.

    ``excerpt`` is the companion's own text (never the user's) and is stored as
    evidence. ``relation`` is relative to an existing item with the same
    kind and subject: ``consistent`` supports it, ``opposing`` challenges it.
    ``change_of_mind`` means the companion explicitly says it now thinks
    differently.
    """

    kind: str
    subject: str
    statement: str
    excerpt: str
    reason: str = ""
    significance: str = "ordinary"
    status: str = "real"
    relation: str = "consistent"
    change_of_mind: bool = False


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    """Where a batch of observations came from (one persisted turn / job)."""

    job_id: str
    turn_id: str
    session_id: str
    source_at: datetime
    message_id: str | None = None
    in_roleplay: bool = False


@dataclass(slots=True)
class SelfModelChange:
    applied: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (item key or subject, reason)
    version: int | None = None  # identity row version after a write

    @property
    def changed(self) -> bool:
        return bool(self.applied)


# --------------------------------------------------------------------------
# Pure accumulator
# --------------------------------------------------------------------------


def empty_self_model() -> dict[str, Any]:
    return {"schema": SCHEMA_VERSION, "items": {}, "log": [], "applied": []}


def next_self_model(
    document: Mapping[str, Any] | None,
    observations: Sequence[SelfObservation],
    source: EvidenceSource,
    *,
    anchors: Sequence[str] = (),
) -> tuple[dict[str, Any], SelfModelChange]:
    """Apply *observations* to *document*; the input is never mutated.

    *anchors* are configured subjects (favourite topics) that already define the
    companion: they never become developed interests.
    """
    doc = _normalized(document)
    change = SelfModelChange()
    anchor_keys = {_subject_key(anchor) for anchor in anchors} - {""}
    source_at = _aware(source.source_at)

    for index, obs in enumerate(observations):
        evidence_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"self-model-evidence:{source.job_id}:{index}")
        )
        label = f"{obs.kind}:{_subject_key(obs.subject)}"
        if evidence_id in doc["applied"]:
            change.rejected.append((label, "already_applied"))
            continue
        reason = _rejection(obs, source, anchor_keys)
        if reason is None:
            reason = _apply_one(doc, obs, source, source_at, evidence_id, change)
        if reason is not None:
            change.rejected.append((label, reason))
        else:
            doc["applied"] = (doc["applied"] + [evidence_id])[-MAX_APPLIED:]

    if change.changed:
        _check_size(doc)
    return (doc if change.changed else _normalized(document)), change


def _rejection(obs: SelfObservation, source: EvidenceSource, anchors: set[str]) -> str | None:
    if obs.kind not in KINDS:
        return "bad_kind"
    key = _subject_key(obs.subject)
    if not key:
        return "bad_subject"
    statement = _clean(obs.statement)
    if not statement or len(statement) > MAX_STATEMENT_CHARS:
        return "bad_statement"
    if len(_clean(obs.reason)) > MAX_STATEMENT_CHARS:
        return "bad_reason"
    if obs.status != "real":
        return f"status_{obs.status}"
    if source.in_roleplay:
        return "roleplay"
    if obs.significance not in WEIGHTS:
        return "bad_significance"
    if obs.relation not in RELATIONS or (obs.change_of_mind and obs.relation != "opposing"):
        return "bad_relation"
    excerpt = _clean(obs.excerpt)
    if not excerpt:
        return "missing_evidence"
    if _NON_REAL.search(excerpt):
        return "roleplay_or_fiction"
    if obs.kind == "interest" and key in anchors:
        return "configured_anchor"
    return None


def _apply_one(
    doc: dict[str, Any],
    obs: SelfObservation,
    source: EvidenceSource,
    source_at: datetime,
    evidence_id: str,
    change: SelfModelChange,
) -> str | None:
    key = f"{obs.kind}:{_subject_key(obs.subject)}"
    items: dict[str, Any] = doc["items"]
    item = items.get(key)
    session = str(source.session_id)
    reason = _clean(obs.reason)
    statement = _clean(obs.statement)
    ref = {
        "id": evidence_id,
        "job_id": str(source.job_id),
        "turn_id": str(source.turn_id),
        "session_id": session,
        "message_id": str(source.message_id) if source.message_id else None,
        "source_at": source_at.isoformat(),
        "weight": WEIGHTS[obs.significance],
        "excerpt": _clean(obs.excerpt)[:MAX_EXCERPT_CHARS],
    }

    if item is None:
        if obs.relation == "opposing":
            return "no_such_item"
        if not _make_room(items, obs.kind):
            return "kind_full"
        weight = min(WEIGHTS[obs.significance], SESSION_WEIGHT_CAP)
        reasoned = obs.kind == "opinion" and bool(reason)
        item = {
            "kind": obs.kind,
            "subject": _clean(obs.subject)[:MAX_SUBJECT_KEY_CHARS],
            "statement": statement,
            "reason": reason,
            "status": "held" if reasoned else "tentative",
            "strength": FIRST_STRENGTH,
            "support": weight,
            "opposition": 0,
            "sessions": {session: weight},
            "opposition_sessions": {},
            "challenge": None,
            "first_at": source_at.isoformat(),
            "updated_at": source_at.isoformat(),
            "evidence": [ref],
            "history": [],
        }
        items[key] = item
        _promote(item)
        _log(doc, source_at, "held" if item["status"] == "held" else "formed", key, [evidence_id])
        change.applied.append({"key": key, "change": "formed", "status": item["status"]})
        return None

    used = item["sessions"].get(session, 0) + item["opposition_sessions"].get(session, 0)
    weight = min(WEIGHTS[obs.significance], SESSION_WEIGHT_CAP - used)
    if weight <= 0:
        return "session_cap"
    ref["weight"] = weight
    updated = _parse(item["updated_at"])
    late = source_at < updated  # may reinforce or oppose, never overturn

    if obs.relation == "consistent":
        first_in_session = session not in item["sessions"]
        item["support"] += weight
        item["sessions"][session] = item["sessions"].get(session, 0) + weight
        if first_in_session:
            item["strength"] = round(min(MAX_STRENGTH, item["strength"] + STRENGTH_STEP), 2)
        if reason and not item["reason"]:
            item["reason"] = reason
        _append_evidence(item, ref)
        _touch(item, source_at)
        was = item["status"]
        _promote(item)
        if was != item["status"]:
            _log(doc, source_at, "held", key, [evidence_id])
        change.applied.append({"key": key, "change": "reinforced", "status": item["status"]})
        return None

    # --- opposing evidence ---
    if obs.kind not in REVISABLE_KINDS:
        return "not_revisable"
    if not reason:
        return "unreasoned_opposition"  # insistence without the companion's own reasons
    differs = _clean(reason).casefold() != _clean(item["reason"]).casefold()
    if obs.change_of_mind and differs and not late:
        _revise(doc, key, item, statement, reason, [ref], {session: weight}, weight, source_at)
        change.applied.append({"key": key, "change": "revised", "status": item["status"]})
        return None

    item["opposition"] += weight
    item["opposition_sessions"][session] = item["opposition_sessions"].get(session, 0) + weight
    challenge = item.get("challenge") or {"evidence": []}
    if not late or not challenge.get("statement"):
        challenge["statement"], challenge["reason"] = statement, reason
    challenge["evidence"] = _trim([*challenge["evidence"], ref])
    item["challenge"] = challenge
    if (
        not late
        and item["opposition"] >= max(2, item["support"])
        and len(item["opposition_sessions"]) >= MIN_SESSIONS
    ):
        _revise(
            doc, key, item, challenge["statement"], challenge["reason"],
            challenge["evidence"], dict(item["opposition_sessions"]), item["opposition"], source_at,
        )
        change.applied.append({"key": key, "change": "revised", "status": item["status"]})
        return None
    _touch(item, source_at)
    change.applied.append({"key": key, "change": "challenged", "status": item["status"]})
    return None


def _revise(
    doc: dict[str, Any],
    key: str,
    item: dict[str, Any],
    statement: str,
    reason: str,
    evidence: list[dict[str, Any]],
    sessions: dict[str, int],
    support: int,
    source_at: datetime,
) -> None:
    item["history"] = [
        {
            "statement": item["statement"],
            "reason": item["reason"],
            "until": source_at.isoformat(),
            "evidence_ids": [ref["id"] for ref in item["evidence"]][-MAX_EVIDENCE:],
        },
        *item["history"],
    ][:MAX_HISTORY]
    item.update(
        statement=statement,
        reason=reason,
        status="held",
        strength=FIRST_STRENGTH,
        support=support,
        opposition=0,
        sessions=dict(list(sessions.items())[-MAX_SESSIONS:]),
        opposition_sessions={},
        challenge=None,
        evidence=_trim(list(evidence)),
    )
    _touch(item, source_at)
    _log(doc, source_at, "revised", key, [ref["id"] for ref in evidence])


def _promote(item: dict[str, Any]) -> None:
    if item["status"] == "held":
        return
    enough_sessions = (
        item["kind"] == "self_understanding" or len(item["sessions"]) >= MIN_SESSIONS
    )
    if item["support"] >= HOLD_SUPPORT[item["kind"]] and enough_sessions:
        item["status"] = "held"


def _make_room(items: dict[str, Any], kind: str) -> bool:
    of_kind = [key for key, item in items.items() if item["kind"] == kind]
    if len(of_kind) < MAX_ITEMS[kind]:
        return True
    tentative = [key for key in of_kind if items[key]["status"] == "tentative"]
    if not tentative:
        return False  # held items are never evicted
    del items[min(tentative, key=lambda k: (items[k]["updated_at"], k))]
    return True


def _append_evidence(item: dict[str, Any], ref: dict[str, Any]) -> None:
    item["evidence"] = _trim([*item["evidence"], ref])


def _trim(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return refs if len(refs) <= MAX_EVIDENCE else [refs[0], *refs[-(MAX_EVIDENCE - 1):]]


def _touch(item: dict[str, Any], source_at: datetime) -> None:
    item["updated_at"] = max(source_at, _parse(item["updated_at"])).isoformat()
    sessions = item["sessions"]
    while len(sessions) > MAX_SESSIONS:
        sessions.pop(next(iter(sessions)))
    while len(item["opposition_sessions"]) > MAX_SESSIONS:
        item["opposition_sessions"].pop(next(iter(item["opposition_sessions"])))


def _log(doc: dict[str, Any], at: datetime, kind: str, key: str, evidence_ids: list[str]) -> None:
    doc["log"] = [
        *doc["log"],
        {"at": at.isoformat(), "change": kind, "key": key, "evidence_ids": evidence_ids[:MAX_EVIDENCE]},
    ][-MAX_LOG:]


def _check_size(doc: Mapping[str, Any]) -> None:
    if len(json.dumps(doc, ensure_ascii=False).encode()) > MAX_DOCUMENT_BYTES:
        raise SelfModelTooLarge("companion self-model exceeds its size bound")


def _normalized(document: Mapping[str, Any] | None) -> dict[str, Any]:
    doc = deepcopy(dict(document or {}))
    base = empty_self_model()
    for name, default in base.items():
        doc.setdefault(name, default)
    return doc


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------

_STOP = frozenset("a an the of to in on at for with and or is are my about".split())
_NON_REAL = re.compile(
    r"\*[^*\n]*\s[^*\n]*\*|\blet'?s (?:pretend|roleplay|role-play|imagine)\b|\bin character\b|"
    r"\b(?:roleplay|role-play)\b|\bin (?:my|a|the|this|our) (?:story|novel|dream|game|scene|script)\b|"
    r"\b(?:just kidding|jk|joking)\b|^if\b|\bif (?:i|we) (?:were|had|could)\b|\bimagine\b|"
    r"\bsuppose\b|\bwhat if\b|\bhypothetical",
    re.IGNORECASE,
)


def _subject_key(subject: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", str(subject).casefold()) if w not in _STOP]
    return " ".join(words)[:MAX_SUBJECT_KEY_CHARS]


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse(value: str) -> datetime:
    return _aware(datetime.fromisoformat(value))


# --------------------------------------------------------------------------
# Reading for prompts and for reflection (pure)
# --------------------------------------------------------------------------


def held_items(document: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Held items only, deterministic order: kind, strength (high first), recency, key."""
    items = (document or {}).get("items") or {}
    order = {kind: index for index, kind in enumerate(KINDS)}
    chosen = [
        {"key": key, **item} for key, item in items.items() if item.get("status") == "held"
    ]
    return sorted(
        chosen,
        key=lambda i: (order.get(i["kind"], 99), -float(i["strength"]), _neg(i["updated_at"]), i["key"]),
    )


def developed_self_text(document: Mapping[str, Any] | None) -> str | None:
    """The prompt block for developed views, or ``None`` when nothing is held.

    Tentative items, evidence, history and the log never appear. Bounded in
    items per kind and in total characters; the output depends only on the
    document, so equal state gives an identical prompt.
    """
    counts = dict.fromkeys(KINDS, 0)
    labels = {
        "opinion": "Opinion", "preference": "Preference", "interest": "Interest",
        "self_understanding": "About yourself",
    }
    lines: list[str] = []
    for item in held_items(document):
        if counts[item["kind"]] >= PROMPT_LIMITS[item["kind"]]:
            continue
        line = f"- {labels[item['kind']]} — {item['subject']}: {item['statement']}"
        if item.get("reason"):
            line += f" (because {item['reason']})"
        lines.append(line)
        counts[item["kind"]] += 1
    if not lines:
        return None
    header = (
        "Views and interests you have developed through your own experiences. Your configured "
        "identity above takes precedence; you may still change your mind for good reasons."
    )
    text, size = [header], len(header)
    for line in lines:
        if size + len(line) + 1 > MAX_PROMPT_CHARS:
            break
        text.append(line)
        size += len(line) + 1
    return "\n".join(text) if len(text) > 1 else None


def known_items_text(document: Mapping[str, Any] | None) -> str:
    """``key: statement`` lines (held and tentative) to show an extractor, bounded."""
    lines, size = [], 0
    for key, item in sorted(((document or {}).get("items") or {}).items()):
        line = f"{key}: {item.get('statement', '')}"
        if size + len(line) + 1 > MAX_KNOWN_ITEM_CHARS:
            break
        lines.append(line)
        size += len(line) + 1
    return "\n".join(lines)


def _neg(iso: str) -> float:
    return -_parse(iso).timestamp()


# --------------------------------------------------------------------------
# Database operations
# --------------------------------------------------------------------------


async def load_self_model(db: Any, *, companion_id: str, user_id: Any) -> dict[str, Any]:
    """The owned companion's self-model document (``{}``-normalized), without locking."""
    await _owned_companion(db, companion_id, user_id)
    identity = await db.get(CompanionIdentityModel, companion_id)
    return _normalized(identity.self_model if identity is not None else None)


async def apply_self_observations(
    db: Any,
    *,
    companion_id: str,
    user_id: Any,
    observations: Sequence[SelfObservation],
    source: EvidenceSource,
) -> SelfModelChange:
    """Apply validated observations to an owned companion's self-model.

    Runs in the caller's transaction and never commits (the durable job's effects
    transaction owns it, so a rollback undoes the self-model together with the
    rest). The identity row is locked ``FOR UPDATE`` and re-read, so concurrent
    jobs for one companion serialize and none overwrites another; the row's
    ``version`` column additionally rejects a stale write with
    ``StaleDataError``, which the job's retry handles. Nothing is written when
    no observation qualified. Raises ``PermissionError`` for a companion the user
    does not own.
    """
    companion = await _owned_companion(db, companion_id, user_id)
    identity = await _locked_identity(db, companion_id)
    document, change = next_self_model(
        identity.self_model,
        observations,
        source,
        anchors=list(companion.favorite_topics or []),
    )
    if change.changed:
        identity.self_model = document
        await db.flush()
        change.version = identity.version
    return change


async def _owned_companion(db: Any, companion_id: str, user_id: Any) -> Companion:
    companion = await db.get(Companion, companion_id)
    if companion is None or str(companion.user_id) != str(user_id):
        raise PermissionError("Companion not found for this user")
    return companion


async def _locked_identity(db: Any, companion_id: str) -> CompanionIdentityModel:
    query = (
        sa.select(CompanionIdentityModel)
        .where(CompanionIdentityModel.companion_id == companion_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    identity = await db.scalar(query)
    if identity is None:  # companion created before durable identity rows existed
        await db.execute(
            insert(CompanionIdentityModel)
            .values(companion_id=companion_id, trait_adjustments={}, self_model={}, version=1)
            .on_conflict_do_nothing(index_elements=["companion_id"])
        )
        identity = await db.scalar(query)
    if identity is None:
        raise LookupError("Companion identity row could not be created")
    return identity


__all__ = [
    "EvidenceSource",
    "SelfModelChange",
    "SelfModelTooLarge",
    "SelfObservation",
    "apply_self_observations",
    "developed_self_text",
    "empty_self_model",
    "held_items",
    "known_items_text",
    "load_self_model",
    "next_self_model",
]
