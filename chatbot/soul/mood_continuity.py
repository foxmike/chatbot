"""Mood continuity across turns and sessions (Phase 5.1b).

This module connects the transient mood machinery (``chatbot.soul.mood``) to
elapsed time and to the persistence that already exists — the per-session
``sessions.mood_state`` JSON, the ``bot_mood_logs`` history and the per-session
``identity_states.mood`` numeric tier — so that a companion's temporary
emotional state survives a pause and a new session of the same authorized
user-companion relationship starts where the last one left off, decayed by the
time in between.

Three responsibilities, all deterministic and all read-only with respect to
durable state:

* :func:`restore_mood` — one persisted mood record (``MoodState`` or the JSON
  stored in ``sessions.mood_state`` / a ``BotMoodLog`` row) becomes a current
  state: elapsed time since it was written relaxes the transient energy toward
  the temperament baseline, stale recent context is dropped, and timestamps are
  normalized to UTC with elapsed intervals clamped to non-negative. Ten minutes
  away keeps most of the recent feeling; months away is back at rest.
* :func:`load_restorable_mood` — the production restoration lookup for a new
  session: the most recent previous session of the *same* user-companion
  relationship, in the same room scope, never a roleplay scene, never another
  user's or another companion's session, with the latest ``BotMoodLog`` of that
  session as a fallback when the session JSON is missing. It returns the raw
  restorable record; the caller applies :func:`restore_mood` (and optionally
  :func:`restore_mood_tier` for the identity mood tier) so that the applied
  temperament is the caller's choice.
* :func:`restore_mood_tier` — the same time-aware restoration for the numeric
  identity mood tier (``identity_states.mood``), regressing it toward its own
  reset target by the elapsed fraction instead of the fixed per-turn step.

The relationship model informs comfort only: a bounded nudge of the transient
baseline derived from the relationship's configured warmth. Depth is never
treated as mood, closeness is never inflated by interaction counts, and no
relationship value is written. Roleplay never contaminates real mood: scene
rooms are excluded as sources and a scene room is never a restoration target.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any

import sqlalchemy as sa

from chatbot.db.models import BotMoodLog, IdentityStateModel, Room, Session
from chatbot.soul.mood import (
    DEFAULT_REACTION_HALF_LIVE_SECONDS,
    MoodEngine,
    MoodState,
    Temperament,
    _clean_text,
    temperament_from_character,
)

# Recent emotional context restored from a previous session describes the
# older conversation; after this much silence it is dropped on restoration.
CONTEXT_RETENTION_SECONDS = 2.0 * 60.0 * 60.0

# The identity mood tier decays per turn inside a session (``evolution``'s
# ``mood_decay``); across a gap it regresses by this time-scaled fraction.
MOOD_TIER_HALF_LIVE_SECONDS = DEFAULT_REACTION_HALF_LIVE_SECONDS

# The relationship's configured warmth may nudge the transient baseline by at
# most this much, in either direction, during restoration. It never writes to
# the relationship and never maps depth or interaction counts into mood.
MAX_RELATIONSHIP_COMFORT = 0.04

_ENGINE = MoodEngine()

__all__ = [
    "CONTEXT_RETENTION_SECONDS",
    "MAX_RELATIONSHIP_COMFORT",
    "MOOD_TIER_HALF_LIVE_SECONDS",
    "RestorableMood",
    "decay_mood",
    "elapsed_seconds",
    "load_restorable_mood",
    "relationship_comfort",
    "restore_mood",
    "restore_mood_tier",
    "temperament_from_character",
]


@dataclass(frozen=True, slots=True)
class RestorableMood:
    """A previous session's persisted mood, ready to be restored.

    ``state`` is the raw recorded state (not yet time-decayed);
    ``mood_tier`` is the source session's identity mood tier with the
    timestamp it was written, for :func:`restore_mood_tier`.
    """

    state: MoodState
    source_session_id: str
    source_updated_at: datetime | None
    mood_tier: dict[str, Any] | None = None
    mood_tier_updated_at: datetime | None = None


# --------------------------------------------------------------------------
# Pure restoration
# --------------------------------------------------------------------------


def elapsed_seconds(previous: datetime | None, now: datetime) -> float:
    """Non-negative seconds from *previous* to *now*; naive times are UTC.

    A future timestamp (clock skew, imported data) is clamped to zero elapsed
    rather than producing a negative interval that would grow the reaction.
    """

    if previous is None:
        return 0.0
    previous_at = _ensure_utc(previous)
    current_at = _ensure_utc(now)
    return max(0.0, (current_at - previous_at).total_seconds())


def restore_mood(
    stored: MoodState | Mapping[str, Any] | None,
    *,
    now: datetime,
    temperament: Temperament | None = None,
    relationship: Mapping[str, Any] | None = None,
) -> MoodState | None:
    """Restore one persisted mood record as the state for a new point in time.

    ``stored`` may be a ``MoodState`` or the JSON shape persisted in
    ``sessions.mood_state`` / a ``BotMoodLog`` row (``mood``, ``energy``,
    ``closeness`` and, for the session shape, ``last_updated`` and the recent
    context). Elapsed time relaxes the transient energy toward the
    temperament's baseline (plus a bounded relationship-comfort nudge); the
    mood label survives a short gap unchanged and returns to the baseline only
    when the reaction has effectively settled. Returns ``None`` when nothing
    restorable was supplied.
    """

    now = _ensure_utc(now)
    state = _coerce_stored_mood(stored)
    if state is None:
        return None
    baseline = temperament.baseline_energy if temperament is not None else 0.5
    baseline = _clamp01(baseline + relationship_comfort(relationship))
    half_life = (
        temperament.reaction_half_life_seconds
        if temperament is not None
        else DEFAULT_REACTION_HALF_LIVE_SECONDS
    )
    gap = elapsed_seconds(state.last_updated, now)
    energy = _decay_toward(state.energy, baseline, gap, half_life)
    recent_context = list(state.recent_emotional_context)
    if gap > CONTEXT_RETENTION_SECONDS:
        recent_context = []
    mood = state.mood
    if temperament is not None and mood != temperament.baseline_label:
        retained = math.pow(0.5, gap / max(half_life, 1.0))
        if retained < 0.1 and energy <= baseline + 0.05:
            mood = temperament.baseline_label
    return MoodState(
        mood=mood,
        energy=energy,
        closeness=state.closeness,
        last_updated=now,
        recent_emotional_context=recent_context,
        turns_since_mood_shift=state.turns_since_mood_shift,
    )


def decay_mood(
    state: MoodState,
    *,
    now: datetime,
    temperament: Temperament | None = None,
) -> MoodState:
    """Relax one current state by elapsed time (thin wrapper over the engine)."""

    return _ENGINE.decay(state, now=now, temperament=temperament)


def relationship_comfort(relationship: Mapping[str, Any] | None) -> float:
    """A bounded comfort nudge for the transient baseline, from the relationship.

    Uses only the relationship's configured ``warmth``; depth, trust and
    interaction counts are deliberately ignored — depth is not mood, and
    familiarity must not inflate affection or closeness. The result is bounded
    by ``±MAX_RELATIONSHIP_COMFORT``.
    """

    if not isinstance(relationship, Mapping):
        return 0.0
    warmth = relationship.get("warmth")
    try:
        warmth_value = float(warmth)
    except (TypeError, ValueError):
        return 0.0
    warmth_value = _clamp01(warmth_value)
    return max(-MAX_RELATIONSHIP_COMFORT, min(MAX_RELATIONSHIP_COMFORT, (warmth_value - 0.5) * 0.16))


def restore_mood_tier(
    mood: Mapping[str, Any] | None,
    reset_target: Mapping[str, Any],
    *,
    now: datetime,
    last_updated: datetime | None,
    temperament: Temperament | None = None,
) -> dict[str, Any]:
    """Time-scale the identity mood tier's regression toward its reset target.

    Inside a session the tier already decays per turn (``evolution.mood_decay``);
    across a session gap this regresses it once, by the elapsed fraction, so a
    new session continues from a consistent point instead of snapping to the
    target. Numeric values move toward the target by ``1 - 0.5 ** (elapsed /
    half-life)``; text values (``short_term_tone``, ``current_interest``) are
    kept only while the gap is recent, then reset. The inputs are not mutated.
    """

    result = dict(mood or {})
    gap = elapsed_seconds(last_updated, now)
    fraction = 1.0 - math.pow(0.5, gap / max(MOOD_TIER_HALF_LIVE_SECONDS, 1.0))
    stale_text = gap > CONTEXT_RETENTION_SECONDS
    for key, target in reset_target.items():
        current = result.get(key, target)
        if isinstance(current, (int, float)) and not isinstance(current, bool) and isinstance(
            target, (int, float)
        ):
            moved = float(current) + (float(target) - float(current)) * fraction
            result[key] = _clamp01(moved)
        elif stale_text:
            result[key] = target
    return result


# --------------------------------------------------------------------------
# Persistence lookup
# --------------------------------------------------------------------------


async def load_restorable_mood(
    db: Any,
    *,
    user_id: Any,
    companion_id: str | None,
    room_id: str | None = None,
    exclude_session_id: Any = None,
    now: datetime | None = None,
) -> RestorableMood | None:
    """The most recent restorable mood of this user-companion relationship.

    Rules enforced by the query itself:

    * ownership — the candidate session must belong to *user_id* and be bound
      to exactly *companion_id*; another companion's or another user's mood is
      never read;
    * room isolation — with a target room, only sessions of that same room are
      candidates; a private target only continues private sessions, so stale
      private context is never copied into a different room;
    * roleplay isolation — a scene room is never a restoration target, and
      sessions of scene rooms (in-character fiction) are never sources.

    Returns ``None`` when no eligible session carries a mood record; the
    caller then starts from the temperament baseline. The returned state is
    not yet time-decayed — apply :func:`restore_mood` with the caller's
    ``now`` and temperament.
    """

    now = _ensure_utc(now) if now is not None else datetime.now(timezone.utc)
    if companion_id is None or user_id is None:
        return None
    if room_id is not None:
        room = await db.get(Room, room_id)
        if room is None or str(room.user_id) != str(user_id):
            return None  # not the user's room: nothing may be restored into it
        if _is_scene_room(room):
            return None  # in-character fiction never imports real mood
    candidates = await _candidate_sessions(
        db,
        user_id=user_id,
        companion_id=companion_id,
        room_id=room_id,
        exclude_session_id=exclude_session_id,
    )
    for candidate in candidates:
        state = _coerce_stored_mood(candidate.mood_state)
        if state is None:
            state = await _latest_mood_log(db, candidate.id)
        if state is not None:
            mood_tier, mood_tier_updated_at = await _mood_tier_of(db, candidate.id)
            return RestorableMood(
                state=state,
                source_session_id=str(candidate.id),
                source_updated_at=candidate.updated_at,
                mood_tier=mood_tier,
                mood_tier_updated_at=mood_tier_updated_at,
            )
    return None


def _is_scene_room(room: Any) -> bool:
    scene_id = getattr(room, "scene_id", None)
    room_type = str(getattr(room, "room_type", "") or "")
    return scene_id is not None or room_type == "roleplay_scene"


async def _candidate_sessions(
    db: Any,
    *,
    user_id: Any,
    companion_id: str,
    room_id: str | None,
    exclude_session_id: Any,
) -> list[Any]:
    """Previous sessions of this exact relationship and room scope, newest first.

    Scene (roleplay) rooms and non-durable rooms are excluded as sources; with
    the outer join, private sessions (``room_id`` NULL) have a NULL Room row
    and pass the scene/durability checks trivially.
    """

    conditions = [
        Session.user_id == _uuid(user_id),
        Session.companion_id == companion_id,
        sa.or_(Session.room_id.is_(None), Room.scene_id.is_(None)),
        sa.or_(Session.room_id.is_(None), Room.room_type != "roleplay_scene"),
        sa.or_(Session.room_id.is_(None), Room.persist_to_durable.is_(True)),
        sa.or_(Session.mood_state.is_not(None), Session.id.in_(_log_session_ids(user_id, companion_id))),
    ]
    if exclude_session_id is not None:
        conditions.append(Session.id != _uuid(exclude_session_id))
    if room_id is None:
        conditions.append(Session.room_id.is_(None))
    else:
        conditions.append(Session.room_id == room_id)
    query = (
        sa.select(Session)
        .join(Room, Session.room_id == Room.room_id, isouter=True)
        .where(*conditions)
        .order_by(sa.desc(Session.updated_at))
        .limit(5)
    )
    return list((await db.scalars(query)).all())


def _log_session_ids(user_id: Any, companion_id: str):
    """Session ids that carry at least one mood log (subquery for the filter)."""

    return (
        sa.select(BotMoodLog.session_id)
        .join(Session, BotMoodLog.session_id == Session.id)
        .where(Session.user_id == _uuid(user_id), Session.companion_id == companion_id)
    )


async def _latest_mood_log(db: Any, session_id: Any) -> MoodState | None:
    row = await db.scalar(
        sa.select(BotMoodLog)
        .where(BotMoodLog.session_id == session_id)
        .order_by(sa.desc(BotMoodLog.created_at))
        .limit(1)
    )
    if row is None:
        return None
    return _coerce_stored_mood(
        {
            "mood": row.mood,
            "energy": row.energy,
            "closeness": row.closeness,
            "last_updated": row.created_at,
            "recent_emotional_context": [],
        }
    )


async def _mood_tier_of(db: Any, session_id: Any) -> tuple[dict[str, Any] | None, datetime | None]:
    row = await db.scalar(
        sa.select(IdentityStateModel)
        .where(IdentityStateModel.session_id == session_id)
        .order_by(sa.desc(IdentityStateModel.last_updated))
        .limit(1)
    )
    if row is None:
        return None, None
    return (dict(row.mood) if row.mood is not None else None), row.last_updated


# --------------------------------------------------------------------------
# Coercion helpers (local; the turn pipeline has its own private ones)
# --------------------------------------------------------------------------


def _coerce_stored_mood(value: Any) -> MoodState | None:
    if isinstance(value, MoodState):
        return value
    if not isinstance(value, Mapping):
        return None
    mood = _clean_text(value.get("mood"))
    if not mood:
        return None
    try:
        return MoodState(
            mood=mood,
            energy=_float(value.get("energy"), 0.5),
            closeness=_float(value.get("closeness"), 0.0),
            last_updated=_parse_datetime(value.get("last_updated")),
            recent_emotional_context=[
                _clean_text(item)
                for item in (value.get("recent_emotional_context") or [])
                if _clean_text(item)
            ],
            turns_since_mood_shift=int(value.get("turns_since_mood_shift") or 0),
        )
    except (TypeError, ValueError):
        return None


def _float(value: Any, default: float) -> float:
    try:
        return _clamp01(float(value))
    except (TypeError, ValueError):
        return default


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip())
        return parsed
    raise ValueError("no usable timestamp")


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _decay_toward(
    value: float, baseline: float, elapsed_seconds_value: float, half_life_seconds: float
) -> float:
    elapsed = max(0.0, float(elapsed_seconds_value))
    half_life = max(float(half_life_seconds), 1.0)
    retained = math.pow(0.5, elapsed / half_life)
    return _clamp01(baseline + (float(value) - baseline) * retained)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _uuid(value: Any) -> Any:
    import uuid as _uuid_module

    if isinstance(value, _uuid_module.UUID):
        return value
    try:
        return _uuid_module.UUID(str(value))
    except ValueError:
        return value