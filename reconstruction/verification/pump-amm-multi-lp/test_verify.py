#!/usr/bin/env python3
"""Adversarial controls for the independent multi-LP receipt verifier."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import struct
import unittest
from pathlib import Path

import lifecycle
import verify


def mutate_u64(record: dict, offset: int, value: int) -> None:
    raw = bytearray(base64.b64decode(record["data_base64"]))
    struct.pack_into("<Q", raw, offset, value)
    record["data_base64"] = base64.b64encode(raw).decode()
    record["data_sha256"] = hashlib.sha256(raw).hexdigest()
    if offset == 64:
        record["token_amount"] = value
    elif offset == 36:
        record["mint_supply"] = value
    elif offset == 203:
        record["pool_lp_supply"] = value


class VerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidate = lifecycle.CAPTURED["original"]
        cls.report = lifecycle.run_suite(cls.candidate)

    def assert_refused(self, mutation):
        report = copy.deepcopy(self.report)
        mutation(report)
        self.assertTrue(verify.verify(report, self.candidate))

    def test_valid_original_control(self):
        self.assertEqual(verify.verify(self.report, self.candidate), [])

    def test_retained_qualification_summary_binds_report(self):
        root = Path(__file__).resolve().parent / "qualification"
        summary = json.loads((root / "summary.json").read_text())
        self.assertEqual(lifecycle.file_sha(root / "report.json.gz"), summary["report_sha256"])
        retained = verify.read(root / "report.json.gz")
        self.assertEqual(retained["case_count"], summary["cases"])
        self.assertEqual(retained["program_sha256"]["candidate"], summary["candidate_sha256"])
        self.assertEqual(retained["candidate_build"]["receipt"]["source_manifest_sha256"],
                         summary["candidate_source_manifest_sha256"])

    def test_signature_substitution(self):
        self.assert_refused(lambda report: report["cases"][0]["original"]["receipts"][0]
                            ["signatures"].__setitem__(0, report["cases"][0]["original"]["receipts"][1]["signatures"][0]))

    def test_lp_supply_corruption(self):
        def mutation(report):
            row = report["cases"][0]
            record = row["original"]["receipts"][-1]["after"][row["fixture"]["lp_mint"]]
            mutate_u64(record, 36, record["mint_supply"] + 1)
        self.assert_refused(mutation)

    def test_provider_balance_corruption(self):
        def mutation(report):
            row = report["cases"][0]
            address = row["fixture"]["provider_a_accounts"]["base"]
            record = row["original"]["receipts"][-1]["after"][address]
            mutate_u64(record, 64, record["token_amount"] + 1)
        self.assert_refused(mutation)

    def test_pool_reserve_corruption(self):
        def mutation(report):
            row = report["cases"][0]
            record = row["original"]["receipts"][-1]["after"][row["fixture"]["vault_base"]]
            mutate_u64(record, 64, record["token_amount"] + 1)
        self.assert_refused(mutation)

    def test_incorrect_owner(self):
        def mutation(report):
            row = report["cases"][0]
            address = row["fixture"]["provider_a_accounts"]["lp"]
            row["original"]["receipts"][-1]["after"][address]["owner"] = str(lifecycle.SYSTEM)
        self.assert_refused(mutation)

    def test_missing_transaction(self):
        self.assert_refused(lambda report: report["cases"][0]["original"]["receipts"].pop())

    def test_altered_event(self):
        self.assert_refused(lambda report: report["cases"][0]["original"]["receipts"][2]
                            ["events"].append("Program data: dGFtcGVy"))

    def test_altered_error(self):
        self.assert_refused(lambda report: report["cases"][3]["original"]["receipts"][-1]
                            .__setitem__("error", None))

    def test_forged_passing_count(self):
        self.assert_refused(lambda report: report.__setitem__("classification_counts", {
            "equal_observed_behavior": 999, "different_behavior": 0,
            "unsupported_fixture": 0, "infrastructure_failure": 0}))

    def test_changed_executable_input(self):
        self.assertTrue(verify.verify(self.report, lifecycle.CAPTURED["spl_token"]))

    def test_changed_declared_source_inputs(self):
        self.assert_refused(lambda report: report["candidate_source_sha256"]
                            .__setitem__("programs/pump-amm/src/lib.rs", "0" * 64))

    def test_duplicate_transaction_identity(self):
        def mutation(report):
            first = report["cases"][0]["original"]["receipts"][0]
            second = report["cases"][0]["original"]["receipts"][1]
            for field in ("transaction_base64", "signatures", "message_sha256", "blockhash",
                          "blockhash_sha256", "signer_pubkeys"):
                second[field] = copy.deepcopy(first[field])
        self.assert_refused(mutation)

    def test_corrupted_account_encoding(self):
        self.assert_refused(lambda report: report["cases"][0]["original"]["receipts"][0]
                            ["after"][report["cases"][0]["fixture"]["base_mint"]]
                            .__setitem__("data_base64", "not-base64"))


if __name__ == "__main__":
    unittest.main()
