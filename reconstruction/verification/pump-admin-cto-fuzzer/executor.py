#!/usr/bin/env python3
"""Execute generated Admin CTO cases in independent authenticated LiteSVMs."""
from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path

from solders.account import Account
from solders.clock import Clock
from solders.compute_budget import set_compute_unit_limit
from solders.instruction import AccountMeta, Instruction
from solders.transaction import Transaction
from solders.transaction_metadata import FailedTransactionMetadata

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
from case_model import canonical, sha, validate_case  # noqa: E402


def _load_lifecycle():
    path = ROOT / "reconstruction/verification/pump-admin-cto-stateful/lifecycle.py"
    spec = importlib.util.spec_from_file_location("pump_admin_cto_stateful_lifecycle", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


L = _load_lifecycle()
SCHEMA = "pump-admin-cto-fuzz-execution-v1"
STRICT_FIELDS = (
    "transaction_base64", "signature", "message_sha256", "error", "outcome",
    "failure_class", "events", "semantic_logs", "inner_instructions", "return_data",
    "before", "after", "rollback_nonfee_equal", "instruction_data_base64",
    "instruction_accounts",
)


class UnsupportedFixture(ValueError):
    """The generated mutation cannot be represented by this fixture version."""


def _account_copy(account, *, lamports=None, data=None, owner=None):
    return Account(
        account.lamports if lamports is None else lamports,
        bytes(account.data) if data is None else data,
        account.owner if owner is None else owner,
        account.executable,
        account.rent_epoch,
    )


class FuzzHarness(L.Harness):
    """Stateful harness with explicit, bounded input mutation hooks."""

    OWNER_ROLES = {
        "system": L.SYSTEM,
        "pump": L.PUMP,
        "fees": L.FEES,
        "token": L.TOKEN,
        "token22": L.TOKEN22,
    }

    def __init__(self, pump_image: Path, case: dict, images=None):
        validate_case(case)
        self.fuzz_case = copy.deepcopy(case)
        super().__init__(pump_image, case["config"], images, case["case_index"])
        self.initial_creators = {
            fixture.mint: (fixture.sharing if case["config"].get("shared")
                           else L.CREATORS[0].pubkey())
            for fixture in self.mints
        }
        for mutation in case["initial_mutations"]:
            self.apply_mutation(mutation)

    def curve_creator(self, fixture):
        """Resolve the live creator, retaining an account-list value for malformed data."""
        account = self.vm.get_account(fixture.curve)
        if account is not None and len(account.data) >= 81:
            return L.Pubkey.from_bytes(bytes(account.data[49:81]))
        return self.initial_creators[fixture.mint]

    def mutation_target(self, name: str):
        fixture = self.mints[0]
        targets = {
            "global": L.GLOBAL,
            "curve": fixture.curve,
            "sharing": fixture.sharing,
            "pool": fixture.pool,
            "quote_mint": fixture.quote,
        }
        if name in ("current_wallet_token", "creator_vault_token", "amm_vault_token"):
            current = self.curve_creator(fixture)
            vault = L.pda(b"creator-vault", bytes(current))
            amm_vault = L.pda(b"creator_vault", bytes(current), program=L.AMM)
            targets.update({
                "current_wallet_token": L.ata(current, fixture.quote, fixture.token_program),
                "creator_vault_token": L.ata(vault, fixture.quote, fixture.token_program),
                "amm_vault_token": L.ata(amm_vault, fixture.quote, fixture.token_program),
            })
        if name not in targets:
            raise UnsupportedFixture(f"unknown account mutation target: {name}")
        return targets[name]

    def apply_mutation(self, mutation: dict) -> None:
        if not isinstance(mutation, dict):
            raise UnsupportedFixture("account mutation must be an object")
        key = self.mutation_target(mutation.get("target"))
        account = self.vm.get_account(key)
        if account is None:
            raise UnsupportedFixture(f"mutation target is absent: {mutation.get('target')}")
        action = mutation.get("action")
        value = mutation.get("value")
        if action == "truncate":
            if type(value) is not int or not 0 <= value <= len(account.data):
                raise UnsupportedFixture("truncate length is outside retained account data")
            changed = _account_copy(account, data=bytes(account.data[:value]))
        elif action == "set_byte":
            offset = mutation.get("offset")
            if type(offset) is not int or not 0 <= offset < len(account.data):
                raise UnsupportedFixture("byte mutation offset is outside retained account data")
            if type(value) is not int or not 0 <= value <= 255:
                raise UnsupportedFixture("byte mutation value is invalid")
            data = bytearray(account.data)
            data[offset] = value
            changed = _account_copy(account, data=bytes(data))
        elif action == "owner":
            if value not in self.OWNER_ROLES:
                raise UnsupportedFixture("unknown owner role")
            changed = _account_copy(account, owner=self.OWNER_ROLES[value])
        elif action == "lamports":
            if type(value) is not int or not 0 <= value < 2**64:
                raise UnsupportedFixture("invalid lamport value")
            changed = _account_copy(account, lamports=value)
        elif action == "token_amount":
            if type(value) is not int or not 0 <= value < 2**64 or len(account.data) < 72:
                raise UnsupportedFixture("token amount mutation needs a token account")
            data = bytearray(account.data)
            data[64:72] = value.to_bytes(8, "little")
            changed = _account_copy(account, data=bytes(data))
        elif action == "token_state":
            if type(value) is not int or not 0 <= value <= 255 or len(account.data) < 109:
                raise UnsupportedFixture("token state mutation needs a token account")
            data = bytearray(account.data)
            data[108] = value
            changed = _account_copy(account, data=bytes(data))
        else:
            raise UnsupportedFixture(f"unknown account mutation action: {action}")
        self.put(key, changed)

    def admin_instruction(self, step: dict) -> Instruction:
        base = super().admin_instruction(step)
        metas = list(base.accounts)
        for row in step.get("replace_accounts", []):
            index, creator = row.get("index"), row.get("creator")
            if type(index) is not int or not 0 <= index < len(metas):
                raise UnsupportedFixture("replacement account index is outside instruction")
            if type(creator) is not int or not 0 <= creator < len(L.CREATORS):
                raise UnsupportedFixture("replacement creator is invalid")
            old = metas[index]
            metas[index] = AccountMeta(L.CREATORS[creator].pubkey(), old.is_signer, old.is_writable)
        for index in step.get("readonly", []):
            if type(index) is not int or not 0 <= index < len(metas):
                raise UnsupportedFixture("readonly account index is outside instruction")
            old = metas[index]
            metas[index] = AccountMeta(old.pubkey, old.is_signer, False)
        remove = step.get("drop_accounts", 0)
        if type(remove) is not int or not 0 <= remove < len(metas):
            raise UnsupportedFixture("invalid account removal count")
        if remove:
            metas = metas[:-remove]
        data = bytearray(base.data)
        mutation = step.get("instruction_mutation")
        if mutation:
            action, value = mutation.get("action"), mutation.get("value")
            if action == "truncate":
                if type(value) is not int or not 0 <= value <= len(data):
                    raise UnsupportedFixture("instruction truncation is outside data")
                data = data[:value]
            elif action == "append":
                try:
                    data.extend(bytes.fromhex(value))
                except (TypeError, ValueError) as error:
                    raise UnsupportedFixture("instruction append is not hex") from error
            elif action == "holder_tag":
                if len(data) < 9 or type(value) is not int or not 0 <= value <= 255:
                    raise UnsupportedFixture("holder option tag cannot be mutated")
                data[8] = value
            else:
                raise UnsupportedFixture("unknown instruction mutation")
        return Instruction(base.program_id, bytes(data), metas)

    @staticmethod
    def _signers(instruction: Instruction):
        known = {pair.pubkey(): pair for pair in (L.ADMIN, *L.CREATORS)}
        pairs = [L.PAYER]
        for meta in instruction.accounts:
            if meta.is_signer and meta.pubkey != L.PAYER.pubkey():
                if meta.pubkey not in known:
                    raise UnsupportedFixture(f"no retained keypair for signer {meta.pubkey}")
                if all(pair.pubkey() != meta.pubkey for pair in pairs):
                    pairs.append(known[meta.pubkey])
        return pairs

    def execute(self, step: dict, index: int) -> dict:
        self.vm.expire_blockhash()
        self.vm.set_clock(Clock(1_000 + index, 0, 0, 0, 1_700_000_000 + index))
        before = self.snapshot()
        instruction = self.admin_instruction(step)
        blockhash = self.vm.latest_blockhash()
        tx = Transaction.new_signed_with_payer(
            [set_compute_unit_limit(1_400_000), instruction], L.PAYER.pubkey(),
            self._signers(instruction), blockhash,
        )
        checks = list(tx.verify_with_results())
        if not checks or not all(checks):
            raise UnsupportedFixture("locally constructed transaction failed signature verification")
        result = self.vm.send_transaction(tx)
        failed = isinstance(result, FailedTransactionMetadata)
        meta = result.meta() if failed else result
        raw = json.loads(meta.to_json())
        logs = list(meta.logs())
        after = self.snapshot()
        nonfee = lambda state: {key: value for key, value in state.items() if key != str(L.PAYER.pubkey())}
        error = str(result.err()) if failed else None
        return {
            "step": step["name"], "operation": "admin_cto", "recent_blockhash": str(blockhash),
            "mint_index": 0,
            "instruction_data_base64": base64.b64encode(bytes(instruction.data)).decode(),
            "instruction_accounts": [{"pubkey": str(row.pubkey), "signer": row.is_signer,
                                      "writable": row.is_writable} for row in instruction.accounts],
            "transaction_base64": base64.b64encode(bytes(tx)).decode(),
            "signature": str(tx.signatures[0]), "signature_verification": checks,
            "message_sha256": L.sha(bytes(tx.message)), "error": error,
            "outcome": L.outcome(logs, error), "failure_class": L.failure_class(error),
            "compute_units": meta.compute_units_consumed(), "logs": logs,
            "semantic_logs": L.semantic_logs(logs),
            "events": [line[14:] for line in logs if line.startswith("Program data: ")],
            "inner_instructions": raw.get("inner_instructions"), "return_data": raw.get("return_data"),
            "before_hash": L.canonical_hash(before), "after_hash": L.canonical_hash(after),
            "before": before, "after": after,
            "rollback_nonfee_equal": None if not failed else nonfee(before) == nonfee(after),
        }


def run_image(image: Path, case: dict, images=None) -> dict:
    harness = FuzzHarness(image, case, images)
    initial = harness.snapshot()
    receipts = [harness.execute(step, index) for index, step in enumerate(case["steps"], 1)]
    signatures = [row["signature"] for row in receipts]
    if len(signatures) != len(set(signatures)):
        raise ValueError("case reused a transaction signature")
    fixture = harness.mints[0]
    return {
        "initial_state_hash": L.canonical_hash(initial),
        "receipts": receipts,
        "mint_accounts": [{"mint": str(fixture.mint), "curve": str(fixture.curve),
                           "pool": str(fixture.pool), "sharing": str(fixture.sharing)}],
    }


def compare_runs(original: dict, candidate: dict) -> tuple[list[str], list[dict]]:
    mismatches = []
    if original["initial_state_hash"] != candidate["initial_state_hash"]:
        mismatches.append("initial_state_hash")
    if len(original["receipts"]) != len(candidate["receipts"]):
        return mismatches + ["receipt_count"], []
    comparisons = []
    for left, right in zip(original["receipts"], candidate["receipts"]):
        strict = [field for field in STRICT_FIELDS
                  if L.canonical_hash(left.get(field)) != L.canonical_hash(right.get(field))]
        diagnostic = [field for field in ("logs", "compute_units")
                      if L.canonical_hash(left.get(field)) != L.canonical_hash(right.get(field))]
        comparisons.append({"step": left["step"], "mismatch_fields": strict,
                            "diagnostic_difference_fields": diagnostic})
        mismatches.extend(f"{left['step']}:{field}" for field in strict)
    return mismatches, comparisons


def validate_run(run: dict) -> list[str]:
    """Verify signatures, encoded instruction identity, state and rollback evidence."""
    failures, signatures, previous = [], [], None
    try:
        for row in run["receipts"]:
            tag = row["step"]
            raw = base64.b64decode(row["transaction_base64"], validate=True)
            tx = Transaction.from_bytes(raw)
            checked = list(tx.verify_with_results())
            required = tx.message.header.num_required_signatures
            instruction = tx.message.instructions[-1]
            actual_accounts = [str(tx.message.account_keys[index]) for index in instruction.accounts]
            declared_accounts = [item["pubkey"] for item in row["instruction_accounts"]]
            if (bytes(tx) != raw or len(tx.signatures) != required or checked != [True] * required
                    or checked != row["signature_verification"]
                    or row["signature"] != str(tx.signatures[0])
                    or row["message_sha256"] != L.sha(bytes(tx.message))
                    or row["recent_blockhash"] != str(tx.message.recent_blockhash)
                    or tx.message.account_keys[0] != L.PAYER.pubkey()
                    or tx.message.account_keys[instruction.program_id_index] != L.PUMP
                    or bytes(instruction.data) != base64.b64decode(row["instruction_data_base64"], validate=True)
                    or actual_accounts != declared_accounts):
                failures.append(tag + ":transaction-integrity")
            signatures.append(str(tx.signatures[0]))
            for stage in ("before", "after"):
                state = row[stage]
                if row[stage + "_hash"] != L.canonical_hash(state):
                    failures.append(tag + ":" + stage + "-hash")
                for key, account in state.items():
                    L.P(key)
                    if account is None:
                        continue
                    data = base64.b64decode(account["data_base64"], validate=True)
                    L.P(account["owner"])
                    if (len(data) != account["data_len"] or L.sha(data) != account["data_sha256"]
                            or type(account["lamports"]) is not int or not 0 <= account["lamports"] < 2**64):
                        failures.append(tag + ":account-snapshot")
            if previous is None:
                if run["initial_state_hash"] != L.canonical_hash(row["before"]):
                    failures.append(tag + ":initial-state")
            elif L.canonical_hash(previous) != L.canonical_hash(row["before"]):
                failures.append(tag + ":state-continuity")
            previous = row["after"]
            nonfee = lambda state: {key: value for key, value in state.items()
                                    if key != str(L.PAYER.pubkey())}
            rollback = None if row["error"] is None else nonfee(row["before"]) == nonfee(row["after"])
            if rollback != row["rollback_nonfee_equal"] or rollback is False:
                failures.append(tag + ":non-atomic-failure")
            if (row["outcome"] != L.outcome(row["logs"], row["error"])
                    or row["failure_class"] != L.failure_class(row["error"])
                    or row["semantic_logs"] != L.semantic_logs(row["logs"])
                    or row["events"] != [line[14:] for line in row["logs"] if line.startswith("Program data: ")]):
                failures.append(tag + ":derived-fields")
        if len(signatures) != len(set(signatures)):
            failures.append("duplicate-signature")
    except (KeyError, TypeError, ValueError, IndexError) as error:
        failures.append("malformed-receipt:" + type(error).__name__)
    return failures


def execute_case(case: dict, original: Path, candidate: Path, images=None) -> dict:
    """Execute one case twice and return a four-way classified record."""
    validate_case(case)
    result = {"schema": SCHEMA, "case_id": case["case_id"], "case": copy.deepcopy(case)}
    try:
        left = run_image(original, case, images)
        right = run_image(candidate, case, images)
    except UnsupportedFixture as error:
        result.update(classification="unsupported_fixture", reason=str(error))
        return result
    except Exception as error:  # retained in the report; callers must not count it as equivalence
        result.update(classification="infrastructure_failure",
                      reason=f"{type(error).__name__}: {error}")
        return result
    mismatches, comparison = compare_runs(left, right)
    invariants = {"original": validate_run(left), "candidate": validate_run(right)}
    infrastructure = [f"{side}:{row['step']}" for side, run in (("original", left), ("candidate", right))
                      for row in run["receipts"] if row["failure_class"] == "infrastructure"]
    if any(invariants.values()) or infrastructure:
        classification = "infrastructure_failure"
    elif mismatches:
        classification = "different_behavior"
    else:
        classification = "equal_observed_behavior"
    result.update(classification=classification, reason=None, original=left, candidate=right,
                  comparison=comparison, mismatch_fields=mismatches,
                  invariant_failures=invariants, infrastructure_failures=infrastructure)
    return result


def execution_identity(result: dict) -> str:
    return hashlib.sha256(canonical(result)).hexdigest()
