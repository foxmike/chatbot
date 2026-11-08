"""Phase 6.1b — character-specific conversational pacing.

Focused, deterministic tests for the extended pacing interface: rhythm
tendencies derived from configured personality, situational adaptation, the
compact guidance text, and compatibility of the original pacing interfaces.
No database, no provider calls, no randomness — every input is fixed.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from chatbot.soul.pacing import (
    PacingContext,
    calculate_momentum,
    conversational_rhythm_directive,
    full_pacing_guidance,
    get_pacing_directive,
    rhythm_options,
)
from chatbot.soul.conversational_rhythm import (
    BRIEF_ACKNOWLEDGMENT,
    DETAILED_EXPLANATION,
    FOLLOW_UP_QUESTION,
    PERSONAL_STORY,
    PLAYFUL_EXCHANGE,
    REFLECTIVE_PAUSE,
    RHYTHM_OPTIONS,
    TOPIC_TRANSITION,
    USER_SPACE,
    character_rhythm,
    rhythm_options as raw_rhythm_options,
)

ENERGETIC = dict(
    personality_sliders={"extraversion": 0.95, "curiosity": 0.6, "openness": 0.6,
                         "conscientiousness": 0.4, "agreeableness": 0.5, "neuroticism": 0.3},
    humor_style="silly",
    communication_style="verbose",
)
RESERVED = dict(
    personality_sliders={"extraversion": 0.1, "curiosity": 0.3, "openness": 0.5,
                         "conscientiousness": 0.85, "agreeableness": 0.4, "neuroticism": 0.2},
    humor_style="none",
    communication_style="concise",
)
CURIOUS = dict(
    personality_sliders={"extraversion": 0.5, "curiosity": 0.95, "openness": 0.9,
                         "conscientiousness": 0.5, "agreeableness": 0.5, "neuroticism": 0.4},
    humor_style="witty",
    communication_style="question_driven",
)


def ctx(character=ENERGETIC, **kw):
    return PacingContext(**{**character, **kw})


def weight(context, option):
    return rhythm_options(context).weight(option)


# --------------------------------------------------------------------------
# 1. Same situation, different configured personalities
# --------------------------------------------------------------------------


def test_the_same_moment_reads_differently_for_different_characters():
    moment = dict(user_message="Tell me about your day.", scene_type="casual", momentum="steady")
    energetic = rhythm_options(ctx(ENERGETIC, **moment))
    reserved = rhythm_options(ctx(RESERVED, **moment))
    curious = rhythm_options(ctx(CURIOUS, **moment))
    # The reserved companion favours measured brevity; the energetic one favours
    # fuller turns; the curious one leans toward questions.
    assert reserved.weight(DETAILED_EXPLANATION) < energetic.weight(DETAILED_EXPLANATION)
    assert curious.weight(FOLLOW_UP_QUESTION) > reserved.weight(FOLLOW_UP_QUESTION)
    assert energetic.weight(PLAYFUL_EXCHANGE) > reserved.weight(PLAYFUL_EXCHANGE)


def test_different_characters_receive_different_guidance_text():
    moment = dict(user_message="I had a rough week and want to talk about it.",
                  scene_type="emotional", momentum="slow")
    energetic_text = conversational_rhythm_directive(ctx(ENERGETIC, **moment))
    reserved_text = conversational_rhythm_directive(ctx(RESERVED, **moment))
    assert energetic_text != reserved_text


def test_introspective_characters_pause_on_meaningful_subjects_but_not_in_banter():
    pause_in_deep_talk = weight(
        ctx(RESERVED, user_message="I keep thinking about what it all means.",
            scene_type="deep_talk", momentum="slow"),
        REFLECTIVE_PAUSE,
    )
    pause_in_banter = weight(
        ctx(RESERVED, user_message="haha nice one!", momentum="fast"),
        REFLECTIVE_PAUSE,
    )
    assert pause_in_deep_talk > pause_in_banter


# --------------------------------------------------------------------------
# 2. Short greeting versus complex discussion
# --------------------------------------------------------------------------


def test_a_simple_greeting_skews_brief():
    options = rhythm_options(ctx(ENERGETIC, user_message="hey!", momentum="steady"))
    top = {option for option, _ in options.top(3)}
    assert BRIEF_ACKNOWLEDGMENT in top
    assert options.depth in {"brief", "measured"}


def test_a_substantive_discussion_skews_fuller():
    options = rhythm_options(
        ctx(ENERGETIC, user_message="I've been wrestling with this decision about my career for weeks and I keep going back and forth.",
            scene_type="deep_talk", momentum="slow")
    )
    assert options.depth in {"measured", "full"}
    assert options.weight(DETAILED_EXPLANATION) > rhythm_options(
        ctx(ENERGETIC, user_message="hey!", momentum="steady")
    ).weight(DETAILED_EXPLANATION)


# --------------------------------------------------------------------------
# 3. Fast conversation versus reflective conversation
# --------------------------------------------------------------------------


def test_fast_conversations_favor_shorter_turns_than_slow_ones():
    fast = rhythm_options(ctx(ENERGETIC, user_message="lol yes exactly", momentum="fast"))
    slow = rhythm_options(ctx(ENERGETIC, user_message="I wanted to think this through properly.",
                              momentum="slow"))
    assert fast.weight(BRIEF_ACKNOWLEDGMENT) > slow.weight(BRIEF_ACKNOWLEDGMENT)
    assert fast.weight(DETAILED_EXPLANATION) < slow.weight(DETAILED_EXPLANATION)


# --------------------------------------------------------------------------
# 4. Significant emotional context affects pacing
# --------------------------------------------------------------------------


def test_user_distress_shifts_pacing_toward_space_and_pause():
    calm = rhythm_options(ctx(ENERGETIC, user_message="so what should we do today?", momentum="steady"))
    distress = rhythm_options(ctx(ENERGETIC, user_message="I'm devastated, I lost my job this week and feel awful.",
                              momentum="steady"))
    assert distress.weight(USER_SPACE) > calm.weight(USER_SPACE)
    assert distress.weight(REFLECTIVE_PAUSE) > calm.weight(REFLECTIVE_PAUSE)
    assert distress.weight(PLAYFUL_EXCHANGE) < calm.weight(PLAYFUL_EXCHANGE)


def test_mood_influences_pacing_without_being_the_whole_story():
    low = rhythm_options(ctx(ENERGETIC, user_message="what's new?", mood_label="tired", mood_energy=0.2))
    high = rhythm_options(ctx(ENERGETIC, user_message="what's new?", mood_label="upbeat", mood_energy=0.9))
    assert low.weight(PLAYFUL_EXCHANGE) < high.weight(PLAYFUL_EXCHANGE)


# --------------------------------------------------------------------------
# 5. Established interests influence depth
# --------------------------------------------------------------------------


def test_a_developed_interest_earns_a_fuller_answer():
    plain = rhythm_options(ctx(CURIOUS, user_message="I listened to some modal jazz today.", momentum="steady"))
    interested = rhythm_options(
        ctx(CURIOUS, user_message="I listened to some modal jazz today.", momentum="steady",
            interests=("modal jazz",)),
    )
    assert interested.weight(DETAILED_EXPLANATION) > plain.weight(DETAILED_EXPLANATION)


def test_an_unfinished_shared_subject_may_lightly_invite_a_return_but_is_capped():
    thread = {"summary": "finishing the treehouse app", "importance": "significant"}
    invited = rhythm_options(
        ctx(CURIOUS, user_message="I finally had a free afternoon to work on the treehouse app.",
            momentum="steady", open_threads=(thread,))
    )
    unrelated = rhythm_options(
        ctx(CURIOUS, user_message="I finally had a free afternoon.", momentum="steady")
    )
    assert invited.has_relevant_thread
    assert invited.weight(TOPIC_TRANSITION) > unrelated.weight(TOPIC_TRANSITION)
    from chatbot.soul.conversational_rhythm import MAX_THREAD_TRANSITION_WEIGHT
    assert invited.weight(TOPIC_TRANSITION) <= MAX_THREAD_TRANSITION_WEIGHT


# --------------------------------------------------------------------------
# 6-8. Urgency suppression, no mandatory question, no rigid selector
# --------------------------------------------------------------------------


def test_urgent_requests_suppress_topic_steering():
    context = ctx(ENERGETIC, user_message="Urgent: can you fix this bug right now?",
                  open_threads=({"summary": "the treehouse app", "importance": "significant"},))
    options = rhythm_options(context)
    assert options.suppresses_topic_steering
    assert not options.has_relevant_thread
    directive = conversational_rhythm_directive(context)
    assert "answer it directly first" in directive
    assert "you may lightly return" not in directive  # no steering mid-urgency


def test_no_mandatory_follow_up_question():
    # Even the most curious companion's guidance never commands a question.
    text = conversational_rhythm_directive(
        ctx(CURIOUS, user_message="I wanted to tell you about my day.", momentum="steady")
    )
    assert text is not None
    assert "never required" in text or "not instructions" in text
    # And the question weight is a preference, not a flag.
    options = rhythm_options(ctx(CURIOUS, user_message="Thanks, that's everything!", momentum="steady"))
    assert options.weight(FOLLOW_UP_QUESTION) > 0  # present but bounded


def test_no_rigid_response_length_selector():
    # Depth is a tendency among bounded values, not a token count; the same
    # option vocabulary always covers all ten possibilities.
    for character in (ENERGETIC, RESERVED, CURIOUS):
        options = rhythm_options(ctx(character, user_message="hello"))
        assert {option for option, _ in options.weights} == set(RHYTHM_OPTIONS)
        assert options.depth in {"brief", "measured", "full"}


# --------------------------------------------------------------------------
# 9. Stable identity remains consistent
# --------------------------------------------------------------------------


def test_pacing_is_derived_from_configuration_and_never_rewrites_it():
    sliders = dict(RESERVED["personality_sliders"])
    character = character_rhythm(sliders, humor_style="none", communication_style="concise")
    options = rhythm_options(ctx(RESERVED, user_message="hi", mood_label="upbeat", mood_energy=0.9))
    # Mood influenced this moment's options but the stable tendencies are unchanged.
    assert character.verbosity == character_rhythm(
        RESERVED["personality_sliders"], humor_style="none", communication_style="concise"
    ).verbosity
    assert sliders == RESERVED["personality_sliders"]


# --------------------------------------------------------------------------
# 10. Temporary mood does not permanently alter pacing
# --------------------------------------------------------------------------


def test_temporary_mood_does_not_change_the_stable_profile():
    sad = ctx(ENERGETIC, user_message="hey", mood_label="tired", mood_energy=0.15)
    glad = ctx(ENERGETIC, user_message="hey", mood_label="upbeat", mood_energy=0.95)
    assert replace(sad, mood_label="", mood_energy=None).character == sad.character
    assert sad.character == glad.character  # the character profile is mood-independent
    assert rhythm_options(sad).weight(PLAYFUL_EXCHANGE) < rhythm_options(glad).weight(PLAYFUL_EXCHANGE)


# --------------------------------------------------------------------------
# 11. Original pacing interfaces remain compatible
# --------------------------------------------------------------------------


def test_calculate_momentum_and_two_argument_directive_are_unchanged():
    assert calculate_momentum() == "steady"
    assert calculate_momentum(5.0, 20) == "fast"
    assert calculate_momentum(200.0, 400) == "slow"
    assert calculate_momentum(30.0, 100) == "steady"
    assert (
        get_pacing_directive("action", "fast")
        == "Pacing: Keep responses short, punchy, and highly reactive to match the fast pace."
    )
    assert (
        get_pacing_directive("emotional", "slow")
        == "Pacing: Be gentle and thoughtful. Match their slow, reflective pace and give them space."
    )
    assert (
        get_pacing_directive("casual", "steady")
        == "Pacing: Maintain a natural, comfortable conversational pacing."
    )


def test_full_guidance_begins_with_the_existing_directive():
    context = ctx(ENERGETIC, user_message="hello there!", scene_type="casual", momentum="fast")
    text = full_pacing_guidance(context)
    assert text is not None
    assert text.startswith(get_pacing_directive("casual", "fast"))


# --------------------------------------------------------------------------
# 12. Deterministic, bounded, non-mutating
# --------------------------------------------------------------------------


def test_output_is_deterministic_and_bounded():
    threads = ({"summary": "finishing the treehouse app", "importance": "significant"},)
    context = ctx(CURIOUS, user_message="I had a free afternoon for the treehouse app.",
                  interests=("the treehouse app",), open_threads=threads, momentum="slow",
                  scene_type="deep_talk", mood_label="reflective", mood_energy=0.6)
    a = full_pacing_guidance(context)
    b = full_pacing_guidance(context)
    assert a == b
    from chatbot.soul.pacing import MAX_GUIDANCE_CHARS
    assert len(a) <= MAX_GUIDANCE_CHARS + 200
    for character in (ENERGETIC, RESERVED, CURIOUS):
        text = conversational_rhythm_directive(ctx(character, user_message="anything at all"))
        assert text is None or len(text) <= MAX_GUIDANCE_CHARS


def test_inputs_are_never_mutated():
    threads = [{"summary": "the treehouse app", "importance": "significant"}]
    sliders = dict(CURIOUS["personality_sliders"])
    relationship = {"completed_turns": 40, "warmth": 0.8}
    context = ctx(CURIOUS, user_message="the treehouse app again", open_threads=tuple(threads),
                  relationship=relationship)
    rhythm_options(context)
    conversational_rhythm_directive(context)
    assert sliders == CURIOUS["personality_sliders"]
    assert threads == [{"summary": "the treehouse app", "importance": "significant"}]
    assert relationship == {"completed_turns": 40, "warmth": 0.8}


def test_raw_options_object_is_also_deterministic():
    character = character_rhythm({"extraversion": 0.7}, humor_style="witty")
    a = raw_rhythm_options(character, user_message="hi there", momentum="fast")
    b = raw_rhythm_options(character, user_message="hi there", momentum="fast")
    assert a == b
