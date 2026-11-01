#!/usr/bin/env python3
"""Synthetic fresh-control build/score tests; no compiler is invoked."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

H = Path(__file__).resolve().parent
sys.path.insert(0, str(H))

import fresh_control


def crate(path: Path, value: int) -> Path:
    (path / "src").mkdir(parents=True)
    (path / "Cargo.toml").write_text('[package]\nname="fixture"\nversion="0.0.0"\n', encoding="utf-8")
    (path / "Cargo.lock").write_text("version = 3\n", encoding="utf-8")
    (path / "src/lib.rs").write_text(f"pub fn value() -> u64 {{ {value} }}\n", encoding="utf-8")
    return path


def row(normalized: bool, raw: bool, size: int = 8, symbols=None) -> dict:
    return {
        "bytes": size,
        "normalized": normalized,
        "raw": raw,
        "symbols": list(symbols or (["fixture"] if normalized else [])),
    }


class FreshControlTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="fresh-control-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.base = crate(self.root / "base", 1)
        self.candidate = crate(self.root / "candidate", 2)
        self.build_order = []
        self.windows = {
            "base": {0x100: row(True, True), 0x108: row(False, False)},
            "candidate": {0x100: row(True, True), 0x108: row(True, False)},
        }

        def copy_inputs(source, destination):
            (destination / "original.so").write_bytes(b"captured original")
            (destination / "functions.tsv").write_text("synthetic inventory\n", encoding="utf-8")

        def build(source, work, output, **kwargs):
            side = "base" if Path(source) == self.base.resolve() else "candidate"
            self.build_order.append((side, Path(work), Path(output)))
            output.mkdir(parents=True)
            image = output / "unstripped.so"
            image.write_bytes(side.encode())
            (output / "candidate.so").write_bytes((side + "-stripped").encode())
            (output / "build.log").write_text("deterministic synthetic build\n", encoding="utf-8")
            return {
                "ok": True,
                "runtime_valid": True,
                "stack_warnings": [],
                "recipe": "identity",
                "triple": "sbpf-solana-solana",
                "source_sha256": fresh_control.snapshot(source)["sha256"],
                "unstripped_sha256": fresh_control.sha256_file(image),
                "candidate_sha256": fresh_control.sha256_file(output / "candidate.so"),
            }

        def score(image, expected):
            side = Path(image).read_bytes().decode()
            self.assertEqual(fresh_control.sha256_file(image), expected)
            return self.windows[side], expected

        self.patchers = [
            mock.patch.object(fresh_control, "copy_original_inputs", side_effect=copy_inputs),
            mock.patch.object(fresh_control, "_binding", return_value={"tools": {"score.py": "pinned"}}),
            mock.patch.object(fresh_control.fastbuild, "build", side_effect=build),
            mock.patch.object(fresh_control.official_score, "score_with_hash", side_effect=score),
            mock.patch.object(fresh_control.fastbuild, "SDK", self.root / "sdk"),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.addCleanup(lambda: [patcher.stop() for patcher in reversed(self.patchers)])

    def run_pair(self, name="out"):
        return fresh_control.run_pair(
            self.base,
            self.candidate,
            self.root / name,
            sdk=self.root / "sdk",
            hybrid=self.root / "hybrid",
        )

    def test_positive_gain_is_accepted_after_two_isolated_builds(self):
        receipt = self.run_pair()
        self.assertTrue(receipt["qualification"]["accepted"])
        self.assertEqual(receipt["comparison"]["normalized_gains"], ["0x108"])
        self.assertEqual(receipt["comparison"]["normalized_losses"], [])
        self.assertEqual([entry[0] for entry in self.build_order], ["base", "candidate"])
        self.assertNotEqual(self.build_order[0][1], self.build_order[1][1])
        self.assertEqual(receipt["sources"]["base"]["sha256"], fresh_control.snapshot(self.base)["sha256"])
        self.assertIn("base-build/unstripped.so", receipt["artifacts"])
        self.assertIn("candidate-score.json", receipt["artifacts"])

    def test_normalized_regression_is_rejected(self):
        self.windows["candidate"][0x100] = row(False, False)
        receipt = self.run_pair()
        self.assertFalse(receipt["qualification"]["accepted"])
        self.assertIn("normalized_match_regression", receipt["qualification"]["rejection_reasons"])
        self.assertEqual(receipt["comparison"]["normalized_losses"], ["0x100"])

    def test_raw_regression_is_rejected_even_when_normalized_match_survives(self):
        self.windows["candidate"][0x100] = row(True, False)
        receipt = self.run_pair()
        self.assertFalse(receipt["qualification"]["accepted"])
        self.assertEqual(receipt["comparison"]["normalized_losses"], [])
        self.assertEqual(receipt["comparison"]["raw_losses"], ["0x100"])
        self.assertIn("raw_match_regression", receipt["qualification"]["rejection_reasons"])

    def test_changed_inventory_is_rejected(self):
        self.windows["candidate"][0x110] = row(False, False)
        receipt = self.run_pair()
        self.assertFalse(receipt["qualification"]["accepted"])
        self.assertEqual(receipt["comparison"]["added"], ["0x110"])
        self.assertIn("added_windows", receipt["qualification"]["rejection_reasons"])

    def test_ambiguous_membership_is_reported(self):
        self.windows["candidate"][0x108] = row(True, False, symbols=["one", "two"])
        receipt = self.run_pair()
        self.assertEqual(receipt["scores"]["candidate"]["ambiguous"], ["0x108"])

    def test_report_is_deterministic_across_output_directories(self):
        first = self.run_pair("one")
        first_bytes = (self.root / "one/receipt.json").read_bytes()
        self.build_order.clear()
        second = self.run_pair("two")
        second_bytes = (self.root / "two/receipt.json").read_bytes()
        self.assertEqual(first, second)
        self.assertEqual(first_bytes, second_bytes)

    def test_existing_output_is_rejected_before_a_build(self):
        (self.root / "collision").mkdir()
        with self.assertRaisesRegex(FileExistsError, "already exists"):
            self.run_pair("collision")
        self.assertEqual(self.build_order, [])

    def test_saved_receipt_is_canonical_json(self):
        receipt = self.run_pair()
        loaded = json.loads((self.root / "out/receipt.json").read_text())
        self.assertEqual(loaded, receipt)
        self.assertTrue((self.root / "out/receipt.json").read_bytes().endswith(b"\n"))


if __name__ == "__main__":
    unittest.main()
