#!/usr/bin/env python3
import base64
import copy
import sys
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import case_model as C  # noqa: E402
import executor as E  # noqa: E402


def fixture_case(*, mutations=None, steps=None, mode="native"):
    case = {
        "schema": C.SCHEMA,
        "generator": C.GENERATOR,
        "root_seed": 7,
        "case_index": 0,
        "case_seed": C.derive_case_seed(7, 0),
        "config": {"mints": [mode], "shared": False, "complete": False,
                   "holder_initial": False, **({"native_vault_lamports": 890_880}
                   if mode == "native" else {"spl_vault_tokens": 100, "amm_tokens": 100})},
        "initial_mutations": mutations or [],
        "steps": steps or [{"name": "A-to-B", "op": "admin_cto", "new": 1}],
    }
    case["case_id"] = C.case_identity(case)
    return C.validate_case(case)


class ExecutorTests(unittest.TestCase):
    original = E.L.CAPTURED["pump_original"]

    def test_original_vs_original_is_equal_and_independently_signed(self):
        result = E.execute_case(fixture_case(), self.original, self.original)
        self.assertEqual(result["classification"], "equal_observed_behavior")
        self.assertEqual(result["invariant_failures"], {"original": [], "candidate": []})
        left, right = result["original"], result["candidate"]
        self.assertIsNot(left, right)
        self.assertIsNot(left["receipts"][0]["after"], right["receipts"][0]["after"])
        self.assertTrue(all(left["receipts"][0]["signature_verification"]))
        self.assertEqual(left["receipts"][0]["transaction_base64"],
                         right["receipts"][0]["transaction_base64"])

    def test_account_mutation_is_encoded_before_execution(self):
        case = fixture_case(mutations=[{"target": "curve", "action": "set_byte",
                                       "offset": 48, "value": 1}])
        harness = E.FuzzHarness(self.original, case)
        curve = harness.vm.get_account(harness.mints[0].curve)
        self.assertEqual(curve.data[48], 1)
        result = E.execute_case(case, self.original, self.original)
        self.assertEqual(result["classification"], "equal_observed_behavior")

    def test_invalid_truncated_data_is_program_behavior_not_success_or_infra(self):
        case = fixture_case(mutations=[{"target": "curve", "action": "truncate", "value": 8}])
        result = E.execute_case(case, self.original, self.original)
        self.assertEqual(result["classification"], "equal_observed_behavior")
        self.assertIsNotNone(result["original"]["receipts"][0]["error"])
        self.assertEqual(result["original"]["receipts"][0]["failure_class"], "program")
        self.assertTrue(result["original"]["receipts"][0]["rollback_nonfee_equal"])

    def test_pda_accounts_are_never_fabricated_as_signers(self):
        result = E.execute_case(fixture_case(), self.original, self.original)
        accounts = result["original"]["receipts"][0]["instruction_accounts"]
        fixture = E.L.Harness(self.original, {"mints": ["native"]}).mints[0]
        pdas = {str(fixture.curve), str(fixture.pool), str(fixture.sharing),
                str(fixture.pool_authority)}
        self.assertFalse(any(row["signer"] for row in accounts if row["pubkey"] in pdas))

    def test_replaced_required_signer_uses_retained_real_keypair(self):
        case = fixture_case(steps=[{"name": "replace-admin", "op": "admin_cto", "new": 1,
                                   "replace_accounts": [{"index": 0, "creator": 3}]}])
        result = E.execute_case(case, self.original, self.original)
        self.assertEqual(result["classification"], "equal_observed_behavior")
        receipt = result["original"]["receipts"][0]
        self.assertTrue(all(receipt["signature_verification"]))
        self.assertEqual(receipt["instruction_accounts"][0]["pubkey"], str(E.L.CREATORS[3].pubkey()))

    def test_unsupported_fixture_is_never_counted_as_equivalence(self):
        case = fixture_case(mutations=[{"target": "curve", "action": "truncate", "value": 8},
                                       {"target": "curve", "action": "set_byte",
                                        "offset": 48, "value": 1}])
        result = E.execute_case(case, self.original, self.original)
        self.assertEqual(result["classification"], "unsupported_fixture")
        self.assertNotIn("original", result)

    def test_behavior_comparator_detects_real_observable_changes(self):
        run = E.run_image(self.original, fixture_case())
        changed = copy.deepcopy(run)
        changed["receipts"][0]["after"][str(E.L.GLOBAL)]["lamports"] += 1
        fields, _ = E.compare_runs(run, changed)
        self.assertIn("A-to-B:after", fields)
        self.assertTrue(E.validate_run(changed))

    def test_corrupted_transaction_receipt_fails_validation(self):
        run = E.run_image(self.original, fixture_case())
        raw = bytearray(base64.b64decode(run["receipts"][0]["transaction_base64"]))
        raw[-1] ^= 1
        run["receipts"][0]["transaction_base64"] = base64.b64encode(raw).decode()
        self.assertTrue(E.validate_run(run))

    def test_extension_panic_is_infrastructure_but_interrupt_propagates(self):
        class ExtensionPanic(BaseException):
            pass

        with mock.patch.object(E, "run_image", side_effect=ExtensionPanic("bounded panic")):
            result = E.execute_case(fixture_case(), self.original, self.original)
        self.assertEqual(result["classification"], "infrastructure_failure")
        self.assertIn("ExtensionPanic", result["reason"])
        with mock.patch.object(E, "run_image", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                E.execute_case(fixture_case(), self.original, self.original)


if __name__ == "__main__":
    unittest.main()
