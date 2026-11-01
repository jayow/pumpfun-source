#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import controls
import producer
import verify


class GenuineReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="relay-multidonor-tests-")
        cls.root = producer.ROOT
        cls.candidate = cls.root / "evidence/dependencies/donation_relay/deployed.so"
        cls.report_path = Path(cls.temporary.name) / "report.json"
        if not producer.execute(cls.root, cls.candidate, cls.report_path):
            raise AssertionError("original/original multi-donor control failed")
        cls.raw = cls.report_path.read_bytes()
        cls.report = json.loads(cls.raw)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_genuine_original_control_is_independently_accepted(self):
        self.assertEqual(verify.validate(self.report, self.root, self.candidate), [])
        self.assertEqual(self.raw, verify.canonical(self.report))

    def test_all_required_adversarial_mutations_are_detected(self):
        result = controls.run(self.report, self.root, self.candidate)
        self.assertTrue(result["all_detected"])
        self.assertEqual(result["control_count"], 7)
        self.assertEqual(result["detected_count"], 7)
        self.assertEqual(set(result["controls"]), set(controls.MUTATIONS))

    def test_duplicate_transaction_is_rejected(self):
        changed = copy.deepcopy(self.report)
        first = changed["cases"][0]["steps"][0]
        second = changed["cases"][0]["steps"][1]
        for field in ("transaction_base64", "transaction_sha256", "message_base64",
                      "message_sha256", "recent_blockhash", "signatures",
                      "signature_verification", "required_signer_pubkeys", "donor_signers"):
            second[field] = copy.deepcopy(first[field])
        problems = verify.validate(changed, self.root, self.candidate)
        self.assertIn("transaction_count_or_duplicate", problems)
        self.assertTrue(any(problem.startswith("transaction_authenticity:") for problem in problems))

    def test_tampered_executable_hash_is_rejected(self):
        changed = copy.deepcopy(self.report)
        changed["inputs"]["candidate"]["sha256"] = "0" * 64
        self.assertIn("candidate", verify.validate(changed, self.root, self.candidate))

    def test_corrupt_source_provenance_is_rejected(self):
        changed = copy.deepcopy(self.report)
        changed["inputs"]["source_manifest_sha256"] = "f" * 64
        self.assertIn("source_provenance", verify.validate(changed, self.root, self.candidate))

    def test_missing_required_evidence_is_rejected(self):
        changed = copy.deepcopy(self.report)
        del changed["inputs"]["original"]
        self.assertIn("input:original", verify.validate(changed, self.root, self.candidate))

    def test_replay_is_byte_deterministic(self):
        second = Path(self.temporary.name) / "second.json"
        self.assertTrue(producer.execute(self.root, self.candidate, second))
        self.assertEqual(self.raw, second.read_bytes())

    def test_existing_output_is_never_replaced(self):
        occupied = Path(self.temporary.name) / "occupied.json"
        occupied.write_bytes(b"retained\n")
        with self.assertRaises(FileExistsError):
            producer.execute(self.root, self.candidate, occupied)
        self.assertEqual(occupied.read_bytes(), b"retained\n")


if __name__ == "__main__":
    unittest.main()
