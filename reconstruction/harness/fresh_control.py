#!/usr/bin/env python3
"""Build and score fresh Pump base/candidate sources with fail-closed receipts."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path

import build_score_pair
import compare_scores
import fastbuild
import score as official_score
from original_input import copy_original_inputs
from source_inputs import assert_unchanged, snapshot


H = Path(__file__).resolve().parent
SC = H.parent


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def publish_json(path: Path, document: dict) -> None:
    """Atomically publish deterministic JSON while preserving an older receipt."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    for _ in range(100):
        temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}"
        try:
            stream = temporary.open("x", encoding="utf-8", newline="\n")
        except FileExistsError:
            continue
        break
    else:
        raise FileExistsError("could not create fresh-control report staging file")
    metadata = os.fstat(stream.fileno())
    identity = metadata.st_dev, metadata.st_ino
    try:
        with stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                current = temporary.lstat()
            except FileNotFoundError:
                pass
            else:
                if (current.st_dev, current.st_ino) == identity:
                    temporary.unlink()


def public_build(result: dict) -> dict:
    """Remove host paths and timings while retaining all acceptance fields."""
    fields = (
        "ok",
        "timed_out",
        "runtime_valid",
        "stack_warnings",
        "source_sha256",
        "recipe",
        "triple",
        "unstripped_sha256",
        "candidate_sha256",
        "link_order",
    )
    return {name: result[name] for name in fields if name in result}


def artifact_inventory(root: Path) -> dict[str, dict]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
    return result


def score_summary(windows: dict[int, dict]) -> dict:
    ambiguous = sorted(start for start, row in windows.items() if len(row.get("symbols", [])) > 1)
    return {
        "total": len(windows),
        "normalized": sum(row["normalized"] for row in windows.values()),
        "raw": sum(row["raw"] for row in windows.values()),
        "matched_text_bytes": sum(row["bytes"] for row in windows.values() if row["normalized"]),
        "ambiguous": [hex(start) for start in ambiguous],
    }


def write_score(path: Path, image_sha256: str, windows: dict[int, dict]) -> None:
    publish_json(
        path,
        {
            "candidate_sha256": image_sha256,
            "summary": score_summary(windows),
            "windows": {str(start): row for start, row in sorted(windows.items())},
        },
    )


def _binding(scoring_inputs: Path, sdk: Path, hybrid: Path) -> dict:
    return build_score_pair.bound_inputs(scoring_inputs, sdk, hybrid)


def _reject(reason: str, detail: str | None = None) -> dict:
    result = {"accepted": False, "rejection_reasons": [reason]}
    if detail:
        result["detail"] = detail
    return result


def run_pair(
    base_source: Path,
    candidate_source: Path,
    output: Path,
    *,
    sdk: Path | None = None,
    hybrid: Path | None = None,
    timeout: float | None = None,
) -> dict:
    """Build/score admitted sources sequentially and return a final receipt.

    The caller owns source composition. This function owns a new output tree,
    private scoring inputs, separate cold build workspaces, and all receipts.
    Expected build/score rejection is returned as an unaccepted receipt;
    preflight output/source errors are raised before a misleading receipt exists.
    """
    base_source = Path(base_source).resolve(strict=True)
    candidate_source = Path(candidate_source).resolve(strict=True)
    output = Path(output).resolve()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"fresh-control output already exists: {output}")
    for source in (base_source, candidate_source):
        if fastbuild.contains_path(source, output) or fastbuild.contains_path(output, source):
            raise ValueError("fresh-control output must not overlap a source tree")
    sdk = Path(sdk or fastbuild.SDK).resolve()
    hybrid = Path(
        hybrid
        or Path(os.environ.get("PUMP_RECON_HOME", str(Path.home() / ".cache/pumpfun-reconstruction")))
        / "toolchains/hybrid144"
    ).resolve()
    fastbuild.validate_timeout(timeout)

    sources = {"base": snapshot(base_source), "candidate": snapshot(candidate_source)}
    output.mkdir(parents=True)
    scoring_inputs = output / "scoring-inputs"
    scoring_inputs.mkdir()
    copy_original_inputs(SC / "base", scoring_inputs)

    receipt = {
        "schema": "pump-fresh-control-v1",
        "mode": "fresh_control",
        "sources": sources,
        "build_order": ["base", "candidate"],
        "isolated_workspaces": True,
        "cold_targets": True,
        "builds": {},
        "scores": {},
        "qualification": _reject("incomplete"),
    }
    receipt_path = output / "receipt.json"
    saved_fastbuild_sdk = fastbuild.SDK
    saved_fastbuild_lockdir = fastbuild.LOCKDIR
    saved_recipe = fastbuild.RECIPES["identity"]
    saved_score_base = official_score.BASE
    missing = object()
    saved_objdump = getattr(official_score, "OBJDUMP", missing)

    try:
        fastbuild.SDK = sdk
        fastbuild.LOCKDIR = output / "locks"
        fastbuild.RECIPES["identity"] = {
            "triple": "sbpf-solana-solana",
            "rustc": hybrid / "rust/bin/rustc",
            "template": output / "no-template",
        }
        official_score.BASE = scoring_inputs
        official_score.OBJDUMP = str(sdk / "llvm/bin/llvm-objdump")
        bound = _binding(scoring_inputs, sdk, hybrid)
        receipt["provenance"] = bound
        publish_json(receipt_path, receipt)

        score_windows = {}
        for side, source in (("base", base_source), ("candidate", candidate_source)):
            result = fastbuild.build(
                source,
                output / f"{side}-work",
                output / f"{side}-build",
                quiet=True,
                recipe="identity",
                timeout=timeout,
            )
            receipt["builds"][side] = public_build(result)
            publish_json(receipt_path, receipt)
            try:
                build_score_pair.require_valid_build(result)
            except ValueError as error:
                receipt["qualification"] = _reject(f"{side}_build_invalid", str(error))
                receipt["artifacts"] = artifact_inventory(output)
                publish_json(receipt_path, receipt)
                return receipt
            assert_unchanged(source, sources[side], phase=f"{side} build")
            if _binding(scoring_inputs, sdk, hybrid) != bound:
                receipt["qualification"] = _reject("toolchain_or_scorer_input_mutation")
                receipt["artifacts"] = artifact_inventory(output)
                publish_json(receipt_path, receipt)
                return receipt

            image = output / f"{side}-build/unstripped.so"
            windows, scored_sha256 = official_score.score_with_hash(image, result["unstripped_sha256"])
            score_path = output / f"{side}-score.json"
            write_score(score_path, scored_sha256, windows)
            # Parse the just-produced score through the independent saved-report
            # admission path before it can participate in acceptance.
            admitted = compare_scores.load_report(score_path)
            score_windows[side] = admitted
            receipt["scores"][side] = {
                "image_sha256": scored_sha256,
                **score_summary(windows),
                "report_sha256": sha256_file(score_path),
            }
            publish_json(receipt_path, receipt)

        for side, source in (("base", base_source), ("candidate", candidate_source)):
            assert_unchanged(source, sources[side], phase="paired qualification")
        if _binding(scoring_inputs, sdk, hybrid) != bound:
            receipt["qualification"] = _reject("toolchain_or_scorer_input_mutation")
        else:
            comparison = compare_scores.compare(score_windows["base"], score_windows["candidate"])
            receipt["comparison"] = comparison
            reasons = []
            if comparison["missing"]:
                reasons.append("missing_windows")
            if comparison["added"]:
                reasons.append("added_windows")
            if comparison["resized"]:
                reasons.append("resized_windows")
            if comparison["normalized_losses"]:
                reasons.append("normalized_match_regression")
            if comparison["raw_losses"]:
                reasons.append("raw_match_regression")
            receipt["qualification"] = {
                "accepted": not reasons,
                "rejection_reasons": reasons,
                "base_source_sha256": sources["base"]["sha256"],
                "candidate_source_sha256": sources["candidate"]["sha256"],
                "same_original_evidence": True,
                "source_inputs_unchanged": True,
                "toolchain_and_scorer_inputs_unchanged": True,
            }
        receipt["artifacts"] = artifact_inventory(output)
        # Do not include receipt.json in its own hash inventory.
        receipt["artifacts"].pop("receipt.json", None)
        publish_json(receipt_path, receipt)
        return receipt
    except Exception as error:
        receipt["qualification"] = _reject("infrastructure_failure", str(error))
        receipt["error_type"] = type(error).__name__
        receipt["artifacts"] = artifact_inventory(output)
        receipt["artifacts"].pop("receipt.json", None)
        publish_json(receipt_path, receipt)
        return receipt
    finally:
        fastbuild.SDK = saved_fastbuild_sdk
        fastbuild.LOCKDIR = saved_fastbuild_lockdir
        fastbuild.RECIPES["identity"] = saved_recipe
        official_score.BASE = saved_score_base
        if saved_objdump is missing:
            official_score.__dict__.pop("OBJDUMP", None)
        else:
            official_score.OBJDUMP = saved_objdump
