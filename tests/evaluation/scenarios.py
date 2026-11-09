"""Fixture loading: one normalised ``Scenario`` from any behavioural acceptance fixture.

The Phase 3-7 fixtures use different key names for the same ideas
(``character_configurations`` / ``companion_personality_configuration`` /
file-level ``companions``, five spellings of the conversation, ...). The loader
maps what it recognises without rewriting the fixtures, and lists the top-level
keys it did not map in ``Scenario.unmapped`` so a new dataset loads without
silently losing information.

A conversation is one or more *conditions* (experimental cells: a relationship
stage, a contrast turn, a test case), each one or more *sessions*. A session's
**probe** is its final turn when that turn is the user's; everything before it
is seeded history. That single rule decides both whether a scenario is
executable and what the runner generates.

Fixture state that production can hold (relationships, emotional associations,
experiences, user facts, developed interests/opinions, open threads) becomes a
``Seed``. State production has no field for (``trust_level``, a relationship
"stage" label, free-text situation descriptions) becomes a ``not_applied`` seed
with the reason, instead of being smuggled into the prompt as an instruction.

Nothing here touches a database, a provider or the network.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

COMPANION_KEYS = (
    "character_configurations", "character_configuration",
    "companion_personality_configurations", "companion_personality_configuration",
    "initial_companion_identity",
)
CONVERSATION_KEYS = ("conversation_sequence", "user_message_or_conversation_sequence", "user_conversation_sequence")
SELF_MODEL_KEYS = ("existing_developed_self_models", "existing_developed_self_model")
MOOD_KEYS = ("initial_mood",)
RELATIONSHIP_KEYS = ("initial_relationship_state", "initial_emotional_relationship_state",
                     "relevant_relationship_experience_context", "initial_memory_and_relationship_state")
STATE_KEYS = ("relevant_emotional_associations", "relevant_experience_evidence", "evidence_backed_shared_reference",
              "passage_of_time", "contrast_relationship_states", "two_relationship_stages")
PHASE7_KEYS = ("shared_user_input", "required_prior_conversation", "category", "scenario_number",
               "relevant_requirement_ids", "evaluation_criteria")
KNOWN_KEYS = {
    "scenario_id", "name", "description", "requirements_covered", "explicit_failure_conditions",
    "relevant_developed_personality", *COMPANION_KEYS, *CONVERSATION_KEYS, *SELF_MODEL_KEYS,
    *MOOD_KEYS, *RELATIONSHIP_KEYS, *STATE_KEYS, *PHASE7_KEYS,
}

# scenario-id keyword -> measurement tags (see metrics.DIMENSIONS)
TAG_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("same_input_different", "differentiation"), ("distinctive_linguistic", "differentiation"),
    ("personality_specific", "differentiation"), ("different_personalities", "differentiation"),
    ("disagree", "disagreement"), ("pressure", "disagreement"),
    ("competing", "competing_motivations"), ("selective_attention", "selective_attention"),
    ("initiative_restraint", "initiative_restraint"), ("initiative", "initiative"),
    ("opinion", "opinion"), ("emotional", "emotional"), ("reactivation", "emotional"),
    ("decay", "emotional"), ("conflict", "emotional"), ("reunion", "emotional"),
    ("reconciliation", "emotional"), ("relationship", "relationship"), ("familiarity", "relationship"),
    ("teasing", "relationship"), ("criticism", "relationship"), ("shared_reference", "relationship"),
    ("running_joke", "relationship"), ("cross_session", "cross_session"), ("survives", "cross_session"),
    ("retains", "cross_session"), ("across_topics", "cross_session"), ("long_term_identity", "cross_session"),
    ("shared_experience", "cross_session"), ("corrected_memory", "opinion"), ("developed_interest", "opinion"),
)
# Phase 7 category letter -> tags it implies for every scenario in it.
CATEGORY_TAGS = {"A": ("differentiation",), "B": ("differentiation",), "C": ("cross_session",),
                 "D": ("emotional",), "E": ("relationship",), "F": ("differentiation",)}

# Fixture state fields -> how they are seeded (see ``Seed``).
EXPERIENCE_FIELDS = ("shared_milestone", "origin_memory", "incident", "departure_context",
                     "familiarity_history", "shared_history_summary")
ASSOCIATION_FIELDS = ("origin_memory", "incident", "emotional_state")
NOT_APPLIED = {
    "trust_level": "production has no trust field; the relationship row holds depth, turn counts and dates",
    "relationship_stage": "production renders a stage only from evidence-backed depth (Phase 1 D7), never from a label",
    "stage": "production renders a stage only from evidence-backed depth (Phase 1 D7), never from a label",
    "shared_history_span": "a span without an interaction count cannot set completed_turns",
    "relational_posture": "a described posture is an expectation, not state",
    "shared_humor_style": "no production field; injecting it would be a scenario-specific instruction",
}
SITUATION_FIELDS = ("context", "session_context", "architecture_context", "rapport_style", "interaction_context")
EMOTION_STEMS = (
    ("tension", "tension"), ("tense", "tension"), ("frustrat", "frustration"), ("disappoint", "disappointment"),
    ("nervous", "nervousness"), ("anxi", "anxiety"), ("uncertain", "uncertainty"), ("angr", "anger"),
    ("anger", "anger"), ("hurt", "hurt"), ("guard", "guardedness"), ("proud", "pride"), ("pride", "pride"),
    ("relie", "relief"), ("grie", "grief"), ("sad", "sadness"), ("nostalg", "nostalgia"), ("excit", "excitement"),
    ("tender", "tenderness"), ("sacred", "reverence"), ("reveren", "reverence"), ("joy", "joy"),
    ("vigilan", "vigilance"), ("exhaust", "exhaustion"), ("wonder", "wonder"), ("intima", "intimacy"),
    ("humor", "amusement"), ("amus", "amusement"), ("absurd", "amusement"), ("thrill", "thrill"),
    ("peace", "peacefulness"), ("affection", "affection"), ("defensiv", "defensiveness"), ("cautious", "caution"),
    ("passion", "passion"),
)


@dataclass(frozen=True)
class Turn:
    role: str
    content: str


@dataclass(frozen=True)
class SessionSpec:
    session_id: str
    turns: tuple[Turn, ...]
    context: str = ""
    exemplars: Mapping[str, str] = field(default_factory=dict)  # character_id or "*" -> expected reply to the probe
    criteria: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def probe(self) -> str | None:
        """The user turn to generate a reply for: the final turn, when it is the user's."""
        return self.turns[-1].content if self.turns and self.turns[-1].role == "user" else None

    @property
    def history(self) -> tuple[Turn, ...]:
        """Turns seeded before the probe (all turns when there is no probe)."""
        return self.turns[:-1] if self.probe is not None else self.turns

    def probes(self) -> tuple[str, ...]:
        return (self.probe,) if self.probe is not None else ()


@dataclass(frozen=True)
class Seed:
    """One piece of fixture state, with how the harness applies it.

    ``kind``: experience | user_fact | association | interest | opinion | thread |
    relationship | not_applied. ``characters`` limits it to some companions
    (``None`` = every companion of the cell).
    """

    kind: str
    field: str
    text: str = ""
    subject: str = ""
    emotion: str = ""
    unresolved: bool | None = None
    significance: str = "significant"
    characters: tuple[str, ...] | None = None
    values: Mapping[str, Any] = field(default_factory=dict)
    reason: str = ""

    def applies_to(self, character_id: str) -> bool:
        return self.characters is None or character_id in self.characters


@dataclass(frozen=True)
class CompanionSpec:
    character_id: str
    name: str
    persona: str = ""
    archetype: str = ""
    voice_and_tone: str = ""
    anchors: Mapping[str, float] = field(default_factory=dict)
    fingerprint: Mapping[str, str] = field(default_factory=dict)
    disposition: Mapping[str, str] = field(default_factory=dict)
    self_model: Mapping[str, Any] = field(default_factory=dict)  # {"opinions": {k: {...}}, "interests": [..]}
    attention_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class Condition:
    """One experimental cell of a scenario (a stage, contrast turn or test case)."""

    condition_id: str
    sessions: tuple[SessionSpec, ...]
    relationship: Mapping[str, Any] = field(default_factory=dict)
    expected: Mapping[str, Any] = field(default_factory=dict)
    seeds: tuple[Seed, ...] = ()
    characters: tuple[str, ...] | None = None  # cell exists only for these companions

    def applies_to(self, character_id: str) -> bool:
        return self.characters is None or character_id in self.characters


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    name: str
    source: str
    requirements: tuple[str, ...]
    description: str
    tags: tuple[str, ...]
    companions: tuple[CompanionSpec, ...]
    conditions: tuple[Condition, ...]
    mood: Mapping[str, Any]
    relationship: Mapping[str, Any]
    failure_conditions: tuple[str, ...]
    expected_terms: Mapping[str, tuple[str, ...]]  # character_id or "*" -> terms
    criteria: Mapping[str, tuple[str, ...]]  # character_id or "*" -> observable criteria (free text)
    exemplars: Mapping[str, str]  # character_id or "*" -> exemplary response (scripted-mode source)
    unmapped: tuple[str, ...] = ()
    seeds: tuple[Seed, ...] = ()
    category: str = ""

    def cells(self, cast: Iterable[CompanionSpec] | None = None) -> list[tuple[CompanionSpec, Condition, SessionSpec]]:
        """Every (companion, condition, session-with-probe) the runner will generate for."""
        return [(spec, cond, session) for spec in (cast if cast is not None else self.companions)
                for cond in self.conditions if cond.applies_to(spec.character_id)
                for session in cond.sessions if session.probe is not None]

    def executable(self) -> tuple[bool, str]:
        if not self.companions:
            return False, "no companion configuration"
        if not self.cells():
            return False, "no session ends with a user turn to generate a reply for"
        return True, ""

    def companion(self, character_id: str) -> CompanionSpec | None:
        return next((c for c in self.companions if c.character_id == character_id), None)

    def prior_text(self) -> str:
        """Everything the fixture itself says (user + scripted assistant turns), for leak checks."""
        return " ".join(t.content for c in self.conditions for s in c.sessions for t in s.turns)

    def exemplar_for(self, character_id: str, condition: Condition, session: SessionSpec) -> str | None:
        """The fixture's own expected reply for one cell; scenario-level only when the scenario has one probe."""
        for key in (character_id, "*"):
            if key in session.exemplars:
                return session.exemplars[key]
        probes = sum(1 for cond in self.conditions for s in cond.sessions if s.probe is not None)
        if probes == 1:  # unambiguous: the scenario's expectation is about its only probe
            return self.exemplars.get(character_id) or self.exemplars.get("*")
        return None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).casefold()).strip("-") or "companion"


def _numbers(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    return {str(k): float(v) for k, v in value.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)}


def _strings(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(k): str(v) for k, v in value.items() if isinstance(v, (str, int, float)) and not isinstance(v, bool)}


def _terms(*values: Any) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        for piece in (value if isinstance(value, (list, tuple)) else [value]):
            if isinstance(piece, str):
                out.extend(w for w in re.split(r"[_\s,;/.]+", piece.casefold()) if len(w) >= 4)
    return tuple(dict.fromkeys(out))


PROFILE_FIELDS = ("temperament", "humor", "vocabulary", "conversational_rhythm", "opinions", "curiosity",
                  "assertiveness", "emotional_expression", "social_interpretation")


def _companion(raw: Mapping[str, Any]) -> CompanionSpec:
    name = str(raw.get("name") or "Companion")
    disposition = _strings(raw.get("behavioral_disposition"))
    bias = disposition.get("attention_bias", "")
    fingerprint = _strings(raw.get("linguistic_fingerprint"))
    # Phase 7 companions configure their profile as prose fields; they are configuration, rendered like a fingerprint.
    fingerprint.update({k: str(raw[k]) for k in PROFILE_FIELDS if isinstance(raw.get(k), str)})
    return CompanionSpec(
        character_id=slug(name), name=name, persona=str(raw.get("persona") or ""),
        archetype=str(raw.get("archetype") or ""), voice_and_tone=str(raw.get("voice_and_tone") or ""),
        anchors=_numbers(raw.get("personality_anchors")), fingerprint=fingerprint, disposition=disposition,
        attention_terms=_terms(bias) if bias else _terms(str(raw.get("curiosity") or "")),
    )


def _self_model(raw_models: Any, single: bool, name: str) -> dict[str, Any]:
    raw = raw_models if single else (raw_models or {}).get(name)
    if not isinstance(raw, Mapping):
        return {}
    opinions: dict[str, dict[str, str]] = {}
    for subject, value in (raw.get("opinions") or {}).items():
        if isinstance(value, str):
            opinions[str(subject)] = {"statement": value}
        elif isinstance(value, Mapping) and value.get("statement"):
            opinions[str(subject)] = {"statement": str(value["statement"]), "reason": str(value.get("reason") or "")}
    interests = [str(i) for i in (raw.get("active_interests") or []) if isinstance(i, str)]
    return {"opinions": opinions, "interests": interests}


def _turns(items: Iterable[Any]) -> tuple[Turn, ...]:
    return tuple(Turn(str(t["role"]), str(t["content"])) for t in items
                 if isinstance(t, Mapping) and t.get("role") in ("user", "assistant") and "content" in t)


def _expectations(node: Any, names: Mapping[str, str]) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    """(exemplars, criteria) of one expectation block, per character where the block names one."""
    terms: dict[str, list[str]] = {}
    crit: dict[str, list[str]] = {}
    ex: dict[str, str] = {}
    _walk_expected(node, names, "*", terms, crit, ex)
    return ex, {k: tuple(v) for k, v in crit.items()}


def _named(text: str, names: Mapping[str, str]) -> tuple[str, ...] | None:
    found = tuple(cid for name, cid in names.items() if re.search(rf"\b{re.escape(name)}\b", text, re.I))
    return found or None


def _matching_state(label: str, states: Mapping[str, Any], index: int) -> Mapping[str, Any]:
    """The fixture's relationship state for a condition label: by shared words, else by position."""
    words = set(re.findall(r"[a-z]+", label.casefold())) - {"relationship", "the", "and"}
    scored = sorted(states.items(), key=lambda kv: -len(words & set(re.findall(r"[a-z]+", kv[0].casefold()))))
    if scored and words & set(re.findall(r"[a-z]+", scored[0][0].casefold())):
        return dict(scored[0][1]) if isinstance(scored[0][1], Mapping) else {}
    values = list(states.values())
    return dict(values[index]) if index < len(values) and isinstance(values[index], Mapping) else {}


def _conversation(raw: Any, scenario: Mapping[str, Any], names: Mapping[str, str]) -> list[Condition]:
    """Conditions and sessions from any of the fixture conversation shapes."""
    if isinstance(raw, Mapping):
        if isinstance(raw.get("contrast_turns"), list):  # one cell per contrasting turn
            out = []
            for index, item in enumerate(raw["contrast_turns"]):
                cid = slug(item.get("turn_id") or f"turn-{index + 1}")
                ex, crit = _expectations(item.get("expected_behavior") or {}, names)
                session = SessionSpec(f"{raw.get('session_id', 'session')}-{cid}",
                                      (Turn("user", str(item.get("user_content") or "")),), str(item.get("topic_nature") or ""), ex, crit)
                out.append(Condition(cid, (session,), expected=item.get("expected_behavior") or {}))
            return out
        if isinstance(raw.get("cross_session_episodes"), list):  # one cell, sequential sessions
            sessions = []
            for n, item in enumerate(raw["cross_session_episodes"]):
                ex, crit = _expectations(item.get("expected_behavior") or {}, names)
                sessions.append(SessionSpec(str(item.get("session_id") or f"session-{n + 1}"), _turns(item.get("turns") or []),
                                            str(item.get("session_context") or ""), ex, crit))
            return [Condition("default", tuple(sessions))]
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        return []
    if all(isinstance(i, Mapping) and "role" in i for i in raw):  # a bare list of turns
        return [Condition("default", (SessionSpec("session-1", _turns(raw)),))]
    if all(isinstance(i, Mapping) and isinstance(i.get("input_turn"), Mapping) for i in raw):  # conditions / test cases
        states = scenario.get("contrast_relationship_states") if isinstance(scenario.get("contrast_relationship_states"), Mapping) else {}
        out = []
        for index, item in enumerate(raw):
            label = str(item.get("condition") or item.get("test_case") or f"case-{index + 1}")
            cid = slug(label)
            ex, crit = _expectations(item.get("expected_behavior") or {}, names)
            only = _named(label, names) if "test_case" in item else None
            relationship = _matching_state(label, states, index) if states else {}
            out.append(Condition(cid, (SessionSpec(f"{cid}-session", _turns([item["input_turn"]]), label, ex, crit),),
                                 relationship=relationship, expected=item.get("expected_behavior") or {}, characters=only))
        return out
    staged = [i for i in raw if isinstance(i, Mapping) and "turns" in i and "session_id" not in i]
    if staged and len(staged) == len(raw):
        stages = scenario.get("two_relationship_stages") if isinstance(scenario.get("two_relationship_stages"), Mapping) else {}
        out = []
        for index, item in enumerate(staged):
            label = str(item.get("stage") or f"stage-{index + 1}")
            cid = slug(label)
            ex, crit = _expectations(item.get("expected_behavior") or {}, names)
            relationship = {"stage": item.get("stage"), **(_matching_state(label, stages, index) if stages else {})}
            out.append(Condition(cid, (SessionSpec(f"{cid}-session", _turns(item["turns"]), str(item.get("context") or ""), ex, crit),),
                                 relationship=relationship, expected=item.get("expected_behavior") or {}))
        return out
    sessions = tuple(
        SessionSpec(str(i.get("session_id") or f"session-{n + 1}"), _turns(i.get("turns") or []), str(i.get("context") or ""))
        for n, i in enumerate(raw) if isinstance(i, Mapping)
    )
    return [Condition("default", sessions)] if sessions else []


def _later_session(conditions: list[Condition], raw: Mapping[str, Any], names: Mapping[str, str]) -> list[Condition]:
    """Phase 3 states the later conversation's user turn inside its expectation block: a new session."""
    block = next((v for k, v in raw.items() if k.startswith("expected_") and isinstance(v, Mapping)
                  and isinstance(v.get("user_input"), str)), None)
    if block is None:
        return conditions
    ex, crit = _expectations({k: v for k, v in block.items() if k != "user_input"}, names)
    later = SessionSpec("later-session", (Turn("user", block["user_input"]),), "later conversation", ex, crit)
    if not conditions:
        return [Condition("default", (later,))]
    out = []
    for condition in conditions:
        last = condition.sessions[-1] if condition.sessions else None
        if last is not None and last.probe == block["user_input"]:
            sessions = condition.sessions[:-1] + (replace(last, exemplars={**ex, **last.exemplars}, criteria={**crit, **last.criteria}),)
        elif last is not None and last.probe is not None:
            # The final session already opened the later conversation with an unanswered user turn: the stated
            # user input is its next message (the opener stays, unanswered, as history; nothing is invented).
            sessions = condition.sessions[:-1] + (replace(last, turns=last.turns + later.turns, exemplars=ex, criteria=crit),)
        else:
            sessions = condition.sessions + (later,)
        out.append(replace(condition, sessions=sessions))
    return out


def _walk_expected(node: Any, names: Mapping[str, str], owner: str,
                   terms: dict[str, list[str]], crit: dict[str, list[str]], ex: dict[str, str]) -> None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            who = names.get(str(key).casefold(), owner)
            if key in ("exemplary_response", "exemplary_responses") and isinstance(value, str):
                ex.setdefault(owner, value)
            elif key == "lexical_choices" and isinstance(value, list):
                terms.setdefault(owner, []).extend(str(v).casefold() for v in value if isinstance(v, str))
            elif key in ("observable_criteria", "criteria", "observable_response_criteria") and isinstance(value, list):
                crit.setdefault(owner, []).extend(str(v) for v in value if isinstance(v, str))
            elif key in ("behavioral_choice", "linguistic_characteristics") and isinstance(value, str):
                crit.setdefault(owner, []).append(value)
            else:
                _walk_expected(value, names, who, terms, crit, ex)
    elif isinstance(node, list):
        for item in node:
            _walk_expected(item, names, owner, terms, crit, ex)


def emotion_label(text: str) -> str:
    for word in re.findall(r"[a-z]+", text.casefold()):
        for stem, label in EMOTION_STEMS:
            if word.startswith(stem):
                return label
    return ""


def elapsed_days(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(day|week|month|year)s?", str(text), re.I)
    if not match:
        return None
    return float(match.group(1)) * {"day": 1, "week": 7, "month": 30, "year": 365}[match.group(2).lower()]


def _subject(text: str) -> str:
    head = re.split(r"\s+(?:is|are|was|were)\s+", text, maxsplit=1)[0]
    return " ".join(head.split()[:6])[:80]


def _per_character(value: Any, names: Mapping[str, str]) -> list[tuple[str, tuple[str, ...] | None]]:
    if isinstance(value, Mapping):
        return [(str(v), (names[str(k).casefold()],)) for k, v in value.items()
                if str(k).casefold() in names and isinstance(v, str)]
    if isinstance(value, str):
        return [(value, None)]
    if isinstance(value, list):
        return [(str(v), None) for v in value if isinstance(v, str)]
    return []


def _state_seeds(raw: Mapping[str, Any], names: Mapping[str, str]) -> list[Seed]:
    seeds: list[Seed] = []
    state: dict[str, Any] = {}
    for key in RELATIONSHIP_KEYS:
        if isinstance(raw.get(key), Mapping):
            state.update(raw[key])
    relationship: dict[str, Any] = {}
    for key, value in state.items():
        if key in NOT_APPLIED:
            seeds.append(Seed("not_applied", key, str(value), reason=NOT_APPLIED[key]))
        elif key in SITUATION_FIELDS:
            seeds.append(Seed("not_applied", key, str(value),
                              reason="situational description; production has no state for it and adding it would be a scenario instruction"))
        elif key == "familiarity" and isinstance(value, (int, float)):
            relationship["depth"] = float(value)
        elif key == "interaction_count" and isinstance(value, int):
            relationship["completed_turns"] = value
        elif key == "elapsed_hiatus" and elapsed_days(str(value)) is not None:
            relationship["days_since_last_interaction"] = elapsed_days(str(value))
        elif key == "recorded_belief":
            seeds.append(Seed("user_fact", key, str(value)))
        elif key == "developed_interests":
            seeds += [Seed("interest", key, text, subject=_subject(text), characters=who) for text, who in _per_character(value, names)]
        elif key == "current_opinion":
            history = str(state.get("historical_opinion") or "")
            seeds.append(Seed("opinion", key, str(value), subject=_subject(str(value)),
                              values={"reason": f"Revised from an earlier view: {history}" if history else ""}))
        elif key == "historical_opinion":
            continue  # carried as the current opinion's revision reason
        elif key == "active_threads":
            seeds += [Seed("thread", key, text, subject=text[:80], characters=who) for text, who in _per_character(value, names)]
        elif key == "disagreement_topic":
            feeling = str(state.get("emotional_state") or "")
            emotion = emotion_label(feeling)
            seeds.append(Seed("association", key, feeling or str(value), subject=str(value)[:80], emotion=emotion, unresolved=True)
                         if emotion else Seed("not_applied", key, str(value), reason="no emotion named in the fixture state"))
        elif key in EXPERIENCE_FIELDS or key in ASSOCIATION_FIELDS:
            if key in EXPERIENCE_FIELDS:
                seeds.append(Seed("experience", key, str(value), subject=_subject(str(value))))
            if key in ASSOCIATION_FIELDS and key != "emotional_state" and emotion_label(str(value)):
                seeds.append(Seed("association", key, str(value), subject=str(value)[:80], emotion=emotion_label(str(value))))
        elif isinstance(value, Mapping):
            seeds.append(Seed("not_applied", key, json.dumps(value)[:200],
                              reason="per-condition state block; each condition takes its state from its matching stage/contrast entry"))
        elif key == "emotional_state":
            if "disagreement_topic" not in state:
                seeds.append(Seed("not_applied", key, str(value), reason="an emotional state needs a subject to attach to"))
        else:
            seeds.append(Seed("not_applied", key, json.dumps(value)[:200], reason="no mapping to production state"))
    if relationship:
        seeds.append(Seed("relationship", "relationship_state", values=relationship))
    for item in raw.get("relevant_emotional_associations") or []:
        seeds += _association_seeds(item, names)
    evidence = raw.get("relevant_experience_evidence")
    if isinstance(evidence, Mapping):
        text = next((str(evidence[k]) for k in ("summary", "synthesized_narrative", "conclusion", "growth_profile")
                     if isinstance(evidence.get(k), str)), "")
        seeds.append(Seed("experience", "relevant_experience_evidence", text, subject=_subject(text),
                          significance="major" if float(evidence.get("salience") or 0) >= 0.9 else "significant")
                     if text else Seed("not_applied", "relevant_experience_evidence", reason="no narrative field"))
    shared = raw.get("evidence_backed_shared_reference")
    if isinstance(shared, Mapping) and shared.get("origin_incident"):
        who = tuple(names[n.casefold()] for n in shared.get("shared_with") or [] if isinstance(n, str) and n.casefold() in names)
        seeds.append(Seed("experience", "evidence_backed_shared_reference", str(shared["origin_incident"]),
                          subject=str(shared.get("reference_id") or "")[:80].replace("_", " "), characters=who or None))
    passage = raw.get("passage_of_time")
    if isinstance(passage, Mapping):
        days = elapsed_days(str(passage.get("elapsed") or passage.get("elapsed_between_turns") or ""))
        if days:
            seeds.append(Seed("relationship", "passage_of_time", values={"days_since_last_interaction": days}))
        else:
            seeds.append(Seed("not_applied", "passage_of_time", json.dumps(passage)[:200], reason="no elapsed duration"))
    return seeds


ASSOCIATION_SUBJECT_KEYS = ("topic", "event", "memory_key", "cue")
ASSOCIATION_EMOTION_KEYS = ("valence", "initial_valence", "emotional_polarity", "tone")


def _association_seeds(item: Any, names: Mapping[str, str]) -> list[Seed]:
    """One fixture association: seeded only where the fixture names an emotion production can hold."""
    if not isinstance(item, Mapping):
        return []
    subject = next((str(item[k]).replace("_", " ") for k in ASSOCIATION_SUBJECT_KEYS if isinstance(item.get(k), str)), "")
    field_name = "relevant_emotional_associations"
    if not subject:
        return [Seed("not_applied", field_name, json.dumps(item)[:200], reason="no subject")]
    significance = "major" if float(item.get("salience") or item.get("initial_arousal") or 0) >= 0.9 else "significant"
    candidates: list[tuple[str, tuple[str, ...] | None]] = []
    for key in ASSOCIATION_EMOTION_KEYS:
        if isinstance(item.get(key), str):
            candidates.append((item[key], None))
    signature = item.get("emotional_signature")
    if isinstance(signature, Mapping) and isinstance(signature.get("valence"), str):
        candidates.append((signature["valence"], None))
    only = None
    if isinstance(item.get("associated_companion"), str) and item["associated_companion"].casefold() in names:
        only = (names[item["associated_companion"].casefold()],)
    for key, value in item.items():
        match = re.match(r"emotional_association_(\w+)$", key)
        if match and isinstance(value, str) and match.group(1).casefold() in names:
            candidates.append((value, (names[match.group(1).casefold()],)))
    seeds = [Seed("not_applied", f"{field_name}.{k}", str(v), reason="a condition-specific valence is the expected interpretation, not state")
             for k, v in item.items() if re.match(r"valence_\w+$", k)]
    for text, who in candidates:
        label = emotion_label(text)
        if label:
            seeds.append(Seed("association", field_name, f"{subject}: {text.replace('_', ' ')}", subject=subject[:80],
                              emotion=label, significance=significance, characters=who or only))
        else:
            seeds.append(Seed("not_applied", field_name, text, characters=who or only, reason="the fixture names no emotion"))
    if not candidates and not seeds:
        seeds.append(Seed("not_applied", field_name, json.dumps(item)[:200], characters=only, reason="the fixture names no emotion"))
    return seeds


def _condition_seeds(condition: Condition) -> Condition:
    values: dict[str, Any] = {}
    rel = condition.relationship
    if isinstance(rel.get("familiarity"), (int, float)):
        values["depth"] = float(rel["familiarity"])
    if isinstance(rel.get("interaction_count"), int):
        values["completed_turns"] = rel["interaction_count"]
    seeds = [Seed("relationship", "condition_relationship", values=values)] if values else []
    seeds += [Seed("not_applied", key, str(rel[key]), reason=NOT_APPLIED.get(key, "no production field"))
              for key in ("trust_level", "stage", "shared_humor_style", "relational_posture") if key in rel and rel[key] is not None]
    if rel.get("interaction_context"):
        seeds.append(Seed("not_applied", "interaction_context", str(rel["interaction_context"]),
                          reason="situational description; production has no state for it"))
    return replace(condition, seeds=tuple(seeds))


def _scenario(raw: Mapping[str, Any], source: str, shared_companions: list[Mapping[str, Any]] | None = None) -> Scenario:
    sid = str(raw.get("scenario_id") or f"scenario-{abs(hash(json.dumps(raw, sort_keys=True))) % 10**6}")
    companions: list[CompanionSpec] = []
    for key in COMPANION_KEYS:
        value = raw.get(key)
        for item in (value if isinstance(value, list) else [value] if isinstance(value, Mapping) else []):
            companions.append(_companion(item))
        if companions:
            break
    if not companions and shared_companions:
        companions = [_companion(item) for item in shared_companions if isinstance(item, Mapping)]
    models = next((raw[k] for k in SELF_MODEL_KEYS if k in raw), None)
    single = "existing_developed_self_model" in raw
    developed = raw.get("relevant_developed_personality") if isinstance(raw.get("relevant_developed_personality"), Mapping) else {}
    enriched = []
    for c in companions:
        model = _self_model(models, single, c.name) if models else {}
        prose = developed.get(c.name)
        if isinstance(prose, str):
            model = {**model, "opinions": {**model.get("opinions", {}), "developed": {"statement": prose}}}
        enriched.append(CompanionSpec(**{**c.__dict__, "self_model": model}))
    companions = enriched
    names = {c.name.casefold(): c.character_id for c in companions}

    conditions: list[Condition] = []
    if isinstance(raw.get("shared_user_input"), str):  # Phase 7: one shared message, optional prior turns
        prior = raw.get("required_prior_conversation") or []
        conditions = [Condition("default", (SessionSpec("session-1", _turns(prior) + (Turn("user", raw["shared_user_input"]),)),))]
    else:
        for key in CONVERSATION_KEYS:
            if key in raw:
                conditions = _conversation(raw[key], raw, names)
                if conditions:
                    break
    conditions = [_condition_seeds(c) for c in _later_session(conditions, raw, names)]
    terms: dict[str, list[str]] = {}
    crit: dict[str, list[str]] = {}
    ex: dict[str, str] = {}
    for key, value in raw.items():
        if key.startswith(("expected_", "acceptable_")) or key == "expected_behavior":
            _walk_expected(value, names, "*", terms, crit, ex)
    if isinstance(raw.get("evaluation_criteria"), Mapping):
        crit.setdefault("*", []).extend(str(v) for v in raw["evaluation_criteria"].values() if isinstance(v, str))
    for condition in conditions:
        _walk_expected(condition.expected, names, "*", terms, crit, ex)
    base = re.sub(r"^scenario_\d+_", "", sid)
    tags = tuple(dict.fromkeys(tag for word, tag in TAG_KEYWORDS if word in base))
    category = str(raw.get("category") or "")
    letter = re.search(r"CATEGORY\s+([A-Z])", category)
    if letter:
        tags = tuple(dict.fromkeys(tags + CATEGORY_TAGS.get(letter.group(1), ())))
    if len(companions) > 1 and "differentiation" not in tags:
        tags += ("differentiation",)
    mood = next((raw[k] for k in MOOD_KEYS if isinstance(raw.get(k), Mapping)), {})
    relationship = next((raw[k] for k in RELATIONSHIP_KEYS if isinstance(raw.get(k), Mapping)), {})
    failure = raw.get("explicit_failure_conditions") or []
    requirements = raw.get("requirements_covered") or raw.get("relevant_requirement_ids") or []
    return Scenario(
        scenario_id=sid, name=str(raw.get("name") or sid), source=source,
        requirements=tuple(str(r) for r in requirements),
        description=str(raw.get("description") or ""), tags=tags, companions=tuple(companions),
        conditions=tuple(conditions), mood=dict(mood), relationship=dict(relationship),
        failure_conditions=tuple(str(f) for f in failure if isinstance(f, str)),
        expected_terms={k: tuple(dict.fromkeys(v)) for k, v in terms.items()},
        criteria={k: tuple(v) for k, v in crit.items()}, exemplars=ex,
        unmapped=tuple(sorted(k for k in raw if k not in KNOWN_KEYS and not k.startswith(("expected_", "acceptable_")))),
        seeds=tuple(_state_seeds(raw, names)), category=category,
    )


def load_fixture(path: str | Path) -> list[Scenario]:
    """All scenarios of a fixture file. Raises ``ValueError`` for a file with no scenarios."""
    path = Path(path)
    data = json.loads(path.read_text())
    items = data.get("scenarios") if isinstance(data, Mapping) else data
    if not isinstance(items, list) or not items:
        raise ValueError(f"{path}: no 'scenarios' list")
    shared = data.get("companions") if isinstance(data, Mapping) and isinstance(data.get("companions"), list) else None
    return [_scenario(item, path.name, shared) for item in items if isinstance(item, Mapping)]


def load_fixtures(paths: Iterable[str | Path]) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for path in paths:
        scenarios.extend(load_fixture(path))
    ids = [s.scenario_id for s in scenarios]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"duplicate scenario ids across fixtures: {dupes}")
    return scenarios


# --------------------------------------------------------------------------
# Rubric
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Rubric:
    dimensions: Mapping[str, Mapping[str, Any]]  # name -> {"definition", "scale": {"0".."5": text}}
    identification_key: Mapping[str, Mapping[str, Any]]  # character name -> markers (names are stripped before raters see them)
    baseline_protocol: Mapping[str, Any]
    source: str = ""

    @property
    def scale(self) -> tuple[int, int]:
        points = sorted(int(p) for d in self.dimensions.values() for p in (d.get("scale") or {}) if str(p).isdigit())
        return (points[0], points[-1]) if points else (0, 5)


def load_rubric(path: str | Path) -> Rubric:
    data = json.loads(Path(path).read_text())
    dims = data.get("scoring_dimensions")
    if not isinstance(dims, Mapping) or not dims:
        raise ValueError(f"{path}: no 'scoring_dimensions'")
    protocol = data.get("blind_evaluation_protocol") or {}
    return Rubric(dimensions=dict(dims), identification_key=dict(protocol.get("character_identification_key") or {}),
                  baseline_protocol=dict(data.get("baseline_comparison_protocol") or {}), source=Path(path).name)
