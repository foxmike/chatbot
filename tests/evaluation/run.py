"""Command line for the behavioural evaluation harness.

Run from the repository root with the project interpreter::

    python tests/evaluation/run.py run --fixtures tests/fixtures/phase4_behavioral_acceptance.json --out /tmp/eval
    python tests/evaluation/run.py run --fixtures <any fixture path> --provider --max-generations 12 --out /tmp/eval-real
    python tests/evaluation/run.py score --truth /tmp/eval/blind_truth.json --judgments r1.json r2.json

Every run keeps ``<out>/generations.jsonl``: each request is written before the
model is called and each reply the moment it returns. An interrupted run is
continued with ``--resume`` (same arguments): recorded replies are replayed, not
regenerated; a request that was sent without obtaining a reply (none, or an error) is
reported, and only re-sent with ``--retry-unanswered``; all earlier requests count against
``--max-generations``. An existing ledger is never overwritten.

``run`` without ``--provider`` is the deterministic scripted acceptance (each
fixture's own exemplary response); with ``--provider`` it uses the configured
conversation provider within the stated generation/token bounds and never
retries. ``--preset initial-real`` is the authorised bounded evaluation: three
contrasting companions, four shared scenarios, at most 12 generations and 200
output tokens each. Output goes only to ``--out``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(1, str(Path(__file__).resolve().parent.parent.parent))
    __package__ = "evaluation"

from evaluation import blind, harness, scenarios as scn  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
DEFAULT_FIXTURES = ("phase*_behavioral_acceptance.json", "phase7_behavioral_evaluation.json")
DEFAULT_RUBRIC = FIXTURES / "phase7_evaluation_rubric.json"
PRESET_SCENARIOS = ("scenario_1_same_input_different_personalities", "scenario_4_selective_attention",
                    "scenario_3_personality_specific_reactions", "scenario_1_distinctive_linguistic_voices")
PRESET_CAST_SCENARIO = "scenario_1_same_input_different_personalities"


def _load(paths: list[str]) -> list[scn.Scenario]:
    return scn.load_fixtures(paths or sorted({str(p) for pattern in DEFAULT_FIXTURES for p in FIXTURES.glob(pattern)}))


def _config(args: argparse.Namespace, loaded: list[scn.Scenario]) -> harness.RunConfig:
    cast = None
    ids = tuple(args.scenario) if args.scenario else None
    if args.preset == "initial-real":
        cast = next(s for s in loaded if s.scenario_id == PRESET_CAST_SCENARIO).companions
        ids = PRESET_SCENARIOS
        args.max_generations = min(12 if args.max_generations is None else args.max_generations, 12)
        args.max_output_tokens = min(200 if args.max_output_tokens is None else args.max_output_tokens, 200)
        args.shared = True
    common = dict(retry_unanswered=args.retry_unanswered, cast=cast, shared_companions=args.shared,
                  max_generations=args.max_generations, max_output_tokens=args.max_output_tokens, seed=args.seed, scenario_ids=ids)
    if args.baseline:
        return harness.baseline_config("layers" if args.baseline is True else args.baseline, **common)
    return harness.RunConfig(name="improved", **common)


async def _run(args: argparse.Namespace) -> int:
    loaded = _load(args.fixtures)
    config = _config(args, loaded)
    if args.provider:
        if config.max_generations is None:
            print("refused: a real-provider run needs an explicit --max-generations (or --preset)", file=sys.stderr)
            return 4
        responder = harness.provider_responder_from_settings()
        if responder is None:
            print("provider unavailable: no conversation provider credentials/endpoint configured; nothing was run", file=sys.stderr)
            return 3
    else:
        responder = harness.ScriptedResponder.from_exemplars(loaded, cast=config.cast)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        ledger = harness.Ledger(out / "generations.jsonl", harness.ledger_header(config, responder), resume=args.resume)
    except harness.LedgerError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 4
    log = out / "records.jsonl"
    log.write_text("")  # this run's records, one line each, appended as produced (replies themselves are in the ledger)

    def keep(record: dict) -> None:
        with log.open("a") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    try:
        result = await harness.run_evaluation(loaded, responder, config, on_record=keep, ledger=ledger)
    except BaseException as exc:  # interrupted or crashed: every reply obtained so far is already in the ledger
        print(f"interrupted ({type(exc).__name__}: {exc}); replies are preserved in {ledger.path}; "
              f"rerun the same command with --resume to continue without regenerating them", file=sys.stderr)
        return 2
    rubric = scn.load_rubric(args.rubric) if args.rubric else None
    paths = harness.write_artifacts(result, {s.scenario_id: s for s in loaded}, args.out, seed=args.seed, rubric=rubric)
    print(json.dumps({"run_id": result["run_id"], "summary": result["summary"], "artifacts": {k: str(v) for k, v in paths.items()}}, indent=2))
    return 0


def _score(args: argparse.Namespace) -> int:
    truth = json.loads(Path(args.truth).read_text())
    judgments = [json.loads(Path(p).read_text()) for p in args.judgments]
    print(json.dumps(blind.score_judgments(truth, judgments, min_raters=args.min_raters), indent=2))
    return 0


def _compare(args: argparse.Namespace) -> int:
    baseline, improved = (json.loads(Path(p).read_text()) for p in (args.baseline_results, args.improved_results))
    print(json.dumps(harness.compare_runs(baseline, improved), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--fixtures", nargs="*", default=[])
    run.add_argument("--scenario", nargs="*", default=[])
    run.add_argument("--out", required=True)
    run.add_argument("--provider", action="store_true", help="use the configured conversation provider")
    run.add_argument("--baseline", nargs="?", const="layers", choices=["layers", "neutral"], default=None,
                     help="layers: drop the Phase 4-6 augmentation prompt layers; neutral: the rubric's condition B")
    run.add_argument("--rubric", default=str(DEFAULT_RUBRIC) if DEFAULT_RUBRIC.exists() else None,
                     help="rubric JSON whose dimensions and markers go into the blind package")
    run.add_argument("--preset", choices=["initial-real"])
    run.add_argument("--shared", action="store_true", help="persist one companion per character across scenarios")
    run.add_argument("--max-generations", type=int)
    run.add_argument("--max-output-tokens", type=int)
    run.add_argument("--seed", default="phase7")
    run.add_argument("--resume", action="store_true", help="continue an interrupted run in --out, replaying recorded replies")
    run.add_argument("--retry-unanswered", action="store_true",
                     help="with --resume, re-send requests an earlier run sent without obtaining a reply (none, or an error); counts against the budget")
    score = sub.add_parser("score")
    score.add_argument("--truth", required=True)
    score.add_argument("--judgments", nargs="*", default=[])
    score.add_argument("--min-raters", type=int, default=2)
    compare = sub.add_parser("compare")
    compare.add_argument("baseline_results")
    compare.add_argument("improved_results")
    args = parser.parse_args(argv)
    for flag in ("max_generations", "max_output_tokens"):
        if getattr(args, flag, None) is not None and getattr(args, flag) < 1:
            parser.error(f"--{flag.replace('_', '-')} must be at least 1")
    return {"run": lambda: asyncio.run(_run(args)), "score": lambda: _score(args), "compare": lambda: _compare(args)}[args.command]()


if __name__ == "__main__":
    raise SystemExit(main())
