"""Phase 3.3 — companion self-observations from the existing extraction call.

Every model response is mocked. The extraction provider is called exactly once
per turn; self-observations ride on the same response as user facts.
"""

from __future__ import annotations

import json

import pytest

from test_memory_llm_extraction import FakeProvider, Recorder, memory
from chatbot.memory.llm_extraction import (
    LLMExtractionLimits,
    LLMMemoryExtractor,
    build_extraction_prompt,
)
from chatbot.memory.self_observation import (
    MAX_OBSERVATIONS_PER_TURN,
    known_item_keys,
    parse_self_observations,
    validate_self_observations,
)
from chatbot.soul.self_model import SelfObservation

USER = "What do you think about the new council plan to use 5000K LEDs?"
ASSISTANT = (
    "I think 5000K lighting is bad civic design, because glare constricts the pupil and deepens "
    "the shadows where hazards hide. Warm shielded fixtures protect night vision."
)
KNOWN = "opinion:civic lighting: I think 5000K lighting is bad civic design."


def view(**overrides):
    base = {
        "kind": "opinion",
        "subject": "civic lighting",
        "statement": "I think 5000K lighting is bad civic design.",
        "reason": "glare constricts the pupil and deepens the shadows",
        "evidence": "5000K lighting is bad civic design",
        "status": "real",
        "significance": "ordinary",
        "relation": "consistent",
        "change_of_mind": False,
    }
    base.update(overrides)
    return base


def check(items, user=USER, assistant=ASSISTANT, known=""):
    return validate_self_observations(
        items, user_message=user, assistant_message=assistant, known_items=known
    )


def reasons(rejected):
    return [r.reason for r in rejected]


# --------------------------------------------------------------------------
# Accepted: grounded in the companion's own dialogue, evidence kept exactly
# --------------------------------------------------------------------------


def test_a_reasoned_view_grounded_in_the_companions_reply_is_accepted_with_exact_evidence():
    accepted, rejected = check([view()])
    assert rejected == []
    [observation] = accepted
    assert observation["excerpt"] == "5000K lighting is bad civic design"  # verbatim, companion's words
    assert observation["reason"] == "glare constricts the pupil and deepens the shadows"
    assert observation["status"] == "real" and observation["relation"] == "consistent"
    SelfObservation(**observation)  # the exact shape the self-model accepts


def test_hedged_voicing_is_how_opinions_are_stated_and_is_allowed():
    assert "I think" in ASSISTANT
    assert check([view()])[1] == []


@pytest.mark.parametrize("kind", ["preference", "interest", "self_understanding"])
def test_every_supported_kind_is_accepted(kind):
    assistant = "I keep coming back to dark-sky astronomy; it changed how I understand patience."
    accepted, rejected = check(
        [view(kind=kind, subject="dark sky astronomy", reason="",
              statement="I keep coming back to dark-sky astronomy.",
              evidence="I keep coming back to dark-sky astronomy")],
        user="Tell me about your week.", assistant=assistant,
    )
    assert rejected == [] and accepted[0]["kind"] == kind


def test_an_explicit_change_of_mind_is_an_opposing_view_that_names_it():
    assistant = ("A timekeeper that works without supply chains is a valuable instrument. "
                 "I need to revise my critique.")
    accepted, rejected = check(
        [view(subject="mechanical watches", statement="A timekeeper that works without supply chains is valuable.",
              reason="it works without supply chains",
              evidence="A timekeeper that works without supply chains is a valuable instrument",
              relation="consistent", change_of_mind=True)],
        user="Mechanical watches need no supply chains.", assistant=assistant,
    )
    assert rejected == []
    assert accepted[0]["relation"] == "opposing" and accepted[0]["change_of_mind"] is True

    # Evidence must itself state the view: a generic admission does not back a position.
    generic = view(subject="mechanical watches", statement="I now value mechanical watches.",
                   evidence="I need to revise my critique", change_of_mind=True)
    assert reasons(check([generic], user="Watches.", assistant=assistant)[1]) == ["unsupported_content"]


def test_a_known_item_key_continues_the_established_item():
    accepted, _ = check([view(subject="street lamps", key="opinion:civic lighting")], known=KNOWN)
    assert accepted[0]["subject"] == "civic lighting"
    ignored, _ = check([view(subject="street lamps", key="opinion:made up")], known=KNOWN)
    assert ignored[0]["subject"] == "street lamps"  # an unknown key is not trusted
    wrong_kind, _ = check([view(subject="street lamps", kind="interest", key="opinion:civic lighting")],
                          known=KNOWN)
    assert wrong_kind[0]["subject"] == "street lamps"
    assert known_item_keys(KNOWN) == {"opinion:civic lighting"}


# --------------------------------------------------------------------------
# Rejected: unsupported, echoed, unreal
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("item", "user", "assistant", "reason"),
    [
        # Only the companion's words ground a view of the companion.
        (view(evidence="5000K LEDs"), USER, ASSISTANT, "evidence_not_in_assistant_reply"),
        (view(evidence="a view I never said"), USER, ASSISTANT, "evidence_not_in_assistant_reply"),
        (view(evidence=""), USER, ASSISTANT, "missing_evidence"),
        # Invented specifics.
        (view(statement="I think 5000K lighting in Flagstaff is bad civic design."), USER, ASSISTANT,
         "unsupported_content"),
        (view(statement="I strongly feel 7000K lighting is bad civic design."), USER, ASSISTANT,
         "unsupported_content"),
        # Not sincere real-world speech.
        (view(status="roleplay"), USER, ASSISTANT, "status_roleplay"),
        (view(status="hypothetical"), USER, ASSISTANT, "status_hypothetical"),
        (view(status="joke"), USER, ASSISTANT, "status_joke"),
        (view(), "Let's pretend you are a city planner. Thoughts on 5000K LEDs?", ASSISTANT, "roleplay"),
        (view(), "*walks in* Thoughts on 5000K LEDs, captain?", ASSISTANT, "roleplay"),
        (view(), USER, "Do you really think 5000K lighting is bad civic design?", "question"),
        (view(), USER, 'The council said "5000K lighting is bad civic design" and I disagree.',
         "quoted_or_action"),
        (view(), USER, "If I were a city planner, I would say 5000K lighting is bad civic design.",
         "hypothetical"),
        (view(), USER, "Just kidding, I think 5000K lighting is bad civic design.", "joke"),
        (view(), USER, "In my story the captain says 5000K lighting is bad civic design.", "fiction"),
        # Structure.
        (view(kind="value"), USER, ASSISTANT, "bad_kind"),
        (view(statement=""), USER, ASSISTANT, "bad_statement"),
        (view(subject="!!"), USER, ASSISTANT, "bad_subject"),
        (view(significance="huge"), USER, ASSISTANT, "bad_significance"),
        (view(relation="sideways"), USER, ASSISTANT, "bad_relation"),
        (view(status=None), USER, ASSISTANT, "missing_status"),
    ],
)
def test_unsupported_unreal_or_malformed_observations_are_rejected(item, user, assistant, reason):
    accepted, rejected = check([item], user=user, assistant=assistant)
    assert accepted == [] and reasons(rejected) == [reason]


def test_mere_repetition_of_the_users_position_is_not_an_earned_view():
    user = "I believe jazz improvisation teaches patience."
    echo = view(kind="opinion", subject="jazz", statement="I believe jazz improvisation teaches patience.",
                reason="", evidence="jazz improvisation teaches patience")
    accepted, rejected = check([echo], user=user, assistant="Yes, jazz improvisation teaches patience.")
    assert accepted == [] and reasons(rejected) == ["echo_of_user"]

    # Agreement that brings its own reasoning is a view of the companion's.
    assistant = ("Yes, jazz improvisation teaches patience, because improvisation forces you to "
                 "listen before you play.")
    reasoned = {**echo, "reason": "improvisation forces you to listen before you play"}
    accepted, rejected = check([reasoned], user=user, assistant=assistant)
    assert rejected == [] and accepted[0]["reason"].startswith("improvisation forces")

    # A "reason" that only parrots the user is still an echo.
    parroted = {**echo, "reason": "jazz improvisation teaches patience"}
    assert reasons(check([parroted], user=user, assistant="Yes, jazz improvisation teaches patience.")[1]) == [
        "echo_of_user"
    ]


def test_a_reason_the_reply_does_not_support_is_dropped_not_invented():
    accepted, rejected = check([view(reason="because the moon is made of cheese")])
    assert rejected == [] and accepted[0]["reason"] == ""  # kept as an unreasoned (tentative) view


def test_duplicates_and_the_per_turn_bound():
    accepted, rejected = check([view(), view()])
    assert len(accepted) == 1 and reasons(rejected) == ["duplicate"]

    topics = ["maps", "tea", "rain", "chess", "owls"]
    assistant = " ".join(f"I really enjoy {t} a great deal." for t in topics)
    items = [view(kind="interest", subject=t, reason="", statement=f"I enjoy {t}.",
                  evidence=f"I really enjoy {t} a great deal") for t in topics]
    accepted, rejected = check(items, user="Tell me something.", assistant=assistant)
    assert len(accepted) == MAX_OBSERVATIONS_PER_TURN
    assert reasons(rejected) == ["over_observation_limit"] * 2


def test_missing_or_malformed_self_lists_mean_none():
    assert parse_self_observations('{"memories": []}') == []
    assert parse_self_observations('{"self": "none"}') == []
    assert parse_self_observations("not json") == []
    assert parse_self_observations('```json\n{"self": [{"kind": "opinion"}]}\n```') == [{"kind": "opinion"}]


# --------------------------------------------------------------------------
# One extraction call carries user facts, experiences and self-observations
# --------------------------------------------------------------------------


async def test_one_provider_call_returns_facts_experiences_and_self_observations():
    user = "My father passed away last week. What do you think about slow mornings?"
    assistant = ("I'm so sorry. I think slow mornings help grief settle, because they give you room "
                 "to feel it without hurry.")
    response = {
        "memories": [memory("User's father passed away last week.", "My father passed away last week",
                            type="experience")],
        "experiences": [{
            "title": "Father's death", "narrative": "User told the companion their father passed away last week.",
            "participants": ["user", "companion"], "significance": "major",
            "evidence": "My father passed away last week", "status": "real", "origin": "conversation",
            "topics": ["father"], "revises": None, "confidence": 0.9,
        }],
        "self": [view(kind="opinion", subject="slow mornings",
                      statement="I think slow mornings help grief settle.",
                      reason="they give you room to feel it without hurry",
                      evidence="slow mornings help grief settle")],
    }
    provider = FakeProvider(response)
    extractor = LLMMemoryExtractor(storage=Recorder(), provider=provider, model="m")

    outcome = await extractor.extract_entries(
        session_id="s", user_message=user, provider_response=assistant, turn_id="t",
        known_self_items=KNOWN,
    )

    assert len(provider.calls) == 1  # no additional request for self-observations
    kinds = [(e["type"], e["metadata"].get("kind")) for e in outcome.entries]
    assert ("fact", "experience") in kinds and ("fact", "shared_experience") in kinds  # user facts intact
    assert [o["subject"] for o in outcome.self_observations] == ["slow mornings"]
    assert outcome.rejected == ()
    prompt = provider.calls[0]["prompt"]
    assert "KNOWN SELF ITEMS" in prompt and "civic lighting" in prompt
    config = provider.calls[0]["config"]
    assert config.max_tokens == LLMExtractionLimits().max_output_tokens  # the configured bound
    assert "self" in provider.calls[0]["history"][0]["content"]


async def test_self_observations_do_not_weaken_user_fact_extraction():
    user = "I live in Oslo."
    provider = FakeProvider({"memories": [memory("User lives in Oslo.", "I live in Oslo")], "self": "garbage"})
    outcome = await LLMMemoryExtractor(storage=Recorder(), provider=provider).extract_entries(
        session_id="s", user_message=user, provider_response="Nice."
    )
    assert outcome.method == "llm" and outcome.self_observations == []
    assert [e["content"] for e in outcome.entries if e["type"] == "fact"] == ["User lives in Oslo."]


async def test_unsupported_model_claims_never_reach_the_outcome():
    response = {"memories": [], "self": [view(evidence="something the companion never said")]}
    provider = FakeProvider(response)
    outcome = await LLMMemoryExtractor(storage=Recorder(), provider=provider).extract_entries(
        session_id="s", user_message=USER, provider_response=ASSISTANT
    )
    assert outcome.self_observations == []
    assert [r.reason for r in outcome.rejected] == ["evidence_not_in_assistant_reply"]


async def test_heuristic_fallback_produces_no_self_observations():
    provider = FakeProvider(RuntimeError("down"))
    outcome = await LLMMemoryExtractor(storage=Recorder(), provider=provider).extract_entries(
        session_id="s", user_message="I live in Oslo.", provider_response=ASSISTANT
    )
    assert outcome.method == "heuristic" and outcome.self_observations == []


def test_the_prompt_adds_known_items_only_when_there_are_some_and_bounded():
    plain = build_extraction_prompt("u", "a")
    assert "KNOWN SELF ITEMS" not in plain
    with_items = build_extraction_prompt("u", "a", KNOWN)
    assert json.dumps(KNOWN, ensure_ascii=False) in with_items
