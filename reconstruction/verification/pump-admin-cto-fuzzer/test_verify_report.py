#!/usr/bin/env python3
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import campaign as C  # noqa: E402
import executor as E  # noqa: E402
import verify_report as V  # noqa: E402


class ReportVerificationTests(unittest.TestCase):
    original = E.L.CAPTURED["pump_original"]

    def _campaign(self, root):
        output = Path(root) / "campaign"
        C.run_campaign(original=self.original, candidate=self.original,
                       output=output, seed=20261101, count=1)
        return output

    def test_complete_report_verifies_and_reexecutes(self):
        with tempfile.TemporaryDirectory() as temp:
            output = self._campaign(temp)
            result = V.verify_campaign(output, self.original, self.original, reexecute=True)
            self.assertTrue(result["valid"], result)
            self.assertEqual(result["verified_receipt_count"], 1)
            self.assertEqual(result["reexecuted_case_count"], 1)

    def test_corrupted_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            output = self._campaign(temp)
            receipt = next((output / "receipts").glob("*.gz"))
            raw = bytearray(receipt.read_bytes())
            raw[len(raw) // 2] ^= 1
            receipt.write_bytes(raw)
            result = V.verify_campaign(output, self.original, self.original)
            self.assertFalse(result["valid"])
            self.assertTrue(any(item.startswith("receipt-load:") for item in result["failures"]))

    def test_tampered_executable_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            output = self._campaign(temp)
            candidate = Path(temp) / "candidate.so"
            shutil.copyfile(self.original, candidate)
            candidate.write_bytes(candidate.read_bytes() + b"tamper")
            result = V.verify_campaign(output, self.original, candidate)
            self.assertFalse(result["valid"])
            self.assertIn("input-hash:pump_candidate", result["failures"])


if __name__ == "__main__":
    unittest.main()
