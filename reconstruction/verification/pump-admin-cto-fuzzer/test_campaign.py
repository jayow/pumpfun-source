#!/usr/bin/env python3
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import campaign as C  # noqa: E402
import corpus as P  # noqa: E402
import executor as E  # noqa: E402


class CampaignTests(unittest.TestCase):
    original = E.L.CAPTURED["pump_original"]

    def test_original_control_is_repeatable_and_detects_negative_controls(self):
        with tempfile.TemporaryDirectory() as temp:
            first = C.run_campaign(original=self.original, candidate=self.original,
                                   output=Path(temp) / "first", seed=20261101, count=2)
            second = C.run_campaign(original=self.original, candidate=self.original,
                                    output=Path(temp) / "second", seed=20261101, count=2)
            self.assertEqual(first, second)
            self.assertEqual(first["classification_counts"]["different_behavior"], 0)
            self.assertEqual(first["classification_counts"]["infrastructure_failure"], 0)
            self.assertTrue(first["negative_controls"]["all_detected"])
            self.assertTrue((Path(temp) / "first/receipts").is_dir())

    def test_source_input_mutation_during_execution_prevents_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            candidate = Path(temp) / "candidate.so"
            shutil.copyfile(self.original, candidate)

            def mutate(index, case, result):
                if index == 0:
                    candidate.write_bytes(candidate.read_bytes() + b"tamper")

            output = Path(temp) / "campaign"
            with self.assertRaisesRegex(RuntimeError, "changed during campaign"):
                C.run_campaign(original=self.original, candidate=candidate, output=output,
                               seed=20261101, count=1, after_case=mutate)
            self.assertFalse(output.exists())
            self.assertEqual(len(P.find_partial_attempts(output)), 1)

    def test_compact_mode_retains_progress_without_full_receipt_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "compact"
            summary = C.run_campaign(original=self.original, candidate=self.original,
                                     output=output, seed=20261101, count=1,
                                     detailed_receipts=False)
            self.assertFalse(summary["detailed_receipts"])
            self.assertFalse((output / "receipts").exists())
            self.assertEqual(len(list((output / "progress").glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
