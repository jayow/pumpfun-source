#!/usr/bin/env python3
"""Independent verifier for Donation Relay multi-donor evidence receipts."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from solders.pubkey import Pubkey
from solders.transaction import Transaction


P = Pubkey.from_string
RELAY = P("RLAYHr9TRFcKB2ubYQhspcnXiaGpaVzNQvHytt47RZu")
RELAYER = P("RLYRf96HAJSbbXCNS6ibQYnkxtb6kbfuFxqEkm9oQUc")
TOKEN = P("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ATA = P("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
ORIGINAL_SHA256 = "367f07a62e3f74d358187d175cf3228848e81a56c781a9a75b9df7fed1268563"
TOKEN_SHA256 = "8190d3f7ceb6cb7a7a8d8924bff89f9f611e15ce1f806f2b6237f3311a98f697"
ATA_SHA256 = "6804554e69fd3a58caa191dc4a58f4c67223d30ca28ab8987f39fc18d2f7374d"
COMPUTE_RE = re.compile(r"Program \S+ consumed \d+ of \d+ compute units")
CASE_NAMES = (
    "same_epoch_a_then_b", "interleaved_a_b_a", "three_donors_one_transaction",
    "a_success_b_fail_c_success", "atomic_rollback_invalid_third",
    "different_balance_boundaries", "authority_substitution", "separate_configurations",
    "separate_mints", "zero_and_u64_boundary", "rejected_close_between_donors",
    "failed_donor_retry",
)
EXPECTED_ERRORS = {
    "same_epoch_a_then_b": [None, None],
    "interleaved_a_b_a": [None, None, None],
    "three_donors_one_transaction": [None],
    "a_success_b_fail_c_success": [None, "Custom(6009)", None],
    "atomic_rollback_invalid_third": ["Custom(6009)"],
    "different_balance_boundaries": [None, "Custom(1)", None],
    "authority_substitution": ["Custom(2015)", None],
    "separate_configurations": [None, None],
    "separate_mints": [None, None],
    "zero_and_u64_boundary": [None, "Custom(1)", None],
    "rejected_close_between_donors": [None, "Custom(2012)", None],
    "failed_donor_retry": [None, "Custom(1)", None],
}
EXPECTED_SHAPES = {
    "same_epoch_a_then_b": [[("A", 1_000_000, "config_a_mint_x", None)],
                              [("B", 2_000_000, "config_a_mint_x", None)]],
    "interleaved_a_b_a": [[("A", 100, "config_a_mint_x", None)],
                            [("B", 200, "config_a_mint_x", None)],
                            [("A", 300, "config_a_mint_x", None)]],
    "three_donors_one_transaction": [[("A", 1_000, "config_a_mint_x", None),
                                        ("B", 2_000, "config_a_mint_x", None),
                                        ("C", 3_000, "config_a_mint_x", None)]],
    "a_success_b_fail_c_success": [[("A", 1_000, "config_a_mint_x", None)],
                                     [("B", 1, "config_a_mint_x", None)],
                                     [("C", 3_000, "config_a_mint_x", None)]],
    "atomic_rollback_invalid_third": [[("A", 100, "config_a_mint_x", None),
                                         ("B", 200, "config_a_mint_x", None),
                                         ("C", 1, "config_a_mint_x", None)]],
    "different_balance_boundaries": [[("A", 5, "config_a_mint_x", None)],
                                       [("B", 11, "config_a_mint_x", None)],
                                       [("C", 0, "config_a_mint_x", None)]],
    "authority_substitution": [[("B", 7, "config_a_mint_x", "A")],
                                 [("B", 17, "config_a_mint_x", None)]],
    "separate_configurations": [[("A", 111, "config_a_mint_x", None)],
                                  [("B", 222, "config_b_mint_x", None)]],
    "separate_mints": [[("A", 333, "config_a_mint_x", None)],
                         [("B", 444, "config_a_mint_y", None)]],
    "zero_and_u64_boundary": [[("A", 0, "config_a_mint_x", None)],
                                [("B", (1 << 64) - 1, "config_a_mint_x", None)],
                                [("C", 1, "config_a_mint_x", None)]],
    "rejected_close_between_donors": [[("A", 777, "config_a_mint_x", None)],
                                        "close_wrong_signer",
                                        [("B", 223, "config_a_mint_x", None)]],
    "failed_donor_retry": [[("A", 10, "config_a_mint_x", None)],
                             [("B", 101, "config_a_mint_x", None)],
                             [("B", 100, "config_a_mint_x", None)]],
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    return sha(path.read_bytes())


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field: " + key)
        result[key] = value
    return result


def canonical(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()


def pda(*seeds: bytes) -> Pubkey:
    return Pubkey.find_program_address(list(seeds), RELAY)[0]


def discriminator(namespace: str, name: str) -> bytes:
    return hashlib.sha256(f"{namespace}:{name}".encode()).digest()[:8]


def ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    return Pubkey.find_program_address([bytes(owner), bytes(TOKEN), bytes(mint)], ATA)[0]


def manifest(base: Path, root: Path, *, recursive: bool = True) -> tuple[dict[str, str], str]:
    paths = base.rglob("*") if recursive else base.iterdir()
    rows = {path.relative_to(root).as_posix(): file_sha(path)
            for path in sorted(paths) if path.is_file()}
    return rows, sha(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode())


def data(row: dict[str, Any] | None) -> bytes:
    return b"" if row is None else base64.b64decode(row.get("data_base64", ""))


def token_amount(row: dict[str, Any] | None) -> int:
    raw = data(row)
    return 0 if len(raw) < 72 else int.from_bytes(raw[64:72], "little")


def nullable_token_amount(row: dict[str, Any] | None) -> int | None:
    raw = data(row)
    return None if len(raw) < 72 else int.from_bytes(raw[64:72], "little")


def lamports(row: dict[str, Any] | None) -> int:
    return 0 if row is None else row["lamports"]


def validate_snapshot(rows: Any, label: str) -> list[str]:
    problems = []
    if not isinstance(rows, dict) or not rows:
        return [label + ":missing"]
    for address, row in rows.items():
        try:
            P(address)
        except Exception:
            problems.append(label + ":address")
            continue
        if row is None:
            continue
        try:
            P(row["owner"])
            if type(row["lamports"]) is not int or row["lamports"] < 0:
                raise ValueError
            if type(row["executable"]) is not bool or type(row["data_len"]) is not int:
                raise ValueError
            if row["executable"]:
                if row.get("data_omitted") is not True or "data_base64" in row:
                    raise ValueError
            else:
                raw = base64.b64decode(row["data_base64"], validate=True)
                if len(raw) != row["data_len"] or sha(raw) != row["data_sha256"]:
                    raise ValueError
        except (KeyError, TypeError, ValueError):
            problems.append(label + ":account:" + address)
    return problems


def comparable(row: dict[str, Any]) -> dict[str, Any]:
    def state(stage: str) -> dict[str, Any]:
        value = json.loads(json.dumps(row[stage]))
        value.pop(str(RELAY), None)
        return value
    return {
        "error": row["error"],
        "transaction_fee_lamports": row["transaction_fee_lamports"],
        "logs": [line for line in row["logs"] if not COMPUTE_RE.fullmatch(line)],
        "events": row["events"],
        "inner_instructions": row["inner_instructions"],
        "return_data": row["return_data"],
        "before": state("before"),
        "after": state("after"),
    }


def plan_shape(plan: list[dict[str, Any]]) -> list[Any]:
    result = []
    for step in plan:
        if "close_wrong_signer" in step:
            result.append("close_wrong_signer")
            continue
        result.append([(row["donor"], row["amount"], row["domain"], row.get("source_donor"))
                       for row in step["donations"]])
    return result


def validate_instruction_binding(tx: Transaction, case: dict[str, Any], index: int,
                                 step: dict[str, Any]) -> bool:
    """Bind the authenticated message bytes to the recorded donor operation."""
    plan = case["plan"][index]
    instructions = list(tx.message.instructions)[1:]  # admitted compute-budget prefix
    keys = tx.message.account_keys
    whitelist = pda(b"mint_whitelist_v1")
    event = pda(b"__event_authority")
    if "close_wrong_signer" in plan:
        if len(instructions) != 1 or step["donations"]:
            return False
        compiled = instructions[0]
        domain = case["fixture"]["domains"][plan["close_wrong_signer"]]
        config, mint = bytes.fromhex(domain["config_hex"]), P(domain["mint"])
        distribution = pda(b"distribution_v1", config, bytes(mint), (0).to_bytes(16, "little"))
        vault = ata(distribution, mint)
        expected_accounts = [distribution, vault, P(domain["epoch"]), P(domain["debouncer"]),
                             P(domain["destination"]), mint, TOKEN,
                             P(case["fixture"]["outsider"]), ATA, Pubkey.default(), event, RELAY]
        expected_data = discriminator("global", "close_donation_epoch_v1") + config + (0).to_bytes(16, "little")
        return (keys[compiled.program_id_index] == RELAY
                and [keys[value] for value in compiled.accounts] == expected_accounts
                and bytes(compiled.data) == expected_data)
    donations = plan["donations"]
    if len(instructions) != len(donations) or step["donations"] != donations:
        return False
    for compiled, row in zip(instructions, donations):
        domain = case["fixture"]["domains"][row["domain"]]
        donor = P(case["fixture"]["donors"][row["donor"]])
        mint_name = "mint_x" if row["domain"].endswith("mint_x") else "mint_y"
        source_owner = row.get("source_donor") or row["donor"]
        source = P(case["fixture"]["sources"][source_owner][mint_name])
        message = row["message"].encode()
        expected_data = (discriminator("global", "donate_v1")
                         + row["amount"].to_bytes(8, "little")
                         + bytes.fromhex(domain["config_hex"])
                         + (125).to_bytes(2, "little")
                         + len(message).to_bytes(4, "little") + message + bytes(donor))
        expected_accounts = [P(domain["epoch"]), P(domain["debouncer"]), P(domain["destination"]),
                             P(domain["mint"]), source, donor, whitelist, ATA, TOKEN,
                             Pubkey.default(), event, RELAY]
        if (keys[compiled.program_id_index] != RELAY
                or [keys[value] for value in compiled.accounts] != expected_accounts
                or bytes(compiled.data) != expected_data):
            return False
    return True


def domain_maps(case: dict[str, Any]) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    return case["fixture"]["domains"], case["fixture"]["sources"]


def token_total(state: dict[str, Any], domains: dict[str, dict[str, str]],
                sources: dict[str, dict[str, str]], mint: str) -> int:
    source_total = sum(token_amount(state[address]) for donor in sources.values()
                       for _mint_name, address in donor.items()
                       if data(state[address])[:32] == bytes(P(mint)))
    destination_total = sum(token_amount(state[row["destination"]])
                            for row in domains.values() if row["mint"] == mint)
    return source_total + destination_total


def validate_invariants(case: dict[str, Any], step: dict[str, Any]) -> list[str]:
    problems = []
    row = step["original"]
    before, after = row["before"], row["after"]
    domains, sources = domain_maps(case)
    if row.get("transaction_fee_lamports", 0) <= 0:
        problems.append("fee")
    for mint in {domain["mint"] for domain in domains.values()}:
        if token_total(before, domains, sources, mint) != token_total(after, domains, sources, mint):
            problems.append("token_conservation")
    if row["error"] is not None:
        fee = case["fixture"]["fee_payer"]
        if any(before[key] != after[key] for key in before if key != fee):
            problems.append("rollback")
        return problems

    source_delta: dict[str, int] = {}
    destination_delta: dict[str, int] = {}
    touched = set()
    for donation in step["donations"]:
        domain = domains[donation["domain"]]
        source_owner = donation.get("source_donor") or donation["donor"]
        mint_name = "mint_x" if donation["domain"].endswith("mint_x") else "mint_y"
        source_address = sources[source_owner][mint_name]
        source_delta[source_address] = source_delta.get(source_address, 0) + donation["amount"]
        destination_delta[donation["domain"]] = destination_delta.get(donation["domain"], 0) + donation["amount"]
        touched.add(donation["domain"])
    for donor in sources.values():
        for address in donor.values():
            if token_amount(before[address]) - token_amount(after[address]) != source_delta.get(address, 0):
                problems.append("source_delta")
    for name, domain in domains.items():
        expected = destination_delta.get(name, 0)
        if token_amount(after[domain["destination"]]) - token_amount(before[domain["destination"]]) != expected:
            problems.append("destination_delta")
        before_debouncer, after_debouncer = data(before[domain["debouncer"]]), data(after[domain["debouncer"]])
        before_total = 0 if len(before_debouncer) < 82 else int.from_bytes(before_debouncer[74:82], "little")
        after_total = 0 if len(after_debouncer) < 82 else int.from_bytes(after_debouncer[74:82], "little")
        if after_total - before_total != expected:
            problems.append("debouncer_delta")
        if name not in touched and any(before[address] != after[address] for address in
                                       (domain["epoch"], domain["debouncer"], domain["destination"])):
            problems.append("domain_isolation")
    donor_loss = sum(lamports(before[pubkey]) - lamports(after[pubkey])
                     for pubkey in case["fixture"]["donors"].values())
    rent_gain = sum(lamports(after[address]) - lamports(before[address])
                    for domain in domains.values()
                    for address in (domain["epoch"], domain["debouncer"], domain["destination"]))
    if donor_loss != rent_gain:
        problems.append("rent_conservation")
    return sorted(set(problems))


def decode_final(case: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    domains, sources = domain_maps(case)
    decoded_domains = {}
    for name, domain in domains.items():
        epoch, debouncer = data(state[domain["epoch"]]), data(state[domain["debouncer"]])
        decoded_domains[name] = {
            "epoch": None if len(epoch) < 90 else int.from_bytes(epoch[74:90], "little"),
            "epoch_state": None if len(epoch) < 10 else epoch[9],
            "debouncer_total": None if len(debouncer) < 82 else int.from_bytes(debouncer[74:82], "little"),
            "debouncer_state": None if len(debouncer) < 10 else debouncer[9],
            "destination_tokens": nullable_token_amount(state[domain["destination"]]),
        }
    decoded_sources = {donor: {mint: token_amount(state[address])
                               for mint, address in donor_sources.items()}
                       for donor, donor_sources in sources.items()}
    return {"domains": decoded_domains, "sources": decoded_sources,
            "lamports": {"fee": state[case["fixture"]["fee_payer"]]["lamports"],
                          **{name: state[pubkey]["lamports"]
                             for name, pubkey in case["fixture"]["donors"].items()}}}


def validate(report: dict[str, Any], root: Path, candidate: Path) -> list[str]:
    problems: list[str] = []
    if report.get("schema") != "donation-relay-multidonor-v1":
        problems.append("schema")
    if report.get("program") != str(RELAY):
        problems.append("program")
    authority = report.get("authority_boundary", {})
    if authority != {"production_relayer": str(RELAYER), "private_key_retained": False,
                     "privileged_signer_injection": False}:
        problems.append("authority_boundary")
    inputs = report.get("inputs", {})
    expected_inputs = {
        "original": (root / "evidence/dependencies/donation_relay/deployed.so", ORIGINAL_SHA256),
        "spl_token": (root / "evidence/dependencies/spl_token/deployed.so", TOKEN_SHA256),
        "associated_token": (root / "evidence/dependencies/associated_token/deployed.so", ATA_SHA256),
    }
    for name, (path, expected) in expected_inputs.items():
        if not path.is_file() or file_sha(path) != expected or inputs.get(name, {}).get("sha256") != expected:
            problems.append("input:" + name)
    if not candidate.is_file() or inputs.get("candidate", {}).get("sha256") != file_sha(candidate) \
            or inputs.get("candidate", {}).get("bytes") != candidate.stat().st_size:
        problems.append("candidate")
    source_rows, source_digest = manifest(root / "programs/donation-relay", root)
    if inputs.get("source_files") != source_rows or inputs.get("source_manifest_sha256") != source_digest:
        problems.append("source_provenance")
    package_rows, _ = manifest(Path(__file__).resolve().parent, root, recursive=False)
    if inputs.get("package_files") != package_rows:
        problems.append("package_provenance")

    cases = report.get("cases")
    if not isinstance(cases, list) or [case.get("name") for case in cases] != list(CASE_NAMES):
        return problems + ["case_membership"]
    fixture_evidence = [{key: case.get(key) for key in
                         ("name", "fixture", "initial_balances", "plan", "expected_errors")}
                        for case in cases]
    if report.get("fixture_identity_sha256") != sha(json.dumps(
            fixture_evidence, sort_keys=True, separators=(",", ":")).encode()):
        problems.append("fixture_identity")

    tx_hashes, blockhashes, signatures = [], [], []
    matching = 0
    previous_by_case: dict[str, dict[str, dict[str, Any]]] = {}
    for case in cases:
        name = case["name"]
        if case.get("expected_errors") != EXPECTED_ERRORS[name] \
                or plan_shape(case.get("plan", [])) != EXPECTED_SHAPES[name]:
            problems.append("case_plan:" + name)
        donor_pubkeys = case["fixture"]["donors"]
        if len(set(donor_pubkeys.values())) != 3 or str(RELAYER) in donor_pubkeys.values():
            problems.append("donor_identity:" + name)
        for domain_name, domain in case["fixture"]["domains"].items():
            config, mint = bytes.fromhex(domain["config_hex"]), P(domain["mint"])
            if P(domain["epoch"]) != pda(b"epoch_tracker_v1", config, bytes(mint)) \
                    or P(domain["debouncer"]) != pda(b"debouncer_v1", config, bytes(mint)) \
                    or P(domain["destination"]) != ata(P(domain["debouncer"]), mint):
                problems.append("domain_pda:" + name + ":" + domain_name)
        for donor, mint_sources in case["fixture"]["sources"].items():
            for mint_name, address in mint_sources.items():
                domain_name = "config_a_" + mint_name
                mint = P(case["fixture"]["domains"][domain_name]["mint"])
                if P(address) != ata(P(donor_pubkeys[donor]), mint):
                    problems.append("source_ata:" + name + ":" + donor + ":" + mint_name)

        case_problems = []
        steps = case.get("steps", [])
        if len(steps) != len(EXPECTED_ERRORS[name]):
            problems.append("step_count:" + name)
            continue
        previous = None
        for index, step in enumerate(steps):
            try:
                raw = base64.b64decode(step["transaction_base64"], validate=True)
                tx = Transaction.from_bytes(raw)
                message = bytes(tx.message)
                if sha(raw) != step["transaction_sha256"] or sha(message) != step["message_sha256"] \
                        or base64.b64encode(message).decode() != step["message_base64"]:
                    raise ValueError
                verified = list(tx.verify_with_results())
                required = [str(key) for key in tx.message.account_keys[:tx.message.header.num_required_signatures]]
                if not verified or not all(verified) or verified != step["signature_verification"] \
                        or [str(value) for value in tx.signatures] != step["signatures"] \
                        or required != step["required_signer_pubkeys"] \
                        or str(tx.message.recent_blockhash) != step["recent_blockhash"]:
                    raise ValueError
                if case["fixture"]["fee_payer"] != required[0]:
                    raise ValueError
                donation_donors = sorted({row["donor"] for row in step["donations"]})
                if donation_donors != step["donor_signers"]:
                    raise ValueError
                if any(donor_pubkeys[donor] not in required for donor in donation_donors):
                    raise ValueError
                if not validate_instruction_binding(tx, case, index, step):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                problems.append(f"transaction_authenticity:{name}:{index}")
                continue
            tx_hashes.append(step["transaction_sha256"])
            blockhashes.append(step["recent_blockhash"])
            signatures.extend(step["signatures"])
            for side in ("original", "candidate"):
                row = step[side]
                problems.extend(validate_snapshot(row.get("before"), f"snapshot:{name}:{index}:{side}:before"))
                problems.extend(validate_snapshot(row.get("after"), f"snapshot:{name}:{index}:{side}:after"))
                if row.get("events") != [line for line in row.get("logs", [])
                                         if line.startswith("Program data: ")]:
                    case_problems.append(f"step_{index}:event_log_binding:{side}")
            if index == 0:
                first = step["original"]["before"]
                for donor_name, mint_sources in case["fixture"]["sources"].items():
                    for mint_name, address in mint_sources.items():
                        row = first[address]
                        raw_account = data(row)
                        mint = P(case["fixture"]["domains"]["config_a_" + mint_name]["mint"])
                        if row["owner"] != str(TOKEN) or raw_account[:32] != bytes(mint) \
                                or raw_account[32:64] != bytes(P(donor_pubkeys[donor_name])):
                            case_problems.append("source_owner_or_mint")
            if comparable(step["original"]) != comparable(step["candidate"]) or step.get("mismatches"):
                case_problems.append(f"step_{index}:differential")
            expected_error = EXPECTED_ERRORS[name][index]
            actual_error = step["original"]["error"]
            if (expected_error is None and actual_error is not None) or (
                    expected_error is not None and expected_error.replace(" ", "") not in (actual_error or "").replace(" ", "")):
                case_problems.append(f"step_{index}:expected_error")
            invariants = validate_invariants(case, step)
            if invariants or step.get("invariant_problems") != invariants:
                case_problems.append(f"step_{index}:invariants")
            if previous is not None:
                for side in ("original", "candidate"):
                    if previous[side] != step[side]["before"]:
                        case_problems.append(f"step_{index}:continuity:{side}")
            previous = {side: step[side]["after"] for side in ("original", "candidate")}
        if steps and decode_final(case, steps[-1]["original"]["after"]) != case.get("final_state"):
            case_problems.append("final_state")
        if steps:
            final_accounts = steps[-1]["original"]["after"]
            for domain in case["fixture"]["domains"].values():
                destination = final_accounts[domain["destination"]]
                raw_destination = data(destination)
                if raw_destination and (destination["owner"] != str(TOKEN)
                        or raw_destination[:32] != bytes(P(domain["mint"]))
                        or raw_destination[32:64] != bytes(P(domain["debouncer"]))):
                    case_problems.append("destination_owner_or_mint")
        expected_classification = "equal_observed_behavior" if not case_problems else "different_behavior"
        if case.get("classification") != expected_classification or case.get("problems") != case_problems:
            problems.append("classification:" + name)
        if not case_problems:
            matching += 1

    if report.get("case_count") != len(cases):
        problems.append("case_count")
    if report.get("transaction_count") != len(tx_hashes) or len(set(tx_hashes)) != len(tx_hashes):
        problems.append("transaction_count_or_duplicate")
    if len(set(blockhashes)) != len(blockhashes):
        problems.append("blockhash_reuse")
    if report.get("individual_signature_count") != len(signatures):
        problems.append("signature_count")
    identities = report.get("transaction_identity", {})
    if identities != {"unique_transaction_count": len(set(tx_hashes)),
                      "unique_blockhash_count": len(set(blockhashes)),
                      "all_signatures_verified": True}:
        problems.append("transaction_identity_summary")
    counts = report.get("classification_counts", {})
    if counts.get("equal_observed_behavior") != matching \
            or counts.get("different_behavior") != len(cases) - matching \
            or counts.get("unsupported_fixture") != 0 or counts.get("infrastructure_failure") != 0:
        problems.append("classification_summary")
    if report.get("strict_mismatches") != [case["name"] for case in cases
                                            if case["classification"] == "different_behavior"]:
        problems.append("strict_mismatches")
    if report.get("original_positive_cases") != sum(
            any(step["original"]["error"] is None for step in case["steps"]) for case in cases):
        problems.append("original_positive_cases")
    unsupported = report.get("unsupported", [])
    if len(unsupported) != 2 or any(row.get("classification") != "unsupported_fixture" for row in unsupported):
        problems.append("unsupported_boundary")
    return sorted(set(problems))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    raw = args.report.read_bytes()
    try:
        report = json.loads(raw, object_pairs_hook=unique_fields)
        problems = validate(report, args.root.resolve(), args.candidate.resolve())
        if raw != canonical(report):
            problems.append("noncanonical_json")
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        problems = ["invalid_report:" + str(error)]
    print(json.dumps({"accepted": not problems, "problems": problems,
                      "report_sha256": sha(raw)}, indent=2, sort_keys=True))
    raise SystemExit(1 if problems else 0)


if __name__ == "__main__":
    main()
