#!/usr/bin/env python3
"""Independently verify a retained Pump Admin CTO fuzz campaign."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import case_model as C  # noqa: E402
import corpus as P  # noqa: E402
import executor as E  # noqa: E402

SCHEMA = "pump-admin-cto-fuzz-verification-v1"
PRODUCERS = (
    HERE / "case_model.py", HERE / "executor.py", HERE / "reducer.py",
    HERE / "corpus.py", HERE / "campaign.py",
    ROOT / "reconstruction/verification/pump-admin-cto-stateful/lifecycle.py",
    E.L.IDL_PATH, E.L.CONFIG_PATH, ROOT / "scripts/pump_differential_output.py",
)


def _input_paths(original: Path, candidate: Path) -> dict[str, Path]:
    paths = {"pump_original": Path(original), "pump_candidate": Path(candidate)}
    paths.update({"dependency_" + name: path for name, path in E.L.CAPTURED.items()
                  if name != "pump_original"})
    paths.update({"producer_" + path.relative_to(ROOT).as_posix().replace("/", "__"): path
                  for path in PRODUCERS})
    return paths


def _reclassify(result: dict) -> tuple[str, list[str]]:
    failures = []
    if result.get("classification") in ("unsupported_fixture", "infrastructure_failure") and "original" not in result:
        return result["classification"], failures
    try:
        mismatches, comparison = E.compare_runs(result["original"], result["candidate"])
        invariants = {"original": E.validate_run(result["original"]),
                      "candidate": E.validate_run(result["candidate"])}
        infrastructure = [f"{side}:{row['step']}"
                          for side, run in (("original", result["original"]),
                                           ("candidate", result["candidate"]))
                          for row in run["receipts"] if row["failure_class"] == "infrastructure"]
        if mismatches != result.get("mismatch_fields") or comparison != result.get("comparison"):
            failures.append("comparison-fields")
        if invariants != result.get("invariant_failures"):
            failures.append("invariant-fields")
        if infrastructure != result.get("infrastructure_failures"):
            failures.append("infrastructure-fields")
        classification = ("infrastructure_failure" if any(invariants.values()) or infrastructure
                          else "different_behavior" if mismatches else "equal_observed_behavior")
        return classification, failures
    except (KeyError, TypeError, ValueError, IndexError):
        return "infrastructure_failure", ["malformed-execution"]


def _summary_row(result: dict) -> dict:
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


def _negative_controls(result: dict) -> dict:
    import copy
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


def verify_campaign(report_dir: Path, original: Path, candidate: Path,
                    *, reexecute: bool = False) -> dict:
    report_dir = Path(report_dir).resolve()
    failures, warnings = [], []
    try:
        summary = json.loads((report_dir / "summary.json").read_text())
        complete = json.loads((report_dir / "complete.json").read_text())
        cases = json.loads((report_dir / "cases.json").read_text())
    except (OSError, json.JSONDecodeError) as error:
        return {"schema": SCHEMA, "valid": False,
                "failures": ["report-load:" + type(error).__name__], "warnings": []}
    if summary.get("schema") != "pump-admin-cto-fuzz-campaign-v1":
        failures.append("summary-schema")
    embedded = summary.get("summary_sha256")
    unsigned = dict(summary)
    unsigned.pop("summary_sha256", None)
    if embedded != hashlib.sha256(P.canonical_json(unsigned)).hexdigest():
        failures.append("summary-hash")
    if complete.get("status") != "complete" or complete.get("summary_sha256") != embedded:
        failures.append("completion-receipt")
    try:
        for case in cases:
            C.validate_case(case)
    except (TypeError, ValueError) as error:
        failures.append("case-manifest:" + type(error).__name__)
    if hashlib.sha256(C.canonical(cases)).hexdigest() != summary.get("case_manifest_sha256"):
        failures.append("case-manifest-hash")
    if len(cases) != summary.get("case_count") or len(cases) != len(summary.get("cases", [])):
        failures.append("case-count")

    paths = _input_paths(original, candidate)
    declared_hashes = summary.get("all_input_sha256", {})
    if set(paths) != set(declared_hashes):
        failures.append("input-set")
    for name in sorted(set(paths) & set(declared_hashes)):
        try:
            actual = P.file_sha(paths[name])
        except OSError:
            failures.append("input-unreadable:" + name)
            continue
        if actual != declared_hashes[name]:
            failures.append("input-hash:" + name)
    if summary.get("program_sha256") != {
        "original": declared_hashes.get("pump_original"),
        "candidate": declared_hashes.get("pump_candidate"),
    }:
        failures.append("program-hash-summary")

    retained = {}
    if summary.get("detailed_receipts"):
        for index, case in enumerate(cases):
            path = report_dir / "receipts" / f"{index:04d}-{case['case_id']}.json.gz"
            try:
                result = json.loads(gzip.decompress(path.read_bytes()))
            except (OSError, EOFError, gzip.BadGzipFile, json.JSONDecodeError) as error:
                failures.append(f"receipt-load:{index}:{type(error).__name__}")
                continue
            retained[case["case_id"]] = result
            row = summary["cases"][index]
            if result.get("case_id") != case["case_id"] or result.get("case") != case:
                failures.append(f"receipt-case:{index}")
            if E.execution_identity(result) != row.get("execution_sha256"):
                failures.append(f"receipt-hash:{index}")
            if _summary_row(result) != row:
                failures.append(f"receipt-summary:{index}")
            computed, local = _reclassify(result)
            failures.extend(f"receipt-{index}:{item}" for item in local)
            if computed != result.get("classification") or computed != row.get("classification"):
                failures.append(f"receipt-classification:{index}")
    else:
        warnings.append("detailed receipts omitted; receipt integrity requires --reexecute")

    first_executed = next((retained[case["case_id"]] for case in cases
                           if case["case_id"] in retained and "original" in retained[case["case_id"]]), None)
    if first_executed is not None and _negative_controls(first_executed) != summary.get("negative_controls"):
        failures.append("negative-controls")

    reexecuted = 0
    if reexecute and not any(item.startswith("input-") for item in failures):
        expected = {name: declared_hashes[name] for name in paths if name in declared_hashes}
        with P.InputSnapshot(paths, expected) as snapshot:
            images = {name: snapshot.paths["dependency_" + name]
                      for name in E.L.CAPTURED if name != "pump_original"}
            for index, case in enumerate(cases):
                actual = E.execute_case(case, snapshot.paths["pump_original"],
                                        snapshot.paths["pump_candidate"], images)
                reexecuted += 1
                expected_sha = summary["cases"][index].get("execution_sha256")
                if E.execution_identity(actual) != expected_sha:
                    failures.append(f"reexecution:{index}")
                if case["case_id"] in retained and actual != retained[case["case_id"]]:
                    failures.append(f"reexecution-receipt:{index}")
            if snapshot.changed_sources():
                failures.append("inputs-changed-during-verification")
    counts = {name: 0 for name in ("equal_observed_behavior", "different_behavior",
                                   "unsupported_fixture", "infrastructure_failure")}
    for row in summary.get("cases", []):
        if row.get("classification") in counts:
            counts[row["classification"]] += 1
        else:
            failures.append("unknown-summary-classification")
    if counts != summary.get("classification_counts"):
        failures.append("classification-counts")
    return {
        "schema": SCHEMA, "valid": not failures, "failures": sorted(set(failures)),
        "warnings": warnings, "case_count": len(cases), "verified_receipt_count": len(retained),
        "reexecuted_case_count": reexecuted,
        "verification_scope": "receipts-and-reexecution" if reexecute else
                              "receipts-and-provenance" if retained else "provenance-only",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reexecute", action="store_true")
    args = parser.parse_args()
    result = verify_campaign(args.report, args.original, args.candidate, reexecute=args.reexecute)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
