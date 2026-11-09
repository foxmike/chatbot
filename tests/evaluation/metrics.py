"""Deterministic measurements of what companions actually said.

Every measure here reads generated dialogue only. None reads the prompt, the
fixture's "expected" text or the provider's description of itself as evidence
that a behaviour happened. They are lexical/stylometric *proxies* with the
formula stated in each docstring; they are cheap, reproducible, and will miss
paraphrase. They are screening instruments, not a verdict on personality
quality: blind human judgment (``blind.py``) is the independent check.

A dimension value is ``None`` (with ``reason``) when the data cannot support it,
never a default number.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence

from .scenarios import CompanionSpec, Scenario

DIMENSIONS = (
    "behavioral_differentiation", "linguistic_distinctiveness", "personality_consistency",
    "principled_disagreement", "competing_motivations", "selective_attention",
    "conversational_initiative", "memory_grounded_opinions", "emotional_continuity",
    "relationship_specific_behavior", "cross_session_identity", "generic_assistant_drift",
)
# Dimensions whose better direction is "lower".
LOWER_IS_BETTER = {"generic_assistant_drift"}

STOP = frozenset("""a about above after again all also am an and any are as at be because been before being below between both but
by can could did do does doing down during each few for from further had has have having he her here hers him his how i if in into is
it its itself just me more most my myself no nor not now of off on once only or other our out over own same she should so some such than
that the their them then there these they this those through to too under until up very was we were what when where which while who whom
why will with would you your yours yourself""".split())

_WORD = re.compile(r"[a-z']+")
_SENT = re.compile(r"[^.!?\n]+[.!?]*")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")

ACTS: dict[str, re.Pattern[str]] = {
    "disagree": re.compile(r"\b(disagree|not convinced|i wouldn'?t|i would not|push back|i don'?t think|can'?t agree|won'?t|not a good idea|bad idea|"
                           r"don'?t do|shouldn'?t|should not|i'?m not (?:going to|sure)|no[,.]|risky|reckless|mistake)\b", re.I),
    "agree": re.compile(r"\b(you'?re right|you are right|i agree|absolutely|exactly|sounds good|great idea|good call|totally|sure thing)\b", re.I),
    "reason": re.compile(r"\b(because|since|which means|so that|therefore|given that|the reason|otherwise|that'?s why|if (?:we|you|it))\b", re.I),
    "tradeoff": re.compile(r"\b(but|however|although|though|on the other hand|yet|that said|at the same time|trade-?off|while)\b", re.I),
    "propose": re.compile(r"\b(let'?s|why don'?t we|you could|you should|try|consider|next step|start with|i suggest|what if|first,)\b", re.I),
    "initiative": re.compile(r"\b(have you|did you|what about|do you want|want to|shall we|i wonder|by the way|speaking of|tell me|curious|"
                             r"how about|would you like)\b", re.I),
    "cheer": re.compile(r"\b(great|awesome|amazing|wonderful|fantastic|excited|love that|congratulations)\b", re.I),
}
DRIFT: dict[str, re.Pattern[str]] = {
    "opening_filler": re.compile(r"^\W*(certainly|sure thing|sure|absolutely|of course|great question|good question)\b", re.I),
    "offer_closer": re.compile(r"\b(let me know if|feel free to|happy to help|hope this helps|(?:is there|need|want|help(?: you)? with) anything else|"
                               r"i'?m here to help|here to assist)\b", re.I),
    "ai_self": re.compile(r"\b(as an ai|language model|i'?m an ai|i am an ai|as a virtual)\b", re.I),
    "faux_empathy": re.compile(r"\b(i understand how|i'?m sorry to hear|that must be (?:so |really )?(?:frustrating|difficult|hard)|i can imagine how)\b", re.I),
    "boilerplate": re.compile(r"\b(it'?s important to note|it is important to note|here are (?:some|a few)|keep in mind that|i'?d be happy to)\b", re.I),
}
LEAK = re.compile(r"\b(my instructions|system prompt|my persona|as a character|i am programmed|my personality sliders|"
                  r"my configuration|character behavior|as your companion character|my prompt)\b", re.I)
_LIST_LINE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S", re.M)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S", re.M)
# Production's own directive forbids lists "when the user is chatting naturally"; an explicit request for an
# explanation or procedure is the case it exempts, so structure is not drift there.
EXPLICIT_EXPLANATION = re.compile(r"\b(explain|how does|how do(?:es)? (?:it|this|that)|walk me through|step[- ]by[- ]step|"
                                  r"what are the steps|list (?:the|all)|outline)\b", re.I)


def words(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


def stems(text: str) -> set[str]:
    """Crude 5-letter stems of content words (enough to match 'archive'/'archival')."""
    return {w[:5] for w in words(text) if len(w) >= 5 and w not in STOP}


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.findall(text) if re.search(r"\w", s)]


def style_profile(text: str) -> dict[str, float]:
    """Function-word frequencies plus punctuation/shape rates; the vector behind distinctiveness."""
    ws = words(text)
    n = max(len(ws), 1)
    sents = sentences(text) or [text]
    profile = {f"fw:{w}": ws.count(w) / n for w in set(ws) if w in STOP}
    profile.update({
        "p:?": text.count("?") / n, "p:!": text.count("!") / n, "p:,": text.count(",") / n,
        "p:;": text.count(";") / n, "p::": text.count(":") / n, "p:dash": len(re.findall(r"—|–|--", text)) / n,
        "p:...": text.count("...") / n, "p:emoji": len(_EMOJI.findall(text)) / n,
        "s:sentlen": (len(ws) / len(sents)) / 30, "s:wordlen": (sum(map(len, ws)) / n) / 8,
        "s:ttr": len(set(ws)) / n, "s:contraction": sum("'" in w for w in ws) / n,
    })
    return profile


def cosine(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    na, nb = math.sqrt(sum(v * v for v in a.values())), math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def jaccard_distance(a: set[Any], b: set[Any]) -> float:
    return 1.0 - len(a & b) / len(a | b) if (a | b) else 0.0


def act_counts(text: str) -> dict[str, int]:
    counts = {name: len(p.findall(text)) for name, p in ACTS.items()}
    counts["question"] = text.count("?")
    return counts


def drift_hits(text: str, probe: str = "") -> list[str]:
    hits = [name for name, p in DRIFT.items() if p.search(text)]
    structured = len(_LIST_LINE.findall(text)) >= 3 or _HEADING.search(text)
    if structured and not EXPLICIT_EXPLANATION.search(probe):
        hits.append("markdown_structure")
    return hits


def is_cheerful(text: str) -> bool:
    return text.count("!") >= 2 or bool(_EMOJI.search(text)) or len(ACTS["cheer"].findall(text)) >= 2


@dataclass(frozen=True)
class Sample:
    """One generated reply plus the minimum context a measure may use."""

    scenario_id: str
    character_id: str
    session_id: str
    text: str
    condition_id: str = "default"
    probe: str = ""
    prior_text: str = ""  # earlier turns of this conversation (not the probe)
    persisted: bool = False  # companion identity persisted across sessions in this run
    spec: CompanionSpec | None = None  # the companion that actually answered (may differ from the scenario's own cast)


def _pairs(samples: Sequence[Sample]) -> list[tuple[Sample, Sample]]:
    return [(a, b) for a, b in combinations(samples, 2) if a.character_id != b.character_id]


def _distinct_pair(a: Sample, b: Sample) -> float:
    return 0.5 * (1 - cosine(style_profile(a.text), style_profile(b.text))) + \
        0.5 * jaccard_distance(stems(a.text), stems(b.text))


def _act_pair(a: Sample, b: Sample) -> float:
    ca, cb = act_counts(a.text), act_counts(b.text)
    la, lb = len(words(a.text)), len(words(b.text))
    return 0.5 * jaccard_distance({k for k, v in ca.items() if v}, {k for k, v in cb.items() if v}) + \
        0.5 * (abs(la - lb) / max(la, lb, 1))


def _dim(value: float | None, n: int, basis: str, reason: str = "", **details: Any) -> dict[str, Any]:
    out = {"value": None if value is None else round(value, 4), "n": n, "basis": basis}
    if value is None:
        out["reason"] = reason or "insufficient data"
    out.update({"details": details} if details else {})
    return out


def _by_scenario(samples: Iterable[Sample]) -> dict[tuple[str, str], list[Sample]]:
    groups: dict[tuple[str, str], list[Sample]] = defaultdict(list)
    for sample in samples:
        groups[(sample.scenario_id, sample.condition_id)].append(sample)
    return groups


def _loo_accuracy(samples: Sequence[Sample], *, other_session_only: bool) -> tuple[float | None, float | None, int]:
    """Leave-one-out nearest-centroid attribution of replies to characters, by style profile.

    Returns ``(accuracy, chance, n_tested)``. A character needs at least two replies.
    """
    by_char: dict[str, list[Sample]] = defaultdict(list)
    for sample in samples:
        by_char[sample.character_id].append(sample)
    usable = {c: v for c, v in by_char.items() if len(v) >= 2}
    if len(usable) < 2:
        return None, None, 0
    hits = tested = 0
    for char, group in usable.items():
        for held in group:
            best, best_sim = None, -1.0
            for other, others in usable.items():
                pool = [s for s in others if s is not held and not (other_session_only and s.session_id == held.session_id)]
                if not pool:
                    best = None
                    break
                centroid: dict[str, float] = defaultdict(float)
                for s in pool:
                    for k, v in style_profile(s.text).items():
                        centroid[k] += v / len(pool)
                sim = cosine(style_profile(held.text), centroid)
                if sim > best_sim:
                    best, best_sim = other, sim
            if best is None:
                continue
            tested += 1
            hits += best == char
    return (hits / tested if tested else None), 1 / len(usable), tested


def grounding_stems(companion: CompanionSpec) -> set[str]:
    parts = [v.get("statement", "") + " " + v.get("reason", "") for v in (companion.self_model.get("opinions") or {}).values()]
    parts += companion.self_model.get("interests") or []
    return stems(" ".join(parts))


def compute_dimensions(samples: Sequence[Sample], scenarios: Mapping[str, Scenario]) -> dict[str, dict[str, Any]]:
    """The twelve dimensions over a set of completed samples (see module docstring)."""
    samples = [s for s in samples if s.text.strip()]
    groups = _by_scenario(samples)
    tags = lambda sid: set(scenarios[sid].tags) if sid in scenarios else set()  # noqa: E731
    out: dict[str, dict[str, Any]] = {}

    multi = {k: v for k, v in groups.items() if len({s.character_id for s in v}) >= 2}
    def pair_dim(fn: Any, name: str, basis: str) -> dict[str, Any]:
        per = {f"{sid}/{cid}": mean(fn(a, b) for a, b in _pairs(v)) for (sid, cid), v in multi.items() if _pairs(v)}
        return _dim(mean(per.values()) if per else None, len(per), basis,
                    "needs at least two characters answering the same scenario", per_scenario=per)
    out["behavioral_differentiation"] = pair_dim(
        _act_pair, "behavioral_differentiation",
        "per scenario, mean over character pairs of 0.5*Jaccard distance of speech acts present + 0.5*relative length gap")
    out["linguistic_distinctiveness"] = pair_dim(
        _distinct_pair, "linguistic_distinctiveness",
        "per scenario, mean over character pairs of 0.5*(1-cosine of function-word/punctuation profile) + 0.5*Jaccard distance of content stems")

    acc, chance, n = _loo_accuracy(samples, other_session_only=False)
    out["personality_consistency"] = _dim(
        acc, n, "leave-one-out nearest-centroid attribution of replies to characters by style profile; compare with chance",
        "needs two characters with two replies each", chance=None if chance is None else round(chance, 4))
    persisted = [s for s in samples if s.persisted]
    pacc, pchance, pn = _loo_accuracy(persisted, other_session_only=True)
    out["cross_session_identity"] = _dim(
        pacc, pn, "as personality_consistency, only replies from persisted companion identities, compared across different sessions only",
        "needs persisted companions with replies in two sessions", chance=None if pchance is None else round(pchance, 4))

    def rate(selector: Any, test: Any, basis: str, reason: str) -> dict[str, Any]:
        chosen = [s for s in samples if selector(s)]
        flags = [bool(test(s)) for s in chosen]
        return _dim(mean(flags) if flags else None, len(flags), basis, reason,
                    scenarios=sorted({s.scenario_id for s in chosen}))
    out["principled_disagreement"] = rate(
        lambda s: "disagreement" in tags(s.scenario_id),
        lambda s: ACTS["disagree"].search(s.text) and ACTS["reason"].search(s.text) and not _capitulates(s.text),
        "share of disagreement-scenario replies that disagree, give a reason, and do not open by agreeing",
        "no disagreement-tagged scenario completed")
    out["competing_motivations"] = rate(
        lambda s: "competing_motivations" in tags(s.scenario_id),
        lambda s: ACTS["tradeoff"].search(s.text) and ACTS["reason"].search(s.text),
        "share of competing-motivation replies that voice a trade-off (but/however/...) and a reason",
        "no competing-motivation scenario completed")
    initiative = [s for s in samples if "initiative" in tags(s.scenario_id) and "initiative_restraint" not in tags(s.scenario_id)]
    restraint = [s for s in samples if "initiative_restraint" in tags(s.scenario_id)]
    took = lambda s: s.text.count("?") or ACTS["initiative"].search(s.text)  # noqa: E731
    out["conversational_initiative"] = _dim(
        mean(bool(took(s)) for s in initiative) if initiative else None, len(initiative),
        "share of initiative-scenario replies that ask something or open a topic (question mark / invitation phrase)",
        "no initiative-tagged scenario completed",
        restraint_rate=(round(mean(not took(s) for s in restraint), 4) if restraint else None), restraint_n=len(restraint))

    def grounded(s: Sample) -> bool | None:
        if s.spec is None:
            return None
        terms = grounding_stems(s.spec) - stems(s.probe)
        return len(stems(s.text) & terms) >= 2 if terms else None
    scored = [(s, grounded(s)) for s in samples if "opinion" in tags(s.scenario_id) or "cross_session" in tags(s.scenario_id)]
    scored = [(s, g) for s, g in scored if g is not None]
    out["memory_grounded_opinions"] = _dim(
        mean(g for _, g in scored) if scored else None, len(scored),
        "share of replies sharing >=2 content stems with the companion's own seeded opinions/interests (stems already in the user's message excluded)",
        "no opinion-tagged reply with seeded opinions")

    def carries(s: Sample) -> bool:
        context = stems(s.prior_text) - stems(s.probe)
        return not is_cheerful(s.text) and bool(stems(s.text) & context)
    emo = [s for s in samples if "emotional" in tags(s.scenario_id) and s.prior_text]
    out["emotional_continuity"] = _dim(
        mean(carries(s) for s in emo) if emo else None, len(emo),
        "share of replies that are not cheerful (>=2 '!' / emoji / cheer words) and carry >=1 content stem of the earlier conversation not in the new message",
        "no emotional scenario with prior turns completed")

    rel: dict[str, float] = {}
    by_char_scn: dict[tuple[str, str], list[Sample]] = defaultdict(list)
    for s in samples:
        by_char_scn[(s.scenario_id, s.character_id)].append(s)
    for key, group in by_char_scn.items():
        cells = {s.condition_id: s for s in group}
        if len(cells) >= 2:
            rel["/".join(key)] = mean(_distinct_pair(a, b) for a, b in combinations(cells.values(), 2))
    out["relationship_specific_behavior"] = _dim(
        mean(rel.values()) if rel else None, len(rel),
        "same character and message under different relationship conditions: mean distinctiveness distance between its replies",
        "needs one character answered under two relationship conditions", per_character_scenario=rel)

    scoped: dict[str, float] = {}
    for (sid, cid), group in multi.items():
        diffs = []
        for s in group:
            if s.spec is None or not s.spec.attention_terms:
                continue
            own = set(s.spec.attention_terms)
            foreign = set().union(*(set(o.spec.attention_terms) for o in group
                                    if o.character_id != s.character_id and o.spec is not None)) - own
            if not foreign:
                continue
            seen = {w[:5] for w in words(s.text)}
            diffs.append(sum(t[:5] in seen for t in own) / len(own) - sum(t[:5] in seen for t in foreign) / len(foreign))
        if diffs:
            scoped[f"{sid}/{cid}"] = mean(diffs)
    out["selective_attention"] = _dim(
        mean(scoped.values()) if scoped else None, len(scoped),
        "per scenario, mean over replies of (share of the character's own attention terms used) - (share of the other characters' terms used)",
        "needs two characters with configured attention terms answering one scenario", per_scenario=scoped)

    drifting = [bool(drift_hits(s.text, s.probe)) for s in samples]
    out["generic_assistant_drift"] = _dim(
        mean(drifting) if drifting else None, len(drifting),
        "share of replies containing assistant boilerplate (filler opener, offer closer, AI self-reference, faux empathy, list/markdown structure)",
        "no completed reply", mean_hits=round(mean(len(drift_hits(s.text, s.probe)) for s in samples), 4) if samples else None)
    assert set(out) == set(DIMENSIONS)
    return {name: out[name] for name in DIMENSIONS}


def _capitulates(text: str) -> bool:
    first = (sentences(text) or [""])[0]
    return bool(ACTS["agree"].search(first))


QUOTED = re.compile(r"""['"“‘]([^'"”’]{12,160})['"”’]""")


def failure_phrases(conditions: Iterable[str]) -> list[str]:
    """Literal phrases quoted inside the fixture's failure conditions (the only part a machine can check)."""
    phrases: list[str] = []
    for condition in conditions:
        phrases.extend(m.group(1) for m in QUOTED.finditer(condition))
    return phrases


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text.casefold()).strip()


def automated_checks(text: str, *, scenario: Scenario, spec: CompanionSpec, probe: str, finish_reason: str | None) -> dict[str, Any]:
    """Per-reply observations and failures. Free-text criteria that need a human are returned as pending."""
    failures: list[str] = []
    drift = drift_hits(text, probe)
    if not text.strip():
        failures.append("empty_response")
    if text.startswith("Provider error:"):
        failures.append("provider_error_text")
    if drift:
        failures.append("generic_assistant_drift:" + ",".join(drift))
    if LEAK.search(text):
        failures.append("prompt_or_persona_self_description")
    quoted = [p for p in failure_phrases(scenario.failure_conditions) if _norm(p) and _norm(p) in _norm(text)]
    failures.extend(f"fixture_failure_phrase:{p}" for p in quoted)
    character_id = spec.character_id
    expected = tuple(scenario.expected_terms.get(character_id, ()))
    seen = [t for t in expected if _norm(t) in _norm(text)]
    return {
        "failures": failures,
        "observed": {
            "words": len(words(text)), "sentences": len(sentences(text)), "acts": act_counts(text),
            "drift_markers": drift, "cheerful": is_cheerful(text), "truncated": finish_reason == "length",
            "expected_terms_seen": seen, "expected_terms_total": len(expected),
            "attention_terms_seen": [t for t in spec.attention_terms if t[:5] in {w[:5] for w in words(text)}],
            "grounding_stems_seen": sorted((stems(text) & grounding_stems(spec)) - stems(probe)),
        },
        "pending_human_review": list(scenario.criteria.get(character_id, ())) + list(scenario.criteria.get("*", ())),
    }
