"""Intent classification for incoming user messages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Sequence


class IntentType(str, Enum):
    """Predefined categories of user message intents."""

    GREETING = "greeting"
    QUESTION = "question"
    COMMAND = "command"
    ROLEPLAY = "roleplay"
    PLANNING = "planning"
    VENTING = "venting"
    FEEDBACK = "feedback"
    CASUAL = "casual"


# Alias for convenience and backward compatibility
Intent = IntentType


@dataclass(frozen=True, slots=True)
class IntentClassification:
    """The result of intent classification on a message."""

    intent: IntentType
    confidence: float
    matched_keywords: list[str] = field(default_factory=list)


class IntentClassifier:
    """Classifies user messages into predefined intents using rule-based/regex matching."""

    def __init__(
        self,
        custom_patterns: Mapping[IntentType, Sequence[str]] | None = None,
        custom_weights: Mapping[IntentType, float] | None = None,
    ) -> None:
        # Define default patterns
        self._patterns: dict[IntentType, list[str]] = {
            IntentType.GREETING: [
                r"\b(hello|hi|hey|howdy|yo|greetings|whats\s+up|what's\s+up|good\s+morning|good\s+afternoon|good\s+evening)\b"
            ],
            IntentType.COMMAND: [
                r"\b(stop|exit|quit|reset|clear|settings|configure|delete|change\s+mode|cancel|disable|enable)\b"
            ],
            IntentType.ROLEPLAY: [
                r"\b(act\s+as|as\s+if\s+you\s+are|be\s+my\s+character|in\s+character|let's\s+roleplay|lets\s+roleplay|pretend\s+you\s+are|role\s+play|role-play|roleplay|you\s+are\s+playing)\b"
            ],
            IntentType.PLANNING: [
                r"\b(action\s+plan|break\s+this\s+down|can\s+you\s+plan|checklist|figure\s+out|goal\s+is|goals\s+are|help\s+me\s+plan|i\s+need\s+a\s+plan|make\s+a\s+plan|next\s+steps|organize\s+this|plan\s+for|planning|prioritize|roadmap|schedule|step\s+by\s+step|strategy|to-do|todo|to\s+do)\b"
            ],
            IntentType.VENTING: [
                r"\b(afraid|ashamed|childhood|confused\s+about\s+my\s+life|deep\s+down|depressed|feel\s+empty|grief|guilty|heartbroken|i\s+feel|i\s+have\s+been\s+feeling|i\s+keep\s+thinking|i\s+never\s+told|i'm\s+scared|im\s+scared|lonely|my\s+family|my\s+relationship|personal|private|trauma|vulnerable|sad|hurt|cry|crying|upset|angry|frustrated|anxious|stress|stressed)\b"
            ],
            IntentType.FEEDBACK: [
                r"\b(thank\s+you|thanks|good\s+job|great|awesome|helpful|bad|terrible|excellent|perfect|love\s+it|hate\s+it|good\s+response|bad\s+response)\b"
            ],
            IntentType.QUESTION: [
                r"^\s*(who|what|why|where|how|when|which|can|could|would|is|are|do|does|did|will|should|have|has)\b",
                r"\b(who|what|why|where|how|when|which)\b",
                r"\?"
            ]
        }

        # Override/Extend with custom patterns if provided
        if custom_patterns:
            for intent_type, regexes in custom_patterns.items():
                self._patterns[intent_type] = list(regexes)

        # Compile regular expressions
        self._compiled_patterns: dict[IntentType, list[re.Pattern]] = {}
        for intent_type, regexes in self._patterns.items():
            self._compiled_patterns[intent_type] = [
                re.compile(r, re.IGNORECASE) for r in regexes
            ]

        # Define default confidence weights/priorities
        self._weights = {
            IntentType.COMMAND: 0.95,
            IntentType.ROLEPLAY: 0.90,
            IntentType.PLANNING: 0.85,
            IntentType.VENTING: 0.80,
            IntentType.FEEDBACK: 0.75,
            IntentType.GREETING: 0.70,
            IntentType.QUESTION: 0.60,
            IntentType.CASUAL: 0.0,
        }

        # Override weights if provided
        if custom_weights:
            for intent_type, weight in custom_weights.items():
                self._weights[intent_type] = weight

    def classify(self, message: str) -> IntentClassification:
        """Categorize an incoming user message into an intent."""
        if not message or not message.strip():
            return IntentClassification(intent=IntentType.CASUAL, confidence=1.0)

        cleaned_message = message.strip()

        matches: dict[IntentType, list[str]] = {}

        # Scan for pattern matches
        for intent_type, compiled_list in self._compiled_patterns.items():
            matched_substrings = []
            for pattern in compiled_list:
                for match in pattern.finditer(cleaned_message):
                    matched_substrings.append(match.group())
            if matched_substrings:
                matches[intent_type] = matched_substrings

        if not matches:
            return IntentClassification(intent=IntentType.CASUAL, confidence=1.0)

        # Find the matched intent with the highest weight
        best_intent = IntentType.CASUAL
        best_weight = 0.0
        best_keywords: list[str] = []

        for intent_type, keywords in matches.items():
            weight = self._weights.get(intent_type, 0.0)
            # Boost confidence slightly if multiple distinct keywords of the same intent match, maxing out at 1.0
            unique_keywords = list(set(keywords))
            confidence_boost = min(0.05 * (len(unique_keywords) - 1), 1.0 - weight)
            final_weight = weight + confidence_boost

            if final_weight > best_weight:
                best_intent = intent_type
                best_weight = final_weight
                best_keywords = unique_keywords

        return IntentClassification(
            intent=best_intent,
            confidence=round(best_weight, 2),
            matched_keywords=best_keywords,
        )


__all__ = [
    "Intent",
    "IntentClassification",
    "IntentClassifier",
    "IntentType",
]
