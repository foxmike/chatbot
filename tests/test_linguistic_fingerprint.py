from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.soul.linguistic_fingerprint import (
    LinguisticFingerprint,
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
