#!/usr/bin/env python3
"""Regression tests for the persistent PumpSwap multi-LP producer."""
from __future__ import annotations

import base64
import copy
import tempfile
import unittest
from pathlib import Path

import lifecycle


class FixtureTests(unittest.TestCase):
    def test_provider_and_fee_keys_are_separate(self):
        keys = {lifecycle.FEE_PAYER.pubkey(), lifecycle.PROVIDER_A.pubkey(),
                lifecycle.PROVIDER_B.pubkey(), lifecycle.MINT_AUTHORITY.pubkey()}
        self.assertEqual(len(keys), 4)

    def test_canonical_pool_lp_and_ata_derivation(self):
        pool = lifecycle.PoolFixture.derive("case-0", index=0)
        expected_pool = lifecycle.Pubkey.find_program_address([
            b"pool", b"\0\0", bytes(lifecycle.PROVIDER_A.pubkey()),
            bytes(pool.base), bytes(pool.quote)], lifecycle.AMM)[0]
        self.assertEqual(pool.pool, expected_pool)
        self.assertEqual(pool.lp, lifecycle.Pubkey.find_program_address(
            [b"pool_lp_mint", bytes(pool.pool)], lifecycle.AMM)[0])
        self.assertNotEqual(pool.provider_accounts(lifecycle.PROVIDER_A)["lp"],
                            pool.provider_accounts(lifecycle.PROVIDER_B)["lp"])

    def test_case_plans_and_oracles_are_complete(self):
        plans = lifecycle.plans()
        self.assertEqual(len(plans), 9)
        self.assertEqual({plan["name"] for plan in plans}, set(lifecycle.EXPECTED_OUTCOMES))
        for plan in plans:
            self.assertEqual(len(plan["steps"]), len(lifecycle.EXPECTED_OUTCOMES[plan["name"]]))


class ExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = lifecycle.run_suite(lifecycle.CAPTURED["original"])

    def test_original_vs_original_control(self):
        self.assertEqual(self.report["classification_counts"], {
            "equal_observed_behavior": 9, "different_behavior": 0,
            "unsupported_fixture": 0, "infrastructure_failure": 0})
        self.assertEqual(self.report["transaction_count_per_image"], 65)
        for row in self.report["cases"]:
            self.assertFalse(row["mismatch_fields"])
            self.assertFalse(row["oracle_mismatches"])

    def test_every_transaction_has_valid_real_signatures(self):
        for row in self.report["cases"]:
            for receipt in row["original"]["receipts"]:
                transaction = lifecycle.Transaction.from_bytes(
                    base64.b64decode(receipt["transaction_base64"], validate=True))
                self.assertTrue(all(transaction.verify_with_results()))
                required = transaction.message.header.num_required_signatures
                signers = list(transaction.message.account_keys[:required])
                self.assertEqual(signers[0], lifecycle.FEE_PAYER.pubkey())
                self.assertNotIn(row["fixture"]["pool"], receipt["signer_pubkeys"])

    def test_atomic_failure_rolls_back_complete_nonfee_state(self):
        row = next(row for row in self.report["cases"]
                   if row["case"] == "atomic-valid-deposit-invalid-withdraw-rollback")
        failed = row["original"]["receipts"][-1]
        self.assertEqual(failed["instruction_count"], 2)
        self.assertIsNotNone(failed["error"])
        self.assertTrue(failed["rollback_nonfee_equal"])

    def test_authority_and_overwithdraw_failures_are_program_errors(self):
        names = ("foreign-lp-authority-refused-then-owner-withdraws",
                 "over-withdraw-refused-then-partial-chain")
        for name in names:
            row = next(row for row in self.report["cases"] if row["case"] == name)
            failed = row["original"]["receipts"][5]
            self.assertEqual(failed["failure_class"], "program")
            self.assertTrue(failed["rollback_nonfee_equal"])
            self.assertEqual(row["original"]["receipts"][-1]["outcome"], "ok")

    def test_lp_and_reserve_conservation(self):
        for row in self.report["cases"]:
            final = row["original"]["receipts"][-1]["accounting"]["after"]
            self.assertEqual(final["locked_lp_difference"], 100)
            self.assertEqual(final["provider_lp_total"], final["lp_mint_supply"])
            self.assertEqual(final["base_total"], 2 * 10**12)

    def test_token2022_base_path_is_positive(self):
        row = next(row for row in self.report["cases"]
                   if row["case"] == "token2022-base-multi-provider")
        self.assertEqual(row["fixture"]["base_token_program"], str(lifecycle.TOKEN22))
        self.assertTrue(all(receipt["outcome"] == "ok" for receipt in row["original"]["receipts"]))

    def test_cross_pool_state_isolation(self):
        row = next(row for row in self.report["cases"]
                   if row["case"] == "two-pool-provider-state-isolation")
        primary = row["fixture"]
        baseline = row["original"]["receipts"][4]["after"]
        protected = [primary["pool"], primary["lp_mint"], primary["vault_base"],
                     primary["vault_quote"], primary["provider_a_accounts"]["base"],
                     primary["provider_b_accounts"]["base"]]
        for receipt in row["original"]["receipts"][5:]:
            for address in protected:
                self.assertEqual(receipt["before"][address], baseline[address])
                self.assertEqual(receipt["after"][address], baseline[address])

    def test_deterministic_replay(self):
        self.assertEqual(self.report, lifecycle.run_suite(lifecycle.CAPTURED["original"]))

    def test_exclusive_report_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report.json.gz"
            lifecycle.write_report(output, self.report)
            retained = output.read_bytes()
            with self.assertRaises(FileExistsError):
                lifecycle.write_report(output, {"tampered": True})
            self.assertEqual(output.read_bytes(), retained)


if __name__ == "__main__":
    unittest.main()
