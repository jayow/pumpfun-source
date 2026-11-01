#!/usr/bin/env python3
"""Independently recompute a fresh-control receipt's acceptance decision."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import compare_scores
import fastbuild
import fresh_control
from source_inputs import snapshot


def load_document(path: Path) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"),
                          object_pairs_hook=compare_scores.unique_fields)
    if not isinstance(document, dict):
        raise ValueError(f"{path.name} must contain an object")
    return document


def expected_reasons(comparison: dict) -> list[str]:
    checks = (
        ("missing", "missing_windows"),
        ("added", "added_windows"),
        ("resized", "resized_windows"),
        ("normalized_losses", "normalized_match_regression"),
        ("raw_losses", "raw_match_regression"),
    )
    return [reason for field, reason in checks if comparison[field]]


def source_matches(path: Path, admitted: dict) -> bool:
    try:
        return snapshot(path) == admitted
    except (OSError, ValueError):
        return False


def find_sources(root: Path, receipt: dict, base_source: Path | None,
                 candidate_source: Path | None) -> dict[str, Path]:
    result = {}
    supplied = {"base": base_source, "candidate": candidate_source}
    for side, path in supplied.items():
        if path is not None:
            path = Path(path).resolve(strict=True)
            if not source_matches(path, receipt["sources"][side]):
                raise ValueError(f"{side} source does not match admitted source receipt")
            result[side] = path

    integration = root.parent
    if "candidate" not in result:
        candidate = integration / "src"
        if source_matches(candidate, receipt["sources"]["candidate"]):
            result["candidate"] = candidate
    if "base" not in result:
        inputs = integration / "inputs"
        matches = [path for path in sorted(inputs.iterdir())
                   if path.is_dir() and source_matches(path, receipt["sources"]["base"])] \
            if inputs.is_dir() else []
        if len(matches) == 1:
            result["base"] = matches[0]
        elif len(matches) > 1:
            # Identical owned snapshots are equivalent, but select deterministically.
            result["base"] = matches[0]
    missing = {"base", "candidate"} - result.keys()
    if missing:
        raise ValueError("cannot locate admitted source snapshot: " + ", ".join(sorted(missing)))
    return result


def verify(root: Path, *, sdk: Path | None = None, hybrid: Path | None = None,
           base_source: Path | None = None, candidate_source: Path | None = None) -> dict:
    root = Path(root).resolve(strict=True)
    receipt_path = root / "receipt.json"
    receipt = load_document(receipt_path)
    if receipt.get("schema") != "pump-fresh-control-v1" or receipt.get("mode") != "fresh_control":
        raise ValueError("unsupported fresh-control receipt schema")

    retained = fresh_control.artifact_inventory(root)
    retained.pop("receipt.json", None)
    if receipt.get("artifacts") != retained:
        raise ValueError("retained artifact inventory does not match receipt")

    sdk = Path(sdk or fastbuild.SDK).resolve()
    hybrid = Path(
        hybrid
        or Path(os.environ.get("PUMP_RECON_HOME", str(Path.home() / ".cache/pumpfun-reconstruction")))
        / "toolchains/hybrid144"
    ).resolve()
    actual_binding = fresh_control._binding(root / "scoring-inputs", sdk, hybrid)
    if receipt.get("provenance") != actual_binding:
        raise ValueError("toolchain, scorer, or captured evidence provenance mismatch")

    sources = find_sources(root, receipt, base_source, candidate_source)
    scores = {}
    for side in ("base", "candidate"):
        build = receipt.get("builds", {}).get(side)
        if not isinstance(build, dict) or not build.get("ok") or not build.get("runtime_valid") \
                or build.get("stack_warnings"):
            raise ValueError(f"{side} build is not admissible")
        if build.get("source_sha256") != receipt["sources"][side]["sha256"]:
            raise ValueError(f"{side} build source digest mismatch")
        for artifact, field in (("unstripped.so", "unstripped_sha256"),
                                ("candidate.so", "candidate_sha256")):
            image = root / f"{side}-build" / artifact
            if not image.is_file() or fresh_control.sha256_file(image) != build.get(field):
                raise ValueError(f"{side} {artifact} digest mismatch")

        score_path = root / f"{side}-score.json"
        score_document = load_document(score_path)
        if score_document.get("candidate_sha256") != build["unstripped_sha256"]:
            raise ValueError(f"{side} score is bound to the wrong image")
        raw_windows = {int(start): row for start, row in score_document.get("windows", {}).items()}
        summary = fresh_control.score_summary(raw_windows)
        if score_document.get("summary") != summary:
            raise ValueError(f"{side} score summary does not match its windows")
        report_sha256 = fresh_control.sha256_file(score_path)
        expected_score = {
            "image_sha256": build["unstripped_sha256"],
            **summary,
            "report_sha256": report_sha256,
        }
        if receipt.get("scores", {}).get(side) != expected_score:
            raise ValueError(f"{side} receipt score summary or digest mismatch")
        scores[side] = compare_scores.load_report(score_path)

    comparison = compare_scores.compare(scores["base"], scores["candidate"])
    if receipt.get("comparison") != comparison:
        raise ValueError("recorded comparison differs from independent recomputation")
    reasons = expected_reasons(comparison)
    qualification = receipt.get("qualification")
    if not isinstance(qualification, dict):
        raise ValueError("missing qualification")
    if qualification.get("accepted") != (not reasons):
        raise ValueError("producer acceptance boolean differs from recomputation")
    if qualification.get("rejection_reasons") != reasons:
        raise ValueError("producer rejection reasons differ from recomputation")
    if qualification.get("base_source_sha256") != receipt["sources"]["base"]["sha256"] \
            or qualification.get("candidate_source_sha256") \
            != receipt["sources"]["candidate"]["sha256"]:
        raise ValueError("qualification source binding mismatch")
    if not all(qualification.get(field) is True for field in
               ("same_original_evidence", "source_inputs_unchanged",
                "toolchain_and_scorer_inputs_unchanged")):
        raise ValueError("qualification does not attest immutable inputs")

    return {
        "accepted": not reasons,
        "artifact_count": len(retained),
        "base_source": str(sources["base"]),
        "candidate_source": str(sources["candidate"]),
        "comparison": comparison,
        "receipt_sha256": fresh_control.sha256_file(receipt_path),
        "schema": "pump-fresh-control-verification-v1",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="directory containing receipt.json")
    parser.add_argument("--sdk", type=Path, default=fastbuild.SDK)
    parser.add_argument("--hybrid", type=Path,
                        default=Path(os.environ.get("PUMP_RECON_HOME", str(Path.home() / ".cache/pumpfun-reconstruction"))) / "toolchains/hybrid144")
    parser.add_argument("--base-source", type=Path)
    parser.add_argument("--candidate-source", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify(args.root, sdk=args.sdk, hybrid=args.hybrid,
                        base_source=args.base_source, candidate_source=args.candidate_source)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"fresh-control receipt invalid: {error}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
