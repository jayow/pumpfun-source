#!/usr/bin/env python3
"""Independently verify a PumpSwap multi-provider LP receipt."""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import struct
from pathlib import Path

from solders.pubkey import Pubkey
from solders.transaction import Transaction

import lifecycle


def read(path: Path) -> dict:
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def raw_account(snapshot: dict, address: str) -> bytes | None:
    record = snapshot.get(address)
    return None if record is None else base64.b64decode(record["data_base64"], validate=True)


def token_amount(snapshot: dict, address: str) -> int | None:
    raw = raw_account(snapshot, address)
    return None if raw is None or len(raw) < 72 else struct.unpack_from("<Q", raw, 64)[0]


def mint_supply(snapshot: dict, address: str) -> int | None:
    raw = raw_account(snapshot, address)
    return None if raw is None or len(raw) < 44 else struct.unpack_from("<Q", raw, 36)[0]


def pool_supply(snapshot: dict, address: str) -> int | None:
    raw = raw_account(snapshot, address)
    return None if raw is None or len(raw) < 211 else struct.unpack_from("<Q", raw, 203)[0]


def economic_state(snapshot: dict, fixture: dict) -> dict:
    a, b = fixture["provider_a_accounts"], fixture["provider_b_accounts"]
    state = {
        "provider_a_base": token_amount(snapshot, a["base"]),
        "provider_a_quote": token_amount(snapshot, a["quote"]),
        "provider_a_lp": token_amount(snapshot, a["lp"]),
        "provider_b_base": token_amount(snapshot, b["base"]),
        "provider_b_quote": token_amount(snapshot, b["quote"]),
        "provider_b_lp": token_amount(snapshot, b["lp"]),
        "pool_base": token_amount(snapshot, fixture["vault_base"]),
        "pool_quote": token_amount(snapshot, fixture["vault_quote"]),
        "buyback_quote": token_amount(snapshot, str(lifecycle.BUYBACK)),
        "lp_mint_supply": mint_supply(snapshot, fixture["lp_mint"]),
        "pool_reported_lp_supply": pool_supply(snapshot, fixture["pool"]),
    }
    lp = (state["provider_a_lp"], state["provider_b_lp"])
    state["provider_lp_total"] = None if None in lp else sum(lp)
    state["locked_lp_difference"] = (
        None if state["pool_reported_lp_supply"] is None or state["lp_mint_supply"] is None
        else state["pool_reported_lp_supply"] - state["lp_mint_supply"])
    base = (state["provider_a_base"], state["provider_b_base"], state["pool_base"])
    quote = (state["provider_a_quote"], state["provider_b_quote"], state["pool_quote"],
             state["buyback_quote"])
    state["base_total"] = None if None in base else sum(base)
    state["quote_total_with_fee_recipient"] = None if None in quote else sum(quote)
    return state


def accounting(before: dict, after: dict, fixture: dict) -> dict:
    left, right = economic_state(before, fixture), economic_state(after, fixture)
    delta = {name: None if left[name] is None or right[name] is None else right[name] - left[name]
             for name in left if isinstance(left[name], int) or isinstance(right[name], int)}
    payer = fixture["fee_payer"]
    return {"before": left, "after": right, "delta": delta,
            "fee_payer_lamport_delta": after[payer]["lamports"] - before[payer]["lamports"]}


def validate_snapshot(snapshot: dict, prefix: str, problems: list[str]) -> None:
    for address, record in snapshot.items():
        if record is None:
            continue
        try:
            raw = base64.b64decode(record["data_base64"], validate=True)
        except Exception:
            problems.append(prefix + ":invalid_account_data:" + address)
            continue
        if record.get("data_sha256") != sha(raw):
            problems.append(prefix + ":account_data_hash:" + address)
        owner = record.get("owner")
        expected_token = (struct.unpack_from("<Q", raw, 64)[0]
                          if owner in (str(lifecycle.TOKEN), str(lifecycle.TOKEN22)) and len(raw) >= 165 else None)
        if record.get("token_amount") != expected_token:
            problems.append(prefix + ":cached_token_amount:" + address)
        expected_mint = (struct.unpack_from("<Q", raw, 36)[0]
                         if owner in (str(lifecycle.TOKEN), str(lifecycle.TOKEN22)) and 82 <= len(raw) < 165 else None)
        if record.get("mint_supply") != expected_mint:
            problems.append(prefix + ":cached_mint_supply:" + address)
        expected_pool = (struct.unpack_from("<Q", raw, 203)[0]
                         if owner == str(lifecycle.AMM) and len(raw) >= 211 else None)
        if record.get("pool_lp_supply") != expected_pool:
            problems.append(prefix + ":cached_pool_supply:" + address)


def expected_fixture(plan: dict, index: int) -> dict:
    pool = lifecycle.PoolFixture.derive(
        f"case-{index}", index=index,
        base_program=lifecycle.TOKEN22 if plan.get("token22_base") else lifecycle.TOKEN)
    fixture = {
        "pool": str(pool.pool), "lp_mint": str(pool.lp), "base_mint": str(pool.base),
        "quote_mint": str(pool.quote), "provider_a": str(lifecycle.PROVIDER_A.pubkey()),
        "provider_b": str(lifecycle.PROVIDER_B.pubkey()), "fee_payer": str(lifecycle.FEE_PAYER.pubkey()),
        "base_token_program": str(pool.base_program), "quote_token_program": str(pool.quote_program),
        "vault_base": str(pool.vault_base), "vault_quote": str(pool.vault_quote),
        "provider_a_accounts": {k: str(v) for k, v in pool.provider_accounts(lifecycle.PROVIDER_A).items()},
        "provider_b_accounts": {k: str(v) for k, v in pool.provider_accounts(lifecycle.PROVIDER_B).items()},
        "minimum_locked_lp": 100,
    }
    if plan.get("cross_pool"):
        second = lifecycle.PoolFixture.derive(f"case-{index}-secondary", index=index + 100)
        fixture["secondary"] = {
            "pool": str(second.pool), "lp_mint": str(second.lp), "base_mint": str(second.base),
            "quote_mint": str(second.quote), "provider_a": str(lifecycle.PROVIDER_A.pubkey()),
            "provider_b": str(lifecycle.PROVIDER_B.pubkey()), "fee_payer": str(lifecycle.FEE_PAYER.pubkey()),
            "base_token_program": str(second.base_program), "quote_token_program": str(second.quote_program),
            "vault_base": str(second.vault_base), "vault_quote": str(second.vault_quote),
            "provider_a_accounts": {k: str(v) for k, v in second.provider_accounts(lifecycle.PROVIDER_A).items()},
            "provider_b_accounts": {k: str(v) for k, v in second.provider_accounts(lifecycle.PROVIDER_B).items()},
            "minimum_locked_lp": 100,
        }
    return fixture


def expected_step(step: dict, number: int) -> tuple[str, str, int]:
    provider = {"A": lifecycle.PROVIDER_A, "B": lifecycle.PROVIDER_B}.get(step.get("provider"))
    if step["op"] == "fund":
        return f"setup-{str(provider.pubkey())[:8]}", "setup_provider", 5
    if step["op"] == "create_pool":
        return "create-pool", "create_pool", 1
    if step["op"] == "create_lp":
        return f"create-lp-{str(provider.pubkey())[:8]}", "create_lp_account", 1
    if step["op"] == "trade":
        return f"{number:02d}-trade", step["name"], 1
    if step["op"] == "atomic":
        return f"{number:02d}-atomic", "atomic_liquidity", len(step["operations"])
    return f"{number:02d}-{step['op']}", step["op"], 1


STRICT_FIELDS = ("transaction_base64", "signatures", "message_sha256", "blockhash",
                 "blockhash_sha256", "signer_pubkeys", "instruction_count", "pool_label", "error", "outcome",
                 "failure_class", "semantic_logs", "events", "inner_instructions", "return_data",
                 "before", "after", "rollback_nonfee_equal", "accounting")


def verify(report: dict, candidate: Path) -> list[str]:
    problems: list[str] = []
    if report.get("schema") != "pump-amm-multi-lp-v1":
        problems.append("schema")
    if report.get("upstream_commit") != "747aae62863ecb052069999be020a7f49943e6a0":
        problems.append("upstream_commit")
    expected_ids = {"pump_amm": str(lifecycle.AMM), "spl_token": str(lifecycle.TOKEN),
                    "spl_token_2022": str(lifecycle.TOKEN22), "associated_token": str(lifecycle.ATA)}
    if report.get("program_ids") != expected_ids:
        problems.append("program_ids")
    if report.get("program_sha256") != lifecycle.program_hashes(candidate):
        problems.append("program_sha256")
    if report.get("producer_input_sha256") != lifecycle.input_hashes():
        problems.append("producer_input_sha256")
    sources, source_digest = lifecycle.source_manifest()
    if (report.get("candidate_source_sha256") != sources
            or report.get("candidate_source_manifest_sha256") != source_digest):
        problems.append("candidate_source_manifest")
    build = report.get("candidate_build")
    if lifecycle.file_sha(candidate) == lifecycle.file_sha(lifecycle.CAPTURED["original"]):
        if build != {"kind": "original_control"}:
            problems.append("candidate_build_control")
    elif not isinstance(build, dict) or build.get("kind") != "source_build":
        problems.append("candidate_build_missing")
    else:
        receipt = build.get("receipt", {})
        if (build.get("receipt_canonical_sha256") != lifecycle.canonical_hash(receipt)
                or receipt.get("schema") != "pump-amm-multi-lp-build-v1"
                or receipt.get("returncode") != 0 or not receipt.get("source_unchanged")
                or receipt.get("artifact_sha256") != lifecycle.file_sha(candidate)
                or receipt.get("source_sha256") != sources
                or receipt.get("source_after_sha256") != sources
                or receipt.get("source_manifest_sha256") != source_digest
                or receipt.get("source_after_manifest_sha256") != source_digest
                or receipt.get("compiler_jobs") != 2 or receipt.get("toolchain") != "v1.44"):
            problems.append("candidate_build_receipt")

    planned = lifecycle.plans()
    rows = report.get("cases", [])
    if [row.get("case") for row in rows] != [plan["name"] for plan in planned]:
        problems.append("case_membership_or_order")
    if report.get("case_count") != len(rows):
        problems.append("case_count")

    classifications = {name: 0 for name in ("equal_observed_behavior", "different_behavior",
                                             "unsupported_fixture", "infrastructure_failure")}
    signatures = {"original": [], "candidate": []}
    transaction_count = 0
    for case_index, (row, plan) in enumerate(zip(rows, planned)):
        prefix = plan["name"]
        if row.get("plan") != plan:
            problems.append(prefix + ":plan")
        fixture = expected_fixture(plan, case_index)
        if row.get("fixture") != fixture:
            problems.append(prefix + ":fixture")
        side_receipts = {}
        for side in ("original", "candidate"):
            receipts = row.get(side, {}).get("receipts", [])
            side_receipts[side] = receipts
            if len(receipts) != len(plan["steps"]):
                problems.append(prefix + ":" + side + ":receipt_count")
            for number, (receipt, step) in enumerate(zip(receipts, plan["steps"])):
                expected_name, operation, instruction_count = expected_step(step, number)
                expected_pool_label = step.get("pool", "primary")
                point = f"{prefix}:{side}:{number}"
                if (receipt.get("step"), receipt.get("operation"), receipt.get("instruction_count")) != (
                        expected_name, operation, instruction_count):
                    problems.append(point + ":step_membership")
                if receipt.get("pool_label") != expected_pool_label:
                    problems.append(point + ":pool_membership")
                try:
                    raw = base64.b64decode(receipt["transaction_base64"], validate=True)
                    transaction = Transaction.from_bytes(raw)
                    if not all(transaction.verify_with_results()):
                        problems.append(point + ":signature_verification")
                    if [str(value) for value in transaction.signatures] != receipt.get("signatures"):
                        problems.append(point + ":signatures")
                    if sha(bytes(transaction.message)) != receipt.get("message_sha256"):
                        problems.append(point + ":message_hash")
                    if sha(bytes(transaction.message.recent_blockhash)) != receipt.get("blockhash_sha256"):
                        problems.append(point + ":blockhash_hash")
                    required = transaction.message.header.num_required_signatures
                    signer_keys = [str(value) for value in transaction.message.account_keys[:required]]
                    if signer_keys != receipt.get("signer_pubkeys"):
                        problems.append(point + ":signer_membership")
                    if signer_keys[0] != fixture["fee_payer"]:
                        problems.append(point + ":fee_payer")
                    signatures[side].extend(receipt["signatures"])
                except Exception:
                    problems.append(point + ":transaction_decode")
                before, after = receipt.get("before", {}), receipt.get("after", {})
                validate_snapshot(before, point + ":before", problems)
                validate_snapshot(after, point + ":after", problems)
                try:
                    active_fixture = fixture if expected_pool_label == "primary" else fixture["secondary"]
                    if receipt.get("accounting") != accounting(before, after, active_fixture):
                        problems.append(point + ":accounting")
                except Exception:
                    problems.append(point + ":accounting_decode")
                if receipt.get("error") is not None:
                    nonfee_before = {k: v for k, v in before.items() if k != fixture["fee_payer"]}
                    nonfee_after = {k: v for k, v in after.items() if k != fixture["fee_payer"]}
                    if not receipt.get("rollback_nonfee_equal") or nonfee_before != nonfee_after:
                        problems.append(point + ":rollback")
                state = economic_state(after, active_fixture)
                if state["pool_reported_lp_supply"] is not None:
                    if state["locked_lp_difference"] != fixture["minimum_locked_lp"]:
                        problems.append(point + ":locked_lp")
                    if (state["provider_lp_total"] is not None
                            and state["provider_lp_total"] != state["lp_mint_supply"]):
                        problems.append(point + ":provider_lp_conservation")
                    if state["base_total"] != 2 * 10**12:
                        problems.append(point + ":base_conservation")
            transaction_count += len(receipts) if side == "original" else 0

            if plan.get("cross_pool") and len(receipts) == len(plan["steps"]):
                primary = fixture
                baseline = receipts[4]["after"]
                protected = [primary["pool"], primary["lp_mint"], primary["vault_base"],
                             primary["vault_quote"], primary["provider_a_accounts"]["base"],
                             primary["provider_a_accounts"]["lp"], primary["provider_b_accounts"]["base"],
                             primary["provider_b_accounts"]["lp"]]
                for number, receipt in enumerate(receipts[5:], start=5):
                    if any(receipt[phase].get(address) != baseline.get(address)
                           for phase in ("before", "after") for address in protected):
                        problems.append(f"{prefix}:{side}:{number}:cross_pool_isolation")

        differences = []
        if len(side_receipts["original"]) != len(side_receipts["candidate"]):
            differences.append("receipt_count")
        else:
            for number, (left, right) in enumerate(zip(side_receipts["original"], side_receipts["candidate"])):
                for field in STRICT_FIELDS:
                    if left.get(field) != right.get(field):
                        differences.append(f"{number}:{field}")
        if differences != row.get("mismatch_fields"):
            problems.append(prefix + ":mismatch_fields")
        observed = [receipt.get("outcome") for receipt in side_receipts["original"]]
        expected = lifecycle.EXPECTED_OUTCOMES[prefix]
        oracle = [] if observed == expected else [{"expected": expected, "observed": observed}]
        if (row.get("expected_original_outcomes") != expected
                or row.get("observed_original_outcomes") != observed
                or row.get("oracle_mismatches") != oracle):
            problems.append(prefix + ":original_oracle")
        infrastructure = any(receipt.get("failure_class") == "infrastructure"
                             for receipts in side_receipts.values() for receipt in receipts)
        classification = ("infrastructure_failure" if infrastructure else
                          "unsupported_fixture" if oracle else
                          "different_behavior" if differences else "equal_observed_behavior")
        if row.get("classification") != classification:
            problems.append(prefix + ":classification")
        classifications[classification] += 1

    for side, values in signatures.items():
        if len(values) != len(set(values)):
            problems.append(side + ":duplicate_signatures")
    if report.get("transaction_count_per_image") != transaction_count:
        problems.append("transaction_count_per_image")
    if report.get("classification_counts") != classifications:
        problems.append("classification_counts")
    if classifications != {"equal_observed_behavior": len(planned), "different_behavior": 0,
                           "unsupported_fixture": 0, "infrastructure_failure": 0}:
        problems.append("qualification_failures")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--build-receipt", type=Path,
                        help="receipt to use for deterministic replay of a source candidate")
    args = parser.parse_args()
    report = read(args.report)
    candidate = args.candidate.resolve()
    problems = verify(report, candidate)
    replay_equal = None
    if args.replay and not problems:
        replay_equal = lifecycle.run_suite(candidate, args.build_receipt) == report
        if not replay_equal:
            problems.append("deterministic_replay")
    print(json.dumps({"verified": not problems, "problems": problems,
                      "deterministic_replay_equal": replay_equal}, indent=2))
    return bool(problems)


if __name__ == "__main__":
    raise SystemExit(main())
