"""Anti-repetition directive generation module for companion prompt dynamics."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Optional

DEFAULT_STOP_WORDS: set[str] = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "for", "with", "at", "by",
    "from", "on", "is", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "this", "that", "these", "those", "then", "there", "here",
    "it", "its", "they", "them", "their", "he", "him", "his", "she", "her", "hers",
    "you", "your", "yours", "we", "us", "our", "ours", "i", "me", "my", "mine", "myself",
    "yourself", "himself", "herself", "itself", "ourselves", "themselves",
    "about", "above", "after", "again", "against", "all", "am", "any", "are", "aren't",
    "as", "because", "before", "below", "between", "both", "can", "can't",
    "cannot", "could", "couldn't", "didn't", "doesn't", "doing",
    "don't", "down", "during", "each", "few", "further", "hadn't",
    "hasn't", "haven't", "having", "how", "how's", "if", "into",
    "isn't", "it's", "itself", "let's", "more", "most", "mustn't",
    "no", "nor", "not", "off", "once", "only", "other", "ought",
    "out", "over", "own", "same", "shan't", "should", "shouldn't", "so", "some", "such",
    "than", "that's", "theirs", "thence", "there's", "they'd", "they'll", "they're",
    "they've", "through", "too", "under", "until", "up", "very", "wasn't", "we'd",
    "we'll", "we're", "we've", "weren't", "what", "what's", "when", "when's",
    "where", "where's", "which", "while", "who", "who's", "whom", "why", "why's",
    "won't", "would", "wouldn't", "you'd", "you'll", "you're", "you've",
    "yourselves"
}


@dataclass(frozen=True, slots=True)
class AntiRepetitionConfig:
    """Configuration options for the anti-repetition analyzer."""

    max_messages: int = 5
    min_word_doc_frequency: int = 2
    min_word_total_count: int = 3
    min_starter_repeats: int = 2
    max_starter_length: int = 3
    min_phrase_repeats: int = 2
    min_phrase_length: int = 2
    max_phrase_length: int = 4
    min_closing_questions: int = 2


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase words, stripping punctuation except apostrophes."""
    cleaned = re.sub(r"[^\w\s']", " ", text.lower())
    return [w for w in cleaned.split() if w]


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences using simple terminal punctuation boundaries."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s.strip() for s in sentences if s.strip()]


def _extract_assistant_texts(history: list[Any], max_messages: int) -> list[str]:
    """Extract up to max_messages recent assistant response strings from history."""
    texts: list[str] = []
    # Process from newest to oldest, but collect in chronological order
    for item in reversed(history):
        if len(texts) >= max_messages:
            break

        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict):
            role = item.get("role")
            content = item.get("content") or item.get("text")
            if role == "assistant" and isinstance(content, str):
                texts.append(content)
        elif hasattr(item, "role") and hasattr(item, "content"):
            role = getattr(item, "role")
            content = getattr(item, "content")
            if role == "assistant" and isinstance(content, str):
                texts.append(content)

    texts.reverse()
    return texts


def detect_repeated_words(
    texts: list[str],
    *,
    min_doc_frequency: int = 2,
    min_total_count: int = 3,
    ignore_words: set[str] | None = None,
) -> list[str]:
    """Identify words used repeatedly across the provided text segments."""
    if not texts:
        return []
    if ignore_words is None:
        ignore_words = DEFAULT_STOP_WORDS

    word_counts: Counter[str] = Counter()
    word_docs: Counter[str] = Counter()

    for text in texts:
        words = _tokenize(text)
        unique_words = set(words)
        for w in words:
            if w not in ignore_words and len(w) > 2:
                word_counts[w] += 1
        for w in unique_words:
            if w not in ignore_words and len(w) > 2:
                word_docs[w] += 1

    repeated: list[str] = []
    for word, count in word_counts.items():
        doc_freq = word_docs[word]
        if count >= min_total_count or doc_freq >= min_doc_frequency:
            repeated.append(word)

    repeated.sort(key=lambda w: (-word_counts[w], w))
    return repeated


def detect_repeated_starters(
    texts: list[str],
    *,
    min_repeats: int = 2,
    max_starter_length: int = 3,
) -> list[str]:
    """Identify sentence-starting phrases that are repeatedly used."""
    all_sentences: list[str] = []
    for text in texts:
        all_sentences.extend(_split_sentences(text))

    if not all_sentences:
        return []

    starter_counts: Counter[str] = Counter()
    for sentence in all_sentences:
        words = _tokenize(sentence)
        if not words:
            continue
        for length in range(1, min(len(words), max_starter_length) + 1):
            starter = " ".join(words[:length])
            starter_counts[starter] += 1

    repeated_starters: list[tuple[str, int]] = []
    for starter, count in starter_counts.items():
        if count >= min_repeats:
            repeated_starters.append((starter, count))

    # Deduplicate starters: sort by length descending to suppress prefixes with same count
    repeated_starters.sort(key=lambda x: (-len(x[0].split()), -x[1]))

    final_starters: list[str] = []
    for starter, count in repeated_starters:
        is_subphrase = False
        for longer in final_starters:
            if longer.startswith(starter + " ") and starter_counts[longer] == count:
                is_subphrase = True
                break
        if not is_subphrase:
            final_starters.append(starter)

    return final_starters


def detect_repeated_phrases(
    texts: list[str],
    *,
    min_repeats: int = 2,
    min_phrase_length: int = 2,
    max_phrase_length: int = 4,
    ignore_words: set[str] | None = None,
) -> list[str]:
    """Identify multi-word phrases (n-grams) that are repeatedly used."""
    if not texts:
        return []
    if ignore_words is None:
        ignore_words = DEFAULT_STOP_WORDS

    phrase_counts: Counter[str] = Counter()
    for text in texts:
        words = _tokenize(text)
        for n in range(min_phrase_length, min(len(words), max_phrase_length) + 1):
            for i in range(len(words) - n + 1):
                phrase_words = words[i : i + n]
                if all(w in ignore_words for w in phrase_words):
                    continue
                phrase = " ".join(phrase_words)
                phrase_counts[phrase] += 1

    repeated_phrases: list[tuple[str, int]] = []
    for phrase, count in phrase_counts.items():
        if count >= min_repeats:
            repeated_phrases.append((phrase, count))

    # Deduplicate subphrases: sort by length descending
    repeated_phrases.sort(key=lambda x: (-len(x[0].split()), -x[1]))
    final_phrases: list[str] = []
    for phrase, count in repeated_phrases:
        is_subphrase = False
        for longer in final_phrases:
            if phrase in longer and phrase_counts[longer] == count:
                is_subphrase = True
                break
        if not is_subphrase:
            final_phrases.append(phrase)

    return final_phrases


def analyze_sentence_lengths(texts: list[str]) -> Optional[str]:
    """Examine sentence length variation and generate warning if monotonous."""
    sentences: list[str] = []
    for text in texts:
        sentences.extend(_split_sentences(text))

    if len(sentences) < 4:
        return None

    lengths = [len(_tokenize(s)) for s in sentences if _tokenize(s)]
    if not lengths:
        return None

    mean_len = sum(lengths) / len(lengths)
    variance = sum((x - mean_len) ** 2 for x in lengths) / len(lengths)
    std_dev = math.sqrt(variance)

    if std_dev < 2.0:
        if mean_len < 8:
            return (
                "Vary sentence lengths: recent responses consist almost exclusively of very short, "
                f"simple sentences (average length {mean_len:.1f} words). Mix in some longer, complex sentences."
            )
        elif mean_len > 20:
            return (
                "Vary sentence lengths: recent responses consist almost exclusively of long, "
                f"complex sentences (average length {mean_len:.1f} words). Mix in some shorter, punchier sentences."
            )
        else:
            return (
                "Vary sentence lengths and structures: recent responses show a monotonous rhythm "
                f"(sentences are all around {mean_len:.0f} words long). Introduce variation in your pacing."
            )
    return None


def analyze_closing_questions(texts: list[str], min_questions: int = 2) -> Optional[str]:
    """Check if the assistant is concluding responses with questions repeatedly."""
    if not texts:
        return None

    question_ends = 0
    total_non_empty = 0
    last_questions: list[str] = []

    for text in texts:
        sentences = _split_sentences(text)
        if not sentences:
            continue
        total_non_empty += 1
        last_sentence = sentences[-1].strip()
        if last_sentence.endswith("?"):
            question_ends += 1
            if len(last_questions) < 2:
                last_questions.append(last_sentence)

    if total_non_empty >= 3 and question_ends >= min_questions:
        examples_str = ", ".join(f'"{q}"' for q in last_questions)
        return (
            f"Avoid ending your responses with questions (e.g., {examples_str}). "
            f"You have asked a question at the end of {question_ends} of the last {total_non_empty} messages."
        )
    return None


def get_anti_repetition_directives(
    history: list[Any],
    config: Optional[AntiRepetitionConfig] = None,
) -> list[str]:
    """Analyze conversation history and return a list of specific anti-repetition prompt directives."""
    if config is None:
        config = AntiRepetitionConfig()

    assistant_texts = _extract_assistant_texts(history, config.max_messages)
    if not assistant_texts:
        return []

    directives: list[str] = []

    # 1. Word repetition
    words = detect_repeated_words(
        assistant_texts,
        min_doc_frequency=config.min_word_doc_frequency,
        min_total_count=config.min_word_total_count,
    )
    if words:
        words_str = ", ".join(f'"{w}"' for w in words)
        directives.append(f"Vary your vocabulary: avoid repeating the words: {words_str}.")

    # 2. Phrase repetition
    phrases = detect_repeated_phrases(
        assistant_texts,
        min_repeats=config.min_phrase_repeats,
        min_phrase_length=config.min_phrase_length,
        max_phrase_length=config.max_phrase_length,
    )
    if phrases:
        phrases_str = ", ".join(f'"{p}"' for p in phrases)
        directives.append(f"Avoid repeating phrases like: {phrases_str}.")

    # 3. Sentence starter repetition
    starters = detect_repeated_starters(
        assistant_texts,
        min_repeats=config.min_starter_repeats,
        max_starter_length=config.max_starter_length,
    )
    if starters:
        starters_str = ", ".join(f'"{s}"' for s in starters)
        directives.append(f"Avoid starting sentences with: {starters_str}.")

    # 4. Sentence length monotony
    len_directive = analyze_sentence_lengths(assistant_texts)
    if len_directive:
        directives.append(len_directive)

    # 5. Closing question patterns
    closing_directive = analyze_closing_questions(
        assistant_texts,
        min_questions=config.min_closing_questions,
    )
    if closing_directive:
        directives.append(closing_directive)

    return directives


def format_anti_repetition_directives(directives: list[str]) -> str:
    """Format a list of directives into a system prompt section."""
    if not directives:
        return ""
    bullet_points = "\n".join(f"- {d}" for d in directives)
    return f"Anti-Repetition Directives:\n{bullet_points}"


def generate_anti_repetition_directives(
    history: list[Any],
    config: Optional[AntiRepetitionConfig] = None,
) -> str:
    """Analyze conversation history and return formatted prompt directives to prevent repetition."""
    directives = get_anti_repetition_directives(history, config)
    return format_anti_repetition_directives(directives)


__all__ = [
    "AntiRepetitionConfig",
    "analyze_closing_questions",
    "analyze_sentence_lengths",
    "detect_repeated_phrases",
    "detect_repeated_starters",
    "detect_repeated_words",
    "format_anti_repetition_directives",
    "generate_anti_repetition_directives",
    "get_anti_repetition_directives",
]
