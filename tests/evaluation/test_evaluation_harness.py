"""Focused acceptance for the behavioural evaluation harness (scripted; no network, no real model)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation import blind, harness, metrics
from evaluation.scenarios import load_fixture, load_fixtures

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
ALL = sorted(FIXTURES.glob("phase*_behavioral_acceptance.json"))
P4 = FIXTURES / "phase4_behavioral_acceptance.json"
SAME_INPUT = "scenario_1_same_input_different_personalities"
ATTENTION = "scenario_4_selective_attention"
VOICES = "scenario_1_distinctive_linguistic_voices"
NAMES = ("Mercury", "Kore", "Aura")


@pytest.fixture(scope="module")
def scenarios():
    return load_fixtures(ALL)


@pytest.fixture(scope="module")
def by_id(scenarios):
    return {s.scenario_id: s for s in scenarios}


class Fake:
    """A non-scripted source so blind claims and comparisons can be exercised."""

    source = "provider"

    def __init__(self, text=None, model="fake-model", fail=False):
        self.text, self.model, self.fail, self.requests = text, model, fail, []

    async def generate(self, request):
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("provider exploded")
        text = self.text(request) if callable(self.text) else self.text
        return harness.Generated(text or f"Reply from {request.character_id} to {request.scenario_id}.", model=self.model,
                                 usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})


def voice(request):
    style = {"mercury": "Measured. Dry. I would check the ground reference first, because that is cheap.",
             "kore": "Breathe, friend, and let the tired eyes rest; the copper will keep, and so will you.",
             "aura": "Whoa - freeze spray! Blast it, watch the trace pop - got it? Go, go, go!"}
    return style[request.character_id] + f" ({request.scenario_id[-6:]})"


# --------------------------------------------------------------------------
# Fixture loading
# --------------------------------------------------------------------------


def test_all_four_existing_fixtures_load_without_rewriting_them(scenarios):
    before = {p.name: p.read_bytes() for p in ALL}
    assert len(ALL) == 4 and len(scenarios) == 38
    assert len({s.scenario_id for s in scenarios}) == 38
    assert {p.name: p.read_bytes() for p in ALL} == before
    voices_scenario = next(s for s in scenarios if s.scenario_id == VOICES)
    assert [c.name for c in voices_scenario.companions] == list(NAMES)
    assert voices_scenario.expected_terms["mercury"] and voices_scenario.exemplars["kore"]
    unexecutable = [s for s in scenarios if not s.executable()[0]]
    assert unexecutable and all(s.executable()[1] for s in unexecutable)  # skipped with a stated reason


def test_runner_accepts_a_supplied_fixture_path_and_reports_unmapped_keys(tmp_path):
    custom = {"scenarios": [{
        "scenario_id": "p7_custom_initiative", "name": "custom", "future_field": {"x": 1},
        "character_configuration": {"name": "Nova", "persona": "Curious.", "personality_anchors": {"openness": 0.9}},
        "conversation_sequence": [{"role": "user", "content": "Hello there."}],
        "expected_behavior": {"observable_criteria": ["asks something"]},
    }]}
    path = tmp_path / "phase7_dataset.json"
    path.write_text(json.dumps(custom))
    [scenario] = load_fixture(path)
    assert scenario.companions[0].character_id == "nova" and scenario.executable()[0]
    assert scenario.unmapped == ("future_field",) and "initiative" in scenario.tags
    (tmp_path / "bad.json").write_text("{}")
    with pytest.raises(ValueError):
        load_fixture(tmp_path / "bad.json")


# --------------------------------------------------------------------------
# Scenario execution and character isolation
# --------------------------------------------------------------------------


async def test_scenario_execution_uses_the_real_prompt_and_records_everything(by_id):
    scenario = by_id[SAME_INPUT]
    result = await harness.run_evaluation([scenario], Fake(), harness.RunConfig(max_output_tokens=200))
    assert [r["character_id"] for r in result["results"]] == ["mercury", "kore", "aura"]
    for record in result["results"]:
        assert record["status"] == "passed" and record["provider_calls"] == 1
        assert record["dialogue"].startswith("Reply from")
        for key in ("scenario_id", "character_id", "provider", "configuration", "prompt_refs", "dialogue", "expected",
                    "observed", "failures", "usage", "timing", "status", "human_ratings", "pending_human_review"):
            assert key in record
        assert record["provider"]["model"] == "fake-model" and record["usage"]["total_tokens"] == 15
        assert record["configuration"]["max_tokens"] == 200  # cap applied at the provider seam
        assert "character_behavior" in record["prompt_refs"]["layers"] and record["prompt_refs"]["system_chars"] > 1500
    spec = scenario.companion("mercury")
    mercury = result["results"][0]["prompt"]
    assert spec.persona in mercury  # the effective persona prompt, not a scenario instruction
    assert "Mercury" in mercury


async def test_characters_are_isolated_from_each_other(by_id):
    scenario = by_id[SAME_INPUT]
    result = await harness.run_evaluation([scenario], Fake(), harness.RunConfig())
    for record in result["results"]:
        mine = scenario.companion(record["character_id"])
        assert mine.persona in record["prompt"]
        for other in scenario.companions:
            if other.character_id != mine.character_id:
                assert other.persona not in record["prompt"]
                assert f"Identity: {other.name}" not in record["prompt"]
    assert result["summary"]["messages_in_eval_db"] == 6  # user + assistant per character, nothing shared


async def test_cast_override_with_foreign_scenario_does_not_crash(by_id):
    """Regression: a cast that is not the scenario's own cast must be measured, not looked up."""
    cast = by_id[SAME_INPUT].companions
    config = harness.RunConfig(cast=cast, shared_companions=True, scenario_ids=(SAME_INPUT, ATTENTION, VOICES))
    seen = []
    result = await harness.run_evaluation(list(by_id.values()), Fake(voice), config, on_record=seen.append)
    assert len(result["results"]) == 9 == len(seen)
    assert {r["status"] for r in result["results"]} <= {"passed", "failed"}
    assert {r["character_id"] for r in result["results"]} == {"mercury", "kore", "aura"}
    sessions = {(r["character_id"], r["session_id"]) for r in result["results"]}
    assert len(sessions) == 9  # three sessions per persisted companion
    assert result["dimensions"]["selective_attention"]["value"] is not None
    assert result["dimensions"]["cross_session_identity"]["n"] > 0


# --------------------------------------------------------------------------
# Incomplete evaluations, provider errors, budget, reproducibility
# --------------------------------------------------------------------------


async def test_incomplete_evaluation_is_reported_not_hidden(by_id):
    scripted = harness.ScriptedResponder({f"{SAME_INPUT}/mercury": "Check the ledger first.", f"{SAME_INPUT}/kore": "Rest, then verify."})
    result = await harness.run_evaluation([by_id[SAME_INPUT]], scripted, harness.RunConfig())
    statuses = {r["character_id"]: r["status"] for r in result["results"]}
    assert statuses == {"mercury": "passed", "kore": "passed", "aura": "incomplete"}
    assert result["scenarios"][0]["status"] == "partial" and result["scenarios"][0]["completed_cells"] == 2
    aura = result["results"][2]
    assert aura["dialogue"] == "" and "MissingScript" in aura["error"]
    assert result["dimensions"]["linguistic_distinctiveness"]["n"] == 1  # computed only from what exists


async def test_provider_errors_are_recorded_and_never_scored_as_dialogue(by_id):
    result = await harness.run_evaluation([by_id[SAME_INPUT]], Fake(fail=True), harness.RunConfig())
    assert {r["status"] for r in result["results"]} == {"error"}
    for record in result["results"]:
        assert record["dialogue"] == "" and "provider exploded" in record["error"] and record["failures"] == []
    assert result["summary"]["statuses"] == {"error": 3}
    assert all(d["value"] is None for d in result["dimensions"].values())
    assert result["scenarios"][0]["status"] == "incomplete"


async def test_generation_and_token_budget_is_enforced(by_id):
    fake = Fake()
    result = await harness.run_evaluation([by_id[SAME_INPUT]], fake, harness.RunConfig(max_generations=2, max_output_tokens=50))
    assert len(fake.requests) == 2 and result["summary"]["generations"] == 2
    assert [r["status"] for r in result["results"]] == ["passed", "passed", "incomplete"]
    assert "BudgetExceeded" in result["results"][2]["error"]
    assert all(r.config.max_tokens <= 50 for r in fake.requests)


async def test_result_structure_is_reproducible(by_id):
    first = await harness.run_evaluation([by_id[VOICES]], Fake(voice), harness.RunConfig())
    second = await harness.run_evaluation([by_id[VOICES]], Fake(voice), harness.RunConfig())
    assert first["run_id"] == second["run_id"]
    assert harness.stable_view(first) == harness.stable_view(second)


async def test_no_production_data_is_touched(by_id, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://nobody:none@127.0.0.1:1/never_reached")
    async with harness.evaluation_env() as env:
        assert env.engine.url.get_backend_name() == "sqlite"
        database = Path(env.engine.url.database)
        assert database.exists() and database.parent != Path.cwd()
    assert not database.exists()  # throw-away database removed
    result = await harness.run_evaluation([by_id[SAME_INPUT]], Fake(), harness.RunConfig())
    assert result["summary"]["statuses"] == {"passed": 3}  # ran with an unreachable application database configured
    before = set(tmp_path.iterdir())
    paths = harness.write_artifacts(result, by_id, tmp_path / "out")
    assert set(tmp_path.iterdir()) == before | {tmp_path / "out"} and all(p.parent == tmp_path / "out" for p in paths.values())


# --------------------------------------------------------------------------
# Baseline comparison
# --------------------------------------------------------------------------


async def test_baseline_removes_only_the_named_layers_and_comparison_refuses_unlike_runs(by_id):
    scenario = by_id[VOICES]
    improved = await harness.run_evaluation([scenario], Fake(voice), harness.RunConfig())
    base_cfg = harness.RunConfig(name="baseline", drop_layers=harness.AUGMENTATION_LAYERS)
    baseline = await harness.run_evaluation([scenario], Fake(voice), base_cfg)
    for b, i in zip(baseline["results"], improved["results"]):
        assert set(b["prompt_refs"]["layers"]) == {"companion_identity"} and "character_behavior" in i["prompt_refs"]["layers"]
        assert b["prompt_refs"]["system_chars"] < i["prompt_refs"]["system_chars"]
    comparison = harness.compare_runs(baseline, improved)
    assert comparison["comparable"] is True and len(comparison["pairs"]) == 3
    assert all(p["context_budget_delta_chars"] > 0 for p in comparison["pairs"]) and "by design" in comparison["context_budget_note"]
    other_model = await harness.run_evaluation([scenario], Fake(voice, model="another"), base_cfg)
    unlike = harness.compare_runs(other_model, improved)
    assert unlike["comparable"] is False and any("different models" in r for r in unlike["reasons"])
    scripted = await harness.run_evaluation([scenario], harness.ScriptedResponder.from_exemplars([scenario]), base_cfg)
    assert harness.compare_runs(scripted, scripted)["comparable"] is False  # scripted replies ignore the prompt


# --------------------------------------------------------------------------
# Blind export, ground truth, scoring
# --------------------------------------------------------------------------


@pytest.fixture
async def model_run(by_id):
    """Two scenarios x three characters answered by a (fake) non-scripted source."""
    config = harness.RunConfig(scenario_ids=(SAME_INPUT, VOICES))
    return await harness.run_evaluation(list(by_id.values()), Fake(voice), config)


async def test_anonymous_export_removes_identity_and_shuffles_reproducibly(model_run, by_id):
    named = [{**r, "dialogue": f"Honestly, {NAMES[n % 3]} would say this. {r['dialogue']}"} for n, r in enumerate(model_run["results"])]
    public, truth = blind.build_blind_package(named, by_id, seed="s1")
    blob = json.dumps(public).lower()
    assert not any(name.lower() in blob for name in NAMES)
    assert "[name]" in blob and truth["redactions"] > 0
    assert len(public["tasks"]) == 2 and all(len(t["items"]) == 3 and len(t["authors"]) == 3 for t in public["tasks"])
    assert all(a["references"] for t in public["tasks"] for a in t["authors"])
    again, _ = blind.build_blind_package(named, by_id, seed="s1")
    other, _ = blind.build_blind_package(named, by_id, seed="s2")
    assert again == public and other != public
    generation_order = [list(t["items"]) for t in truth["tasks"].values()]
    shuffled = {tuple(tuple(i["item_id"] for i in t["items"]) for t in blind.build_blind_package(named, by_id, seed=f"x{n}")[0]["tasks"])
                for n in range(6)}
    assert len(shuffled) > 1 and not all(list(t) in generation_order for run in shuffled for t in run)  # order varies with the seed
    assert len(public["judgment_template"]["judgments"]) == 6


async def test_ground_truth_is_preserved_separately_and_complete(model_run, by_id):
    public, truth = blind.build_blind_package(model_run["results"], by_id, seed="s1")
    assert truth["package_id"] == public["package_id"]
    public_ids = {i["item_id"] for t in public["tasks"] for i in t["items"]}
    truth_ids = {i for t in truth["tasks"].values() for i in t["items"]}
    assert public_ids == truth_ids and len(truth_ids) == 6
    assert set(truth["tasks"][public["tasks"][0]["task_id"]]["items"].values()) == {"mercury", "kore", "aura"}
    assert "mercury" not in json.dumps(public).lower() and "character_id" not in json.dumps(public)
    for task in public["tasks"]:  # reference authors are the truth's labelled characters
        assert set(truth["tasks"][task["task_id"]]["author_labels"]) == {a["label"] for a in task["authors"]}


def judgment(truth, rater, *, correct=True, drop=0, saw=False, kind="human"):
    entries = []
    for task_id, task in truth["tasks"].items():
        by_char = {c: lab for lab, c in task["author_labels"].items()}
        labels = sorted(task["author_labels"])
        for n, (item, char) in enumerate(task["items"].items()):
            guess = by_char[char] if correct else labels[(labels.index(by_char[char]) + 1) % len(labels)]
            entries.append({"task_id": task_id, "item_id": item, "author": guess})
    return {"format": blind.JUDGMENT_FORMAT, "package_id": truth["package_id"], "rater_id": rater,
            "judge": {"type": kind, "saw_ground_truth": saw}, "judgments": entries[: len(entries) - drop]}


async def test_scoring_aggregation_and_the_refusal_to_overclaim(model_run, by_id):
    _, truth = blind.build_blind_package(model_run["results"], by_id, seed="s1")
    none = blind.score_judgments(truth, [])
    assert none["status"] == "awaiting_judgments" and none["claim"]["blind_recognizability_established"] is False
    good = blind.score_judgments(truth, [judgment(truth, "r1"), judgment(truth, "r2")])
    assert good["pooled_accuracy"] == 1.0 and good["chance"] == pytest.approx(1 / 3, abs=1e-3)
    assert good["complete_raters"] == 2 and good["claim"]["blind_recognizability_established"] is True
    assert good["mean_pairwise_agreement"] == 1.0 and set(good["per_character_recall"]) == {"mercury", "kore", "aura"}
    one = blind.score_judgments(truth, [judgment(truth, "r1")])
    assert one["claim"]["blind_recognizability_established"] is False and "at least 2" in one["claim"]["reason"]
    wrong = blind.score_judgments(truth, [judgment(truth, "r1", correct=False), judgment(truth, "r2", correct=False)])
    assert wrong["pooled_accuracy"] == 0.0 and wrong["claim"]["blind_recognizability_established"] is False
    partial = blind.score_judgments(truth, [judgment(truth, "r1", drop=2), judgment(truth, "r2", drop=1)])
    assert partial["status"] == "incomplete" and partial["complete_raters"] == 0
    assert partial["claim"]["blind_recognizability_established"] is False
    bad = blind.score_judgments(truth, [judgment(truth, "r1", saw=True), judgment(truth, "r1"), {**judgment(truth, "r3"), "package_id": "x"}])
    assert bad["complete_raters"] == 0 and any("saw_ground_truth" in p for p in bad["raters"][0]["problems"])
    assert any("duplicate" in p for p in bad["raters"][1]["problems"])


async def test_scripted_responses_can_never_establish_blind_recognizability(by_id):
    scenarios = [by_id[SAME_INPUT], by_id[VOICES]]
    result = await harness.run_evaluation(scenarios, harness.ScriptedResponder.from_exemplars(scenarios), harness.RunConfig())
    _, truth = blind.build_blind_package(result["results"], by_id, seed="s1")
    scored = blind.score_judgments(truth, [judgment(truth, "r1"), judgment(truth, "r2")])
    assert scored["pooled_accuracy"] == 1.0
    assert scored["claim"]["blind_recognizability_established"] is False and "scripted" in scored["claim"]["reason"]


def test_a_surviving_identifying_label_blocks_the_package(by_id, monkeypatch):
    rows = [{"scenario_id": SAME_INPUT, "condition_id": "default", "character_id": c, "status": "passed",
             "dialogue": "Ask Mercury.", "probe": "x", "source": "provider"} for c in ("mercury", "kore")]
    rows += [{**r, "scenario_id": VOICES} for r in rows]
    assert blind.redact("MERCURY's idea; ask Kore.", ["Mercury", "Kore"]) == ("[name]'s idea; ask [name].", 2)
    assert blind.build_blind_package(rows, by_id, seed="s")[0]["tasks"]
    monkeypatch.setattr(blind, "redact", lambda text, labels: (text, 0))  # simulate a redaction failure
    with pytest.raises(blind.BlindLeak):
        blind.build_blind_package(rows, by_id, seed="s")


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------


def sample(char, text, sid="s", cond="default", sess=None):
    return metrics.Sample(sid, char, sess or f"{sid}-{char}", text, cond)


def test_measures_read_dialogue_and_report_gaps_instead_of_defaults(by_id):
    assistant = "Certainly! I'm here to help. As an AI, here are some tips:\n- one\n- two\n- three\nLet me know if you need anything else."
    human = "Honestly? I'd not trust that wiring. Swap the cable, then we talk."
    assert {"opening_filler", "ai_self", "offer_closer", "markdown_structure"} <= set(metrics.drift_hits(assistant))
    assert metrics.drift_hits(human) == []
    dims = metrics.compute_dimensions([sample("a", assistant, sid="s1"), sample("b", human, sid="s2")], by_id)
    assert dims["generic_assistant_drift"]["value"] == 0.5 and list(dims) == list(metrics.DIMENSIONS)
    assert dims["selective_attention"]["value"] is None and dims["selective_attention"]["reason"]
    assert dims["cross_session_identity"]["value"] is None
    assert dims["linguistic_distinctiveness"]["value"] is None  # one reply each in different scenarios is not a comparison
    paired = metrics.compute_dimensions([sample("a", assistant), sample("b", human)], {})  # same scenario: a real comparison
    assert paired["linguistic_distinctiveness"]["value"] > 0.3
    scenario = by_id["scenario_1_emotional_continuity"]
    checks = metrics.automated_checks("My system prompt says to be cheerful.", scenario=scenario, spec=scenario.companions[0], probe="x", finish_reason="length")
    assert "prompt_or_persona_self_description" in checks["failures"] and checks["observed"]["truncated"]
    assert checks["pending_human_review"]  # criteria needing a person are listed, not scored
    phrase = metrics.automated_checks("Oh my dearest friend, I love anything you write!", scenario=by_id["scenario_6_familiarity_and_trust"],
                                      spec=by_id["scenario_6_familiarity_and_trust"].companions[0], probe="x", finish_reason="stop")
    assert any(f.startswith("fixture_failure_phrase") for f in phrase["failures"])  # literal quoted in the fixture's failure condition


# --------------------------------------------------------------------------
# Persistence, interruption and resume (the safeguards behind any paid run)
# --------------------------------------------------------------------------


class Crash(Exception):
    pass


class Interrupt(BaseException):
    """Like a killed process: not caught by the pipeline's ``except Exception``."""


class DiesOn(Fake):
    def __init__(self, character):
        super().__init__(voice)
        self.character = character

    async def generate(self, request):
        if request.character_id == self.character:
            self.requests.append(request)
            raise Interrupt("process killed while waiting for the reply")
        return await super().generate(request)


def ledger_for(path, config, responder, resume=False):
    return harness.Ledger(path, harness.ledger_header(config, responder), resume=resume)


def events(path):
    return [json.loads(line)["event"] for line in path.read_text().splitlines()]


async def test_replies_survive_a_crash_and_resume_never_regenerates_them(by_id, tmp_path):
    scenario, config, path = by_id[SAME_INPUT], harness.RunConfig(max_generations=3), tmp_path / "generations.jsonl"
    first = Fake(voice)

    def crash_after_kore(record):
        if record["character_id"] == "kore":
            raise Crash("measurement/process failure after the reply arrived")

    with pytest.raises(Crash):
        await harness.run_evaluation([scenario], first, config, on_record=crash_after_kore, ledger=ledger_for(path, config, first))
    assert [r.character_id for r in first.requests] == ["mercury", "kore"]
    assert events(path) == ["run", "request", "response", "request", "response"]  # request written before each call
    kore_text = [json.loads(line) for line in path.read_text().splitlines()][-1]["text"]

    second = Fake(voice)
    resumed = await harness.run_evaluation([scenario], second, config, ledger=ledger_for(path, config, second, resume=True))
    assert [r.character_id for r in second.requests] == ["aura"]  # only the missing cell is generated
    assert [r["replayed"] for r in resumed["results"]] == [True, True, False]
    assert resumed["results"][1]["dialogue"] == kore_text and {r["status"] for r in resumed["results"]} == {"passed"}
    assert resumed["summary"]["generation_accounting"] == {
        "new_generations": 1, "replayed_from_ledger": 2, "earlier_requests_in_ledger": 2, "total_consumed": 3, "max_generations": 3}

    third = Fake(voice)
    again = await harness.run_evaluation([scenario], third, config, ledger=ledger_for(path, config, third, resume=True))
    assert third.requests == [] and all(r["replayed"] for r in again["results"])
    assert [r["dialogue"] for r in again["results"]] == [r["dialogue"] for r in resumed["results"]]


async def test_an_unanswered_request_is_reported_not_silently_resent(by_id, tmp_path):
    scenario, path = by_id[SAME_INPUT], tmp_path / "generations.jsonl"
    config = harness.RunConfig(max_generations=3)
    dying = DiesOn("kore")
    with pytest.raises(Interrupt):
        await harness.run_evaluation([scenario], dying, config, ledger=ledger_for(path, config, dying))
    assert events(path) == ["run", "request", "response", "request"]  # kore was sent, never answered

    resumed_fake = Fake(voice)
    resumed = await harness.run_evaluation([scenario], resumed_fake, config, ledger=ledger_for(path, config, resumed_fake, resume=True))
    assert [r.character_id for r in resumed_fake.requests] == ["aura"]
    status = {r["character_id"]: r["status"] for r in resumed["results"]}
    assert status == {"mercury": "passed", "kore": "incomplete", "aura": "passed"}
    assert "UnansweredRequest" in resumed["results"][1]["error"] and resumed["results"][1]["dialogue"] == ""
    assert resumed["summary"]["generation_accounting"]["total_consumed"] == 3  # the unanswered request still counts


async def test_explicit_retry_of_an_unanswered_request_counts_against_the_budget(by_id, tmp_path):
    scenario, path = by_id[SAME_INPUT], tmp_path / "generations.jsonl"
    config = harness.RunConfig(max_generations=4)
    dying = DiesOn("kore")
    with pytest.raises(Interrupt):
        await harness.run_evaluation([scenario], dying, config, ledger=ledger_for(path, config, dying))
    retry = dataclasses_replace(config, retry_unanswered=True)
    fake = Fake(voice)
    result = await harness.run_evaluation([scenario], fake, retry, ledger=ledger_for(path, retry, fake, resume=True))
    assert [r.character_id for r in fake.requests] == ["kore", "aura"]
    assert result["summary"]["generation_accounting"]["total_consumed"] == 4 and result["summary"]["statuses"] == {"passed": 3}


async def test_ledger_refusals_torn_lines_and_unrecorded_requests_bound_the_budget(by_id, tmp_path):
    config, path = harness.RunConfig(max_generations=12), tmp_path / "generations.jsonl"
    ledger = ledger_for(path, config, Fake())
    ledger._write({"event": "unrecorded_requests", "count": 10, "reason": "lost before the ledger existed"})
    with path.open("a") as handle:
        handle.write('{"event": "response", "cell": "x", "te')  # a write cut off by an interruption
    with pytest.raises(harness.LedgerError, match="already records"):
        ledger_for(path, config, Fake())  # never overwritten / restarted
    with pytest.raises(harness.LedgerError, match="different run"):
        ledger_for(path, harness.RunConfig(max_generations=12, name="baseline"), Fake(), resume=True)
    with pytest.raises(harness.LedgerError, match="different run"):
        ledger_for(path, config, Fake(model="another-model"), resume=True)
    fake = Fake(voice)
    resumed = ledger_for(path, config, fake, resume=True)
    assert resumed.prior_requests == 10 and "x" not in resumed.responses
    assert all(line.startswith("{") for line in path.read_text().splitlines()[-1:])  # the torn line was not extended
    result = await harness.run_evaluation([by_id[SAME_INPUT], by_id[VOICES]], fake, config, ledger=resumed)
    assert len(fake.requests) == 2  # 12 authorised - 10 already consumed
    assert result["summary"]["statuses"] == {"passed": 2, "incomplete": 4}
    assert result["summary"]["generation_accounting"]["total_consumed"] == 12


async def test_a_failed_request_is_not_resent_on_resume_and_specs_follow_each_scenario(by_id, tmp_path):
    scenario, config, path = by_id[SAME_INPUT], harness.RunConfig(max_generations=6), tmp_path / "generations.jsonl"
    failing = Fake(fail=True)
    await harness.run_evaluation([scenario], failing, config, ledger=ledger_for(path, config, failing))
    later = Fake(fail=True)
    later.fail = False
    resumed = await harness.run_evaluation([scenario], later, config, ledger=ledger_for(path, config, later, resume=True))
    assert later.requests == [] and {r["status"] for r in resumed["results"]} == {"incomplete"}
    native = await harness.run_evaluation(list(by_id.values()), Fake(voice), harness.RunConfig(scenario_ids=(SAME_INPUT, VOICES)))
    # both scenarios configure a "mercury"; each reply is measured against its own scenario's configuration
    assert native["dimensions"]["selective_attention"]["value"] is not None


def test_cli_rejects_invalid_or_missing_generation_bounds(tmp_path, capsys):
    from evaluation import run
    for flag in ("--max-generations", "--max-output-tokens"):
        with pytest.raises(SystemExit) as exit_info:
            run.main(["run", "--out", str(tmp_path), flag, "0"])
        assert exit_info.value.code == 2 and "at least 1" in capsys.readouterr().err
    assert run.main(["run", "--provider", "--out", str(tmp_path / "p")]) == 4  # unbounded provider run refused, nothing sent
    loaded = run._load([])
    args = __import__("argparse").Namespace(scenario=[], preset="initial-real", max_generations=50, max_output_tokens=900,
                                            shared=False, baseline=False, seed="s", retry_unanswered=False)
    config = run._config(args, loaded)
    assert (config.max_generations, config.max_output_tokens, config.shared_companions) == (12, 200, True)
    assert [c.character_id for c in config.cast] == ["mercury", "kore", "aura"] and len(config.scenario_ids) == 4


def test_recorded_real_evaluation_status_is_incomplete_and_bounds_any_resume():
    folder = Path(__file__).resolve().parent / "results" / "initial-real"
    status = json.loads((folder / "evaluation_status.json").read_text())
    assert status["status"] == "incomplete" and status["recorded_responses"] == 0 and status["real_model_findings"] is None
    assert status["consumed_generations"]["approximate"] == 10 and status["remaining_generations"] == 2
    entries = [json.loads(line) for line in (folder / "generations.jsonl").read_text().splitlines()]
    assert [e["event"] for e in entries] == ["run", "unrecorded_requests"] and entries[1]["count"] == 10
    assert not any(e["event"] == "response" for e in entries)  # no reconstructed replies


def dataclasses_replace(config, **changes):
    import dataclasses
    return dataclasses.replace(config, **changes)
