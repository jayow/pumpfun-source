#!/usr/bin/env python3
"""Adversarial mutations for the independent multi-donor receipt verifier."""
from __future__ import annotations

import argparse
import base64
import copy
import json
import sys
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts"))

import verify
from pump_differential_output import PumpReport


def replace_account_data(row: dict[str, Any], raw: bytes) -> None:
    row["data_base64"] = base64.b64encode(raw).decode()
    row["data_len"] = len(raw)
    row["data_sha256"] = verify.sha(raw)


def first_step(report: dict[str, Any]) -> dict[str, Any]:
    return report["cases"][0]["steps"][0]


def corrupt_signature(report: dict[str, Any]) -> None:
    step = first_step(report)
    raw = bytearray(base64.b64decode(step["transaction_base64"]))
    raw[1] ^= 1
    step["transaction_base64"] = base64.b64encode(raw).decode()
    step["transaction_sha256"] = verify.sha(raw)


def corrupt_token_balance(report: dict[str, Any]) -> None:
    case = report["cases"][0]
    step = case["steps"][0]
    source = case["fixture"]["sources"]["A"]["mint_x"]
    for side in ("original", "candidate"):
        row = step[side]["after"][source]
        raw = bytearray(base64.b64decode(row["data_base64"]))
        raw[64:72] = (int.from_bytes(raw[64:72], "little") + 1).to_bytes(8, "little")
        replace_account_data(row, bytes(raw))


def corrupt_debouncer(report: dict[str, Any]) -> None:
    case = report["cases"][0]
    step = case["steps"][-1]
    address = case["fixture"]["domains"]["config_a_mint_x"]["debouncer"]
    for side in ("original", "candidate"):
        row = step[side]["after"][address]
        raw = bytearray(base64.b64decode(row["data_base64"]))
        raw[74:82] = (int.from_bytes(raw[74:82], "little") + 1).to_bytes(8, "little")
        replace_account_data(row, bytes(raw))


def corrupt_event(report: dict[str, Any]) -> None:
    step = first_step(report)
    for side in ("original", "candidate"):
        step[side]["events"].append("Program data: Y29ycnVwdGVk")


def corrupt_outcome(report: dict[str, Any]) -> None:
    step = first_step(report)
    for side in ("original", "candidate"):
        step[side]["error"] = "forged-success-outcome"


def corrupt_owner(report: dict[str, Any]) -> None:
    case = report["cases"][0]
    source = case["fixture"]["sources"]["A"]["mint_x"]
    for side in ("original", "candidate"):
        case["steps"][0][side]["before"][source]["owner"] = str(verify.RELAY)


def corrupt_case_count(report: dict[str, Any]) -> None:
    report["case_count"] += 1


MUTATIONS: dict[str, Callable[[dict[str, Any]], None]] = {
    "donor_signature": corrupt_signature,
    "token_balance": corrupt_token_balance,
    "debouncer_aggregate": corrupt_debouncer,
    "event": corrupt_event,
    "transaction_outcome": corrupt_outcome,
    "account_owner": corrupt_owner,
    "case_count": corrupt_case_count,
}


def run(report: dict[str, Any], root: Path, candidate: Path) -> dict[str, Any]:
    baseline = verify.validate(report, root, candidate)
    if baseline:
        raise ValueError("baseline report is invalid: " + ", ".join(baseline))
    results = {}
    for name, mutate in MUTATIONS.items():
        changed = copy.deepcopy(report)
        mutate(changed)
        problems = verify.validate(changed, root, candidate)
        results[name] = {"detected": bool(problems), "problems": problems[:8]}
    return {
        "schema": "donation-relay-multidonor-negative-controls-v1",
        "control_count": len(results),
        "detected_count": sum(row["detected"] for row in results.values()),
        "all_detected": all(row["detected"] for row in results.values()),
        "controls": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, candidate, report_path = args.root.resolve(), args.candidate.resolve(), args.report.resolve()
    raw = report_path.read_bytes()
    report = json.loads(raw, object_pairs_hook=verify.unique_fields)
    result = run(report, root, candidate)
    result["report_sha256"] = verify.sha(raw)
    publisher = PumpReport(args.output.resolve(), root, candidate, __file__, overwrite=False,
                           extra_inputs=[report_path, HERE / "verify.py"])
    publisher.write(verify.canonical(result))
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["all_detected"] else 1)


if __name__ == "__main__":
    main()
