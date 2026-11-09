"""Phase 7 integration: the thirty-scenario dataset and rubric through the evaluation harness (scripted only)."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path

import pytest

from evaluation import blind, harness, metrics
from evaluation.scenarios import load_fixture, load_fixtures, load_rubric

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
DATASET = FIXTURES / "phase7_behavioral_evaluation.json"
RUBRIC = FIXTURES / "phase7_evaluation_rubric.json"
ALL = sorted([*FIXTURES.glob("phase*_behavioral_acceptance.json"), DATASET])


@pytest.fixture(scope="module")
def dataset():
    return load_fixture(DATASET)


@pytest.fixture(scope="module")
def everything():
    return {s.scenario_id: s for s in load_fixtures(ALL)}


@pytest.fixture(scope="module")
def rubric():
    return load_rubric(RUBRIC)


@pytest.fixture(scope="module")
def phase7_run(dataset):  # one scripted pass shared by the tests below (sync fixture: no event-loop scope coupling)
    return asyncio.run(harness.run_evaluation(dataset, harness.ScriptedResponder.from_exemplars(dataset), harness.RunConfig()))


def test_all_thirty_scenarios_load_unmodified_with_three_distinct_companions(dataset):
    raw = json.loads(DATASET.read_text())
    assert len(dataset) == 30 == len(raw["scenarios"])
    assert Counter(s.category.split(" — ")[0] for s in dataset) == {f"CATEGORY {c}": 5 for c in "ABCDEF"}
    assert all(s.executable()[0] and len(s.cells()) == 3 for s in dataset)
    first = dataset[0].companions
    assert [c.character_id for c in first] == ["mercury", "kore", "aura"]
    assert all(s.companions == first for s in dataset)  # one shared cast, as the dataset defines it
    for field in ("persona", "fingerprint", "anchors", "attention_terms"):
        values = [json.dumps(getattr(c, field), sort_keys=True, default=list) for c in first]
        assert len(set(values)) == 3, field  # configurations remain distinct
    assert all(s.exemplar_for(c.character_id, s.conditions[0], s.conditions[0].sessions[0]) for s in dataset for c in s.companions)
    assert all(s.failure_conditions and s.requirements for s in dataset) and not any(s.unmapped for s in dataset)


def test_rubric_loads_with_eight_dimensions_and_identification_key(rubric):
    assert list(rubric.dimensions) == ["recognizability", "character_consistency", "naturalness", "behavioral_differentiation",
                                       "memory_grounding", "emotional_continuity", "relationship_coherence", "generic_assistant_drift"]
    assert rubric.scale == (0, 5) and set(rubric.identification_key) == {"Mercury", "Kore", "Aura"}
    assert "condition_B_baseline" in rubric.baseline_protocol["conditions"]


async def test_full_phase7_dataset_executes_through_the_scripted_interface(phase7_run):
    assert phase7_run["summary"]["records"] == 90 and phase7_run["summary"]["generations"] == 90
    assert phase7_run["summary"]["statuses"] == {"passed": 89, "failed": 1}
    [failed] = [r for r in phase7_run["results"] if r["status"] == "failed"]
    # Classified B: the fixture's own example reply contains a phrase the drift criterion treats as boilerplate.
    assert (failed["scenario_id"], failed["character_id"]) == ("scenario_16_shared_achievement", "mercury")
    assert failed["failures"] == ["generic_assistant_drift:boilerplate"] and "keep in mind that" in failed["dialogue"].lower()
    assert all(r["provider_calls"] == 1 for r in phase7_run["results"])
    assert all(r["pending_human_review"] for r in phase7_run["results"])  # free-text criteria stay for humans


async def test_scenario_state_is_initialized_through_production_state_and_stays_with_its_owner(everything):
    ids = ("scenario_11_important_shared_experience", "scenario_13_developed_interest", "scenario_17_unresolved_disagreement",
           "scenario_12_corrected_memory", "scenario_8_shared_references_and_running_jokes", "scenario_6_familiarity_and_trust")
    scenarios = [everything[i] for i in ids]
    result = await harness.run_evaluation(scenarios, harness.ScriptedResponder.from_exemplars(scenarios), harness.RunConfig())
    cell = {(r["scenario_id"], r["condition_id"], r["character_id"]): r for r in result["results"]}

    def prompt(sid, char, cond="default"):
        return cell[(sid, cond, char)]["prompt"]

    assert "winter cluster partition" in prompt("scenario_11_important_shared_experience", "kore")  # experience memory
    assert "3D-printing" in prompt("scenario_13_developed_interest", "aura")  # its own developed interest only
    assert "3D-printing" not in prompt("scenario_13_developed_interest", "mercury")
    assert "licensing" in prompt("scenario_17_unresolved_disagreement", "mercury")  # unresolved emotional association
    assert "emotional_associations" in cell[("scenario_17_unresolved_disagreement", "default", "mercury")]["prompt_refs"]["layers"]
    assert "Tuesday" in prompt("scenario_12_corrected_memory", "aura")  # recorded (stale) belief as a user fact
    shared = "scenario_8_shared_references_and_running_jokes"
    with_history = next(r for r in result["results"] if r["scenario_id"] == shared and r["character_id"] == "mercury")
    without = next(r for r in result["results"] if r["scenario_id"] == shared and r["character_id"] == "kore")
    assert "yellow FTDI" in with_history["prompt"] and "yellow FTDI" not in without["prompt"]  # evidence follows its companion
    stages = "scenario_6_familiarity_and_trust"
    assert "3 completed exchanges" in prompt(stages, "theron", "early-acquaintance")
    assert "85 completed exchanges" in prompt(stages, "theron", "established-veteran")  # stage contrast uses real columns
    for record in result["results"]:
        entries = record["prompt_refs"]["seeding"]["entries"]
        assert not [e for e in entries if e["status"] == "rejected"]
        assert all(e.get("detail") for e in entries if e["status"] == "not_applied")  # every gap has its reason
        assert "trust_level" not in record["prompt"]  # no field -> never smuggled in as an instruction


async def test_relationship_contrast_without_production_state_is_reported_not_faked(everything):
    scenario = everything["scenario_5_teasing_versus_criticism"]
    assert [c.condition_id for c in scenario.conditions] == ["high-trust-established-friendship", "strained-low-trust-relationship"]
    result = await harness.run_evaluation([scenario], harness.ScriptedResponder.from_exemplars([scenario]), harness.RunConfig())
    high, low = result["results"]
    assert high["dialogue"] != low["dialogue"]  # each cell answered with its own expected reply
    # Trust, humour style and the dispute context have no production representation: identical prompts, reported.
    assert high["prompt_refs"]["system_sha256_16"] == low["prompt_refs"]["system_sha256_16"]
    gaps = {e["field"] for e in low["prompt_refs"]["seeding"]["entries"] if e["status"] == "not_applied"}
    assert {"trust_level", "interaction_context"} <= gaps


async def test_blind_package_carries_the_rubric_without_names_and_scores_only_real_judgments(phase7_run, dataset, rubric):
    by_id = {s.scenario_id: s for s in dataset}
    public, truth = blind.build_blind_package(phase7_run["results"], by_id, seed="p7", rubric=rubric)
    blob = json.dumps(public)
    assert len(public["tasks"]) == 30 and not any(n in blob for n in ("Mercury", "Kore", "Aura"))
    assert list(public["rubric"]["dimensions"]) == list(rubric.dimensions)
    assert all(a.get("profile", {}).get("linguistic_markers") for t in public["tasks"] for a in t["authors"])
    assert all(set(j["scores"]) == set(rubric.dimensions) for j in public["judgment_template"]["judgments"])
    assert "character_id" not in blob and truth["rubric"]["scale"] == [0, 5]
    absent = blind.score_judgments(truth, [])
    assert absent["rubric_scores"] is None and absent["pooled_accuracy"] is None  # nothing invented without raters

    def judged(rater, score, generic=False):
        entries = []
        for task_id, task in truth["tasks"].items():
            back = {c: lab for lab, c in task["author_labels"].items()}
            for item, char in task["items"].items():
                entries.append({"task_id": task_id, "item_id": item, "author": "generic" if generic else back[char],
                                "scores": {"recognizability": score}})
        return {"format": blind.JUDGMENT_FORMAT, "package_id": truth["package_id"], "rater_id": rater,
                "judge": {"type": "human", "saw_ground_truth": False}, "judgments": entries}

    scored = blind.score_judgments(truth, [judged("r1", 4), judged("r2", 2)])
    assert scored["rubric_scores"]["mercury"]["recognizability"] == {"mean": 3.0, "n": 60}
    assert scored["claim"]["blind_recognizability_established"] is False and "scripted" in scored["claim"]["reason"]
    generic = blind.score_judgments(truth, [judged("r1", 1, generic=True)])
    assert generic["generic_votes"] == 90 and generic["pooled_accuracy"] == 0.0
    bad = judged("r3", 9)
    assert any("invalid rubric scores" in p for p in blind.score_judgments(truth, [bad])["raters"][0]["problems"])


async def test_rubric_neutral_baseline_replaces_personality_directives_only(dataset):
    scenario = dataset[0]

    class Fake:
        source, model = "provider", "fake-model"

        async def generate(self, request):
            return harness.Generated(f"reply {request.character_id}")

    improved = await harness.run_evaluation([scenario], Fake(), harness.RunConfig())
    neutral = await harness.run_evaluation([scenario], Fake(), harness.baseline_config("neutral"))
    for b, i in zip(neutral["results"], improved["results"]):
        assert harness.NEUTRAL_BASELINE_INSTRUCTION in b["prompt"] and "Identity:" not in b["prompt"]
        assert set(b["prompt_refs"]["layers"]) == {"companion_identity"} and "Identity:" in i["prompt"]
    comparison = harness.compare_runs(neutral, improved)
    assert comparison["comparable"] is True and neutral["configuration"]["replaced_prompt_layers"] == ["companion_identity"]
    assert harness.compare_runs(improved, improved)["comparable"] is False  # same configuration is not a comparison


def test_drift_classification_fixes_are_narrow_and_keep_the_criterion():
    assert metrics.drift_hits("Check heap pressure before you touch anything else.") == []  # A: not an offer
    assert "offer_closer" in metrics.drift_hits("Is there anything else you need?")
    steps = "Here is how it works:\n1. Cut off at K.\n2. Keep appending.\n3. Truncate.\n"
    assert metrics.drift_hits(steps, "Can you explain how Raft log compaction works?") == []  # A: production's exemption
    assert "markdown_structure" in metrics.drift_hits(steps, "Morning! How's it going?")  # still drift in casual chat
    assert "boilerplate" in metrics.drift_hits("Just keep in mind that traffic spikes.")  # unchanged criterion


async def test_whole_suite_scripted_totals_are_exact(everything):
    result = await harness.run_evaluation(list(everything.values()),
                                          harness.ScriptedResponder.from_exemplars(everything.values()), harness.RunConfig())
    assert result["summary"]["statuses"] == {"passed": 141, "failed": 1, "incomplete": 2}
    assert result["summary"]["scenarios_skipped"] == 0 and result["summary"]["records"] == 144
    incomplete = sorted((r["scenario_id"], r["character_id"]) for r in result["results"] if r["status"] == "incomplete")
    # D: the fixtures provide no expected reply for these cells; nothing is fabricated for them.
    assert incomplete == [("scenario_2_related_experiences_connect_into_coherent_history", "kore"),
                          ("scenario_8_streaming_performance", "kore")]
    assert all("MissingScript" in r["error"] and r["dialogue"] == "" for r in result["results"] if r["status"] == "incomplete")
    by_scenario = Counter(s["status"] for s in result["scenarios"])
    assert by_scenario == {"complete": 66, "partial": 1, "incomplete": 1}
