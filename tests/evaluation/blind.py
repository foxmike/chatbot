"""Blind recognizability: anonymised export, hidden ground truth, judgment scoring.

Design
------
A rater cannot tell characters apart from one reply with nothing to compare
against, and with one reply per character per scenario a "which group is which"
task is trivially solvable. So the unit is an **attribution task**:

* ``authors``: anonymous labels (A, B, C ...), each with reference replies that
  the character gave in *other* scenarios;
* ``items``: held-out replies from one scenario/condition, shuffled;
* the rater says which author wrote each item.

Names and identifying labels are redacted from every text; the character IDs
live only in the separate ground-truth document. Chance is 1/(number of
authors). Nothing here claims blind success: ``score_judgments`` reports
``blind_recognizability_established = False`` unless enough complete
judgments from raters who attest they did not see the ground truth exist, and
never for scripted responses.

Judgment file (one per rater, JSON)::

    {"format": "blind-judgment/1", "package_id": "<from the package>",
     "rater_id": "r1", "judge": {"type": "human"|"model", "saw_ground_truth": false},
     "judgments": [{"task_id": "...", "item_id": "...", "author": "A" | "generic",
                    "confidence": 1-5 (optional), "note": "",
                    "scores": {"<rubric dimension>": 0-5, ...} (optional)}]}

With a rubric (``scenarios.load_rubric``) the package carries the rubric's
dimension definitions and scales, each anonymous author's identification
markers with names removed, and an empty ``scores`` object per item. ``generic``
records the rubric's "Generic Baseline" prediction and counts as a miss. Rubric
scores are averaged only over complete, valid raters; with none, they are absent.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from itertools import combinations
from typing import Any, Iterable, Mapping, Sequence

from .scenarios import Rubric, Scenario

PACKAGE_FORMAT = "blind-attribution/1"
JUDGMENT_FORMAT = "blind-judgment/1"
REDACTION = "[name]"
GENERIC = "generic"
GENERIC_WORDS = frozenset({"companion", "assistant", "user", "character"})
INSTRUCTIONS = (
    "Each task shows reference replies from anonymous authors (A, B, ...) and some further replies. "
    "For every further reply choose the author who most likely wrote it. Judge only voice and choices; "
    "names have been removed. Do not look for the answer key."
)


class BlindLeak(AssertionError):
    """An identifying label survived anonymisation; the package is not written."""


def _labels(scenarios: Mapping[str, Scenario], extra: Iterable[str]) -> list[str]:
    found: set[str] = set(extra)
    for scenario in scenarios.values():
        for companion in scenario.companions:
            found.update({companion.name, companion.character_id, companion.archetype})
    # A loader fallback such as "Companion" is an ordinary word, not an identity; redacting it would rewrite dialogue.
    cleaned = {label.strip() for label in found if label and len(label.strip()) >= 3
               and label.strip().casefold() not in GENERIC_WORDS}
    return sorted(cleaned, key=len, reverse=True)


def redact(text: str, labels: Sequence[str]) -> tuple[str, int]:
    """Replace every whole-word occurrence of any label; returns (text, replacement count)."""
    total = 0
    for label in labels:
        text, n = re.subn(rf"(?<![\w]){re.escape(label)}(?![\w])", REDACTION, text, flags=re.I)
        total += n
    return text, total


def _digest(*parts: Any) -> str:
    return hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()


def usable(record: Mapping[str, Any]) -> bool:
    return record.get("status") in ("passed", "failed") and bool((record.get("dialogue") or "").strip())


def build_blind_package(
    records: Sequence[Mapping[str, Any]], scenarios: Mapping[str, Scenario], *, seed: str,
    references_per_author: int = 2, extra_labels: Iterable[str] = (), rubric: Rubric | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(public_package, ground_truth)``; same inputs and seed give identical output."""
    rows = [r for r in records if usable(r)]
    labels = _labels(scenarios, extra_labels)
    package_id = _digest("package", seed, *sorted(f"{r['scenario_id']}|{r.get('condition_id', 'default')}|{r['character_id']}" for r in rows))[:12]
    redactions = 0

    def key(r: Mapping[str, Any]) -> str:
        return f"{r['scenario_id']}|{r.get('condition_id', 'default')}"

    def item_id(r: Mapping[str, Any]) -> str:
        return "i-" + _digest("item", package_id, key(r), r["character_id"])[:10]

    def shown(text: str) -> str:
        nonlocal redactions
        clean, n = redact(text, labels)
        redactions += n
        return clean

    names = {c.character_id: c.name for s in scenarios.values() for c in s.companions}
    key_by_name = {str(n).casefold(): v for n, v in (rubric.identification_key.items() if rubric else [])}

    def profile(character_id: str) -> dict[str, Any] | None:
        markers = key_by_name.get(str(names.get(character_id, "")).casefold())
        if not markers:
            return None
        return {k: [shown(str(m)) for m in v] if isinstance(v, list) else shown(str(v)) for k, v in markers.items()}

    cells: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        cells[key(r)].append(r)
    tasks, skipped = [], []
    truth_tasks: dict[str, Any] = {}
    for cell in sorted(cells):
        held_out = cells[cell]
        authors = sorted({r["character_id"] for r in held_out})
        if len(authors) < 2:
            skipped.append({"task_id": "t-" + _digest("task", package_id, cell)[:8], "reason": "fewer than two authors answered this scenario"})
            continue
        task_id = "t-" + _digest("task", package_id, cell)[:8]
        rng = random.Random(_digest("rng", seed, task_id))
        refs = {a: [r for r in rows if r["character_id"] == a and key(r) != cell] for a in authors}
        if any(not v for v in refs.values()):
            skipped.append({"task_id": task_id, "reason": "an author has no reference replies from other scenarios"})
            continue
        label_names = [chr(ord("A") + i) for i in range(len(authors))]
        shuffled = authors[:]
        rng.shuffle(shuffled)
        mapping = dict(zip(label_names, shuffled))
        author_entries = []
        for label in label_names:
            chosen = sorted(refs[mapping[label]], key=lambda r: _digest("ref", seed, task_id, key(r), r["character_id"]))[:references_per_author]
            entry = {"label": label, "references": [
                {"item_id": item_id(r), "user_message": shown(r.get("probe", "")), "text": shown(r["dialogue"])} for r in chosen]}
            if rubric is not None and profile(mapping[label]) is not None:
                entry["profile"] = profile(mapping[label])
            author_entries.append(entry)
        items = [{"item_id": item_id(r), "text": shown(r["dialogue"])} for r in held_out]
        rng.shuffle(items)
        context = shown(held_out[0].get("probe", ""))
        tasks.append({"task_id": task_id, "user_message": context, "authors": author_entries, "items": items})
        truth_tasks[task_id] = {
            "cell": cell, "author_labels": mapping,
            "items": {item_id(r): r["character_id"] for r in held_out},
            "sources": sorted({str(r.get("source", "unknown")) for r in held_out}),
        }
    rng = random.Random(_digest("order", seed))
    rng.shuffle(tasks)
    public = {
        "format": PACKAGE_FORMAT, "package_id": package_id, "instructions": INSTRUCTIONS,
        "tasks": tasks, "skipped_tasks": skipped,
        "judgment_template": {
            "format": JUDGMENT_FORMAT, "package_id": package_id, "rater_id": "",
            "judge": {"type": "human", "saw_ground_truth": False},
            "judgments": [{"task_id": t["task_id"], "item_id": i["item_id"], "author": None, "confidence": None, "note": "",
                           **({"scores": {d: None for d in rubric.dimensions}} if rubric else {})}
                          for t in tasks for i in t["items"]],
        },
    }
    if rubric is not None:
        public["rubric"] = {"source": rubric.source, "scale": list(rubric.scale),
                            "dimensions": {n: {"definition": d.get("definition"), "scale": d.get("scale")} for n, d in rubric.dimensions.items()}}
        public["instructions"] = INSTRUCTIONS + (" Optionally score each further reply on every rubric dimension, and answer "
                                                 f"'{GENERIC}' when a reply reads like a generic assistant rather than any author.")
    leaks = [label for label in labels if re.search(rf"(?<![\w]){re.escape(label)}(?![\w])", json.dumps(public, ensure_ascii=False), re.I)]
    if leaks:
        raise BlindLeak(f"identifying labels survived anonymisation: {leaks}")
    truth = {"package_id": package_id, "tasks": truth_tasks, "redactions": redactions,
             "sources": sorted({str(r.get("source", "unknown")) for r in rows}),
             "rubric": {"dimensions": sorted(rubric.dimensions), "scale": list(rubric.scale)} if rubric else None}
    return public, truth


def _validate(judgment: Mapping[str, Any], truth: Mapping[str, Any]
              ) -> tuple[list[str], dict[tuple[str, str], str], dict[tuple[str, str], dict[str, int]]]:
    problems: list[str] = []
    answers: dict[tuple[str, str], str] = {}
    scores: dict[tuple[str, str], dict[str, int]] = {}
    rubric = truth.get("rubric") or {}
    dims, (low, high) = set(rubric.get("dimensions") or []), tuple(rubric.get("scale") or (0, 5))
    if judgment.get("format") != JUDGMENT_FORMAT:
        problems.append("wrong format")
    if judgment.get("package_id") != truth["package_id"]:
        problems.append("package_id mismatch")
    if not str(judgment.get("rater_id") or "").strip():
        problems.append("missing rater_id")
    judge = judgment.get("judge") or {}
    if judge.get("saw_ground_truth") is not False:
        problems.append("judge must attest saw_ground_truth is false")
    if judge.get("type") not in ("human", "model"):
        problems.append("judge.type must be human or model")
    for entry in judgment.get("judgments") or []:
        task = truth["tasks"].get(entry.get("task_id"))
        if task is None or entry.get("item_id") not in task["items"]:
            problems.append(f"unknown item {entry.get('item_id')}")
        elif entry.get("author") in task["author_labels"] or entry.get("author") == GENERIC:
            answers[(entry["task_id"], entry["item_id"])] = entry["author"]
        elif entry.get("author") is not None:
            problems.append(f"unknown author label {entry.get('author')!r}")
        given = {k: v for k, v in (entry.get("scores") or {}).items() if v is not None}
        if given:
            bad = [k for k, v in given.items() if k not in dims or not isinstance(v, int) or isinstance(v, bool) or not low <= v <= high]
            if bad:
                problems.append(f"invalid rubric scores {sorted(bad)} for {entry.get('item_id')}")
            else:
                scores[(entry.get("task_id"), entry.get("item_id"))] = dict(given)
    return problems, answers, scores


def score_judgments(truth: Mapping[str, Any], judgments: Sequence[Mapping[str, Any]], *,
                    min_raters: int = 2, min_accuracy: float = 0.70, min_items: int = 6) -> dict[str, Any]:
    """Aggregate independent judgments against the hidden truth. Pure and deterministic."""
    expected = [(t, i) for t, task in truth["tasks"].items() for i in task["items"]]
    chance_by_item = {(t, i): 1 / len(task["author_labels"]) for t, task in truth["tasks"].items() for i in task["items"]}
    raters: list[dict[str, Any]] = []
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    complete_answers: list[dict[tuple[str, str], str]] = []
    complete_scores: list[dict[tuple[str, str], dict[str, int]]] = []
    seen_ids: set[str] = set()

    def right(t: str, i: str, a: str) -> bool:
        return truth["tasks"][t]["author_labels"].get(a) == truth["tasks"][t]["items"][i]

    for judgment in judgments:
        problems, answers, scores = _validate(judgment, truth)
        rid = str(judgment.get("rater_id") or "")
        if rid in seen_ids:
            problems.append("duplicate rater_id")
        seen_ids.add(rid)
        correct = sum(right(t, i, a) for (t, i), a in answers.items())
        complete = not problems and len(answers) == len(expected) and bool(expected)
        raters.append({"rater_id": rid, "judge_type": (judgment.get("judge") or {}).get("type"),
                       "answered": len(answers), "expected": len(expected), "complete": complete,
                       "accuracy": round(correct / len(answers), 4) if answers else None, "problems": problems})
        if complete:
            complete_answers.append(answers)
            complete_scores.append(scores)
            for (t, i), a in answers.items():
                confusion[truth["tasks"][t]["items"][i]][truth["tasks"][t]["author_labels"].get(a, GENERIC)] += 1
    scripted = [s for s in truth.get("sources", []) if s == "scripted"]
    pooled = [right(t, i, a) for answers in complete_answers for (t, i), a in answers.items()]
    rubric_sum: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for scores in complete_scores:
        for (t, i), given in scores.items():
            for dim, value in given.items():
                rubric_sum[truth["tasks"][t]["items"][i]][dim].append(value)
    accuracy = sum(pooled) / len(pooled) if pooled else None
    chance = sum(chance_by_item[k] for answers in complete_answers for k in answers) / len(pooled) if pooled else None
    agreement = [sum(a[k] == b[k] for k in a) / len(a) for a, b in combinations(complete_answers, 2)]
    reason = "no independent judgments were supplied"
    established = False
    if judgments and not complete_answers:
        reason = "no judgment file was complete and valid"
    elif complete_answers:
        if scripted:
            reason = "responses are scripted fixtures, not model output; recognizability of a model was not tested"
        elif len(complete_answers) < min_raters:
            reason = f"{len(complete_answers)} complete rater(s); at least {min_raters} required"
        elif len(expected) < min_items:
            reason = f"only {len(expected)} items; at least {min_items} required"
        elif accuracy is not None and accuracy < min_accuracy:
            reason = f"pooled accuracy {accuracy:.2f} below threshold {min_accuracy:.2f}"
        else:
            established, reason = True, f"pooled accuracy {accuracy:.2f} over {len(complete_answers)} complete raters (chance {chance:.2f})"
    status = "awaiting_judgments" if not judgments else ("scored" if complete_answers else "incomplete")
    return {
        "status": status, "package_id": truth["package_id"], "items": len(expected), "raters": raters,
        "complete_raters": len(complete_answers), "pooled_accuracy": None if accuracy is None else round(accuracy, 4),
        "chance": None if chance is None else round(chance, 4),
        "mean_pairwise_agreement": round(sum(agreement) / len(agreement), 4) if agreement else None,
        "per_character_recall": {c: round(row[c] / sum(row.values()), 4) for c, row in sorted(confusion.items())},
        "confusion": {c: dict(row) for c, row in sorted(confusion.items())},
        "generic_votes": sum(a == GENERIC for answers in complete_answers for a in answers.values()),
        "rubric_scores": ({c: {d: {"mean": round(sum(v) / len(v), 4), "n": len(v)} for d, v in sorted(dims.items())}
                           for c, dims in sorted(rubric_sum.items())} or None),
        "thresholds": {"min_raters": min_raters, "min_accuracy": min_accuracy, "min_items": min_items},
        "claim": {"blind_recognizability_established": established, "reason": reason},
    }
