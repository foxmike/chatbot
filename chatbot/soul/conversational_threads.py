"""Persistent conversational threads: unfinished business between companion and user.

Phase 4.3a storage and reconciliation for P05/P09/P16: a companion remembers
what it wants to discuss or revisit with its user in later conversations — a
project discussed but not completed, an unanswered question, a topic it wanted
to explore, a meaningful event with an expected outcome, an explicit user
request, or a bounded goal.

Storage is the ``conversational_threads`` table (one row per thread, scoped to
``(user_id, companion_id, room_id)``). Private threads (``room_id`` NULL) follow
the companion-user pair across sessions; a thread born in a durable room stays
inside that room. Ownership distinguishes whose business a thread is:

* ``companion`` — the companion's own interest or goal;
* ``user`` — something the user wants or explicitly asked for;
* ``shared`` — unfinished business the two are working on together.

Temporary conversational topics never become threads: extraction validation
(``chatbot.memory.thread_observation``) rejects small talk, answered questions,
jokes, fiction and roleplay, so only durable business reaches this module.

Like the self-model, the reconciliation is a pure function
(:func:`reconcile_threads`) over the open threads of a scope, and
:func:`apply_thread_observations` is the one database operation — it verifies
ownership, locks the scope's open threads, reconciles, and lets the durable
job's transaction own the commit. Replay is idempotent: new threads get
deterministic ids (``job_id`` + receipt position) and are inserted idempotently.

Stale or resolved business is never presented again: retrieval keeps only
``open`` threads discussed within a bounded recency window (stricter for
ordinary threads than significant ones), and resolution/superseding happens
inside the same durable extraction transaction that observed it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re
from typing import Any
import uuid

import sqlalchemy as sa

from chatbot.db.models import Companion, ConversationalThread

# --- vocabulary and bounds (constants, not configuration) ------------------

OWNERSHIPS = frozenset({"companion", "user", "shared"})
KINDS = frozenset({"interest", "request", "subject", "goal"})
OPEN_STATUSES = frozenset({"open"})
STATUSES = frozenset({"open", "resolved"})
IMPORTANCE = frozenset({"ordinary", "significant"})

MAX_THREADS_PER_SCOPE = 24          # rows retained per scope before creation is refused
MAX_SUMMARY_CHARS = 240
MAX_TOPIC_CHARS = 80
MAX_SUBJECT_KEY_CHARS = 64
MAX_EVIDENCE = 6                    # evidence references kept per thread
MAX_EXCERPT_CHARS = 160

# A thread no longer counts as unfinished business once it has not come up for
# this long; significant business (projects, plans, awaited events) is kept
# longer. Nothing is deleted: stale threads simply stop being presented.
STALE_AFTER_DAYS = {"ordinary": 14, "significant": 45}
# Room/private threads with a completed-turn relationship snapshot.
MAX_OPEN_RETRIEVED = 8              # rows returned to later conversational context
PROMPT_THREAD_LIMIT = 5             # threads rendered in a prompt section
MAX_PROMPT_CHARS = 700
MAX_KNOWN_THREADS_CHARS = 1_200

# Two subjects are the same thread when their content words overlap this much.
OVERLAP_THRESHOLD = 0.6

_STOP = frozenset("a an the of to in on at for with and or is are my about".split())


@dataclass(frozen=True, slots=True)
class ThreadObservation:
    """One validated proposal from the extraction call (already checked by
    ``chatbot.memory.thread_observation``): unfinished business observed in one
    completed turn, or an existing open thread that turn resolved."""

    ownership: str          # companion | user | shared
    kind: str               # interest | request | subject | goal
    subject: str
    status: str = "open"    # open | resolved
    topic: str = ""
    importance: str = "ordinary"
    excerpt: str = ""
    role: str = "user"      # whose words grounded it (user | assistant)
    key: str = ""           # subject_key of the known open thread it continues or resolves
    supersedes: str = ""    # subject_key of the known open thread it replaces


@dataclass(slots=True)
class ThreadChange:
    """What one apply did; ``applied`` entries are ``{"op", "thread_id", ...}``."""

    applied: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (subject, reason)

    @property
    def changed(self) -> bool:
        return bool(self.applied)


# --------------------------------------------------------------------------
# Pure reconciliation
# --------------------------------------------------------------------------


def subject_key(subject: str) -> str:
    """Normalized, deterministic thread key (content words only)."""
    words = [w for w in re.findall(r"[a-z0-9]+", str(subject).casefold()) if w not in _STOP]
    return " ".join(words)[:MAX_SUBJECT_KEY_CHARS]


def reconcile_threads(
    existing_open: Sequence[Mapping[str, Any]],
    observations: Sequence[ThreadObservation],
    *,
    job_id: str,
    source_at: datetime,
    observation_slots: Sequence[int] | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """Match *observations* against the scope's open threads; return ``(ops, rejected)``.

    Repeated discussion of the same subject reinforces the existing thread
    instead of creating a duplicate (requirement: no duplicates). ``resolved``
    observations close the matched thread. ``supersedes`` closes the named open
    thread and opens a new one with the new framing. New threads get
    deterministic ids derived from *job_id*, so replaying a receipt applies the
    same create again without a second row. The inputs are never mutated.
    """
    at = _aware(source_at)
    # Working copy of the scope's open threads (never the caller's dicts).
    threads: dict[str, dict[str, Any]] = {
        str(t["thread_id"]): dict(t) for t in existing_open if t.get("status", "open") == "open"
    }
    ops: list[dict[str, Any]] = []
    rejected: list[tuple[str, str]] = []

    def evidence(index: int, obs: ThreadObservation) -> dict[str, Any]:
        return {
            "job_id": str(job_id),
            "turn_id": None,
            "session_id": None,
            "message_id": None,
            "source_at": at.isoformat(),
            "role": obs.role,
            "excerpt": obs.excerpt[:MAX_EXCERPT_CHARS],
            "slot": index,
        }

    for index, obs in enumerate(observations):
        index = observation_slots[index] if observation_slots is not None else index
        label = obs.subject or obs.key or obs.supersedes
        if obs.supersedes:
            old = _find_open(threads, obs.supersedes, obs)
            if old is None:
                rejected.append((label, "no_such_open_thread"))
                continue
            new_id = _thread_id(job_id, index)
            ops.append(
                {
                    "op": "supersede",
                    "thread_id": old["thread_id"],
                    "superseded_by": new_id,
                    "at": at.isoformat(),
                    "evidence": evidence(index, obs),
                    "thread": _new_thread(obs, new_id, at),
                }
            )
            threads.pop(old["thread_id"])
            continue
        match = _find_open(threads, obs.key or None, obs)
        if obs.status == "resolved":
            if match is None:
                rejected.append((label, "no_such_open_thread"))
                continue
            ops.append(
                {
                    "op": "resolve",
                    "thread_id": match["thread_id"],
                    "at": at.isoformat(),
                    "evidence": evidence(index, obs),
                }
            )
            threads.pop(match["thread_id"])
            continue
        if match is not None:
            ops.append(
                {
                    "op": "reinforce",
                    "thread_id": match["thread_id"],
                    "summary": obs.subject[:MAX_SUMMARY_CHARS],
                    "topic": obs.topic[:MAX_TOPIC_CHARS],
                    "importance": obs.importance,
                    "at": at.isoformat(),
                    "evidence": evidence(index, obs),
                }
            )
            continue
        if len(threads) >= MAX_THREADS_PER_SCOPE:
            rejected.append((label, "scope_full"))
            continue
        new_id = _thread_id(job_id, index)
        thread = _new_thread(obs, new_id, at)
        ops.append(
            {"op": "create", "thread_id": new_id, "thread": thread, "evidence": evidence(index, obs)}
        )
        threads[new_id] = {**thread, "status": "open"}
    return ops, rejected


def _new_thread(obs: ThreadObservation, thread_id: str, at: datetime) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "ownership": obs.ownership,
        "kind": obs.kind,
        "subject_key": subject_key(obs.subject) or subject_key(obs.key or obs.supersedes),
        "summary": obs.subject[:MAX_SUMMARY_CHARS],
        "topic": obs.topic[:MAX_TOPIC_CHARS] or None,
        "importance": obs.importance,
        "source_evidence": [
            {
                "source_at": at.isoformat(),
                "role": obs.role,
                "excerpt": obs.excerpt[:MAX_EXCERPT_CHARS],
            }
        ],
        "last_discussed_at": at.isoformat(),
        "relationship_context": None,
    }


def _thread_id(job_id: str, index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"conversational-thread:{job_id}:{index}"))


def _find_open(
    threads: Mapping[str, Mapping[str, Any]], key: str | None, obs: ThreadObservation
) -> dict[str, Any] | None:
    """The open thread this observation continues, or ``None``.

    An explicitly carried key wins; otherwise the subject must match an open
    thread's key or share most of its content words (same subject, reworded).
    Each thread matches at most one observation per batch.
    """
    if key:
        for thread in threads.values():
            if thread.get("subject_key") == key:
                return dict(thread)
    wanted = subject_key(obs.subject)
    if not wanted:
        return None
    wanted_stems = _stems(wanted)
    for thread in threads.values():
        if thread.get("subject_key") is None:  # already matched in this batch
            continue
        if thread.get("subject_key") == wanted:
            return dict(thread)
        existing_stems = _stems(str(thread.get("subject_key") or ""))
        if wanted_stems and existing_stems:
            overlap = len(wanted_stems & existing_stems) / len(wanted_stems | existing_stems)
            if overlap >= OVERLAP_THRESHOLD:
                return dict(thread)
    return None


def _stems(key: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", key) if word not in _STOP}


# --------------------------------------------------------------------------
# Prompt and extractor views (pure)
# --------------------------------------------------------------------------


def known_threads_text(threads: Sequence[Mapping[str, Any]]) -> str:
    """``subject_key: summary`` lines of open threads, for the extraction prompt."""
    lines, size = [], 0
    for thread in threads:
        line = f"{thread.get('subject_key', '')}: {thread.get('summary', '')}"
        if size + len(line) + 1 > MAX_KNOWN_THREADS_CHARS:
            break
        lines.append(line)
        size += len(line) + 1
    return "\n".join(lines)


_LABELS = {"companion": "your own interest", "user": "the user's", "shared": "shared"}
_PROMPT_HEADER = (
    "Unfinished threads between you and the user. You may return to one when it fits "
    "naturally; do not force a topic, and drop any the user has clearly moved past."
)


def open_threads_text(
    threads: Sequence[Mapping[str, Any]], *, now: datetime | None = None
) -> str | None:
    """The prompt block for open threads, or ``None`` when there is nothing open.

    Bounded in threads and characters; stale and resolved threads never reach it.
    Deterministic given the same rows and ``now``.
    """
    lines: list[str] = []
    size = len(_PROMPT_HEADER)
    for thread in [t for t in threads if t.get("status", "open") == "open"][:PROMPT_THREAD_LIMIT]:
        parts = [f"- {_LABELS.get(thread.get('ownership'), thread.get('ownership'))}"]
        kind = thread.get("kind")
        if kind:
            parts.append(str(kind))
        line = f"{' '.join(parts)}: {thread.get('summary', '')}"
        discussed = _format_day(thread.get("last_discussed_at"), now)
        if discussed:
            line += f" (last discussed {discussed})"
        if size + len(line) + 1 > MAX_PROMPT_CHARS:
            break
        lines.append(line)
        size += len(line) + 1
    if not lines:
        return None
    return "\n".join([_PROMPT_HEADER, *lines])


def _format_day(value: Any, now: datetime | None) -> str:
    at = value if isinstance(value, datetime) else None
    if at is None and isinstance(value, str):
        try:
            at = datetime.fromisoformat(value)
        except ValueError:
            return ""
    if at is None:
        return ""
    at = _aware(at)
    if now is not None:
        days = (_aware(now) - at).days
        if days <= 0:
            return "today"
        if days == 1:
            return "yesterday"
        return f"{days} days ago"
    return at.date().isoformat()


def thread_rows_for_prompt(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The prompt-facing view of retrieval rows (bounded fields only)."""
    return [
        {
            "thread_id": row.get("thread_id"),
            "ownership": row.get("ownership"),
            "kind": row.get("kind"),
            "subject_key": row.get("subject_key"),
            "summary": row.get("summary"),
            "topic": row.get("topic"),
            "importance": row.get("importance"),
            "last_discussed_at": _iso(row.get("last_discussed_at")),
        }
        for row in rows
    ]


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


# --------------------------------------------------------------------------
# Database operations
# --------------------------------------------------------------------------


def _room_filter(room_id: str | None):
    """Threads visible in this session's scope (private threads follow the pair)."""
    if room_id:
        return sa.or_(ConversationalThread.room_id.is_(None), ConversationalThread.room_id == room_id)
    return ConversationalThread.room_id.is_(None)


async def _owned_companion(db: Any, companion_id: str, user_id: Any) -> Companion:
    companion = await db.get(Companion, companion_id)
    if companion is None or str(companion.user_id) != str(user_id):
        raise PermissionError("Companion not found for this user")
    return companion


async def _verify_room(db: Any, room_id: str | None, user_id: Any) -> None:
    if not room_id:
        return
    from chatbot.db.models import Room

    room = await db.get(Room, room_id)
    if room is None or str(room.user_id) != str(user_id):
        raise PermissionError("Room not found for this user")


async def open_thread_rows(
    db: Any,
    *,
    user_id: Any,
    companion_id: str,
    room_id: str | None = None,
    now: datetime | None = None,
    limit: int = MAX_OPEN_RETRIEVED,
    for_update: bool = False,
    stale: bool = True,
) -> list[ConversationalThread]:
    """Open threads of one scope, most relevant first.

    With ``stale`` set (the default), threads that have not been discussed
    within their recency window are excluded — they are no longer presented as
    unfinished business. Reconciliation passes ``stale=False``: discussing a
    stale thread again makes it live again, so it must be matched and
    reinforced, never duplicated. ``for_update`` locks the matched rows so a
    concurrent job reconciles against the same set.
    """
    now = _aware(now or datetime.now(timezone.utc))
    earliest = now - timedelta(days=max(STALE_AFTER_DAYS.values()))
    staleness = (
        sa.or_(
            sa.and_(
                ConversationalThread.importance == "significant",
                ConversationalThread.last_discussed_at >= now - timedelta(days=STALE_AFTER_DAYS["significant"]),
            ),
            sa.and_(
                ConversationalThread.importance == "ordinary",
                ConversationalThread.last_discussed_at >= now - timedelta(days=STALE_AFTER_DAYS["ordinary"]),
            ),
        )
        if stale
        else sa.true()
    )
    query = (
        sa.select(ConversationalThread)
        .where(
            ConversationalThread.user_id == _uuid(user_id),
            ConversationalThread.companion_id == companion_id,
            ConversationalThread.status == "open",
            _room_filter(room_id),
            staleness,
        )
        .order_by(
            sa.desc(sa.case((ConversationalThread.importance == "significant", 1), else_=0)),
            sa.desc(ConversationalThread.last_discussed_at),
            ConversationalThread.thread_id,
        )
        .limit(limit)
    )
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    return list((await db.scalars(query)).all())


async def load_open_threads(
    db: Any,
    *,
    user_id: Any,
    companion_id: str,
    room_id: str | None = None,
    now: datetime | None = None,
    check_ownership: bool = True,
    limit: int = MAX_OPEN_RETRIEVED,
) -> list[dict[str, Any]]:
    """The prompt-facing open threads of one scope, as plain dicts.

    Raises ``PermissionError`` when the companion (or room) does not belong to
    the user and ``check_ownership`` is set.
    """
    if check_ownership:
        await _owned_companion(db, companion_id, user_id)
        await _verify_room(db, room_id, user_id)
    rows = await open_thread_rows(
        db, user_id=user_id, companion_id=companion_id, room_id=room_id, now=now, limit=limit
    )
    return [
        {
            "thread_id": row.thread_id,
            "user_id": str(row.user_id),
            "companion_id": row.companion_id,
            "room_id": row.room_id,
            "ownership": row.ownership,
            "kind": row.kind,
            "subject_key": row.subject_key,
            "summary": row.summary,
            "topic": row.topic,
            "status": row.status,
            "importance": row.importance,
            "source_evidence": list(row.source_evidence or []),
            "relationship_context": dict(row.relationship_context or {}),
            "last_discussed_at": _iso(row.last_discussed_at),
            "resolved_at": _iso(row.resolved_at),
            "superseded_by": row.superseded_by,
        }
        for row in rows
    ]


async def apply_thread_observations(
    db: Any,
    *,
    user_id: Any,
    companion_id: str,
    room_id: str | None,
    observations: Sequence[ThreadObservation],
    job_id: str,
    turn_id: str | None = None,
    session_id: str | None = None,
    message_id: str | None = None,
    source_at: datetime,
    relationship_context: Mapping[str, Any] | None = None,
) -> ThreadChange:
    """Apply validated observations in the caller's transaction; never commits.

    The durable job's effects transaction owns the commit, so a rollback undoes
    thread changes together with the turn's other effects. The companion parent
    and open thread rows are locked, so initially empty scopes also serialize.
    Raises ``PermissionError`` for a companion (or room) the user does not own.
    """
    await _owned_companion(db, companion_id, user_id)
    await _verify_room(db, room_id, user_id)
    change = ThreadChange()

    # An empty set of thread rows cannot be locked. Serialize on the existing
    # companion parent, using NO KEY UPDATE so foreign-key readers remain free.
    # This lock is transaction-owned and needs neither a new row nor a lock service.
    await db.execute(
        sa.select(Companion.companion_id)
        .where(Companion.companion_id == companion_id, Companion.user_id == _uuid(user_id))
        .with_for_update(key_share=True)
    )

    # The write scope covers private threads plus the current room's, so a room
    # conversation reinforces a private thread instead of duplicating it.
    existing = await open_thread_rows(
        db,
        user_id=user_id,
        companion_id=companion_id,
        room_id=room_id,
        limit=MAX_THREADS_PER_SCOPE,
        for_update=True,
        stale=False,  # a re-discussed stale thread is reinforced, not duplicated
    )
    rows = {row.thread_id: row for row in existing}
    eligible, slots = [], []
    at = _aware(source_at)
    for slot, obs in enumerate(observations):
        keys = {key for key in (subject_key(obs.subject), obs.key, obs.supersedes) if key}
        closed = list((await db.scalars(
            sa.select(ConversationalThread).where(
                ConversationalThread.user_id == _uuid(user_id),
                ConversationalThread.companion_id == companion_id,
                _room_filter(room_id),
                ConversationalThread.status != "open",
                ConversationalThread.subject_key.in_(keys),
            ).order_by(ConversationalThread.last_discussed_at.desc(), ConversationalThread.thread_id)
            .execution_options(populate_existing=True).limit(MAX_THREADS_PER_SCOPE)
        )).all()) if keys else []
        candidates = [*existing, *closed]
        if any(any(str(ref.get("job_id")) == str(job_id) and ref.get("slot") == slot
                   for ref in (row.source_evidence or [])) for row in candidates):
            change.rejected.append((obs.subject, "already_applied"))
            continue
        matched = _find_open(
            {row.thread_id: {"thread_id": row.thread_id, "subject_key": row.subject_key}
             for row in candidates}, obs.key or obs.supersedes or None, obs,
        )
        previous = next((row for row in candidates if matched and row.thread_id == matched["thread_id"]), None)
        if previous is not None and at < _aware(previous.last_discussed_at):
            _append_evidence(previous, {
                "job_id": str(job_id), "slot": slot, "source_at": at.isoformat(),
                "role": obs.role, "excerpt": obs.excerpt[:MAX_EXCERPT_CHARS],
                "turn_id": str(turn_id) if turn_id else None,
                "session_id": str(session_id) if session_id else None,
                "message_id": str(message_id) if message_id else None,
            })
            change.applied.append({"op": "historical_evidence", "thread_id": previous.thread_id})
            continue
        eligible.append(obs)
        slots.append(slot)
    ops, rejected = reconcile_threads(
        [
            {
                "thread_id": row.thread_id,
                "status": row.status,
                "subject_key": row.subject_key,
                "summary": row.summary,
                "ownership": row.ownership,
                "kind": row.kind,
                "topic": row.topic,
                "importance": row.importance,
                "source_evidence": row.source_evidence,
                "last_discussed_at": row.last_discussed_at,
            }
            for row in rows.values()
        ],
        eligible,
        job_id=str(job_id),
        source_at=source_at,
        observation_slots=slots,
    )
    change.rejected.extend((label, reason) for label, reason in rejected)
    if not ops:
        if change.changed:
            await db.flush()
        return change

    context = dict(relationship_context or {}) or None
    for op in ops:
        evidence_ref = _with_ids(
            op.get("evidence") or {},
            turn_id=turn_id,
            session_id=session_id,
            message_id=message_id,
        )
        if op["op"] == "create":
            if await db.get(ConversationalThread, op["thread_id"]) is not None:
                change.rejected.append((op["thread"]["summary"], "already_applied"))
                continue
            db.add(
                ConversationalThread(
                    thread_id=op["thread_id"],
                    user_id=_uuid(user_id),
                    companion_id=companion_id,
                    room_id=room_id,
                    ownership=op["thread"]["ownership"],
                    kind=op["thread"]["kind"],
                    subject_key=op["thread"]["subject_key"][:MAX_SUBJECT_KEY_CHARS],
                    summary=op["thread"]["summary"],
                    topic=op["thread"]["topic"],
                    status="open",
                    importance=op["thread"]["importance"],
                    source_evidence=[evidence_ref],
                    relationship_context=context,
                    last_discussed_at=_aware(source_at),
                )
            )
            change.applied.append({"op": "created", "thread_id": op["thread_id"]})
        elif op["op"] == "reinforce":
            row = rows.get(op["thread_id"])
            if row is None:
                change.rejected.append((op["summary"], "thread_vanished"))
                continue
            _append_evidence(row, evidence_ref)
            row.importance = (
                "significant"
                if op["importance"] == "significant" or row.importance == "significant"
                else "ordinary"
            )
            if op.get("topic") and not row.topic:
                row.topic = op["topic"]
            row.last_discussed_at = max(_aware(source_at), _aware(row.last_discussed_at))
            row.relationship_context = context or row.relationship_context
            change.applied.append({"op": "reinforced", "thread_id": op["thread_id"]})
        elif op["op"] == "resolve":
            row = rows.get(op["thread_id"])
            if row is None:
                change.rejected.append((op["thread_id"], "thread_vanished"))
                continue
            _append_evidence(row, evidence_ref)
            row.status = "resolved"
            row.resolved_at = _aware(source_at)
            row.last_discussed_at = max(_aware(source_at), _aware(row.last_discussed_at))
            row.relationship_context = context or row.relationship_context
            change.applied.append({"op": "resolved", "thread_id": op["thread_id"]})
        elif op["op"] == "supersede":
            row = rows.get(op["thread_id"])
            if row is None:
                change.rejected.append((op["thread_id"], "thread_vanished"))
                continue
            _append_evidence(row, evidence_ref)
            row.status = "superseded"
            row.superseded_by = op["superseded_by"]
            row.last_discussed_at = max(_aware(source_at), _aware(row.last_discussed_at))
            new = op["thread"]
            if await db.get(ConversationalThread, new["thread_id"]) is not None:
                change.rejected.append((new["summary"], "already_applied"))
                continue
            db.add(
                ConversationalThread(
                    thread_id=new["thread_id"],
                    user_id=_uuid(user_id),
                    companion_id=companion_id,
                    room_id=room_id,
                    ownership=new["ownership"],
                    kind=new["kind"],
                    subject_key=new["subject_key"][:MAX_SUBJECT_KEY_CHARS],
                    summary=new["summary"],
                    topic=new["topic"],
                    status="open",
                    importance=new["importance"],
                    source_evidence=[evidence_ref],
                    relationship_context=context,
                    last_discussed_at=_aware(source_at),
                )
            )
            change.applied.append(
                {"op": "superseded", "thread_id": op["thread_id"], "superseded_by": new["thread_id"]}
            )
    await db.flush()
    return change


def _with_ids(
    evidence_ref: Mapping[str, Any],
    *,
    turn_id: str | None,
    session_id: str | None,
    message_id: str | None,
) -> dict[str, Any]:
    ref = dict(evidence_ref)
    ref["turn_id"] = str(turn_id) if turn_id else None
    ref["session_id"] = str(session_id) if session_id else None
    ref["message_id"] = str(message_id) if message_id else None
    return ref


def _append_evidence(row: Any, evidence_ref: Mapping[str, Any]) -> None:
    refs = [dict(item) for item in (row.source_evidence or [])]
    if any(ref.get("job_id") == evidence_ref.get("job_id") and ref.get("slot") == evidence_ref.get("slot")
           for ref in refs):
        return
    refs.append(dict(evidence_ref))
    if len(refs) > MAX_EVIDENCE:
        refs = [refs[0], *refs[-(MAX_EVIDENCE - 1):]]
    row.source_evidence = refs


def _uuid(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return value


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


__all__ = [
    "IMPORTANCE",
    "KINDS",
    "MAX_OPEN_RETRIEVED",
    "OWNERSHIPS",
    "PROMPT_THREAD_LIMIT",
    "STALE_AFTER_DAYS",
    "ThreadChange",
    "ThreadObservation",
    "apply_thread_observations",
    "known_threads_text",
    "load_open_threads",
    "open_thread_rows",
    "open_threads_text",
    "reconcile_threads",
    "subject_key",
    "thread_rows_for_prompt",
]
