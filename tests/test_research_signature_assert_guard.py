"""Research harnesses must reject a transaction whose signature vector is empty.

solders returns no verification results for a transaction that carries no
signatures, and ``all([])`` is true, so a bare ``all(tx.verify_with_results())``
assert accepts a stripped transaction. The sweep in
``tests/test_signature_nonempty_sweep.py`` scans ``scripts``, ``reconstruction``
and ``tests``; ``research`` was outside it, so two research harnesses kept the
bare form. Both now route through a module-level ``require_signed`` guard.

The research modules cannot be imported here (one hardcodes a workstation root
path, both resolve evidence and build LiteSVM state at import time), so this
test parses, extracts and executes the guard source itself.
"""
import ast
import unittest
from pathlib import Path

from solders.compute_budget import set_compute_unit_limit
from solders.hash import Hash
from solders.keypair import Keypair
from solders.transaction import Transaction

ROOT = Path(__file__).resolve().parents[1]
HARNESSES = (
    'research/pump-multi-hop-corpus/harness.py',
    'research/handler-closure-multihop-mayhem-20261029/suites/pump_create_v2_mayhem_mode_differential.py',
)
BARE_GATE = 'assert all(tx.verify_with_results())'


def guard_from(relative):
    """Compile a module's ``require_signed`` without importing the module."""
    path = ROOT / relative
    text = path.read_text(encoding='utf-8')
    tree = ast.parse(text, filename=str(path))
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name == 'require_signed']
    if len(functions) != 1:
        raise AssertionError(f'{relative}: expected one module-level require_signed')
    segment = ast.get_source_segment(text, functions[0])
    namespace = {}
    exec(compile(segment, str(path), 'exec'), namespace)  # noqa: S102 - repository source
    return namespace['require_signed']


def signed_transaction():
    payer = Keypair.from_seed(bytes([31]) * 32)
    return Transaction.new_signed_with_payer(
        [set_compute_unit_limit(1_400_000)], payer.pubkey(), [payer], Hash.default())


def stripped_transaction():
    return Transaction.populate(signed_transaction().message, [])


class ResearchSignatureGuardTests(unittest.TestCase):
    def test_no_research_harness_keeps_a_bare_all_gate(self):
        for relative in HARNESSES:
            with self.subTest(module=relative):
                self.assertNotIn(BARE_GATE, (ROOT / relative).read_text(encoding='utf-8'))

    def test_a_stripped_vector_verifies_as_nothing(self):
        self.assertEqual(list(stripped_transaction().verify_with_results()), [])

    def test_require_signed_rejects_a_transaction_without_signatures(self):
        stripped = stripped_transaction()
        for relative in HARNESSES:
            with self.subTest(module=relative):
                with self.assertRaises(AssertionError):
                    guard_from(relative)(stripped)

    def test_require_signed_accepts_a_signed_transaction(self):
        signed = signed_transaction()
        self.assertTrue(signed.signatures)
        for relative in HARNESSES:
            with self.subTest(module=relative):
                guard_from(relative)(signed)


if __name__ == '__main__':
    unittest.main()
