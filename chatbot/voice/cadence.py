"""Emotional cadence mapping for text-to-speech adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class CadenceProfile:
    """Provider-neutral voice synthesis cadence parameters."""

    pitch_factor: float
    speed_factor: float
    volume_factor: float
    pause_ms: int
    emphasis_words: list[str]
    tone: str


DEFAULT_CADENCE = CadenceProfile(
    pitch_factor=1.0,
    speed_factor=1.0,
    volume_factor=1.0,
    pause_ms=300,
    emphasis_words=[],
    tone="neutral",
)


class CadenceMapper:
    """Map phase-K mood and mode strings to provider-neutral TTS cadence."""

    MOOD_PROFILES: ClassVar[dict[str, CadenceProfile]] = {
        "happy": CadenceProfile(1.04, 1.06, 1.03, 250, ["great", "glad"], "warm"),
        "sad": CadenceProfile(0.96, 0.88, 0.92, 475, ["sorry", "hard"], "gentle"),
        "angry": CadenceProfile(1.02, 1.02, 1.04, 275, ["clear", "now"], "urgent"),
        "anxious": CadenceProfile(1.01, 0.94, 0.95, 425, ["steady", "breathe"], "gentle"),
        "calm": CadenceProfile(0.98, 0.92, 0.96, 425, ["steady"], "gentle"),
        "excited": CadenceProfile(1.08, 1.1, 1.05, 225, ["yes", "love"], "excited"),
        "tired": CadenceProfile(0.94, 0.84, 0.88, 525, ["rest"], "gentle"),
        "neutral": DEFAULT_CADENCE,
        "warm": CadenceProfile(1.01, 0.98, 1.0, 325, ["with", "here"], "warm"),
        "playful": CadenceProfile(1.06, 1.08, 1.03, 240, ["fun", "little"], "excited"),
        "reflective": CadenceProfile(0.97, 0.9, 0.95, 500, ["maybe", "notice"], "gentle"),
        "direct": CadenceProfile(1.0, 1.03, 1.02, 250, ["key", "next"], "neutral"),
        "gentle": CadenceProfile(0.97, 0.9, 0.92, 500, ["safe", "slow"], "gentle"),
        "upbeat": CadenceProfile(1.05, 1.07, 1.04, 240, ["good", "bright"], "excited"),
    }

    MODE_OVERRIDES: ClassVar[dict[str, CadenceProfile]] = {
        "storytelling": CadenceProfile(0.99, 0.9, 0.98, 550, ["then", "because"], "warm"),
        "reflective": CadenceProfile(0.97, 0.88, 0.95, 575, ["feel", "mean"], "gentle"),
        "coaching": CadenceProfile(1.01, 1.02, 1.02, 275, ["next", "try"], "warm"),
        "playful": CadenceProfile(1.05, 1.08, 1.03, 225, ["fun", "yes"], "excited"),
        "serious": CadenceProfile(0.98, 0.93, 1.0, 425, ["important", "clear"], "neutral"),
        "normal": DEFAULT_CADENCE,
        "deep_talk": CadenceProfile(0.97, 0.88, 0.95, 575, ["feel", "mean"], "gentle"),
        "roleplay": CadenceProfile(1.0, 0.92, 1.0, 525, ["then", "scene"], "warm"),
        "planning": CadenceProfile(1.0, 1.04, 1.02, 250, ["first", "next"], "neutral"),
        "venting": CadenceProfile(0.97, 0.89, 0.94, 550, ["hard", "hear"], "gentle"),
        "bedtime": CadenceProfile(0.92, 0.8, 0.84, 650, ["rest", "soft"], "gentle"),
    }

    _EMPHASIS_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"\b[A-Z][A-Z0-9'-]{2,}\b|(?:\*\*|__)(?P<marked>[^*_]+)(?:\*\*|__)"
    )
    _WORD_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"[A-Za-z][A-Za-z'-]*")

    def map(self, mood: str, mode: str, text: str) -> CadenceProfile:
        """Return a cadence profile for string mood and mode labels."""

        mood_key = _normalize_label(mood)
        mood_profile = self.MOOD_PROFILES.get(mood_key)
        if mood_profile is None:
            return DEFAULT_CADENCE

        mode_profile = self.MODE_OVERRIDES.get(_normalize_label(mode))
        if mode_profile is None or mode_profile == DEFAULT_CADENCE:
            return _with_text_emphasis(mood_profile, text)

        return _with_text_emphasis(_merge_profiles(mood_profile, mode_profile), text)


def _normalize_label(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _merge_profiles(base: CadenceProfile, override: CadenceProfile) -> CadenceProfile:
    return CadenceProfile(
        pitch_factor=_clamp_factor(base.pitch_factor * override.pitch_factor),
        speed_factor=_clamp_factor(base.speed_factor * override.speed_factor),
        volume_factor=_clamp_factor(base.volume_factor * override.volume_factor),
        pause_ms=max(150, min(900, round((base.pause_ms + override.pause_ms) / 2))),
        emphasis_words=_unique_words([*base.emphasis_words, *override.emphasis_words]),
        tone=override.tone,
    )


def _with_text_emphasis(profile: CadenceProfile, text: str) -> CadenceProfile:
    emphasis = _unique_words([*profile.emphasis_words, *_extract_emphasis_words(text)])
    if emphasis == profile.emphasis_words:
        return profile
    return CadenceProfile(
        pitch_factor=profile.pitch_factor,
        speed_factor=profile.speed_factor,
        volume_factor=profile.volume_factor,
        pause_ms=profile.pause_ms,
        emphasis_words=emphasis,
        tone=profile.tone,
    )


def _extract_emphasis_words(text: str) -> list[str]:
    words: list[str] = []
    for match in CadenceMapper._EMPHASIS_PATTERN.finditer(str(text or "")):
        marked = match.group("marked")
        source = marked if marked is not None else match.group(0)
        for word in CadenceMapper._WORD_PATTERN.findall(source):
            words.append(word.lower())
    return words


def _unique_words(words: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for word in words:
        cleaned = word.strip().lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique.append(cleaned)
    return unique


def _clamp_factor(value: float) -> float:
    return round(max(0.5, min(1.5, value)), 3)


__all__ = ["CadenceMapper", "CadenceProfile", "DEFAULT_CADENCE"]
