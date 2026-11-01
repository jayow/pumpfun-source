#!/usr/bin/env python3
"""Run, replay, and retain deterministic Pump Admin CTO fuzz campaigns."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import case_model as C  # noqa: E402
import corpus as P  # noqa: E402
import executor as E  # noqa: E402
import reducer as R  # noqa: E402

SCHEMA = "pump-admin-cto-fuzz-campaign-v1"
PRODUCERS = (
    HERE / "case_model.py", HERE / "executor.py", HERE / "reducer.py",
    HERE / "corpus.py", HERE / "campaign.py",
    ROOT / "reconstruction/verification/pump-admin-cto-stateful/lifecycle.py",
    E.L.IDL_PATH, E.L.CONFIG_PATH, ROOT / "scripts/pump_differential_output.py",
)


def _git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


def declared_inputs(original: Path, candidate: Path) -> dict[str, Path]:
    paths = {"pump_original": Path(original), "pump_candidate": Path(candidate)}
    paths.update({"dependency_" + name: path for name, path in E.L.CAPTURED.items()
                  if name != "pump_original"})
    paths.update({"producer_" + path.relative_to(ROOT).as_posix().replace("/", "__"): path
                  for path in PRODUCERS})
    return paths


def _images(snapshot: P.InputSnapshot) -> dict[str, Path]:
    return {name: snapshot.paths["dependency_" + name]
            for name in E.L.CAPTURED if name != "pump_original"}


def _case_summary(result: dict) -> dict:
    row = {"case_id": result["case_id"], "classification": result["classification"],
           "reason": result.get("reason"), "execution_sha256": E.execution_identity(result)}
    if "original" in result:
        row.update(
            transaction_count=len(result["original"]["receipts"]),
            original_outcomes=[item["outcome"] for item in result["original"]["receipts"]],
            candidate_outcomes=[item["outcome"] for item in result["candidate"]["receipts"]],
            mismatch_fields=result["mismatch_fields"],
            diagnostic_difference_fields=sorted({field for item in result["comparison"]
                                                 for field in item["diagnostic_difference_fields"]}),
        )
    return row


def negative_controls(result: dict) -> dict:
    if "original" not in result:
        return {"available": False}
    checks = {}
    for field, mutate in (
        ("error", lambda run: run["receipts"][0].update(error="negative-control")),
        ("events", lambda run: run["receipts"][0]["events"].append("negative-control")),
        ("after", lambda run: run["receipts"][0]["after"].update({"negative-control": None})),
    ):
        changed = copy.deepcopy(result["candidate"])
        mutate(changed)
        differences, _ = E.compare_runs(result["original"], changed)
        checks[field] = any(item.endswith(":" + field) for item in differences)
    return {"available": True, "detected": checks, "all_detected": all(checks.values())}


def run_campaign(*, original: Path, candidate: Path, output: Path, seed: int, count: int,
                 expected_original_sha256: str | None = None,
                 expected_candidate_sha256: str | None = None,
                 detailed_receipts: bool = True, reduce_mismatches: bool = True,
                 reduction_budget: int = 500, after_case=None) -> dict:
    if type(count) is not int or not 1 <= count <= 1_000:
        raise ValueError("campaign count must be between 1 and 1000")
    cases = C.generate_corpus(seed, count)
    expected = {}
    if expected_original_sha256:
        expected["pump_original"] = expected_original_sha256
    if expected_candidate_sha256:
        expected["pump_candidate"] = expected_candidate_sha256
    inputs = declared_inputs(original, candidate)
    attempt = {"campaign_schema": SCHEMA, "seed": seed, "count": count,
               "case_manifest_sha256": hashlib.sha256(C.canonical(cases)).hexdigest()}
    with P.CampaignPublisher(output, attempt) as publisher:
        publisher.write_json("cases.json", cases)
        with P.InputSnapshot(inputs, expected) as snapshot:
            images = _images(snapshot)
            results, summaries, reductions = [], [], []
            for index, case in enumerate(cases):
                result = E.execute_case(case, snapshot.paths["pump_original"],
                                        snapshot.paths["pump_candidate"], images)
                results.append(result)
                summaries.append(_case_summary(result))
                if detailed_receipts:
                    publisher.write_json(f"receipts/{index:04d}-{case['case_id']}.json.gz",
                                         result, compressed=True)
                else:
                    publisher.write_json(f"progress/{index:04d}-{case['case_id']}.json",
                                         summaries[-1])
                if result["classification"] == "different_behavior" and reduce_mismatches:
                    evaluation = lambda value: E.execute_case(
                        value, snapshot.paths["pump_original"], snapshot.paths["pump_candidate"], images)
                    reduction = R.reduce_case(case, evaluation, max_trials=reduction_budget)
                    reductions.append(reduction)
                    name = reduction["reduced_case_id"]
                    publisher.write_json(f"counterexamples/{name}/case.json", reduction["case"])
                    publisher.write_json(f"counterexamples/{name}/reduction.json.gz",
                                         reduction, compressed=True)
                    publisher.write_json(f"counterexamples/{name}/manifest.json", {
                        "schema": "pump-admin-cto-counterexample-manifest-v1",
                        "original_seed": seed, "original_case_id": case["case_id"],
                        "reduced_case_id": name, "program_sha256": {
                            "original": snapshot.hashes["pump_original"],
                            "candidate": snapshot.hashes["pump_candidate"],
                        },
                        "replay": "campaign.py replay --case case.json --original ORIGINAL.so --candidate CANDIDATE.so",
                        "minimality": reduction["minimality"],
                    })
                if after_case is not None:
                    after_case(index, case, result)
            changed = snapshot.changed_sources()
            if changed:
                raise RuntimeError(f"declared inputs changed during campaign: {json.dumps(changed, sort_keys=True)}")
            counts = Counter(row["classification"] for row in results)
            executed = [row for row in results if "original" in row]
            control = negative_controls(executed[0]) if executed else {"available": False}
            summary = {
                "schema": SCHEMA, "upstream_sha": _git_sha(), "seed": seed, "case_count": count,
                "case_manifest_sha256": attempt["case_manifest_sha256"],
                "program_sha256": {"original": snapshot.hashes["pump_original"],
                                   "candidate": snapshot.hashes["pump_candidate"]},
                "all_input_sha256": snapshot.hashes,
                "classification_counts": {name: counts.get(name, 0) for name in (
                    "equal_observed_behavior", "different_behavior", "unsupported_fixture",
                    "infrastructure_failure")},
                "transaction_count_per_image": sum(len(row["original"]["receipts"]) for row in executed),
                "original_successful_case_count": sum(all(receipt["error"] is None
                    for receipt in row["original"]["receipts"]) for row in executed),
                "negative_controls": control,
                "reduced_counterexample_count": len(reductions),
                "detailed_receipts": detailed_receipts,
                "cases": summaries,
            }
            summary["summary_sha256"] = hashlib.sha256(P.canonical_json(summary)).hexdigest()
            publisher.write_json("summary.json", summary)
            publisher.commit({"summary_sha256": summary["summary_sha256"],
                              "classification_counts": summary["classification_counts"]})
            return summary


def replay_case(case_path: Path, original: Path, candidate: Path,
                expected_original: str | None = None,
                expected_candidate: str | None = None) -> dict:
    case = C.validate_case(json.loads(Path(case_path).read_text()))
    expected = {key: value for key, value in (("pump_original", expected_original),
                                                ("pump_candidate", expected_candidate)) if value}
    with P.InputSnapshot(declared_inputs(original, candidate), expected) as snapshot:
        result = E.execute_case(case, snapshot.paths["pump_original"],
                                snapshot.paths["pump_candidate"], _images(snapshot))
        if snapshot.changed_sources():
            raise RuntimeError("declared inputs changed during replay")
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate", help="write a deterministic case manifest to stdout")
    generate.add_argument("--seed", type=int, required=True)
    generate.add_argument("--count", type=int, required=True)
    run = sub.add_parser("run", help="execute and atomically publish a bounded campaign")
    run.add_argument("--original", type=Path, required=True)
    run.add_argument("--candidate", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--seed", type=int, required=True)
    run.add_argument("--count", type=int, required=True)
    run.add_argument("--expected-original-sha256")
    run.add_argument("--expected-candidate-sha256")
    run.add_argument("--omit-detailed-receipts", action="store_true")
    run.add_argument("--no-reduce", action="store_true")
    run.add_argument("--reduction-budget", type=int, default=500)
    run.add_argument("--allow-mismatches", action="store_true")
    replay = sub.add_parser("replay", help="execute one retained case")
    replay.add_argument("--case", type=Path, required=True)
    replay.add_argument("--original", type=Path, required=True)
    replay.add_argument("--candidate", type=Path, required=True)
    replay.add_argument("--expected-original-sha256")
    replay.add_argument("--expected-candidate-sha256")
    args = parser.parse_args()
    if args.command == "generate":
        print(json.dumps(C.generate_corpus(args.seed, args.count), sort_keys=True, indent=2))
        return 0
    if args.command == "replay":
        result = replay_case(args.case, args.original, args.candidate,
                             args.expected_original_sha256, args.expected_candidate_sha256)
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0 if result["classification"] not in ("infrastructure_failure",) else 1
    summary = run_campaign(
        original=args.original, candidate=args.candidate, output=args.output,
        seed=args.seed, count=args.count,
        expected_original_sha256=args.expected_original_sha256,
        expected_candidate_sha256=args.expected_candidate_sha256,
        detailed_receipts=not args.omit_detailed_receipts,
        reduce_mismatches=not args.no_reduce, reduction_budget=args.reduction_budget,
    )
    print(json.dumps(summary, sort_keys=True, indent=2))
    failed = (summary["classification_counts"]["infrastructure_failure"]
              or not summary["negative_controls"].get("all_detected", False)
              or (summary["classification_counts"]["different_behavior"] and not args.allow_mismatches))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
