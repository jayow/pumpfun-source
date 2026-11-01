"""Multihop evidence recorders must not claim verification without signatures.

Both 20261029 multihop ``focused.py`` producers store ``signatures_verified``
in their evidence JSON. solders returns no results for a transaction whose
signature vector is empty, so ``all(tx.verify_with_results())`` is true for a
stripped transaction; the recorders now gate on the signature vector itself.

Loading a recorder runs its module-level setup: it prepends the upstream
``/mnt/c`` script path (which does not exist here), imports
``pump_captured_loader`` and calls ``loader.install()`` (replacing
``solders.litesvm.LiteSVM`` process-wide), loads the two-curve fixture and
points that fixture's ``LiteSVM`` at its observed subclass. It also sets
``sys.dont_write_bytecode`` globally. No VM is created and no transaction is
sent. The previous ``LiteSVM`` is restored after every test.

The checked-in fixture was path-adapted to a Windows checkout, so its reads
under ``STALE_ROOT`` are redirected to this checkout during import only; the
function under test does not touch paths.
"""
import importlib.util
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction

ROOT = Path(__file__).resolve().parents[1]
STALE_ROOT = '/mnt/c/Users/ahi20/Documents/ChatGPT/pumpfun-source'
RECORDERS = (
    'research/pump-multihop-sell-fees-20261029/runtime/inputs/focused.py',
    'research/pump-multihop-token-quote-20261029/runtime/inputs/focused.py',
)
USER = Keypair.from_seed(bytes([201]) * 32)


def redirected_read_text(original):
    """Serve ``STALE_ROOT`` reads from this checkout so the fixture can import."""
    def read_text(path, *args, **kwargs):
        text = str(path)
        if text.startswith(STALE_ROOT):
            path = ROOT / text[len(STALE_ROOT):].lstrip('/')
        return original(path, *args, **kwargs)
    return read_text


def load_recorder(relative_path):
    """Load a recorder script as a module, keeping its unique top-level name."""
    path = ROOT / relative_path
    name = 'recorder_' + re.sub(r'\W+', '_', relative_path)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.object(Path, 'read_text', redirected_read_text(Path.read_text)):
        spec.loader.exec_module(module)
    return module


def signed_transaction():
    """An ordinary signed transaction, as the producers normally send."""
    instruction = Instruction(Pubkey.new_unique(), b'\x00',
                              [AccountMeta(USER.pubkey(), True, True)])
    return Transaction.new_signed_with_payer([instruction], USER.pubkey(), [USER],
                                             Hash.new_unique())


def unsigned_transaction():
    """An explicit transaction with no signatures at all."""
    instruction = Instruction(Pubkey.new_unique(), b'\x00',
                              [AccountMeta(USER.pubkey(), True, True)])
    message = Message.new_with_blockhash([instruction], USER.pubkey(), Hash.new_unique())
    return Transaction.populate(message, [])


class RecorderSignatureEvidenceTests(unittest.TestCase):
    def setUp(self):
        # The recorder patches solders.litesvm.LiteSVM at import time.
        import solders.litesvm
        original = solders.litesvm.LiteSVM
        self.addCleanup(setattr, solders.litesvm, 'LiteSVM', original)
        sys.path.insert(0, str(ROOT / 'scripts'))
        self.addCleanup(sys.path.remove, str(ROOT / 'scripts'))

    def test_unsigned_transaction_is_not_recorded_as_verified(self):
        transaction = unsigned_transaction()
        self.assertEqual(list(transaction.signatures), [])
        # The bare expression this fix replaces accepts the same transaction.
        self.assertTrue(all(transaction.verify_with_results()))
        for relative_path in RECORDERS:
            with self.subTest(recorder=relative_path):
                verified = load_recorder(relative_path).signatures_verified(transaction)
                self.assertIs(verified, False)

    def test_signed_transaction_is_recorded_as_verified(self):
        transaction = signed_transaction()
        self.assertEqual(len(transaction.signatures), 1)
        for relative_path in RECORDERS:
            with self.subTest(recorder=relative_path):
                verified = load_recorder(relative_path).signatures_verified(transaction)
                self.assertIs(verified, True)


if __name__ == '__main__':
    unittest.main()
