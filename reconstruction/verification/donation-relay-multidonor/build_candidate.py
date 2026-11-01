#!/usr/bin/env python3
"""Build and hash-bind a Donation Relay candidate with pinned local tools."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(root: Path) -> dict[str, str]:
    paths = [root / "Cargo.toml", root / "Cargo.lock"]
    paths.extend(sorted((root / "programs/donation-relay").rglob("*")))
    return {path.relative_to(root).as_posix(): sha(path)
            for path in paths if path.is_file()}


def digest(rows: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(
        rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def tool_manifest(sdk: Path) -> dict[str, dict[str, str]]:
    relative = (
        "rust/bin/cargo", "rust/bin/rustc", "rust/bin/rustdoc",
        "llvm/bin/clang", "llvm/bin/llvm-ar", "llvm/bin/llvm-objdump",
        "llvm/bin/llvm-objcopy",
    )
    rows: dict[str, dict[str, str]] = {}
    for name in relative:
        path = sdk / name
        if not path.is_file() or not os.access(path, os.X_OK):
            raise SystemExit(f"missing executable tool: {path}")
        rows[name] = {"path": str(path.resolve()), "sha256": sha(path)}
    return rows


def run(root: Path, output: Path, sdk: Path) -> dict[str, object]:
    if output.exists():
        raise SystemExit(f"refusing existing output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".{output.name}.building-{os.getpid()}"
    if staging.exists():
        raise SystemExit(f"refusing existing staging path: {staging}")
    staging.mkdir()

    source_before = manifest(root)
    tools_before = tool_manifest(sdk)
    target = staging / "target"
    command = [
        str(sdk / "rust/bin/cargo"), "rustc", "--release",
        "--target", "sbpf-solana-solana", "--locked", "--offline",
        "--manifest-path", str(root / "programs/donation-relay/Cargo.toml"),
        "--lib", "--", "--remap-path-prefix=src/=programs/contracts/src/",
    ]
    env = dict(os.environ)
    env.update({
        "CARGO_TARGET_DIR": str(target),
        "CARGO_NET_OFFLINE": "true",
        "CARGO_TERM_COLOR": "never",
        "RUSTC": str(sdk / "rust/bin/rustc"),
        "RUSTDOC": str(sdk / "rust/bin/rustdoc"),
        "CC": str(sdk / "llvm/bin/clang"),
        "AR": str(sdk / "llvm/bin/llvm-ar"),
        "OBJDUMP": str(sdk / "llvm/bin/llvm-objdump"),
        "OBJCOPY": str(sdk / "llvm/bin/llvm-objcopy"),
        "CARGO_TARGET_SBPF_SOLANA_SOLANA_RUSTFLAGS": "-Zremap-cwd-prefix=",
    })
    try:
        subprocess.run(command, cwd=root, env=env, check=True)
        unstripped = target / "sbpf-solana-solana/release/donation_relay_source_proposal.so"
        if not unstripped.is_file():
            raise RuntimeError(f"build did not create {unstripped}")
        candidate = staging / "donation_relay_candidate.so"
        subprocess.run([
            str(sdk / "llvm/bin/llvm-objcopy"), "--strip-all",
            str(unstripped), str(candidate),
        ], check=True)
        source_after = manifest(root)
        tools_after = tool_manifest(sdk)
        if source_after != source_before:
            raise RuntimeError("source inputs changed during build")
        if tools_after != tools_before:
            raise RuntimeError("toolchain inputs changed during build")
        versions = {
            name: subprocess.run([row["path"], "--version"], check=True,
                                 capture_output=True, text=True).stdout.splitlines()[0]
            for name, row in tools_before.items() if name.endswith(("cargo", "rustc", "clang"))
        }
        receipt: dict[str, object] = {
            "schema": "donation-relay-candidate-build-v1",
            "builder": {
                "path": Path(__file__).resolve().relative_to(root).as_posix(),
                "sha256": sha(Path(__file__).resolve()),
            },
            "command": command,
            "offline": True,
            "locked": True,
            "source_files": source_before,
            "source_manifest_sha256": digest(source_before),
            "toolchain": tools_before,
            "tool_versions": versions,
            "unstripped": {"sha256": sha(unstripped), "bytes": unstripped.stat().st_size},
            "candidate": {"sha256": sha(candidate), "bytes": candidate.stat().st_size},
        }
        (staging / "build-receipt.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        # The target tree is merely an intermediate; retained evidence is the
        # hash-bound unstripped image, stripped candidate, and receipt.
        shutil.copy2(unstripped, staging / "donation_relay_unstripped.so")
        shutil.rmtree(target)
        os.replace(staging, output)
        return receipt
    except BaseException:
        # Preserve interrupted or failed build state for crash recovery.
        print(f"build state retained at {staging}", file=sys.stderr)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sdk", type=Path,
                        default=Path.home() / ".cache/solana/v1.44/platform-tools")
    args = parser.parse_args()
    receipt = run(args.root.resolve(), args.output.resolve(), args.sdk.resolve())
    print(json.dumps(receipt["candidate"], sort_keys=True))


if __name__ == "__main__":
    main()
