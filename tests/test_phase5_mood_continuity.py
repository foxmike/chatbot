"""Phase 5.1b — mood continuity and decay.

Focused tests for the temperament-aware mood update (``chatbot.soul.mood``),
the time-aware decay/restoration and the cross-session restoration lookup
(``chatbot.soul.mood_continuity``). Persistence runs against throwaway SQLite
tables; no provider calls anywhere and every input is deterministic (fixed
``now`` values).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from chatbot.db import models
from chatbot.soul.mood import (
    DEFAULT_REACTION_HALF_LIVE_SECONDS,
    MOOD_LABELS,
    MoodEngine,
    MoodState,
    Temperament,
    temperament_from_character,
)
from chatbot.soul.mood_continuity import (
    CONTEXT_RETENTION_SECONDS,
    load_restorable_mood,
    relationship_comfort,
    restore_mood,
    restore_mood_tier,
)

T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
USER_A = uuid.UUID("abcdef01-0000-4000-8000-000000000001")
USER_B = uuid.UUID("abcdef02-0000-4000-8000-000000000002")
COMPANION_A = "11111111-1111-4111-8111-111111111111"
COMPANION_B = "22222222-2222-4222-8222-222222222222"
ROOM_A = "room-a"
ROOM_B = "room-b"

ENGINE = MoodEngine()


def state(mood="warm", energy=0.5, closeness=0.0, last_updated=T0, context=(), turns=0):
    return MoodState(
        mood=mood,
        energy=energy,
        closeness=closeness,
        last_updated=last_updated,
        recent_emotional_context=list(context),
        turns_since_mood_shift=turns,
    )


async def _lookup(factory, **kw):
    async with factory() as db:
        return await load_restorable_mood(db, **kw)


def positive_turn(text="Thank you so much, that was really helpful and fun!"):
    return {"user_message": text, "provider_response": text}


def negative_turn(text="This is so frustrating, everything failed and I am stressed."):
    return {"user_message": text, "provider_response": text}


def chatter_turn(text="hey what's up, how's it going"):
    return {"user_message": text, "provider_response": text}


EXTRAVERT = temperament_from_character(
    {"personality_sliders": {"extraversion": 0.9, "neuroticism": 0.1, "conscientiousness": 0.5,
                             "agreeableness": 0.5, "openness": 0.5}, "humor_style": "silly"}
)
SENSITIVE = temperament_from_character(
    {"personality_sliders": {"extraversion": 0.3, "neuroticism": 0.9, "conscientiousness": 0.2,
                             "agreeableness": 0.5, "openness": 0.5}, "humor_style": "dry"}
)
RESERVED = temperament_from_character(
    {"personality_sliders": {"extraversion": 0.1, "neuroticism": 0.2, "conscientiousness": 0.8,
                             "agreeableness": 0.2, "openness": 0.5}, "humor_style": "none"}
)


# --------------------------------------------------------------------------
# 1-2. Continuity across successive turns / persistence within a session
# --------------------------------------------------------------------------


def test_excitement_persists_across_successive_turns_instead_of_resetting():
    s = ENGINE.baseline_state(EXTRAVERT)
    for _ in range(4):
        s = ENGINE.update(positive_turn(), s, temperament=EXTRAVERT, now=T0)
    assert s.energy > EXTRAVERT.baseline_energy  # still elevated after several turns
    again = ENGINE.update(positive_turn(), s, temperament=EXTRAVERT, now=T0)
    assert again.energy >= s.energy  # the next turn continues from carried-over state


def test_each_reply_continues_the_carried_state_rather_than_starting_over():
    first = ENGINE.update(positive_turn(), ENGINE.baseline_state(EXTRAVERT), temperament=EXTRAVERT, now=T0)
    second = ENGINE.update(positive_turn(), first, temperament=EXTRAVERT, now=T0 + timedelta(seconds=30))
    assert second.energy > first.energy > ENGINE.baseline_state(EXTRAVERT).energy
    assert second.recent_emotional_context  # context accumulates across turns


# --------------------------------------------------------------------------
# 4. Time-based decay: ten minutes versus a long gap
# --------------------------------------------------------------------------


def test_a_ten_minute_gap_keeps_most_of_the_recent_feeling():
    excited = state(mood="upbeat", energy=0.8, last_updated=T0)
    restored = restore_mood(excited, now=T0 + timedelta(minutes=10), temperament=EXTRAVERT)
    assert restored is not None
    assert restored.mood == "upbeat"  # the label survives a short gap
    assert restored.energy > (EXTRAVERT.baseline_energy + 0.8) / 2  # mostly retained


def test_a_long_gap_returns_the_companion_to_its_own_baseline():
    excited = state(mood="upbeat", energy=0.8, closeness=0.4, last_updated=T0, context=["positive engagement"])
    restored = restore_mood(excited, now=T0 + timedelta(days=90), temperament=EXTRAVERT)
    assert restored.mood == EXTRAVERT.baseline_label
    assert restored.energy == pytest.approx(EXTRAVERT.baseline_energy, abs=0.05)
    assert restored.recent_emotional_context == []  # stale context is dropped
    assert restored.closeness == 0.4  # closeness is not decayed away


def test_zero_elapsed_time_changes_nothing():
    excited = state(mood="upbeat", energy=0.8, last_updated=T0)
    restored = restore_mood(excited, now=T0, temperament=EXTRAVERT)
    assert restored.energy == pytest.approx(0.8)
    assert restored.mood == "upbeat"


# --------------------------------------------------------------------------
# 5. Personality-specific behaviour
# --------------------------------------------------------------------------


def test_volatile_temperaments_decay_faster_than_stable_ones():
    volatile = SENSITIVE.reaction_half_life_seconds
    stable = RESERVED.reaction_half_life_seconds
    assert volatile < stable


def test_baselines_come_from_the_companions_own_configuration_not_a_shared_ideal():
    assert EXTRAVERT.baseline_energy > SENSITIVE.baseline_energy
    assert EXTRAVERT.baseline_label != RESERVED.baseline_label
    assert RESERVED.baseline_label == "reflective"  # humor_style "none": not warm


def test_temperament_scales_reaction_intensity_differentially():
    extravert_gain = EXTRAVERT.reaction_gain_positive
    sensitive_gain = SENSITIVE.reaction_gain_negative
    assert extravert_gain > 1.0 and sensitive_gain > 1.0
    assert EXTRAVERT.reaction_gain_negative < extravert_gain
    assert SENSITIVE.reaction_gain_positive < sensitive_gain


def test_reactive_companions_shift_mood_sooner_than_steady_ones():
    assert SENSITIVE.shift_patience < RESERVED.shift_patience


def test_an_introverted_companion_rests_lower_than_an_extraverted_one():
    assert RESERVED.baseline_energy < EXTRAVERT.baseline_energy


# --------------------------------------------------------------------------
# 6-7. Meaningful changes vs ordinary chatter
# --------------------------------------------------------------------------


def test_a_meaningful_negative_event_lowers_energy_and_persists():
    s = ENGINE.baseline_state(SENSITIVE)
    s = ENGINE.update(negative_turn(), s, temperament=SENSITIVE, now=T0)
    assert s.energy < SENSITIVE.baseline_energy
    later = ENGINE.update(chatter_turn(), s, temperament=SENSITIVE, now=T0 + timedelta(minutes=1))
    assert later.energy < SENSITIVE.baseline_energy  # frustration carries into the next reply


def test_ordinary_chatter_never_moves_mood_wildly():
    s = ENGINE.baseline_state(EXTRAVERT)
    current = s
    for index in range(5):
        current = ENGINE.update(
            chatter_turn(), current, temperament=EXTRAVERT, now=T0 + timedelta(minutes=index)
        )
    assert abs(current.energy - EXTRAVERT.baseline_energy) <= 0.05
    assert current.mood in MOOD_LABELS


# --------------------------------------------------------------------------
# 3. Restoration interface (pure + DB)
# --------------------------------------------------------------------------


def test_restore_accepts_the_persisted_session_json_shape():
    stored = {
        "mood": "upbeat",
        "energy": 0.8,
        "closeness": 0.3,
        "last_updated": T0.isoformat(),
        "recent_emotional_context": ["positive engagement"],
        "turns_since_mood_shift": 3,
    }
    restored = restore_mood(stored, now=T0 + timedelta(minutes=5), temperament=EXTRAVERT)
    assert restored is not None
    assert restored.mood == "upbeat" and restored.closeness == 0.3
    assert restored.turns_since_mood_shift == 3


def test_restore_returns_none_for_missing_or_shapeless_records():
    assert restore_mood(None, now=T0) is None
    assert restore_mood({"unrelated": 1}, now=T0) is None
    assert restore_mood("upbeat", now=T0) is None


def test_relationship_warmth_comfort_is_bounded_and_never_written():
    assert relationship_comfort({"warmth": 0.9}) > 0
    assert relationship_comfort({"warmth": 0.1}) < 0
    assert relationship_comfort({"warmth": 0.5}) == 0.0
    assert abs(relationship_comfort({"warmth": 1.0})) <= 0.04 + 1e-12
    assert relationship_comfort(None) == 0.0
    assert relationship_comfort({"depth": 0.9, "completed_turns": 500}) == 0.0  # depth is not mood


def test_restore_mood_tier_regresses_numeric_values_over_elapsed_time():
    mood = {"energy": 0.9, "interest": 0.8, "warmth": 0.6, "playfulness": 0.5,
            "short_term_tone": "excited", "current_interest": "jazz"}
    target = {"energy": 0.5, "interest": 0.5, "warmth": 0.7, "playfulness": 0.4,
              "short_term_tone": "warm", "current_interest": ""}
    short = restore_mood_tier(mood, target, now=T0 + timedelta(minutes=10), last_updated=T0)
    assert short["energy"] > 0.8  # barely moved in ten minutes
    assert short["short_term_tone"] == "excited"  # recent text values survive
    long = restore_mood_tier(mood, target, now=T0 + timedelta(days=30), last_updated=T0)
    assert long["energy"] == pytest.approx(0.5, abs=0.01)
    assert long["short_term_tone"] == "warm"  # stale text values reset
    assert mood["energy"] == 0.9  # the input is never mutated


# --------------------------------------------------------------------------
# DB: restoration lookup with ownership and isolation
# --------------------------------------------------------------------------

_TABLES = [models.User, models.Companion, models.Room, models.Session,
           models.BotMoodLog, models.IdentityStateModel]


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mood.db'}")
    async with engine.begin() as connection:
        for model in _TABLES:
            await connection.run_sync(lambda sync, m=model: m.__table__.create(sync))
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        for user, tag in ((USER_A, "a"), (USER_B, "b")):
            db.add(models.User(id=user, external_id=tag, display_name=tag, username=tag,
                               email=f"{tag}@example.com", password_hash="h"))
        for cid, user in ((COMPANION_A, USER_A), (COMPANION_B, USER_A)):
            db.add(models.Companion(companion_id=cid, user_id=user, name=cid, persona="p",
                                    description="d", backstory="b", personality_sliders={}))
        db.add(models.Room(room_id=ROOM_A, user_id=str(USER_A), room_type="one_on_one_chat",
                           display_name="A", persist_to_durable=True, memory_scope="room"))
        db.add(models.Room(room_id=ROOM_B, user_id=str(USER_A), room_type="one_on_one_chat",
                           display_name="B", persist_to_durable=True, memory_scope="room"))
        db.add(models.Room(room_id="scene-room", user_id=str(USER_A), room_type="roleplay_scene",
                           display_name="S", persist_to_durable=True, memory_scope="room"))
        await db.commit()
    yield session_factory
    await engine.dispose()


async def _seed_session(
    factory,
    session_id,
    *,
    user=USER_A,
    companion=COMPANION_A,
    room_id=None,
    mood_state=None,
    minutes_ago=30,
    scene_room=False,
):
    async with factory() as db:
        session = models.Session(
            id=session_id, user_id=user, companion_id=companion, room_id=room_id, title="t",
            updated_at=T0 - timedelta(minutes=minutes_ago),
        )
        if mood_state is not None:
            session.mood_state = mood_state
        db.add(session)
        await db.commit()
    return session_id


def session_mood(mood="upbeat", energy=0.8, minutes_ago=30):
    return {
        "mood": mood,
        "energy": energy,
        "closeness": 0.3,
        "last_updated": (T0 - timedelta(minutes=minutes_ago)).isoformat(),
        "recent_emotional_context": ["positive engagement"],
        "turns_since_mood_shift": 2,
    }


async def test_the_previous_session_of_the_same_relationship_is_restorable(factory):
    previous = uuid.uuid4()
    await _seed_session(factory, previous, mood_state=session_mood())
    record = await _lookup(factory, user_id=USER_A, companion_id=COMPANION_A, now=T0)
    assert record is not None
    assert record.source_session_id == str(previous)
    restored = restore_mood(record.state, now=T0, temperament=EXTRAVERT)
    assert restored.mood == "upbeat"
    assert restored.energy > EXTRAVERT.baseline_energy  # a 30-minute gap keeps most of it


async def test_a_new_session_can_exclude_its_own_stale_row(factory):
    own = uuid.uuid4()
    await _seed_session(factory, own, mood_state=session_mood())
    record = await _lookup(factory, user_id=USER_A, companion_id=COMPANION_A,
                           exclude_session_id=own, now=T0)
    assert record is None


async def test_another_users_and_another_companions_mood_is_never_used(factory):
    await _seed_session(factory, uuid.uuid4(), user=USER_B, companion=COMPANION_A,
                        mood_state=session_mood())
    assert await _lookup(factory, user_id=USER_A, companion_id=COMPANION_A, now=T0) is None

    await _seed_session(factory, uuid.uuid4(), companion=COMPANION_B, mood_state=session_mood())
    assert await _lookup(factory, user_id=USER_A, companion_id=COMPANION_A, now=T0) is None


async def test_room_and_private_scopes_do_not_leak(factory):
    room_a_session = uuid.uuid4()
    private_session = uuid.uuid4()
    await _seed_session(factory, room_a_session, room_id=ROOM_A, mood_state=session_mood())
    await _seed_session(factory, private_session, room_id=None, mood_state=session_mood())

    # A private target continues only private sessions.
    record = await _lookup(factory, user_id=USER_A, companion_id=COMPANION_A, room_id=None, now=T0)
    assert record is not None and record.source_session_id == str(private_session)
    # A room target continues only that same room's sessions.
    record = await _lookup(factory, user_id=USER_A, companion_id=COMPANION_A,
                           room_id=ROOM_A, now=T0)
    assert record is not None and record.source_session_id == str(room_a_session)
    # A different room gets neither.
    assert await _lookup(factory, user_id=USER_A, companion_id=COMPANION_A,
                         room_id=ROOM_B, now=T0) is None


async def test_roleplay_scenes_never_exchange_real_mood(factory):
    await _seed_session(factory, uuid.uuid4(), room_id=ROOM_A, mood_state=session_mood())
    # A scene room is never a restoration target.
    assert await _lookup(factory, user_id=USER_A, companion_id=COMPANION_A,
                         room_id="scene-room", now=T0) is None
    # A scene session is never a restoration source for the private scope.
    await _seed_session(factory, uuid.uuid4(), room_id=None, mood_state=None)
    scene_session = uuid.uuid4()
    await _seed_session(factory, scene_session, room_id="scene-room", mood_state=session_mood())
    record = await _lookup(factory, user_id=USER_A, companion_id=COMPANION_A,
                           room_id=None, now=T0)
    assert record is None or record.source_session_id != str(scene_session)


async def test_a_missing_session_mood_falls_back_to_the_bot_mood_log(factory):
    session_id = uuid.uuid4()
    await _seed_session(factory, session_id, mood_state=None)
    async with factory() as db:
        db.add(models.BotMoodLog(session_id=session_id, mood="reflective", energy=0.35,
                                 closeness=0.2, created_at=T0 - timedelta(minutes=45)))
        await db.commit()
    record = await _lookup(factory, user_id=USER_A, companion_id=COMPANION_A, now=T0)
    assert record is not None
    assert record.state.mood == "reflective"
    assert record.state.energy == 0.35


async def test_the_identity_mood_tier_of_the_source_session_is_included(factory):
    session_id = uuid.uuid4()
    await _seed_session(factory, session_id, mood_state=session_mood())
    async with factory() as db:
        db.add(models.IdentityStateModel(
            session_id=session_id,
            fixed={"name": "C"}, slow={"openness": 0.7},
            mood={"energy": 0.85, "interest": 0.7, "warmth": 0.6, "playfulness": 0.4,
                  "short_term_tone": "excited", "current_interest": "jazz"},
            last_updated=T0 - timedelta(minutes=20),
        ))
        await db.commit()
    record = await _lookup(factory, user_id=USER_A, companion_id=COMPANION_A, now=T0)
    assert record is not None
    assert record.mood_tier is not None and record.mood_tier["energy"] == 0.85
    tier = restore_mood_tier(
        record.mood_tier, {"energy": 0.5, "interest": 0.5, "warmth": 0.7, "playfulness": 0.4,
                           "short_term_tone": "warm", "current_interest": ""},
        now=T0, last_updated=record.mood_tier_updated_at,
    )
    assert tier["energy"] > 0.7  # mostly retained after 20 minutes


# --------------------------------------------------------------------------
# 10. Timezone correctness
# --------------------------------------------------------------------------


def test_naive_timestamps_are_treated_as_utc():
    naive = state(mood="upbeat", energy=0.8, last_updated=datetime(2026, 6, 1, 12, 0, 0))
    restored = restore_mood(naive, now=datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc),
                            temperament=EXTRAVERT)
    assert restored is not None and restored.energy < 0.8  # 30 elapsed minutes did decay


def test_non_utc_timestamps_are_normalized_before_measuring_elapsed():
    tokyo = timezone(timedelta(hours=9))
    stored = state(mood="upbeat", energy=0.8, last_updated=datetime(2026, 6, 1, 21, 0, tzinfo=tokyo))
    restored = restore_mood(stored, now=T0, temperament=EXTRAVERT)  # same instant as T0
    assert restored.energy == pytest.approx(0.8)


def test_future_timestamps_never_produce_negative_elapsed_intervals():
    future = state(mood="upbeat", energy=0.8, last_updated=T0 + timedelta(hours=5))
    restored = restore_mood(future, now=T0, temperament=EXTRAVERT)
    assert restored.energy == pytest.approx(0.8)  # clamped to zero elapsed
    assert restored.last_updated == T0


def test_restored_values_are_always_clamped_to_valid_bounds():
    weird = {"mood": "upbeat", "energy": 5.0, "closeness": -3.0, "last_updated": T0.isoformat()}
    restored = restore_mood(weird, now=T0 + timedelta(minutes=1))
    assert 0.0 <= restored.energy <= 1.0 and 0.0 <= restored.closeness <= 1.0


# --------------------------------------------------------------------------
# 11. No numeric trait mutation; 12. Determinism
# --------------------------------------------------------------------------


def test_mood_operations_never_touch_identity_traits():
    from chatbot.soul.evolution import IdentityState

    identity = IdentityState.defaults()
    slow_before = json.dumps(identity.slow, sort_keys=True)
    fixed_before = json.dumps(identity.fixed, sort_keys=True)
    mood_state = ENGINE.baseline_state(EXTRAVERT)
    mood_state = ENGINE.update(positive_turn(), mood_state, temperament=EXTRAVERT, now=T0)
    restore_mood(mood_state, now=T0 + timedelta(minutes=5), temperament=EXTRAVERT)
    tier = restore_mood_tier(identity.mood, identity.mood_reset_target, now=T0, last_updated=T0)
    assert json.dumps(identity.slow, sort_keys=True) == slow_before
    assert json.dumps(identity.fixed, sort_keys=True) == fixed_before
    assert tier  # the tier restoration produced values without touching identity


def test_the_same_inputs_produce_identical_output():
    a = ENGINE.update(positive_turn(), ENGINE.baseline_state(EXTRAVERT),
                      temperament=EXTRAVERT, now=T0)
    b = ENGINE.update(positive_turn(), ENGINE.baseline_state(EXTRAVERT),
                      temperament=EXTRAVERT, now=T0)
    assert a == b  # frozen dataclass equality: no randomness anywhere

    stored = {"mood": "upbeat", "energy": 0.8, "closeness": 0.3,
              "last_updated": T0.isoformat(), "recent_emotional_context": ["positive engagement"]}
    r1 = restore_mood(stored, now=T0 + timedelta(minutes=10), temperament=EXTRAVERT)
    r2 = restore_mood(stored, now=T0 + timedelta(minutes=10), temperament=EXTRAVERT)
    assert r1 == r2


def test_temperament_is_deterministic_for_the_same_character():
    profile = {"personality_sliders": {"extraversion": 0.7, "neuroticism": 0.3},
               "humor_style": "witty"}
    assert temperament_from_character(profile) == temperament_from_character(profile)