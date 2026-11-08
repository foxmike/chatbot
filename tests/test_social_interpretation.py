from __future__ import annotations

from copy import deepcopy
import json

from chatbot.soul.relationship import RelationshipConfig
from chatbot.soul.social_interpretation import (
    MAX_SOCIAL_CONTEXT_CHARS,
    assemble_social_interpretation,
)


def character(*, name: str, humor: str, traits: list[str], sliders: dict[str, float] | None = None) -> dict:
    return {
        "name": name,
        "humor_style": humor,
        "communication_style": "concise",
        "core_traits": traits,
        "personality_sliders": sliders or {"agreeableness": 0.5},
    }


def make_context(personality: dict, message: str, **kwargs):
    return assemble_social_interpretation(
        personality=personality,
        relationship=kwargs.pop("relationship", RelationshipConfig.defaults()),
        conversation_context=message,
        **kwargs,
    )


def test_same_message_keeps_contrasting_character_evidence():
    message = "You're an idiot."
    sarcastic = make_context(
        character(name="Ash", humor="sarcastic", traits=["wry", "playful"]), message
    )
    sensitive = make_context(
        character(name="Bea", humor="none", traits=["sensitive", "careful"]), message
    )

    assert sarcastic.as_dict()["character_signals"] != sensitive.as_dict()["character_signals"]
    assert "sarcastic" in sarcastic.to_json()
    assert "sensitive" in sensitive.to_json()


def test_teasing_and_criticism_evidence_are_not_collapsed_into_a_label():
    message = "You're an idiot."
    context = make_context(
        character(name="Ash", humor="dry", traits=["observant"]),
        message,
        social_references=[
            {
                "id": "joke-1", "kind": "shared_joke", "status": "established",
                "statement": "Repeated teasing joke where we call each other an idiot.",
                "interpretation": "playful teasing", "alternative_interpretation": "could still sting",
            },
            {
                "id": "conflict-1", "kind": "past_exchange", "certainty": "uncertain",
                "statement": "An argument after being called an idiot.",
                "interpretation": "actual criticism", "alternative_interpretation": "intent was unclear",
            },
        ],
    ).as_dict()

    assert len(context["shared_evidence"]) == 2
    assert {item["evidence"]["interpretation"] for item in context["shared_evidence"]} == {
        "playful teasing", "actual criticism"
    }
    uncertain = next(item for item in context["shared_evidence"] if item["id"] == "conflict-1")
    assert uncertain["certainty"] == "uncertain"
    assert "could still sting" in json.dumps(context)


def test_new_acquaintance_and_long_history_remain_distinct_without_emotional_inference():
    person = character(name="Jo", humor="witty", traits=["reserved"])
    new = make_context(
        person, "That was a sharp joke.",
        relationship={"type": "peer", "tone": "formal", "depth": 0.1},
        relationship_history={"completed_turns": 0},
    ).as_dict()
    established = make_context(
        person, "That was a sharp joke.",
        relationship={"type": "peer", "tone": "formal", "depth": 0.8},
        relationship_history={"completed_turns": 28, "first_interaction_at": "2024-04-02"},
    ).as_dict()

    assert not new["interaction_history"]
    assert established["interaction_history"]["completed_turns"] == 28
    assert {x["field"]: x["value"] for x in new["relationship_signals"]}["depth"] == 0.1
    assert {x["field"]: x["value"] for x in established["relationship_signals"]}["depth"] == 0.8
    assert "trust" not in json.dumps(established)
    assert "affection" not in json.dumps(established)


def test_sarcasm_has_both_character_style_and_relationship_context():
    result = make_context(
        character(name="Max", humor="sarcastic", traits=["proud"]),
        "How is the debugging project? Well, that went brilliantly.",
        relationship={"type": "friend", "tone": "sarcastic", "depth": 0.6},
        social_references=[{
            "id": "habit-1", "kind": "conversation_habit", "status": "recorded",
            "statement": "We often use dry sarcasm when debugging the same project.",
        }],
    ).as_dict()

    assert "sarcastic" in json.dumps(result["character_signals"])
    assert "sarcastic" in json.dumps(result["relationship_signals"])
    assert result["shared_evidence"][0]["id"] == "habit-1"


def test_only_relevant_previous_shared_experiences_are_included():
    result = make_context(
        character(name="Rin", humor="witty", traits=["curious"]),
        "How did graduation go?",
        shared_experiences=[
            {"id": "exp-1", "title": "Graduation", "narrative": "We celebrated graduation together.", "source_message_id": "msg-1"},
            {"id": "exp-2", "title": "Garden", "narrative": "We planted tulips in spring.", "source_message_id": "msg-2"},
        ],
    ).as_dict()

    assert [item["id"] for item in result["shared_evidence"]] == ["exp-1"]


def test_established_shared_joke_is_available_as_supplied_evidence():
    result = make_context(
        character(name="Tess", humor="silly", traits=["playful"]),
        "The moon duck returns!",
        social_references=[{
            "id": "duck-joke", "kind": "shared_joke", "status": "established",
            "title": "Moon duck", "statement": "Our running joke about a duck on the moon.",
        }],
    ).as_dict()

    assert result["shared_evidence"][0]["id"] == "duck-joke"
    assert result["shared_evidence"][0]["certainty"] == "established"


def test_unrelated_or_unsupported_shared_history_is_excluded():
    result = make_context(
        character(name="Tess", humor="silly", traits=["playful"]),
        "How is the launch going?",
        social_references=[
            {"id": "unrelated", "statement": "We once discussed a lighthouse."},
            {"id": "unsupported", "statement": "We always celebrate every launch together."},
        ],
    ).as_dict()

    assert result["shared_evidence"] == []


def test_contradictory_social_readings_remain_separate_evidence():
    result = make_context(
        character(name="Uma", humor="dry", traits=["direct"]),
        "You always arrive late.",
        social_references=[
            {"id": "a", "status": "recorded", "statement": "You always arrive late.", "interpretation": "recurring joke"},
            {"id": "b", "certainty": "uncertain", "statement": "You always arrive late.", "interpretation": "criticism about lateness"},
        ],
    ).as_dict()

    assert len(result["shared_evidence"]) == 2
    assert {item["evidence"]["interpretation"] for item in result["shared_evidence"]} == {
        "recurring joke", "criticism about lateness"
    }


def test_companion_scoped_inputs_do_not_leak_between_calls():
    message = "How is the lighthouse project?"
    first = make_context(
        character(name="A", humor="dry", traits=["curious"]), message,
        social_references=[{"id": "a-only", "status": "recorded", "statement": "Our lighthouse project joke."}],
    ).to_json()
    second = make_context(
        character(name="B", humor="none", traits=["formal"]), message,
        social_references=[{"id": "b-only", "status": "recorded", "statement": "A private lighthouse disagreement."}],
    ).to_json()

    assert "a-only" in first and "b-only" not in first
    assert "b-only" in second and "a-only" not in second


def test_output_is_deterministic_bounded_and_does_not_mutate_inputs():
    person = character(name="Kai", humor="witty", traits=["curious", "formal"])
    relationship = {"type": "friend", "tone": "neutral", "depth": 0.4}
    model = {"items": {
        "opinion:humor": {
            "kind": "opinion", "subject": "humor", "statement": "Humor helps during tense discussions.",
            "reason": "It can relieve tension.", "status": "held", "strength": 0.8,
            "challenge": {"statement": "Humor can also hide hurt."},
        },
    }}
    refs = [{"id": "r1", "status": "established", "title": "A joke", "statement": "Humor during tense discussions."}]
    originals = deepcopy((person, relationship, model, refs))
    args = dict(
        personality=person,
        relationship=relationship,
        conversation_context="humor during tense discussions",
        self_model=model,
        social_references=refs,
    )

    first = assemble_social_interpretation(**args)
    second = assemble_social_interpretation(**args)

    assert first.as_dict() == second.as_dict()
    assert first.to_json() == second.to_json()
    assert len(first.to_json()) <= MAX_SOCIAL_CONTEXT_CHARS
    json.loads(first.to_json())
    assert (person, relationship, model, refs) == originals
    assert first.as_dict()["established_views"][0]["counterpoint"] == "Humor can also hide hurt."


def test_relationship_config_object_is_supported_without_changing_identity():
    profile = character(name="Eli", humor="dark", traits=["introspective"])
    result = make_context(
        profile, "We disagreed about the novel.",
        relationship=RelationshipConfig(
            type="peer", tone="formal", directness=0.8, warmth=0.3,
            initiative_level=0.2, content_level="intellectual", depth=0.55,
        ),
    ).as_dict()

    values = {item["field"]: item["value"] for item in result["relationship_signals"]}
    assert values["type"] == "peer"
    assert values["tone"] == "formal"
    assert values["depth"] == 0.55
    assert "novel" not in str(result["shared_evidence"])
