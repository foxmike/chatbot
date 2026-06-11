"""Tests for the anti-repetition directive generation module."""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.soul.anti_repetition import (
    AntiRepetitionConfig,
    analyze_closing_questions,
    analyze_sentence_lengths,
    detect_repeated_phrases,
    detect_repeated_starters,
    detect_repeated_words,
    generate_anti_repetition_directives,
    get_anti_repetition_directives,
)


def test_detect_repeated_words() -> None:
    texts = [
        "Actually, I think that is a great idea. I think we should explore it.",
        "Actually, let's explore that option. I think we can make it work.",
        "Actually, let's explore it now.",
    ]
    # "think", "actually", "explore" should be detected as repeated
    repeated = detect_repeated_words(texts)
    assert "think" in repeated
    assert "actually" in repeated
    assert "explore" in repeated
    # Stop words like "i", "that", "is", "a", "we", "should" should not be in there
    assert "that" not in repeated
    assert "should" not in repeated


def test_detect_repeated_starters() -> None:
    texts = [
        "Actually, I think that is a great idea. I think we should explore it.",
        "Actually, let's explore that option. I think we can make it work.",
        "Actually, let's explore it now.",
    ]
    starters = detect_repeated_starters(texts)
    # "actually" is repeated 3 times, "i think we" is repeated 2 times
    assert "actually" in starters
    assert "i think we" in starters


def test_detect_repeated_phrases() -> None:
    texts = [
        "Actually, let's explore that option. I think we can make it work.",
        "Actually, let's explore it now.",
    ]
    phrases = detect_repeated_phrases(texts)
    assert "actually let's explore" in phrases
    assert "let's explore" not in phrases  # suppressed by "actually let's explore" since same count


def test_analyze_sentence_lengths_monotonous() -> None:
    # All sentences are short and around 4-5 words
    texts = [
        "Hello there my friend. How are you today. I am doing good. Let us go now.",
    ]
    warning = analyze_sentence_lengths(texts)
    assert warning is not None
    assert "Vary sentence lengths" in warning


def test_analyze_sentence_lengths_varied() -> None:
    # Sentences have varied lengths
    texts = [
        "Hello.",
        "This is a much longer sentence that has quite a few words to introduce variety.",
        "Yes, indeed.",
        "We are testing the standard deviation of these lengths.",
    ]
    warning = analyze_sentence_lengths(texts)
    assert warning is None


def test_analyze_closing_questions() -> None:
    # Ends with question 3 times
    texts = [
        "How is the weather?",
        "Do you want to play a game?",
        "Are you sure about that?",
    ]
    warning = analyze_closing_questions(texts)
    assert warning is not None
    assert "Avoid ending your responses with questions" in warning
    assert "weather?" in warning or "game?" in warning or "sure" in warning


def test_get_anti_repetition_directives() -> None:
    history = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Actually, I think that is a great idea. What do you think?"},
        {"role": "user", "content": "It is"},
        {"role": "assistant", "content": "Actually, let's explore that option. What do you think?"},
        {"role": "user", "content": "Indeed"},
        {"role": "assistant", "content": "Actually, let's explore it now. What do you think?"},
    ]

    directives = get_anti_repetition_directives(history)
    assert len(directives) > 0
    # Starter, word, phrase, and closing question repetition should trigger directives
    directives_str = "\n".join(directives)
    assert "actually" in directives_str
    assert "explore" in directives_str
    assert "questions" in directives_str


def test_generate_anti_repetition_directives_empty() -> None:
    assert generate_anti_repetition_directives([]) == ""
