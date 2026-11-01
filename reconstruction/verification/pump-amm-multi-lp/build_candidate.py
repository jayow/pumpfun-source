#!/usr/bin/env python3
"""Build current PumpSwap source with the pinned v1.44 toolchain and two jobs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "programs/pump-amm/Cargo.toml"
ARTIFACT = "pump_amm_recovered.so"
EXACT_MEANINGFUL_SHA256 = "51a5f6a7fb5a812de7686e593ce6159f6ee59b15771a3d5d37ab661c46d64586"
MIN_FREE = 20 * 1024**3


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def source_manifest() -> tuple[dict, str]:
    paths = [ROOT / "Cargo.lock", ROOT / "Cargo.toml"]
    for source_root in (ROOT / "programs/pump-amm", ROOT / "crates/native-transfer-fee"):
        paths.extend(path for path in source_root.rglob("*")
                     if path.is_file() and "target" not in path.relative_to(source_root).parts)
    records = {str(path.relative_to(ROOT)): sha(path) for path in sorted(set(paths))}
    return records, canonical_hash(records)


def plan(output: Path) -> dict:
    cargo_build_sbf = Path(shutil.which("cargo-build-sbf") or "")
    rustc = Path.home() / ".cache/solana/v1.44/platform-tools/rust/bin/rustc"
    command = ["cargo", "build-sbf", "--manifest-path", str(MANIFEST),
               "--tools-version", "v1.44", "--offline", "--sbf-out-dir", str(output / "artifacts"),
               "--", "--locked", "--offline", "-j", "2"]
    sources, source_digest = source_manifest()
    return {
        "schema": "pump-amm-multi-lp-build-v1",
        "scope": "Current source build for bounded runtime differential; no byte-identity claim unless exact_checkpoint_match is true.",
        "repository_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "manifest": str(MANIFEST.relative_to(ROOT)),
        "toolchain": "v1.44",
        "compiler_jobs": 2,
        "command": [value.replace(str(ROOT), "<repo>").replace(str(output), "<output>") for value in command],
        "source_sha256": sources,
        "source_manifest_sha256": source_digest,
        "cargo_build_sbf_sha256": sha(cargo_build_sbf) if cargo_build_sbf.is_file() else None,
        "rustc_sha256": sha(rustc) if rustc.is_file() else None,
        "exact_checkpoint_sha256": EXACT_MEANINGFUL_SHA256,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="new output directory; existing paths are refused")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("output already exists: " + str(output))
    if shutil.disk_usage(output.parent).free < MIN_FREE:
        raise SystemExit("refusing build with less than 20 GiB free")
    output.mkdir(parents=True)
    receipt = plan(output)
    (output / "build-attempt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    command = ["cargo", "build-sbf", "--manifest-path", str(MANIFEST),
               "--tools-version", "v1.44", "--offline", "--sbf-out-dir", str(output / "artifacts"),
               "--", "--locked", "--offline", "-j", "2"]
    environment = os.environ.copy()
    environment["CARGO_BUILD_JOBS"] = "2"
    environment["CARGO_TARGET_DIR"] = str(output / "target")
    result = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True)
    log = result.stdout + result.stderr
    (output / "build.log").write_text(log, encoding="utf-8", newline="\n")
    sources_after, digest_after = source_manifest()
    receipt.update({
        "returncode": result.returncode,
        "build_log_sha256": sha(output / "build.log"),
        "source_after_sha256": sources_after,
        "source_after_manifest_sha256": digest_after,
        "source_unchanged": sources_after == receipt["source_sha256"],
    })
    artifact = output / "artifacts" / ARTIFACT
    if result.returncode == 0 and artifact.is_file():
        artifact_sha = sha(artifact)
        receipt.update({"artifact": "artifacts/" + ARTIFACT, "artifact_bytes": artifact.stat().st_size,
                        "artifact_sha256": artifact_sha,
                        "exact_checkpoint_match": artifact_sha == EXACT_MEANINGFUL_SHA256})
    (output / "build-receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    if result.returncode or not artifact.is_file() or not receipt["source_unchanged"]:
        print(log, file=sys.stderr)
        return result.returncode or 1
    print(json.dumps({key: receipt[key] for key in (
        "artifact_sha256", "artifact_bytes", "source_manifest_sha256",
        "source_unchanged", "exact_checkpoint_match")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
