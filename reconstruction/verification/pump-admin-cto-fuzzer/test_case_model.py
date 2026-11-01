import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import case_model as model


class CaseModelTests(unittest.TestCase):
    def test_seed_replay_is_exact_and_duplicate_free(self):
        first = model.generate_corpus(20261101, 64)
        second = model.generate_corpus(20261101, 64)
        self.assertEqual(first, second)
        self.assertEqual(len({row["case_id"] for row in first}), 64)
        for index, row in enumerate(first):
            self.assertEqual(row, model.generate_case(20261101, index))

    def test_generation_is_python_hash_seed_independent(self):
        script = ("import json,sys;sys.path.insert(0,sys.argv[1]);"
                  "import case_model;print(json.dumps(case_model.generate_corpus(9,12),sort_keys=True))")
        outputs = []
        for seed in ("1", "987654"):
            env = dict(__import__("os").environ, PYTHONHASHSEED=seed)
            outputs.append(subprocess.check_output(
                [sys.executable, "-c", script, str(Path(__file__).resolve().parent)], env=env))
        self.assertEqual(outputs[0], outputs[1])

    def test_tampered_case_identity_is_rejected(self):
        case = model.generate_case(1, 2)
        changed = copy.deepcopy(case)
        changed["steps"][0]["fee"] = 123
        with self.assertRaisesRegex(ValueError, "identity"):
            model.validate_case(changed)

    def test_case_manifest_is_compact_and_complete(self):
        case = model.generate_case(7, 11)
        encoded = model.canonical(case)
        self.assertLess(len(encoded), 4096)
        decoded = json.loads(encoded)
        self.assertEqual(model.validate_case(decoded), case)
        self.assertEqual(decoded["case_seed"], model.derive_case_seed(7, 11))

    def test_invalid_seed_and_count_domains_fail_closed(self):
        for value in (-1, 2**128, True):
            with self.assertRaises(ValueError):
                model.generate_case(value, 0)
        for value in (0, 10001, True):
            with self.assertRaises(ValueError):
                model.generate_corpus(0, value)


if __name__ == "__main__":
    unittest.main()
