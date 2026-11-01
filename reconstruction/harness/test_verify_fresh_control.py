#!/usr/bin/env python3
"""Independent fresh-control verifier and negative-control tests."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

H = Path(__file__).resolve().parent
sys.path.insert(0, str(H))

import fresh_control
import test_fresh_control
import verify_fresh_control


class VerifyFreshControlTests(unittest.TestCase):
    def setUp(self):
        test_fresh_control.FreshControlTests.setUp(self)
        # The producer's mocked binding is the verifier's admitted provenance.
        self.verifier_binding = mock.patch.object(
            verify_fresh_control.fresh_control, "_binding",
            return_value={"tools": {"score.py": "pinned"}},
        )
        self.verifier_binding.start()
        self.addCleanup(self.verifier_binding.stop)

    run_pair = test_fresh_control.FreshControlTests.run_pair

    def verify(self):
        return verify_fresh_control.verify(
            self.root / "out",
            sdk=self.root / "sdk",
            hybrid=self.root / "hybrid",
            base_source=self.base,
            candidate_source=self.candidate,
        )

    def rewrite_receipt(self, mutate):
        path = self.root / "out/receipt.json"
        document = json.loads(path.read_text())
        mutate(document)
        fresh_control.publish_json(path, document)

    def refresh_artifact(self, relative):
        self.rewrite_receipt(lambda document: document["artifacts"].__setitem__(
            relative,
            {
                "bytes": (self.root / "out" / relative).stat().st_size,
                "sha256": fresh_control.sha256_file(self.root / "out" / relative),
            },
        ))

    def test_independent_verifier_accepts_valid_receipt(self):
        self.run_pair()
        result = self.verify()
        self.assertTrue(result["accepted"])
        self.assertEqual(result["comparison"]["normalized_gains"], ["0x108"])

    def test_negative_control_rejects_forged_acceptance_boolean(self):
        self.run_pair()
        self.rewrite_receipt(lambda document: document["qualification"].__setitem__("accepted", False))
        with self.assertRaisesRegex(ValueError, "acceptance boolean"):
            self.verify()

    def test_negative_control_rejects_changed_binary_even_with_inventory_rehashed(self):
        self.run_pair()
        image = self.root / "out/candidate-build/unstripped.so"
        image.write_bytes(b"changed candidate binary")
        self.refresh_artifact("candidate-build/unstripped.so")
        with self.assertRaisesRegex(ValueError, "unstripped.so digest mismatch"):
            self.verify()

    def test_negative_control_recomputes_score_comparison(self):
        self.run_pair()
        score_path = self.root / "out/candidate-score.json"
        document = json.loads(score_path.read_text())
        document["windows"]["256"].update(normalized=False, raw=False, symbols=[])
        changed_windows = {int(start): row for start, row in document["windows"].items()}
        document["summary"] = fresh_control.score_summary(changed_windows)
        fresh_control.publish_json(score_path, document)
        score_hash = fresh_control.sha256_file(score_path)
        def forge(receipt):
            receipt["scores"]["candidate"] = {
                "image_sha256": receipt["builds"]["candidate"]["unstripped_sha256"],
                **document["summary"],
                "report_sha256": score_hash,
            }
            receipt["artifacts"]["candidate-score.json"] = {
                "bytes": score_path.stat().st_size,
                "sha256": score_hash,
            }
        self.rewrite_receipt(forge)
        with self.assertRaisesRegex(ValueError, "recorded comparison"):
            self.verify()

    def test_negative_control_rejects_wrong_admitted_source(self):
        self.run_pair()
        (self.candidate / "src/lib.rs").write_text("pub fn changed() {}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "candidate source does not match"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
