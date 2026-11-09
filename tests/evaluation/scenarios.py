"""Fixture loading: one normalised ``Scenario`` from any behavioural acceptance fixture.

The Phase 3-6 acceptance fixtures use different key names for the same ideas
(``character_configurations`` / ``companion_personality_configuration`` /
``initial_companion_identity``, three spellings of the conversation, ...). The
loader maps what it recognises, keeps everything else out of the way, and lists
the top-level keys it did not map in ``Scenario.unmapped`` so a new dataset
(for example the independently produced Phase 7 one) loads without code changes
and without silently losing information.

Nothing here touches a database, a provider or the network.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
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
                     "relevant_relationship_experience_context")
KNOWN_KEYS = {
    "scenario_id", "name", "description", "requirements_covered", "explicit_failure_conditions",
    "relevant_developed_personality", *COMPANION_KEYS, *CONVERSATION_KEYS, *SELF_MODEL_KEYS,
    *MOOD_KEYS, *RELATIONSHIP_KEYS,
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
    ("relationship", "relationship"), ("familiarity", "relationship"), ("teasing", "relationship"),
    ("shared_references", "relationship"), ("cross_session", "cross_session"),
    ("survives", "cross_session"), ("retains", "cross_session"), ("across_topics", "cross_session"),
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

    def probes(self) -> tuple[str, ...]:
        """User turns the companion has not answered in the fixture (to be generated)."""
        pending: list[str] = []
        for index, turn in enumerate(self.turns):
            followed = index + 1 < len(self.turns) and self.turns[index + 1].role == "assistant"
            if turn.role == "user" and not followed:
                pending.append(turn.content)
        return tuple(pending)


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
    """One experimental cell of a scenario (a fixture stage such as 'early acquaintance')."""

    condition_id: str
    sessions: tuple[SessionSpec, ...]
    relationship: Mapping[str, Any] = field(default_factory=dict)
    expected: Mapping[str, Any] = field(default_factory=dict)


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

    def executable(self) -> tuple[bool, str]:
        if not self.companions:
            return False, "no companion configuration"
        if not any(session.probes() for cond in self.conditions for session in cond.sessions):
            return False, "no unanswered user turn to generate"
        return True, ""

    def companion(self, character_id: str) -> CompanionSpec | None:
        return next((c for c in self.companions if c.character_id == character_id), None)

    def prior_text(self) -> str:
        """Everything the fixture itself says (user + scripted assistant turns), for leak checks."""
        return " ".join(t.content for c in self.conditions for s in c.sessions for t in s.turns)


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
                out.extend(w for w in re.split(r"[_\s,;/]+", piece.casefold()) if len(w) >= 4)
    return tuple(dict.fromkeys(out))


def _companion(raw: Mapping[str, Any]) -> CompanionSpec:
    name = str(raw.get("name") or "Companion")
    disposition = _strings(raw.get("behavioral_disposition"))
    bias = disposition.get("attention_bias", "")
    return CompanionSpec(
        character_id=slug(name), name=name, persona=str(raw.get("persona") or ""),
        archetype=str(raw.get("archetype") or ""), voice_and_tone=str(raw.get("voice_and_tone") or ""),
        anchors=_numbers(raw.get("personality_anchors")),
        fingerprint=_strings(raw.get("linguistic_fingerprint")), disposition=disposition,
        attention_terms=_terms(bias),
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


def _conversation(raw: Any) -> list[Condition]:
    """Sessions of one condition, or one condition per fixture 'stage'."""
    if isinstance(raw, Mapping):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        return []
    if all(isinstance(i, Mapping) and "role" in i for i in raw):  # a bare list of turns
        return [Condition("default", (SessionSpec("session-1", _turns(raw)),))]
    staged = [i for i in raw if isinstance(i, Mapping) and "turns" in i and "session_id" not in i]
    if staged and len(staged) == len(raw):
        out = []
        for index, item in enumerate(staged):
            cid = slug(item.get("stage") or f"stage-{index + 1}")
            out.append(Condition(cid, (SessionSpec(f"{cid}-session", _turns(item["turns"]), str(item.get("context") or "")),),
                                 relationship={"stage": item.get("stage")}, expected=item.get("expected_behavior") or {}))
        return out
    sessions = tuple(
        SessionSpec(str(i.get("session_id") or f"session-{n + 1}"), _turns(i.get("turns") or []), str(i.get("context") or ""))
        for n, i in enumerate(raw) if isinstance(i, Mapping)
    )
    return [Condition("default", sessions)] if sessions else []


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
            else:
                _walk_expected(value, names, who, terms, crit, ex)
    elif isinstance(node, list):
        for item in node:
            _walk_expected(item, names, owner, terms, crit, ex)


def _scenario(raw: Mapping[str, Any], source: str) -> Scenario:
    sid = str(raw.get("scenario_id") or f"scenario-{abs(hash(json.dumps(raw, sort_keys=True))) % 10**6}")
    companions: list[CompanionSpec] = []
    for key in COMPANION_KEYS:
        value = raw.get(key)
        for item in (value if isinstance(value, list) else [value] if isinstance(value, Mapping) else []):
            companions.append(_companion(item))
        if companions:
            break
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

    conditions: list[Condition] = []
    for key in CONVERSATION_KEYS:
        if key in raw:
            conditions = _conversation(raw[key])
            if conditions:
                break
    names = {c.name.casefold(): c.character_id for c in companions}
    terms: dict[str, list[str]] = {}
    crit: dict[str, list[str]] = {}
    ex: dict[str, str] = {}
    for key, value in raw.items():
        if key.startswith(("expected_", "acceptable_")) or key == "expected_behavior":
            _walk_expected(value, names, "*", terms, crit, ex)
    for condition in conditions:
        _walk_expected(condition.expected, names, "*", terms, crit, ex)
    base = re.sub(r"^scenario_\d+_", "", sid)
    tags = tuple(dict.fromkeys(tag for word, tag in TAG_KEYWORDS if word in base))
    if len(companions) > 1 and "differentiation" not in tags:
        tags += ("differentiation",)
    mood = next((raw[k] for k in MOOD_KEYS if isinstance(raw.get(k), Mapping)), {})
    relationship = next((raw[k] for k in RELATIONSHIP_KEYS if isinstance(raw.get(k), Mapping)), {})
    failure = raw.get("explicit_failure_conditions") or []
    return Scenario(
        scenario_id=sid, name=str(raw.get("name") or sid), source=source,
        requirements=tuple(str(r) for r in raw.get("requirements_covered") or []),
        description=str(raw.get("description") or ""), tags=tags, companions=tuple(companions),
        conditions=tuple(conditions), mood=dict(mood), relationship=dict(relationship),
        failure_conditions=tuple(str(f) for f in failure if isinstance(f, str)),
        expected_terms={k: tuple(dict.fromkeys(v)) for k, v in terms.items()},
        criteria={k: tuple(v) for k, v in crit.items()}, exemplars=ex,
        unmapped=tuple(sorted(k for k in raw if k not in KNOWN_KEYS and not k.startswith(("expected_", "acceptable_")))),
    )


def load_fixture(path: str | Path) -> list[Scenario]:
    """All scenarios of a fixture file. Raises ``ValueError`` for a file with no scenarios."""
    path = Path(path)
    data = json.loads(path.read_text())
    items = data.get("scenarios") if isinstance(data, Mapping) else data
    if not isinstance(items, list) or not items:
        raise ValueError(f"{path}: no 'scenarios' list")
    return [_scenario(item, path.name) for item in items if isinstance(item, Mapping)]


def load_fixtures(paths: Iterable[str | Path]) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for path in paths:
        scenarios.extend(load_fixture(path))
    ids = [s.scenario_id for s in scenarios]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"duplicate scenario ids across fixtures: {dupes}")
    return scenarios
