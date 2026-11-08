from __future__ import annotations

from copy import deepcopy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.soul.linguistic_fingerprint import (
    LinguisticFingerprint,
    MAX_FINGERPRINT_CHARS,
    assemble_linguistic_fingerprint,
    format_linguistic_fingerprint,
)


class MockCompanion:
    """Mock companion DB model for testing attribute access."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_linguistic_fingerprint_empty() -> None:
    # All None or empty dict should return empty string
    assert format_linguistic_fingerprint() == ""
    assert format_linguistic_fingerprint(punctuation_habits={}) == ""


def test_linguistic_fingerprint_kwargs() -> None:
    result = format_linguistic_fingerprint(
        punctuation_habits={"exclamation_frequency": "never", "capitalization": "lowercase"},
        sentence_length_profile={"preference": "short", "max_words": 12},
        emoji_style={"frequency": "rare", "placement": "end"},
        vocabulary_density=0.2,
        slang_profile={"frequency": "occasional", "type": "gen_z"},
    )
    assert "Linguistic Directives:" in result
    assert "Do not use exclamation marks." in result
    assert "Write exclusively in lowercase" in result
    assert "Prefer very short, direct, and simple sentences." in result
    assert "Limit sentences to a maximum of 12 words." in result
    assert "Use emojis very sparingly" in result
    assert "Place emojis only at the very end" in result
    assert "Use simple, common, and highly accessible vocabulary." in result
    assert "Use slang or casual colloquialisms occasionally" in result
    assert "Incorporate modern youth slang" in result


def test_linguistic_fingerprint_dict() -> None:
    companion_dict = {
        "punctuation_habits": {"ellipsis_usage": "frequent"},
        "sentence_length_profile": {"complexity": "complex"},
        "emoji_style": {"frequency": 1.0},
        "vocabulary_density": 0.8,
        "slang_profile": {"frequency": "never"},
    }
    result = format_linguistic_fingerprint(companion_dict)
    assert "Linguistic Directives:" in result
    assert "Use ellipses (...) frequently" in result
    assert "Use complex, sophisticated, and multi-layered sentence structures." in result
    assert "Use emojis frequently" in result
    assert "Use a rich, diverse, and dense vocabulary." in result
    assert "Do not use slang, idioms, or colloquialisms" in result


def test_linguistic_fingerprint_object() -> None:
    companion_obj = MockCompanion(
        punctuation_habits={"trailing_punctuation": "never"},
        sentence_length_profile={"style": "long"},
        emoji_style={"preferred_emojis": ["😊", "🔥"]},
        vocabulary_density=0.5,
        slang_profile={"slang_words": "lit, bet"},
    )
    result = format_linguistic_fingerprint(companion_obj)
    assert "Linguistic Directives:" in result
    assert "Omit terminal periods at the very end of your messages." in result
    assert "Prefer longer, flowing, or run-on sentences" in result
    assert "When using emojis, prefer using: 😊 🔥" in result
    assert "Use a moderate, natural vocabulary density" in result
    assert "Incorporate slang words like: lit, bet" in result


def test_linguistic_fingerprint_custom_and_fallbacks() -> None:
    result = format_linguistic_fingerprint(
        punctuation_habits={"custom_punc": "Always use semicolons"},
        sentence_length_profile="prefer medium sentences",
        emoji_style="use smiley faces",
        vocabulary_density="Academic",
        slang_profile="no slang allowed",
    )
    assert "Linguistic Directives:" in result
    assert "Punctuation - Custom punc: Always use semicolons" in result
    assert "Sentence length profile: prefer medium sentences" in result
    assert "Emoji style: use smiley faces" in result
    assert "Vocabulary style: Academic" in result
    assert "Slang profile: no slang allowed" in result


def test_configured_personalities_produce_distinct_voice_guidance() -> None:
    analytical = assemble_linguistic_fingerprint({
        "description": "Analytical and technically precise, with dry humor.",
        "core_traits": ["reserved", "intellectually confrontational"],
        "humor_style": "dry",
        "communication_style": "concise",
        "vocabulary_preferences": "precise technical terms",
    })
    expressive = assemble_linguistic_fingerprint({
        "description": "Imaginative, enthusiastic and spontaneous.",
        "core_traits": ["playful", "expressive"],
        "humor_style": "silly",
        "communication_style": "storyteller",
        "figurative_language": "vivid metaphors",
    })
    reflective = assemble_linguistic_fingerprint({
        "description": "Reflective, understated and skeptical.",
        "core_traits": ["careful with language", "emotionally reserved"],
        "humor_style": "none",
        "communication_style": "aphoristic",
        "register": "measured and understated",
    })

    texts = {analytical.rendered_prompt_text, expressive.rendered_prompt_text,
             reflective.rendered_prompt_text}
    assert len(texts) == 3
    assert "intellectually confrontational" in analytical.rendered_prompt_text
    assert "vivid metaphors" in expressive.rendered_prompt_text
    assert "measured and understated" in reflective.rendered_prompt_text


def test_only_relevant_held_interests_and_opinions_influence_voice() -> None:
    result = assemble_linguistic_fingerprint(
        {"communication_style": "reflective"},
        conversation_context="Let's discuss astronomy and whether scientific evidence matters.",
        self_model={"items": {
            "interest:astronomy": {
                "kind": "interest", "subject": "astronomy",
                "statement": "I enjoy studying astronomy.", "status": "held",
            },
            "opinion:evidence": {
                "kind": "opinion", "subject": "scientific evidence",
                "statement": "Evidence should guide scientific claims.", "status": "held",
            },
            "opinion:unsupported": {
                "kind": "opinion", "subject": "astronomy",
                "statement": "Astronomy is pointless.", "status": "tentative",
            },
            "interest:cooking": {
                "kind": "interest", "subject": "cooking",
                "statement": "I enjoy cooking.", "status": "held",
            },
        }},
    )

    assert any("astronomy" in item.lower() for item in result.contextual_influences)
    assert any("evidence" in item.lower() for item in result.contextual_influences)
    assert all("pointless" not in item.lower() for item in result.contextual_influences)
    assert all("cooking" not in item.lower() for item in result.contextual_influences)


def test_temporary_mood_is_separate_from_stable_voice() -> None:
    configured = {
        "description": "Careful, reflective, and skeptical.",
        "core_traits": ["understated"],
        "communication_style": "measured",
    }
    calm = assemble_linguistic_fingerprint(configured, mood={"mood": "calm"})
    frustrated = assemble_linguistic_fingerprint(configured, mood={"mood": "frustrated"})

    assert calm.characteristics == frustrated.characteristics
    assert calm.guidance == frustrated.guidance
    assert calm.immediate_context == "calm"
    assert frustrated.immediate_context == "frustrated"
    assert "does not change the configured voice" in frustrated.rendered_prompt_text
    different_topic = assemble_linguistic_fingerprint(
        configured,
        conversation_context="astronomy",
        self_model={"items": {
            "interest:astronomy": {
                "kind": "interest", "subject": "astronomy",
                "statement": "I enjoy astronomy.", "status": "held",
            }
        }},
    )
    assert different_topic.guidance == calm.guidance
    assert different_topic.characteristics == calm.characteristics
    assert different_topic.contextual_influences


def test_natural_variation_avoids_mandatory_humor_or_repeated_catchphrases() -> None:
    result = assemble_linguistic_fingerprint({
        "humor_style": "dry",
        "characteristic_expressions": ["Well, that is a plot twist."],
    })

    assert "do not force humor" in result.guidance
    assert "repeat signature phrases" in result.guidance
    assert "Well, that is a plot twist." in result.characteristics["characteristic_expressions"]
    assert result.rendered_prompt_text.count("Well, that is a plot twist.") == 1


def test_linguistic_context_is_bounded_deterministic_and_non_mutating() -> None:
    configured = {
        "description": "distinctive " * 2_000,
        "core_traits": ["analytical", "reflective"],
        "characteristic_expressions": ["phrase one", "phrase two"],
        "punctuation_habits": {"exclamation_frequency": "rare"},
    }
    model = {"items": {
        "interest:astronomy": {
            "kind": "interest", "subject": "astronomy",
            "statement": "I enjoy astronomy.", "status": "held",
        }
    }}
    before_configured, before_model = deepcopy(configured), deepcopy(model)
    first = assemble_linguistic_fingerprint(
        configured, conversation_context="astronomy", self_model=model
    )
    second = assemble_linguistic_fingerprint(
        configured, conversation_context="astronomy", self_model=model
    )

    assert first.as_dict() == second.as_dict()
    assert len(first.rendered_prompt_text) <= MAX_FINGERPRINT_CHARS
    assert configured == before_configured
    assert model == before_model
    assert first.characteristics["description"].startswith("distinctive")


def test_formatter_keeps_legacy_settings_and_accepts_character_configuration() -> None:
    result = format_linguistic_fingerprint({
        "communication_style": "storyteller",
        "punctuation_habits": {"exclamation_frequency": "never"},
    })

    assert "Do not use exclamation marks." in result
    assert "communication style: storyteller" in result


def test_existing_character_profile_fields_are_available_as_voice_evidence() -> None:
    from chatbot.soul.character import CharacterProfile

    profile = CharacterProfile(
        name="Mira",
        backstory="",
        personality_sliders={"curiosity": 0.9, "creativity": 0.8},
        humor_style="dry",
        communication_style="storyteller",
        favorite_topics=["astronomy", "old maps"],
    )
    result = assemble_linguistic_fingerprint(profile)

    assert result.characteristics["personality_sliders"] == "creativity=0.8, curiosity=0.9"
    assert result.characteristics["favorite_topics"] == "astronomy, old maps"
    assert "storyteller" in result.guidance
