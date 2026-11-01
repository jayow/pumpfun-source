#!/usr/bin/env python3
"""Persistent, authenticated PumpSwap multi-provider LP differential."""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import re
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

from solders.account import Account
from solders.clock import Clock
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.litesvm import LiteSVM
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solders.transaction_metadata import FailedTransactionMetadata

ROOT = Path(__file__).resolve().parents[3]
IDL_PATH = ROOT / "evidence/upstream/pump_amm.json"
CONFIG_PATH = ROOT / "research/imported-lab/analysis/pump_fun/config-snapshot.json"
TRADE_FIXTURE_PATH = ROOT / "research/imported-lab/analysis/pump_fun/amm-v2-trade-fixture.json"
IDL = json.loads(IDL_PATH.read_text())

P = Pubkey.from_string
AMM = P(IDL["address"])
TOKEN = P("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
TOKEN22 = P("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
ATA = P("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
FEES = P("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ")
SYSTEM = Pubkey.default()
EVENT = Pubkey.find_program_address([b"__event_authority"], AMM)[0]

CAPTURED = {
    "original": ROOT / "evidence/original/pump_amm/deployed.so",
    "spl_token": ROOT / "evidence/dependencies/spl_token/deployed.so",
    "spl_token_2022": ROOT / "evidence/dependencies/spl_token_2022/deployed.so",
    "associated_token": ROOT / "evidence/dependencies/associated_token/deployed.so",
    "pump_fees": ROOT / "evidence/original/pump_fees/deployed.so",
}

FEE_PAYER = Keypair.from_seed(hashlib.sha256(b"pump-amm-multi-lp/fee-payer").digest())
PROVIDER_A = Keypair.from_seed(hashlib.sha256(b"pump-amm-multi-lp/provider-a").digest())
PROVIDER_B = Keypair.from_seed(hashlib.sha256(b"pump-amm-multi-lp/provider-b").digest())
MINT_AUTHORITY = Keypair.from_seed(hashlib.sha256(b"pump-amm-multi-lp/mint-authority").digest())

CONFIG_ROW = next(row for row in json.loads(CONFIG_PATH.read_text())["accounts"]
                  if row["program_name"] == "pump_amm")
CONFIG = P(CONFIG_ROW["address"])
CONFIG_DATA = bytearray(base64.b64decode(CONFIG_ROW["data_base64"]))
CONFIG_DATA[56] = 0

TRADE_FIXTURE = json.loads(TRADE_FIXTURE_PATH.read_text())
FEE_CONFIG_ROW = TRADE_FIXTURE["accounts"]["fee_config"]
BUYBACK_ROW = TRADE_FIXTURE["accounts"]["buyback_fee_recipient"]
FEE_CONFIG = P(FEE_CONFIG_ROW["address"])
BUYBACK = P(BUYBACK_ROW["address"])

IX = {row["name"]: row for row in IDL["instructions"]}
INPUT_PATHS = (Path(__file__), IDL_PATH, CONFIG_PATH, TRADE_FIXTURE_PATH)
U64_MAX = 2**64 - 1
EXPECTED_OUTCOMES = {
    "two-provider-independent-withdrawals": ["ok"] * 7,
    "deposit-trade-independent-withdraw": ["ok"] * 7,
    "atomic-two-provider-deposits": ["ok"] * 5,
    "atomic-valid-deposit-invalid-withdraw-rollback": [
        "ok", "ok", "ok", "ok", "ok",
        "error:TransactionErrorInstructionError((1, Tagged(InstructionErrorCustom(1))))",
    ],
    "foreign-lp-authority-refused-then-owner-withdraws": [
        "ok", "ok", "ok", "ok", "ok", "error:ConstraintTokenOwner", "ok",
    ],
    "over-withdraw-refused-then-partial-chain": [
        "ok", "ok", "ok", "ok", "ok",
        "error:TransactionErrorInstructionError((0, Tagged(InstructionErrorCustom(1))))", "ok", "ok",
    ],
    "tiny-deposits-and-rounding": ["ok"] * 8,
    "token2022-base-multi-provider": ["ok"] * 6,
    "two-pool-provider-state-isolation": ["ok"] * 11,
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_sha(path: Path) -> str:
    return sha(path.read_bytes())


def canonical_hash(value) -> str:
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def key(label: str) -> Pubkey:
    return Pubkey.from_bytes(hashlib.sha256(("pump-amm-multi-lp/" + label).encode()).digest())


def ata(owner: Pubkey, mint: Pubkey, token_program: Pubkey) -> Pubkey:
    return Pubkey.find_program_address(
        [bytes(owner), bytes(token_program), bytes(mint)], ATA)[0]


def mint_data(authority: Pubkey, decimals: int = 6, supply: int = 0) -> bytes:
    data = bytearray(82)
    struct.pack_into("<I", data, 0, 1)
    data[4:36] = bytes(authority)
    struct.pack_into("<Q", data, 36, supply)
    data[44] = decimals
    data[45] = 1
    return bytes(data)


def token_amount(account: Account | None) -> int | None:
    if account is None or len(account.data) < 72:
        return None
    return struct.unpack_from("<Q", account.data, 64)[0]


def mint_supply(account: Account | None) -> int | None:
    if account is None or len(account.data) < 44:
        return None
    return struct.unpack_from("<Q", account.data, 36)[0]


def pool_supply(account: Account | None) -> int | None:
    if account is None or len(account.data) < 211:
        return None
    return struct.unpack_from("<Q", account.data, 203)[0]


def account_record(account: Account | None) -> dict | None:
    if account is None:
        return None
    return {
        "lamports": account.lamports,
        "owner": str(account.owner),
        "executable": account.executable,
        "rent_epoch": account.rent_epoch,
        "data_base64": base64.b64encode(bytes(account.data)).decode(),
        "data_sha256": sha(bytes(account.data)),
        "token_amount": token_amount(account) if account.owner in (TOKEN, TOKEN22) and len(account.data) >= 165 else None,
        "mint_supply": mint_supply(account) if account.owner in (TOKEN, TOKEN22) and 82 <= len(account.data) < 165 else None,
        "pool_lp_supply": pool_supply(account) if account.owner == AMM else None,
    }


def instruction(name: str, keys: list[Pubkey], data: bytes) -> Instruction:
    spec = IX[name]
    if len(keys) != len(spec["accounts"]):
        raise ValueError(f"{name}: expected {len(spec['accounts'])} accounts, got {len(keys)}")
    metas = [AccountMeta(pubkey, bool(account.get("signer")), bool(account.get("writable")))
             for pubkey, account in zip(keys, spec["accounts"])]
    return Instruction(AMM, bytes(spec["discriminator"]) + data, metas)


def ata_create(owner: Pubkey, mint: Pubkey, token_program: Pubkey) -> Instruction:
    destination = ata(owner, mint, token_program)
    return Instruction(ATA, b"\x01", [
        AccountMeta(FEE_PAYER.pubkey(), True, True),
        AccountMeta(destination, False, True),
        AccountMeta(owner, False, False),
        AccountMeta(mint, False, False),
        AccountMeta(SYSTEM, False, False),
        AccountMeta(token_program, False, False),
    ])


def mint_to(mint: Pubkey, destination: Pubkey, amount: int, token_program: Pubkey) -> Instruction:
    return Instruction(token_program, bytes([7]) + struct.pack("<Q", amount), [
        AccountMeta(mint, False, True),
        AccountMeta(destination, False, True),
        AccountMeta(MINT_AUTHORITY.pubkey(), True, False),
    ])


def system_transfer(destination: Pubkey, amount: int) -> Instruction:
    return Instruction(SYSTEM, struct.pack("<IQ", 2, amount), [
        AccountMeta(FEE_PAYER.pubkey(), True, True),
        AccountMeta(destination, False, True),
    ])


def sync_native(account: Pubkey) -> Instruction:
    return Instruction(TOKEN, bytes([17]), [AccountMeta(account, False, True)])


def semantic_logs(logs: list[str]) -> list[str]:
    result = []
    for line in logs:
        if re.fullmatch(r"Program [1-9A-HJ-NP-Za-km-z]+ consumed \d+ of \d+ compute units", line):
            continue
        if line.startswith("Program ") and (" invoke [" in line or line.endswith((" success", " failed"))):
            continue
        result.append(line.replace("programs/pump-amm/src/", "src/"))
    return result


def outcome(logs: list[str], error: str | None) -> str:
    if error is None:
        return "ok"
    for line in logs:
        if "Error Code: " in line:
            return "error:" + line.split("Error Code: ", 1)[1].split(".", 1)[0]
    return "error:" + error


def failure_class(error: str | None) -> str | None:
    if error is None:
        return None
    infrastructure = ("BlockhashNotFound", "AlreadyProcessed", "SignatureFailure",
                      "ProgramFailedToComplete", "ComputationalBudgetExceeded")
    return "infrastructure" if any(item in error for item in infrastructure) else "program"


@dataclass(frozen=True)
class PoolFixture:
    label: str
    index: int
    base: Pubkey
    quote: Pubkey
    base_program: Pubkey
    quote_program: Pubkey
    pool: Pubkey
    lp: Pubkey
    vault_base: Pubkey
    vault_quote: Pubkey

    @classmethod
    def derive(cls, label: str, index: int = 0, base_program: Pubkey = TOKEN,
               quote_program: Pubkey = TOKEN) -> "PoolFixture":
        # The captured quote allowlist recognizes wrapped SOL.  The account is
        # a deterministic local mint fixture; no RPC state is consulted.
        quote = P("So11111111111111111111111111111111111111112")
        base = key(f"{label}/base")
        pool = Pubkey.find_program_address([
            b"pool", struct.pack("<H", index), bytes(PROVIDER_A.pubkey()), bytes(base), bytes(quote)
        ], AMM)[0]
        lp = Pubkey.find_program_address([b"pool_lp_mint", bytes(pool)], AMM)[0]
        return cls(label, index, base, quote, base_program, quote_program, pool, lp,
                   ata(pool, base, base_program), ata(pool, quote, quote_program))

    def provider_accounts(self, provider: Keypair) -> dict[str, Pubkey]:
        return {
            "base": ata(provider.pubkey(), self.base, self.base_program),
            "quote": ata(provider.pubkey(), self.quote, self.quote_program),
            "lp": ata(provider.pubkey(), self.lp, TOKEN22),
        }


class DualHarness:
    """Two independent VMs receiving byte-identical signed transactions."""

    def __init__(self, candidate: Path, *, case_index: int = 0, token22_base: bool = False,
                 cross_pool: bool = False):
        self.images = {"original": CAPTURED["original"], "candidate": candidate}
        self.vms = {side: self._vm(image, case_index) for side, image in self.images.items()}
        primary = PoolFixture.derive(
            f"case-{case_index}", index=case_index,
            base_program=TOKEN22 if token22_base else TOKEN)
        self.pools = {"primary": primary}
        if cross_pool:
            self.pools["secondary"] = PoolFixture.derive(
                f"case-{case_index}-secondary", index=case_index + 100)
        self.pool_label = "primary"
        self.pool = primary
        self.tracked: set[Pubkey] = set()
        self.receipts = {"original": [], "candidate": []}
        self._seed()

    @staticmethod
    def _vm(image: Path, case_index: int) -> LiteSVM:
        vm = (LiteSVM().with_sigverify(True).with_blockhash_check(True)
              .with_transaction_history(128).with_log_bytes_limit(None))
        for _ in range(case_index * 16):
            vm.expire_blockhash()
        vm.set_clock(Clock(1_000, 0, 0, 0, 1_700_000_000))
        for address, path in ((AMM, image), (TOKEN, CAPTURED["spl_token"]),
                              (TOKEN22, CAPTURED["spl_token_2022"]),
                              (ATA, CAPTURED["associated_token"]),
                              (FEES, CAPTURED["pump_fees"])):
            vm.add_program_from_file(address, path)
        return vm

    def put(self, pubkey: Pubkey, account: Account) -> None:
        self.tracked.add(pubkey)
        for vm in self.vms.values():
            # LiteSVM copies the solders value into each bank; Account itself
            # is immutable and deliberately does not implement Python pickle.
            vm.set_account(pubkey, account)

    def _seed(self) -> None:
        for signer in (FEE_PAYER, PROVIDER_A, PROVIDER_B, MINT_AUTHORITY):
            self.put(signer.pubkey(), Account(10**15, b"", SYSTEM))
        self.put(CONFIG, Account(CONFIG_ROW["lamports"], bytes(CONFIG_DATA), AMM))
        self.put(EVENT, Account(0, b"", SYSTEM))
        self.put(self.pool.quote, Account(10**9, mint_data(MINT_AUTHORITY.pubkey(), 9), self.pool.quote_program))
        self.put(FEE_CONFIG, Account(FEE_CONFIG_ROW["lamports"], base64.b64decode(FEE_CONFIG_ROW["data_base64"]), P(FEE_CONFIG_ROW["owner"])))
        self.put(BUYBACK, Account(BUYBACK_ROW["lamports"], base64.b64decode(BUYBACK_ROW["data_base64"]), P(BUYBACK_ROW["owner"])))
        for pool in self.pools.values():
            self.put(pool.base, Account(10**9, mint_data(MINT_AUTHORITY.pubkey(), 6), pool.base_program))
            for provider in (PROVIDER_A, PROVIDER_B):
                self.tracked.update(pool.provider_accounts(provider).values())
            self.tracked.update((pool.pool, pool.lp, pool.vault_base, pool.vault_quote))

    def select_pool(self, label: str) -> None:
        self.pool_label = label
        self.pool = self.pools[label]

    def snapshot(self, side: str) -> dict:
        vm = self.vms[side]
        return {str(pubkey): account_record(vm.get_account(pubkey)) for pubkey in sorted(self.tracked, key=str)}

    @staticmethod
    def _amount(snapshot: dict, pubkey: Pubkey) -> int | None:
        record = snapshot.get(str(pubkey))
        return None if record is None else record.get("token_amount")

    @staticmethod
    def _supply(snapshot: dict, pubkey: Pubkey, field: str) -> int | None:
        record = snapshot.get(str(pubkey))
        return None if record is None else record.get(field)

    def accounting(self, before: dict, after: dict) -> dict:
        a_accounts = self.pool.provider_accounts(PROVIDER_A)
        b_accounts = self.pool.provider_accounts(PROVIDER_B)

        def state(snapshot):
            result = {
                "provider_a_base": self._amount(snapshot, a_accounts["base"]),
                "provider_a_quote": self._amount(snapshot, a_accounts["quote"]),
                "provider_a_lp": self._amount(snapshot, a_accounts["lp"]),
                "provider_b_base": self._amount(snapshot, b_accounts["base"]),
                "provider_b_quote": self._amount(snapshot, b_accounts["quote"]),
                "provider_b_lp": self._amount(snapshot, b_accounts["lp"]),
                "pool_base": self._amount(snapshot, self.pool.vault_base),
                "pool_quote": self._amount(snapshot, self.pool.vault_quote),
                "buyback_quote": self._amount(snapshot, BUYBACK),
                "lp_mint_supply": self._supply(snapshot, self.pool.lp, "mint_supply"),
                "pool_reported_lp_supply": self._supply(snapshot, self.pool.pool, "pool_lp_supply"),
            }
            lp_values = (result["provider_a_lp"], result["provider_b_lp"])
            result["provider_lp_total"] = None if None in lp_values else sum(lp_values)
            result["locked_lp_difference"] = (
                None if result["pool_reported_lp_supply"] is None or result["lp_mint_supply"] is None
                else result["pool_reported_lp_supply"] - result["lp_mint_supply"])
            base_values = (result["provider_a_base"], result["provider_b_base"], result["pool_base"])
            quote_values = (result["provider_a_quote"], result["provider_b_quote"],
                            result["pool_quote"], result["buyback_quote"])
            result["base_total"] = None if None in base_values else sum(base_values)
            result["quote_total_with_fee_recipient"] = None if None in quote_values else sum(quote_values)
            return result

        left, right = state(before), state(after)
        delta = {name: None if left[name] is None or right[name] is None else right[name] - left[name]
                 for name in left if isinstance(left[name], int) or isinstance(right[name], int)}
        fee_before = before[str(FEE_PAYER.pubkey())]["lamports"]
        fee_after = after[str(FEE_PAYER.pubkey())]["lamports"]
        return {"before": left, "after": right, "delta": delta,
                "fee_payer_lamport_delta": fee_after - fee_before}

    def execute(self, step: str, instructions: list[Instruction], signers: list[Keypair], operation: str) -> None:
        left, right = self.vms["original"], self.vms["candidate"]
        left.expire_blockhash()
        right.expire_blockhash()
        blockhash = left.latest_blockhash()
        if blockhash != right.latest_blockhash():
            raise RuntimeError("VM blockhash divergence")
        unique = {signer.pubkey(): signer for signer in [FEE_PAYER, *signers]}
        tx = Transaction.new_signed_with_payer(
            instructions, FEE_PAYER.pubkey(), list(unique.values()), blockhash)
        if not all(tx.verify_with_results()):
            raise RuntimeError("locally generated transaction failed Ed25519 verification")
        raw = bytes(tx)
        message = bytes(tx.message)
        common = {
            "step": step,
            "operation": operation,
            "transaction_base64": base64.b64encode(raw).decode(),
            "signatures": [str(value) for value in tx.signatures],
            "message_sha256": sha(message),
            "blockhash": str(blockhash),
            "blockhash_sha256": sha(bytes(blockhash)),
            "signer_pubkeys": [str(pubkey) for pubkey in unique],
            "instruction_count": len(instructions),
            "pool_label": self.pool_label,
        }
        for side, vm in self.vms.items():
            before = self.snapshot(side)
            result = vm.send_transaction(tx)
            failed = isinstance(result, FailedTransactionMetadata)
            meta = result.meta() if failed else result
            raw_meta = json.loads(meta.to_json())
            error = str(result.err()) if failed else None
            after = self.snapshot(side)
            nonfee_before = {k: v for k, v in before.items() if k != str(FEE_PAYER.pubkey())}
            nonfee_after = {k: v for k, v in after.items() if k != str(FEE_PAYER.pubkey())}
            receipt = {
                **common,
                "error": error,
                "outcome": outcome(meta.logs(), error),
                "failure_class": failure_class(error),
                "raw_logs": meta.logs(),
                "semantic_logs": semantic_logs(meta.logs()),
                "events": [line for line in meta.logs() if line.startswith("Program data: ")],
                "inner_instructions": raw_meta.get("inner_instructions", []),
                "return_data": raw_meta.get("return_data"),
                "compute_units": raw_meta.get("compute_units_consumed"),
                "before": before,
                "after": after,
                "rollback_nonfee_equal": nonfee_before == nonfee_after if failed else None,
                "accounting": self.accounting(before, after),
            }
            self.receipts[side].append(receipt)

    def fund_provider(self, provider: Keypair, amount: int = 10**12) -> None:
        accounts = self.pool.provider_accounts(provider)
        self.execute(f"setup-{str(provider.pubkey())[:8]}", [
            ata_create(provider.pubkey(), self.pool.base, self.pool.base_program),
            ata_create(provider.pubkey(), self.pool.quote, self.pool.quote_program),
            mint_to(self.pool.base, accounts["base"], amount, self.pool.base_program),
            # Wrapped SOL is the captured config's supported quote mint.  It
            # cannot be MintTo'd: fund the genuine ATA and invoke SyncNative.
            system_transfer(accounts["quote"], amount),
            sync_native(accounts["quote"]),
        ], [MINT_AUTHORITY], "setup_provider")

    def create_pool(self, base_amount: int = 10**9, quote_amount: int = 10**9) -> None:
        accounts = self.pool.provider_accounts(PROVIDER_A)
        keys = [self.pool.pool, CONFIG, PROVIDER_A.pubkey(), self.pool.base, self.pool.quote,
                self.pool.lp, accounts["base"], accounts["quote"], accounts["lp"],
                self.pool.vault_base, self.pool.vault_quote, SYSTEM, TOKEN22,
                self.pool.base_program, self.pool.quote_program, ATA, EVENT, AMM]
        args = (struct.pack("<HQQ", self.pool.index, base_amount, quote_amount)
                + bytes(PROVIDER_A.pubkey()) + b"\0")
        self.execute("create-pool", [instruction("create_pool", keys, args)], [PROVIDER_A], "create_pool")

    def create_lp_account(self, provider: Keypair) -> None:
        self.execute(f"create-lp-{str(provider.pubkey())[:8]}", [
            ata_create(provider.pubkey(), self.pool.lp, TOKEN22)
        ], [], "create_lp_account")

    def liquidity_ix(self, name: str, provider: Keypair, amount: int,
                     base_limit: int, quote_limit: int,
                     lp_owner: Keypair | None = None) -> Instruction:
        provider_accounts = self.pool.provider_accounts(provider)
        lp_accounts = self.pool.provider_accounts(lp_owner or provider)
        keys = [self.pool.pool, CONFIG, provider.pubkey(), self.pool.base, self.pool.quote,
                self.pool.lp, provider_accounts["base"], provider_accounts["quote"], lp_accounts["lp"],
                self.pool.vault_base, self.pool.vault_quote, TOKEN, TOKEN22, EVENT, AMM]
        return instruction(name, keys, struct.pack("<QQQ", amount, base_limit, quote_limit))

    def trade_ix(self, name: str, provider: Keypair, amount: int, limit: int) -> Instruction:
        accounts = self.pool.provider_accounts(provider)
        volume = Pubkey.find_program_address(
            [b"user_volume_accumulator", bytes(provider.pubkey())], AMM)[0]
        self.tracked.add(volume)
        for vm in self.vms.values():
            if vm.get_account(volume) is None:
                vm.set_account(volume, Account(0, b"", SYSTEM))
        keys = [self.pool.pool, provider.pubkey(), CONFIG, self.pool.base, self.pool.quote,
                accounts["base"], accounts["quote"], self.pool.vault_base, self.pool.vault_quote,
                self.pool.base_program, self.pool.quote_program, SYSTEM, volume, FEE_CONFIG,
                BUYBACK, EVENT, AMM]
        return instruction(name, keys, struct.pack("<QQ", amount, limit))

    def deposit(self, provider: Keypair, amount: int, step: str = "deposit") -> None:
        self.execute(step, [self.liquidity_ix("deposit", provider, amount, U64_MAX, U64_MAX)],
                     [provider], "deposit")

    def withdraw(self, provider: Keypair, amount: int, step: str = "withdraw",
                 lp_owner: Keypair | None = None) -> None:
        self.execute(step, [self.liquidity_ix("withdraw", provider, amount, 0, 0, lp_owner)],
                     [provider], "withdraw")

    def trade(self, name: str, provider: Keypair, amount: int, limit: int, step: str) -> None:
        self.execute(step, [self.trade_ix(name, provider, amount, limit)], [provider], name)

    def atomic(self, step: str, operations: list[dict]) -> None:
        instructions, signers = [], []
        providers = {"A": PROVIDER_A, "B": PROVIDER_B}
        for operation in operations:
            provider = providers[operation["provider"]]
            name = operation["op"]
            instructions.append(self.liquidity_ix(
                name, provider, operation["amount"],
                U64_MAX if name == "deposit" else 0,
                U64_MAX if name == "deposit" else 0,
                providers.get(operation.get("lp_owner"))))
            signers.append(provider)
        self.execute(step, instructions, signers, "atomic_liquidity")


def common_steps() -> list[dict]:
    return [
        {"op": "fund", "provider": "A"},
        {"op": "fund", "provider": "B"},
        {"op": "create_pool", "base": 10**9, "quote": 10**9},
        {"op": "create_lp", "provider": "B"},
    ]


def plans() -> list[dict]:
    return [{
        "name": "two-provider-independent-withdrawals",
        "token22_base": False,
        "steps": common_steps() + [
            {"op": "deposit", "provider": "B", "amount": 100_000_000},
            {"op": "withdraw", "provider": "A", "amount": 100_000_000},
            {"op": "withdraw", "provider": "B", "amount": 50_000_000},
        ],
    }, {
        "name": "deposit-trade-independent-withdraw",
        "token22_base": False,
        "steps": common_steps() + [
            {"op": "deposit", "provider": "B", "amount": 100_000_000},
            {"op": "trade", "name": "buy_v2", "provider": "A", "amount": 10_000_000,
             "limit": U64_MAX},
            {"op": "withdraw", "provider": "A", "amount": 100_000_000},
        ],
    }, {
        "name": "atomic-two-provider-deposits",
        "token22_base": False,
        "steps": common_steps() + [{"op": "atomic", "operations": [
            {"op": "deposit", "provider": "A", "amount": 20_000_000},
            {"op": "deposit", "provider": "B", "amount": 30_000_000},
        ]}],
    }, {
        "name": "atomic-valid-deposit-invalid-withdraw-rollback",
        "token22_base": False,
        "steps": common_steps() + [
            {"op": "deposit", "provider": "B", "amount": 20_000_000},
            {"op": "atomic", "operations": [
                {"op": "deposit", "provider": "A", "amount": 10_000_000},
                {"op": "withdraw", "provider": "B", "amount": 20_000_001},
            ]},
        ],
    }, {
        "name": "foreign-lp-authority-refused-then-owner-withdraws",
        "token22_base": False,
        "steps": common_steps() + [
            {"op": "deposit", "provider": "B", "amount": 40_000_000},
            {"op": "withdraw", "provider": "B", "lp_owner": "A", "amount": 1_000_000},
            {"op": "withdraw", "provider": "B", "amount": 20_000_000},
        ],
    }, {
        "name": "over-withdraw-refused-then-partial-chain",
        "token22_base": False,
        "steps": common_steps() + [
            {"op": "deposit", "provider": "B", "amount": 40_000_000},
            {"op": "withdraw", "provider": "B", "amount": 40_000_001},
            {"op": "withdraw", "provider": "B", "amount": 10_000_000},
            {"op": "withdraw", "provider": "B", "amount": 10_000_000},
        ],
    }, {
        "name": "tiny-deposits-and-rounding",
        "token22_base": False,
        "steps": common_steps() + [
            {"op": "deposit", "provider": "A", "amount": 1},
            {"op": "deposit", "provider": "B", "amount": 2},
            {"op": "withdraw", "provider": "A", "amount": 1},
            {"op": "withdraw", "provider": "B", "amount": 1},
        ],
    }, {
        "name": "token2022-base-multi-provider",
        "token22_base": True,
        "steps": common_steps() + [
            {"op": "deposit", "provider": "B", "amount": 25_000_000},
            {"op": "withdraw", "provider": "B", "amount": 10_000_000},
        ],
    }, {
        "name": "two-pool-provider-state-isolation",
        "token22_base": False,
        "cross_pool": True,
        "steps": common_steps() + [
            {"op": "deposit", "provider": "B", "amount": 25_000_000},
            {"op": "fund", "provider": "A", "pool": "secondary"},
            {"op": "fund", "provider": "B", "pool": "secondary"},
            {"op": "create_pool", "base": 2 * 10**9, "quote": 10**9, "pool": "secondary"},
            {"op": "create_lp", "provider": "B", "pool": "secondary"},
            {"op": "deposit", "provider": "B", "amount": 40_000_000, "pool": "secondary"},
            {"op": "withdraw", "provider": "A", "amount": 100_000_000, "pool": "secondary"},
        ],
    }]


def run_plan(candidate: Path, plan: dict, index: int = 0) -> dict:
    harness = DualHarness(candidate, case_index=index, token22_base=plan.get("token22_base", False),
                          cross_pool=plan.get("cross_pool", False))
    providers = {"A": PROVIDER_A, "B": PROVIDER_B}
    for number, step in enumerate(plan["steps"]):
        harness.select_pool(step.get("pool", "primary"))
        op = step["op"]
        label = f"{number:02d}-{op}"
        if op == "fund":
            harness.fund_provider(providers[step["provider"]], step.get("amount", 10**12))
        elif op == "create_pool":
            harness.create_pool(step["base"], step["quote"])
        elif op == "create_lp":
            harness.create_lp_account(providers[step["provider"]])
        elif op == "deposit":
            harness.deposit(providers[step["provider"]], step["amount"], label)
        elif op == "withdraw":
            harness.withdraw(providers[step["provider"]], step["amount"], label,
                             providers.get(step.get("lp_owner")))
        elif op == "trade":
            harness.trade(step["name"], providers[step["provider"]], step["amount"],
                          step["limit"], label)
        elif op == "atomic":
            harness.atomic(label, step["operations"])
        else:
            raise ValueError(f"unknown operation: {op}")
    def fixture_record(pool):
        return {
            "pool": str(pool.pool), "lp_mint": str(pool.lp),
            "base_mint": str(pool.base), "quote_mint": str(pool.quote),
            "provider_a": str(PROVIDER_A.pubkey()), "provider_b": str(PROVIDER_B.pubkey()),
            "fee_payer": str(FEE_PAYER.pubkey()),
            "base_token_program": str(pool.base_program),
            "quote_token_program": str(pool.quote_program),
            "vault_base": str(pool.vault_base), "vault_quote": str(pool.vault_quote),
            "provider_a_accounts": {k: str(v) for k, v in pool.provider_accounts(PROVIDER_A).items()},
            "provider_b_accounts": {k: str(v) for k, v in pool.provider_accounts(PROVIDER_B).items()},
            "minimum_locked_lp": 100,
        }

    fixture = fixture_record(harness.pools["primary"])
    if "secondary" in harness.pools:
        fixture["secondary"] = fixture_record(harness.pools["secondary"])
    return {
        "case": plan["name"],
        "plan": plan,
        "fixture": fixture,
        "original": {"receipts": harness.receipts["original"]},
        "candidate": {"receipts": harness.receipts["candidate"]},
    }


def compare_case(row: dict) -> list[str]:
    fields = ("transaction_base64", "signatures", "message_sha256", "pool_label", "error", "outcome",
              "failure_class", "semantic_logs", "events", "inner_instructions", "return_data",
              "before", "after", "rollback_nonfee_equal", "accounting")
    differences = []
    left = row["original"]["receipts"]
    right = row["candidate"]["receipts"]
    if len(left) != len(right):
        return ["receipt_count"]
    for index, (a, b) in enumerate(zip(left, right)):
        for field in fields:
            if a.get(field) != b.get(field):
                differences.append(f"{index}:{field}")
    return differences


def input_hashes() -> dict:
    return {str(path.relative_to(ROOT)): file_sha(path) for path in INPUT_PATHS}


def source_manifest() -> tuple[dict, str]:
    roots = (ROOT / "programs/pump-amm", ROOT / "crates/native-transfer-fee")
    paths = [ROOT / "Cargo.lock", ROOT / "Cargo.toml"]
    for source_root in roots:
        paths.extend(path for path in source_root.rglob("*")
                     if path.is_file() and "target" not in path.relative_to(source_root).parts)
    manifest = {str(path.relative_to(ROOT)): file_sha(path) for path in sorted(set(paths))}
    return manifest, canonical_hash(manifest)


def program_hashes(candidate: Path) -> dict:
    return {"original": file_sha(CAPTURED["original"]), "candidate": file_sha(candidate),
            **{name: file_sha(path) for name, path in CAPTURED.items() if name != "original"}}


def build_provenance(candidate: Path, receipt_path: Path | None) -> dict:
    if receipt_path is None:
        if file_sha(candidate) != file_sha(CAPTURED["original"]):
            raise ValueError("a non-original candidate requires --build-receipt")
        return {"kind": "original_control"}
    receipt = json.loads(receipt_path.read_text())
    sources, source_digest = source_manifest()
    if (receipt.get("schema") != "pump-amm-multi-lp-build-v1"
            or receipt.get("returncode") != 0 or not receipt.get("source_unchanged")
            or receipt.get("artifact_sha256") != file_sha(candidate)
            or receipt.get("source_sha256") != sources
            or receipt.get("source_after_sha256") != sources
            or receipt.get("source_manifest_sha256") != source_digest
            or receipt.get("source_after_manifest_sha256") != source_digest
            or receipt.get("compiler_jobs") != 2 or receipt.get("toolchain") != "v1.44"):
        raise ValueError("candidate build receipt does not bind current source and artifact")
    return {"kind": "source_build", "receipt": receipt,
            "receipt_canonical_sha256": canonical_hash(receipt)}


def run_suite(candidate: Path, build_receipt: Path | None = None) -> dict:
    cases = []
    for index, plan in enumerate(plans()):
        row = run_plan(candidate, plan, index)
        row["mismatch_fields"] = compare_case(row)
        observed = [receipt["outcome"] for receipt in row["original"]["receipts"]]
        row["expected_original_outcomes"] = EXPECTED_OUTCOMES[plan["name"]]
        row["observed_original_outcomes"] = observed
        row["oracle_mismatches"] = ([] if observed == row["expected_original_outcomes"] else [{
            "expected": row["expected_original_outcomes"], "observed": observed}])
        infrastructure = any(receipt["failure_class"] == "infrastructure"
                             for side in ("original", "candidate") for receipt in row[side]["receipts"])
        if infrastructure:
            row["classification"] = "infrastructure_failure"
        elif row["oracle_mismatches"]:
            row["classification"] = "unsupported_fixture"
        elif row["mismatch_fields"]:
            row["classification"] = "different_behavior"
        else:
            row["classification"] = "equal_observed_behavior"
        cases.append(row)
    sources, source_digest = source_manifest()
    return {
        "schema": "pump-amm-multi-lp-v1",
        "upstream_commit": "747aae62864e490ac2142906c54fe039a8e9ae66",
        "program_ids": {"pump_amm": str(AMM), "spl_token": str(TOKEN),
                        "spl_token_2022": str(TOKEN22), "associated_token": str(ATA)},
        "program_sha256": program_hashes(candidate),
        "candidate_build": build_provenance(candidate, build_receipt),
        "producer_input_sha256": input_hashes(),
        "candidate_source_sha256": sources,
        "candidate_source_manifest_sha256": source_digest,
        "case_count": len(cases),
        "transaction_count_per_image": sum(len(row["plan"]["steps"]) for row in cases),
        "cases": cases,
        "classification_counts": {name: sum(row["classification"] == name for row in cases)
                                  for name in ("equal_observed_behavior", "different_behavior",
                                               "unsupported_fixture", "infrastructure_failure")},
    }


def write_report(path: Path, report: dict) -> None:
    raw = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
    payload = gzip.compress(raw, mtime=0) if path.suffix == ".gz" else raw
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        # Hard-link publication is atomic and exclusive on the destination's
        # filesystem.  A prior report is evidence, never an overwrite target.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build-receipt", type=Path,
                        help="required for a non-original candidate")
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    report = run_suite(candidate, args.build_receipt.resolve() if args.build_receipt else None)
    write_report(args.output.resolve(), report)
    print(json.dumps({"cases": report["case_count"],
                      "transactions_per_image": report["transaction_count_per_image"],
                      "classification_counts": report["classification_counts"]}, indent=2))
    return bool(report["classification_counts"]["different_behavior"]
                or report["classification_counts"]["infrastructure_failure"])


if __name__ == "__main__":
    raise SystemExit(main())
