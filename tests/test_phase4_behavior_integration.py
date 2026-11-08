"""Focused Phase 4 prompt integration using the existing acceptance fixture."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

import pytest

from test_completion_persistence import USER_ID, env  # noqa: F401
from test_persistent_identity_integration import (
    add_companion as add_prompt_companion,
    new_session,
    provider,
    spoken_turn,
    standard_turn,
    stream_turn,
)

from chatbot.core import turn as turn_module
from chatbot.core.steps import TurnContext
from chatbot.db import models
from chatbot.soul.behavior import (
    CHARACTER_INTERPRETATION_INSTRUCTION,
    MAX_CHARACTER_BEHAVIOR_CHARS,
    build_character_behavior,
    build_open_thread_context,
)
from chatbot.soul.character import CharacterProfile

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "phase4_behavioral_acceptance.json").read_text()
)
SCENARIOS = {scenario["scenario_id"]: scenario for scenario in FIXTURE["scenarios"]}


def profile(*, name: str, sliders: dict[str, float], topics: list[str]) -> CharacterProfile:
    return CharacterProfile(
        name=name,
        backstory=f"{name} has a distinctive established identity.",
        personality_sliders=sliders,
        humor_style="dry",
        communication_style="concise",
        favorite_topics=topics,
    )


def held_model(*, subject: str, statement: str, challenge: str | None = None) -> dict:
    return {
        "items": {
            f"opinion:{subject}": {
                "kind": "opinion", "subject": subject, "statement": statement,
                "reason": "The evidence supports this position.", "status": "held", "strength": 0.7,
            "updated_at": "2026-11-08T12:00:00+00:00", "first_at": "2026-11-01T12:00:00+00:00",
                "challenge": ({"statement": challenge, "reason": "A competing consideration also matters."} if challenge else None),
            },
            "interest:architecture": {
                "kind": "interest", "subject": "architecture",
                "statement": "I enjoy exploring systems architecture.", "status": "held", "strength": 0.8,
            "updated_at": "2026-11-08T12:00:00+00:00", "first_at": "2026-11-01T12:00:00+00:00",
            },
            "interest:gardening": {
                "kind": "interest", "subject": "gardening",
                "statement": "I enjoy gardening.", "status": "tentative", "strength": 0.4,
            },
        }
    }


def scenario_one_message() -> str:
    scenario = SCENARIOS["scenario_1_same_input_different_personalities"]
    return scenario["user_message_or_conversation_sequence"]["turns"][0]["content"]


def _fixture_user_messages(value):
    if isinstance(value, dict):
        for role_key, text_key in (("role", "content"), ("speaker", "text")):
            if value.get(role_key) == "user" and isinstance(value.get(text_key), str):
                yield value[text_key]
        if isinstance(value.get("user_content"), str):
            yield value["user_content"]
        for child in value.values():
            yield from _fixture_user_messages(child)
    elif isinstance(value, list):
        for child in value:
            yield from _fixture_user_messages(child)


def test_all_ten_gemini_scenarios_produce_bounded_grounded_context():
    assert len(SCENARIOS) == 10
    for scenario in FIXTURE["scenarios"]:
        sequence = scenario["user_message_or_conversation_sequence"]
        [message] = list(_fixture_user_messages(sequence))[:1]
        configurations = scenario.get("character_configurations") or [scenario["character_configuration"]]
        for configured in configurations:
            anchors = configured.get("personality_anchors") or {}
            sliders = {
                key: value for key, value in anchors.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
            text, metadata = build_character_behavior(
                personality=profile(
                    name=configured.get("name", "Companion"), sliders=sliders,
                    topics=configured.get("favorite_topics", []),
                ),
                conversation_context=message,
                effective_traits=sliders,
            )
            assert len(text) <= MAX_CHARACTER_BEHAVIOR_CHARS, scenario["scenario_id"]
            assert CHARACTER_INTERPRETATION_INSTRUCTION in text
            assert len(metadata["pulls"]) <= 4 and len(metadata["noticed"]) <= 3


def test_fixture_scenario_contrasts_personality_opinion_and_selective_attention():
    message = scenario_one_message()
    mercury = build_character_behavior(
        personality=profile(name="Mercury", sliders={"conscientiousness": 0.9, "agreeableness": 0.3}, topics=["systems architecture"]),
        conversation_context=message + " pipeline architecture deployment",
        effective_traits={"conscientiousness": 0.9, "agreeableness": 0.3},
        self_model=held_model(subject="deployment", statement="Deployment should follow measured tests.", challenge="Moving quickly can also be valuable."),
    )
    kore = build_character_behavior(
        personality=profile(name="Kore", sliders={"agreeableness": 0.9, "openness": 0.9}, topics=["human fatigue"]),
        conversation_context=message + " team fatigue",
        effective_traits={"agreeableness": 0.9, "openness": 0.9},
        self_model=held_model(subject="fatigue", statement="Human fatigue changes how a deadline should be weighed."),
    )

    assert mercury[0] != kore[0]
    assert "deployment" in mercury[0]
    assert "fatigue" in kore[0]
    assert "also weighing" in mercury[0]  # competing motives remain unresolved
    assert "gardening" not in mercury[0]  # tentative interests are never established context
    assert len(mercury[0]) <= MAX_CHARACTER_BEHAVIOR_CHARS
    assert mercury[1]["noticed"] and all(item["ref"] for item in mercury[1]["noticed"])


def test_private_interpretation_is_only_a_prompt_instruction_and_threads_are_relevance_gated():
    message = "The database migration is still failing and I need to decide what to try next."
    prompt, _metadata = build_character_behavior(
        personality=profile(name="Mercury", sliders={"conscientiousness": 0.9}, topics=[]),
        conversation_context=message,
        effective_traits={"conscientiousness": 0.9},
    )
    threads = [
        {"thread_id": "open-1", "status": "open", "ownership": "shared", "kind": "goal", "importance": "significant", "summary": "Finish the database migration", "subject_key": "database migration", "last_discussed_at": datetime.now(timezone.utc).isoformat()},
        {"thread_id": "resolved", "status": "resolved", "ownership": "user", "kind": "goal", "summary": "Old migration", "subject_key": "old migration"},
        {"thread_id": "unrelated", "status": "open", "ownership": "companion", "kind": "interest", "summary": "Explore medieval poetry", "subject_key": "medieval poetry"},
    ]
    thread_prompt, thread_meta = build_open_thread_context(threads, conversation_context=message)
    urgent_prompt, urgent_meta = build_open_thread_context(
        threads, conversation_context="This is an emergency; I need help right now."
    )

    assert CHARACTER_INTERPRETATION_INSTRUCTION in prompt
    assert "hidden chain" not in prompt.casefold()
    assert thread_prompt and "database migration" in thread_prompt
    assert "Old migration" not in thread_prompt and "medieval poetry" not in thread_prompt
    assert thread_meta["included"] == 1 and thread_meta["thread_ids"] == ["open-1"]
    assert urgent_prompt is None and urgent_meta["omitted"] == "priority_current_message"


class _Assembler:
    def assemble(self, _persona, *, character_profile=None, developed_self=None, **_kwargs):
        return "IDENTITY\nDEVELOPED SELF\n" + (developed_self or "")


def _turn(*, mode: str, companion: bool = True) -> TurnContext:
    metadata = {
        "delivery_mode": mode,
        "intent": "question",
        "character_profile": profile(name="Mercury", sliders={"openness": 0.8}, topics=["maps"]),
        "companion_identity": {"openness": 0.8, "conscientiousness": 0.9},
        "companion_relationship": {"type": "mentor", "tone": "warm", "directness": 0.8, "warmth": 0.7, "initiative_level": 0.3, "content_level": "moderate", "depth": 0.0},
        "companion_self_model": held_model(subject="maps", statement="I value maps that make a route easy to verify."),
        "companion_open_threads": [{
            "thread_id": "thread-route", "status": "open", "ownership": "shared", "kind": "goal",
            "importance": "significant", "summary": "Finish the route map", "subject_key": "route map",
            "last_discussed_at": datetime.now(timezone.utc).isoformat(),
        }],
    }
    if not companion:
        metadata.pop("companion_identity")
    return TurnContext(
        session_id=str(uuid.uuid4()), user_message="Can we finish the route map? I want your view on maps.",
        history=[], persona={"name": "Mercury"}, retrieved_memories=[], compiled_context=None,
        provider_response=None, extracted_memories=[], final_response=None, metadata=metadata,
    )


@pytest.mark.asyncio
async def test_compile_context_shares_phase4_prompt_across_delivery_modes_without_extra_calls():
    contexts = [_turn(mode=mode) for mode in ("standard", "sse", "spoken")]
    step = turn_module.CompileContextStep(assembler=_Assembler(), intent_classifier=object())
    # Intent is preloaded above; the inert classifier is never called.
    for context in contexts:
        await step.run(context)

    prompts = [context.metadata["persona_prompt"] for context in contexts]
    assert prompts[0] == prompts[1] == prompts[2]
    prompt = prompts[0]
    assert prompt.index("DEVELOPED SELF") < prompt.index("Unfinished threads")
    assert prompt.index("Unfinished threads") < prompt.index("CHARACTER BEHAVIOR")
    assert "your opinion on maps" in prompt
    assert "your interest on architecture" not in prompt  # irrelevant to current message
    assert all(len(context.metadata["identity_sources"]["behavior"]["pulls"]) <= 4 for context in contexts)
    assert all(len(context.metadata["identity_sources"]["behavior"]["noticed"]) <= 3 for context in contexts)
    assert all(context.metadata["identity_sources"]["behavior"]["chars"] <= MAX_CHARACTER_BEHAVIOR_CHARS for context in contexts)
    # CompileContextStep is pure prompt preparation: no provider or embedding is configured/called.


@pytest.mark.asyncio
async def test_scene_and_roleplay_omit_phase4_augmentation_but_keep_configured_persona():
    scene = _turn(mode="spoken")
    scene.metadata["scene_memory_isolation"] = True
    roleplay = _turn(mode="standard")
    roleplay.metadata["intent"] = "roleplay"
    no_companion = _turn(mode="standard", companion=False)
    step = turn_module.CompileContextStep(assembler=_Assembler())
    await step.run(scene)
    await step.run(roleplay)
    await step.run(no_companion)

    for context in (scene, roleplay, no_companion):
        prompt = context.metadata["persona_prompt"]
        assert "IDENTITY" in prompt and "DEVELOPED SELF" in prompt
        assert "CHARACTER BEHAVIOR" not in prompt
        assert "Unfinished threads" not in prompt
        expected = "scene" if context is scene else "roleplay" if context is roleplay else "no_companion"
        assert context.metadata["identity_sources"]["behavior"]["omitted"] == expected
    assert no_companion.metadata["persona_prompt"] == "IDENTITY\nDEVELOPED SELF"


@pytest.mark.asyncio
async def test_phase4_prompt_uses_owned_persistent_state_in_standard_sse_and_spoken(env, provider, monkeypatch):
    # Each delivery mode gets equivalent durable state; thread rows are private to
    # that user's companion and therefore follow the pair across sessions.
    from test_persistent_identity_integration import add_companion as add_companion_real

    async with env.engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: models.ConversationalThread.__table__.create(
                sync_connection, checkfirst=True
            )
        )

    cids = [
        await add_companion_real(env, name="Mercury", sliders={"openness": 0.8},
                                 relationship={"type": "mentor", "tone": "warm", "directness": 0.8})
        for _ in range(3)
    ]
    stamp = datetime.now(timezone.utc)
    for cid in cids:
        async with env.factory() as db:
            identity = await db.get(models.CompanionIdentityModel, cid)
            identity.self_model = held_model(subject="maps", statement="I value maps that make a route easy to verify.")
            await db.flush()
            db.add(models.ConversationalThread(
                thread_id=str(uuid.uuid4()), user_id=USER_ID, companion_id=cid, room_id=None,
                ownership="shared", kind="goal", subject_key="finish route map",
                summary="Finish the route map", topic="maps", status="open", importance="significant",
                source_evidence=[], relationship_context={}, last_discussed_at=stamp,
            ))
            await db.commit()
    sessions = [await new_session(env, cid) for cid in cids]
    message = "Can we finish the route map? I want your view on maps."
    response = await standard_turn(env, sessions[0], message)
    await stream_turn(env, sessions[1], message)
    await spoken_turn(env, sessions[2], cids[2], provider, monkeypatch, message)

    system = [provider.system(i) for i in range(3)]
    assert system[0] == system[1] == system[2]
    assert "DEVELOPED SELF" in system[0] and "CHARACTER BEHAVIOR" in system[0]
    assert "Finish the route map" in system[0]
    assert len(provider.inputs) == 3  # one conversation generation per completed turn
    assert response.response == "Glad to. Let's start."
    assert "privately decide" not in response.response

    traces = await turn_module.trace_collector.list_traces(session_id=str(sessions[0]))
    [trace] = traces
    assert "CHARACTER BEHAVIOR" in trace.prompt
    (compile_step,) = [item for item in trace.steps if item.step_name == "compile_context"]
    source_meta = compile_step.metadata["identity_sources"]
    assert source_meta["behavior"]["noticed"]
    assert source_meta["open_threads"]["included"] == 1

