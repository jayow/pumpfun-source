#!/usr/bin/env python3
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import corpus as C  # noqa: E402


class CorpusTests(unittest.TestCase):
    def test_snapshot_binds_bytes_and_detects_source_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "program.so"
            source.write_bytes(b"original")
            expected = C.file_sha(source)
            with C.InputSnapshot({"program": source}, {"program": expected}) as snapshot:
                source.write_bytes(b"changed")
                self.assertEqual(snapshot.paths["program"].read_bytes(), b"original")
                self.assertIn("program", snapshot.changed_sources())

    def test_tampered_executable_identity_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "program.so"
            source.write_bytes(b"actual")
            with self.assertRaisesRegex(ValueError, "input hash mismatch"):
                with C.InputSnapshot({"program": source}, {"program": "0" * 64}):
                    pass

    def test_atomic_publication_and_overwrite_refusal(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "campaign"
            with C.CampaignPublisher(output, {"seed": 1}) as publisher:
                publisher.write_json("summary.json", {"ok": True})
                publisher.commit({"case_count": 1})
            self.assertTrue((output / "complete.json").is_file())
            self.assertEqual(json.loads((output / "summary.json").read_text()), {"ok": True})
            with self.assertRaisesRegex(FileExistsError, "overwrite"):
                with C.CampaignPublisher(output, {"seed": 1}):
                    pass

    def test_concurrent_destination_locking(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "campaign"
            with C.CampaignPublisher(output, {"seed": 1}):
                with self.assertRaisesRegex(FileExistsError, "locked"):
                    with C.CampaignPublisher(output, {"seed": 2}):
                        pass

    def test_interrupted_attempt_is_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "campaign"
            with self.assertRaisesRegex(RuntimeError, "bounded failure"):
                with C.CampaignPublisher(output, {"seed": 1}) as publisher:
                    publisher.write_json("cases/0.json", {"partial": True})
                    raise RuntimeError("bounded failure")
            partials = C.find_partial_attempts(output)
            self.assertEqual(len(partials), 1)
            self.assertTrue((partials[0] / "attempt.json").is_file())
            self.assertTrue((partials[0] / "interrupted.json").is_file())
            self.assertTrue((partials[0] / "cases/0.json").is_file())

    def test_compressed_json_is_reproducible(self):
        value = {"z": 1, "a": [2, 3]}
        self.assertEqual(C.compressed_json(value), C.compressed_json(value))


if __name__ == "__main__":
    unittest.main()
