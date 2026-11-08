from __future__ import annotations

from copy import deepcopy
import json

from chatbot.soul.behavioral_context import (
    MAX_OUTPUT_CHARS,
    assemble_behavioral_context,
)


def personality(*, conscientiousness: float, interests: list[str]) -> dict:
    return {
        "name": "Ari",
        "description": "A distinctive configured identity.",
        "core_traits": ["patient", "curious"],
        "tone": "casual",
        "humor_style": "dry",
        "communication_style": "storyteller",
        "favorite_topics": interests,
        "personality_sliders": {"conscientiousness": conscientiousness, "curiosity": 0.8},
    }


def model() -> dict:
    return {
        "items": {
            "opinion:honesty": {
                "kind": "opinion", "subject": "honesty", "statement": "Honesty matters in close friendships.",
                "reason": "Trust needs accurate information.", "status": "held", "strength": 0.7,
                "challenge": {"statement": "Kind timing can matter more than bluntness.", "reason": "Timing can prevent needless hurt."},
            },
            "interest:astronomy": {
                "kind": "interest", "subject": "astronomy", "statement": "I enjoy learning about astronomy.",
                "status": "held", "strength": 0.8,
            },
            "preference:directness": {
                "kind": "preference", "subject": "directness", "statement": "I prefer direct conversations.",
                "status": "held", "strength": 0.6,
            },
            "opinion:coffee": {
                "kind": "opinion", "subject": "coffee", "statement": "Coffee is the best drink.",
                "status": "tentative", "strength": 0.4,
            },
            "interest:gardening": {
                "kind": "interest", "subject": "gardening", "statement": "I like gardening.",
                "status": "held", "strength": 0.8,
            },
        }
    }


def test_same_situation_selects_distinct_configured_personality_signals():
    situation = "I am worried about the deadline and the responsibility at work."
    careful = assemble_behavioral_context(
        personality(conscientiousness=0.9, interests=[]), situation
    )
    relaxed = assemble_behavioral_context(
        personality(conscientiousness=0.2, interests=[]), situation
    )

    assert careful.relevant_traits[0].subject == "conscientiousness"
    assert careful.relevant_traits[0].statement == "conscientiousness=0.9"
    assert relaxed.relevant_traits[0].statement == "conscientiousness=0.2"
    assert careful.configured_personality["name"] == "Ari"


def test_only_relevant_established_views_and_interests_are_selected():
    context = assemble_behavioral_context(
        personality(conscientiousness=0.7, interests=["astronomy", "cooking"]),
        "I want to talk about honesty and astronomy.",
        self_model=model(),
    )

    assert [item.subject for item in context.developed_opinions] == ["honesty"]
    assert [item.subject for item in context.developed_interests] == ["astronomy"]
    assert context.developed_preferences == ()
    assert context.configured_personality["favorite_topics"] == ["astronomy", "cooking"]


def test_explicit_opposing_evidence_is_preserved_as_a_tension():
    context = assemble_behavioral_context(
        personality(conscientiousness=0.5, interests=[]),
        "Honesty matters in close friendships.",
        self_model=model(),
    )

    [tension] = context.tensions
    assert tension.statement == "Honesty matters in close friendships."
    assert tension.opposing_statement == "Kind timing can matter more than bluntness."
    assert tension.opposing_reason == "Timing can prevent needless hurt."


def test_mood_user_preferences_experiences_and_relationship_remain_separate():
    context = assemble_behavioral_context(
        personality(conscientiousness=0.6, interests=["astronomy"]),
        "I am excited to talk about astronomy.",
        self_model=model(),
        mood={"mood": "upbeat", "energy": 0.8, "recent_emotional_context": ["ignored"]},
        user_preferences=["The user prefers astronomy explanations", "The user likes cooking"],
        experiences=[
            {"title": "Planetarium visit", "narrative": "We visited a planetarium", "topics": ["astronomy"],
             "source_message_id": "message-1", "session_id": "session-1"},
            {"title": "Garden", "narrative": "We planted flowers", "topics": ["gardening"]},
        ],
        relationship={"type": "friend", "tone": "warm", "depth": 0.4},
    )

    assert context.temporary_mood == {"mood": "upbeat", "energy": 0.8}
    assert context.user_preferences == ("The user prefers astronomy explanations",)
    assert [item["title"] for item in context.relevant_experiences] == ["Planetarium visit"]
    assert context.relevant_experiences[0]["source_message_id"] == "message-1"
    assert context.relationship == {"type": "friend", "tone": "warm", "depth": 0.4}
    assert "recent_emotional_context" not in context.temporary_mood


def test_output_is_deterministic_bounded_and_does_not_mutate_inputs():
    configured = personality(conscientiousness=0.8, interests=["astronomy", "cooking"])
    developed = model()
    original_configured, original_developed = deepcopy(configured), deepcopy(developed)
    args = dict(
        personality=configured,
        conversation_context="astronomy honesty responsibility deadline",
        self_model=developed,
        experiences=[{"title": "A" * 5000, "topics": ["astronomy"]}],
        user_preferences=["astronomy " + "preference " * 400],
    )

    first = assemble_behavioral_context(**args)
    second = assemble_behavioral_context(**args)

    assert first.as_dict() == second.as_dict()
    assert first.to_json() == second.to_json()
    assert len(first.to_json()) <= MAX_OUTPUT_CHARS
    json.loads(first.to_json())
    assert configured == original_configured
    assert developed == original_developed

