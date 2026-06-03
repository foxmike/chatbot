from __future__ import annotations

import asyncio
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.api import config as config_api
from chatbot.db.models import (
    CharacterProfileModel,
    IdentityStateModel,
    RelationshipConfigModel,
)
from chatbot.soul import evolution
from chatbot.soul.character import (
    COMMUNICATION_STYLES,
    HUMOR_STYLES,
    CharacterProfile,
)
from chatbot.soul.evolution import (
    DRIFT_BOUND_RATIO,
    IdentityState,
    bounded_drift,
    mood_decay,
)
from chatbot.soul.persona_prompt import PersonaPromptAssembler
from chatbot.soul.relationship import (
    CONTENT_LEVELS,
    RELATIONSHIP_TONES,
    RELATIONSHIP_TYPES,
    RelationshipConfig,
)

FastAPI = fastapi.FastAPI


@pytest.fixture
def relationship_config() -> RelationshipConfig:
    return RelationshipConfig.defaults()


@pytest.fixture
def character_profile() -> CharacterProfile:
    return CharacterProfile.defaults()


@pytest.fixture
def identity_state() -> IdentityState:
    return IdentityState.defaults()


def test_relationship_config_defaults_returns_valid_config(
    relationship_config: RelationshipConfig,
) -> None:
    assert relationship_config.type in RELATIONSHIP_TYPES
    assert relationship_config.tone in RELATIONSHIP_TONES
    assert relationship_config.content_level in CONTENT_LEVELS
    assert isinstance(relationship_config.directness, float)
    assert isinstance(relationship_config.warmth, float)
    assert isinstance(relationship_config.initiative_level, float)


def test_relationship_config_to_prompt_fragment_returns_text(
    relationship_config: RelationshipConfig,
) -> None:
    fragment = relationship_config.to_prompt_fragment()

    assert fragment
    assert "Relationship:" in fragment


def test_relationship_config_rejects_invalid_literals() -> None:
    with pytest.raises(ValueError, match="type must be one of"):
        RelationshipConfig(
            type="invalid",
            tone="warm",
            directness=0.5,
            warmth=0.5,
            initiative_level=0.5,
            content_level="moderate",
        )


def test_relationship_config_clamps_numeric_fields() -> None:
    config = RelationshipConfig(
        type="friend",
        tone="warm",
        directness=-1.0,
        warmth=2.0,
        initiative_level=4.0,
        content_level="moderate",
    )

    assert config.directness == 0.0
    assert config.warmth == 1.0
    assert config.initiative_level == 1.0


def test_relationship_config_serialization_round_trip(
    relationship_config: RelationshipConfig,
) -> None:
    restored = RelationshipConfig(**asdict(relationship_config))

    assert restored == relationship_config


def test_character_profile_defaults_returns_valid_profile(
    character_profile: CharacterProfile,
) -> None:
    assert isinstance(character_profile.name, str)
    assert isinstance(character_profile.backstory, str)
    assert isinstance(character_profile.personality_sliders, dict)
    assert isinstance(character_profile.favorite_topics, list)
    assert character_profile.humor_style in HUMOR_STYLES
    assert character_profile.communication_style in COMMUNICATION_STYLES


def test_character_profile_to_prompt_fragment_contains_name(
    character_profile: CharacterProfile,
) -> None:
    fragment = character_profile.to_prompt_fragment()

    assert fragment
    assert character_profile.name in fragment


def test_character_profile_personality_sliders_are_clamped() -> None:
    profile = CharacterProfile(
        name="Test",
        backstory="Test profile.",
        personality_sliders={"low": -0.5, "high": 1.5, "mid": 0.5},
        humor_style="dry",
        communication_style="concise",
        favorite_topics=[],
    )

    assert profile.personality_sliders == {"low": 0.0, "high": 1.0, "mid": 0.5}


def test_character_profile_rejects_invalid_slider_values() -> None:
    with pytest.raises(ValueError):
        CharacterProfile(
            name="Test",
            backstory="Test profile.",
            personality_sliders={"bad": "not-a-number"},
            humor_style="dry",
            communication_style="concise",
            favorite_topics=[],
        )


def test_character_profile_rejects_invalid_styles() -> None:
    with pytest.raises(ValueError, match="humor_style must be one of"):
        CharacterProfile(
            name="Test",
            backstory="Test profile.",
            personality_sliders={},
            humor_style="invalid",
            communication_style="concise",
            favorite_topics=[],
        )

    with pytest.raises(ValueError, match="communication_style must be one of"):
        CharacterProfile(
            name="Test",
            backstory="Test profile.",
            personality_sliders={},
            humor_style="dry",
            communication_style="invalid",
            favorite_topics=[],
        )


def test_identity_state_defaults_returns_populated_tiers(
    identity_state: IdentityState,
) -> None:
    assert identity_state.fixed
    assert identity_state.slow
    assert identity_state.mood
    assert identity_state.slow_baseline == identity_state.slow
    assert identity_state.mood_reset_target == identity_state.mood


def test_bounded_drift_modifies_slow_fields_within_baseline_bounds(
    monkeypatch: pytest.MonkeyPatch,
    identity_state: IdentityState,
) -> None:
    monkeypatch.setattr(evolution.random, "uniform", lambda _lower, upper: upper)

    updated = bounded_drift(
        identity_state.slow,
        identity_state.slow_baseline,
        rate=1.0,
    )

    assert any(
        updated[key] != identity_state.slow[key]
        for key, value in identity_state.slow_baseline.items()
        if _is_plain_number(value)
    )
    for key, baseline in identity_state.slow_baseline.items():
        if _is_plain_number(baseline):
            lower = baseline * (1.0 - DRIFT_BOUND_RATIO)
            upper = baseline * (1.0 + DRIFT_BOUND_RATIO)
            assert lower <= updated[key] <= upper


def test_bounded_drift_never_modifies_fixed_fields(
    monkeypatch: pytest.MonkeyPatch,
    identity_state: IdentityState,
) -> None:
    monkeypatch.setattr(evolution.random, "uniform", lambda _lower, upper: upper)
    fixed_before = dict(identity_state.fixed)

    identity_state.slow = bounded_drift(
        identity_state.slow,
        identity_state.slow_baseline,
        rate=1.0,
    )

    assert identity_state.fixed == fixed_before


def test_mood_decay_regresses_values_toward_reset_target() -> None:
    mood: dict[str, Any] = {"energy": 1.0, "warmth": 0.0, "short_term_tone": "excited"}
    target: dict[str, Any] = {"energy": 0.5, "warmth": 0.5, "short_term_tone": "warm"}

    decayed = mood
    for _ in range(5):
        decayed = mood_decay(decayed, target, steps=2)

    assert 0.5 < decayed["energy"] < mood["energy"]
    assert mood["warmth"] < decayed["warmth"] < 0.5
    assert decayed["short_term_tone"] == "excited"


def test_apply_evolution_updates_mood_from_positive_and_negative_context() -> None:
    positive = IdentityState.defaults()
    negative = IdentityState.defaults()

    positive.apply_evolution({"sentiment": "positive", "topic": "testing"})
    negative.apply_evolution({"sentiment": "negative"})

    assert positive.mood["energy"] > positive.mood_reset_target["energy"]
    assert positive.mood["warmth"] > positive.mood_reset_target["warmth"]
    assert positive.mood["current_interest"] == "testing"
    assert negative.mood["energy"] < negative.mood_reset_target["energy"]
    assert negative.mood["playfulness"] < negative.mood_reset_target["playfulness"]


def test_apply_evolution_never_modifies_fixed_fields(
    monkeypatch: pytest.MonkeyPatch,
    identity_state: IdentityState,
) -> None:
    monkeypatch.setattr(evolution.random, "uniform", lambda _lower, upper: upper)
    fixed_before = dict(identity_state.fixed)

    identity_state.apply_evolution(
        {
            "sentiment": "positive",
            "drift_interval": 1,
            "drift_rate": 1.0,
        }
    )

    assert identity_state.fixed == fixed_before


def test_identity_state_to_prompt_fragment_contains_all_tiers(
    identity_state: IdentityState,
) -> None:
    fragment = identity_state.to_prompt_fragment()

    assert "Stable identity:" in fragment
    assert "Drift-adjusted traits:" in fragment
    assert "Current mood for this turn:" in fragment


def test_persona_prompt_assembly_includes_identity_fragments(
    relationship_config: RelationshipConfig,
    character_profile: CharacterProfile,
    identity_state: IdentityState,
) -> None:
    prompt = PersonaPromptAssembler().assemble(
        {"name": "Assistant", "tone": "clear"},
        relationship_config=relationship_config,
        character_profile=character_profile,
        identity_state=identity_state,
    )

    assert "RELATIONSHIP CONFIG" in prompt
    assert "Relationship:" in prompt
    assert "IDENTITY" in prompt
    assert character_profile.name in prompt
    assert "FIXED IDENTITY" in prompt
    assert "CURRENT DRIFT TRAITS" in prompt
    assert "MOOD" in prompt


def test_persona_prompt_assembly_is_unchanged_without_identity_args() -> None:
    assembler = PersonaPromptAssembler()
    persona = {"name": "Assistant", "tone": "clear", "traits": ["precise"]}

    baseline = assembler.assemble(persona)
    explicit_none = assembler.assemble(
        persona,
        relationship_config=None,
        character_profile=None,
        identity_state=None,
    )

    assert explicit_none == baseline


def test_config_api_identity_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_db = _FakeConfigDb()
    app = FastAPI()

    async def get_fake_db() -> _FakeConfigDb:
        return fake_db

    app.dependency_overrides[config_api.get_db] = get_fake_db
    app.include_router(config_api.config_router)

    async def get_relationship(db: _FakeConfigDb, session_id: UUID) -> Any:
        return db.relationship_models.get(session_id)

    async def get_character(db: _FakeConfigDb, session_id: UUID) -> Any:
        return db.character_models.get(session_id)

    async def get_identity(db: _FakeConfigDb, session_id: UUID) -> Any:
        return db.identity_models.get(session_id)

    async def commit_or_session_not_found(_db: _FakeConfigDb) -> None:
        return None

    monkeypatch.setattr(config_api, "_get_latest_relationship_model", get_relationship)
    monkeypatch.setattr(config_api, "_get_latest_character_model", get_character)
    monkeypatch.setattr(config_api, "_get_latest_identity_model", get_identity)
    monkeypatch.setattr(config_api, "_commit_or_session_not_found", commit_or_session_not_found)

    session_id = str(uuid4())

    async def exercise_endpoints() -> tuple[Any, Any, Any, Any, Any, Any, Any]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=True,
        ) as client:
            relationship_get = await client.get(f"/relationship/{session_id}")
            relationship_put = await client.put(
                f"/relationship/{session_id}",
                json={
                    "type": "mentor",
                    "tone": "formal",
                    "directness": 0.8,
                    "warmth": 0.6,
                    "initiative_level": 0.4,
                    "content_level": "deep",
                },
            )
            character_get = await client.get(f"/character/{session_id}")
            character_put = await client.put(
                f"/character/{session_id}",
                json={
                    "name": "Guide",
                    "backstory": "A precise testing companion.",
                    "personality_sliders": {"openness": 0.9},
                    "humor_style": "dry",
                    "communication_style": "verbose",
                    "favorite_topics": ["tests"],
                },
            )
            identity_get = await client.get(f"/identity/{session_id}")
            evolve = await client.post(
                f"/identity/{session_id}/evolve",
                json={"turn_context": {"sentiment": "positive", "topic": "pytest"}},
            )
            reset_mood = await client.post(f"/identity/{session_id}/reset-mood")

        return (
            relationship_get,
            relationship_put,
            character_get,
            character_put,
            identity_get,
            evolve,
            reset_mood,
        )

    (
        relationship_get,
        relationship_put,
        character_get,
        character_put,
        identity_get,
        evolve,
        reset_mood,
    ) = asyncio.run(exercise_endpoints())

    assert relationship_get.status_code == 200
    assert _has_relationship_shape(relationship_get.json())
    assert relationship_put.status_code == 200
    assert relationship_put.json()["type"] == "mentor"
    assert _has_relationship_shape(relationship_put.json())

    assert character_get.status_code == 200
    assert _has_character_shape(character_get.json())
    assert character_put.status_code == 200
    assert character_put.json()["name"] == "Guide"
    assert _has_character_shape(character_put.json())

    assert identity_get.status_code == 200
    assert _has_identity_shape(identity_get.json())
    assert evolve.status_code == 200
    assert evolve.json()["mood"]["current_interest"] == "pytest"
    assert _has_identity_shape(evolve.json())
    assert reset_mood.status_code == 200
    assert _has_identity_shape(reset_mood.json())


class _FakeConfigDb:
    def __init__(self) -> None:
        self.relationship_models: dict[UUID, RelationshipConfigModel] = {}
        self.character_models: dict[UUID, CharacterProfileModel] = {}
        self.identity_models: dict[UUID, IdentityStateModel] = {}

    def add(self, model: Any) -> None:
        if isinstance(model, RelationshipConfigModel):
            self.relationship_models[model.session_id] = model
        elif isinstance(model, CharacterProfileModel):
            self.character_models[model.session_id] = model
        elif isinstance(model, IdentityStateModel):
            self.identity_models[model.session_id] = model

    async def refresh(self, _model: Any) -> None:
        return None


def _has_relationship_shape(payload: dict[str, Any]) -> bool:
    return set(payload) == {
        "type",
        "tone",
        "directness",
        "warmth",
        "initiative_level",
        "content_level",
    }


def _has_character_shape(payload: dict[str, Any]) -> bool:
    return set(payload) == {
        "name",
        "backstory",
        "personality_sliders",
        "humor_style",
        "communication_style",
        "favorite_topics",
    }


def _has_identity_shape(payload: dict[str, Any]) -> bool:
    return set(payload) == {"fixed", "slow", "mood"}


def _is_plain_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
