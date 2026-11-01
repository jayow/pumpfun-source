"""No tracked Python may treat a transaction with no signatures as signed.

``solders`` returns an empty result list for a transaction whose signature is
absent, and ``all([])`` is true, so ``all(tx.verify_with_results())`` accepted a
stripped transaction. The guard is the signature vector itself: ``tx.signatures
and all(...)``, or, where the results are held in a variable, ``results and
all(results)``.

Every tracked Python file under ``scripts``, ``reconstruction`` and ``tests`` is
parsed, including the source of ``-c`` drivers held as string constants. A guard
may sit in the same condition (``tx.signatures and all(...)``, ``not checks or
not all(checks)``, ``len(results) == required and all(results)``) or in an
earlier statement of the same block (``self.assertTrue(results)``). Docstrings
are prose, not code, and are not parsed.
"""
import ast
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACKED_ROOTS = ('scripts', 'reconstruction', 'tests')
# These files assert that all() of an empty or partial vector is true, then
# assert that the strong guard rejects the same transaction: the demonstration
# is the counter-example, not a signature check.
DEMONSTRATIONS = {
    'reconstruction/pump-main/lanes/v3-sell-payment-layout/test_external_inputs.py',
    'tests/test_signature_nonempty_sweep.py',
}

CALL_NAMES = {'exec', 'eval', 'compile', 'run', 'check_output', 'check_call', 'Popen', 'call'}


def tracked_python():
    """Every tracked ``.py`` under the scanned roots, listed by git."""
    listed = subprocess.run(['git', 'ls-files', '--', *TRACKED_ROOTS], cwd=ROOT,
                            text=True, capture_output=True, check=True).stdout.split()
    return [ROOT / path for path in listed if path.endswith('.py')]


def results_source(node):
    """The receiver of a ``verify_with_results()`` call, or None.

    ``tx.verify_with_results()`` and ``list(tx.verify_with_results())`` both
    yield ``tx``.
    """
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Attribute) and node.func.attr == 'verify_with_results':
        return ast.unparse(node.func.value)
    if (isinstance(node.func, ast.Name) and node.func.id == 'list'
            and len(node.args) == 1 and isinstance(node.args[0], ast.Call)
            and isinstance(node.args[0].func, ast.Attribute)
            and node.args[0].func.attr == 'verify_with_results'):
        return ast.unparse(node.args[0].func.value)
    return None


def bound_vectors(tree):
    """Names assigned from a signature result vector, mapped to their receiver."""
    bound = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
            targets, value = (node.target,), node.value
        else:
            continue
        source = results_source(value)
        if source is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                bound[target.id] = source
    return bound


def truth_test(node, positive=True):
    """The vector ``node`` truth-tests, or None when its polarity is wrong.

    ``x`` and ``bool(x)`` assert a non-empty vector; ``not x`` asserts an empty
    one. ``not x or not all(x)`` is still a guard, so the polarity the caller
    needs is the one that makes the ``all`` unreachable.
    """
    negated = isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)
    if negated:
        node = node.operand
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == 'bool' and len(node.args) == 1):
        node = node.args[0]
    if negated != (not positive):
        return None
    return node


def compares_length(node, vectors):
    for inner in ast.walk(node):
        if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                and inner.func.id == 'len' and inner.args
                and ast.unparse(inner.args[0]) in vectors):
            return True
    return False


def tests_vector(node, vectors, positive=True):
    """True when ``node`` establishes that one of ``vectors`` is non-empty."""
    tested = truth_test(node, positive)
    if tested is None:
        return False
    text = ast.unparse(tested)
    if any(text == vector or text == vector + '.signatures' for vector in vectors):
        return True
    return isinstance(tested, ast.Compare) and compares_length(tested, vectors)


def condition_guard(node, child, vectors, parents):
    """Walk from ``child`` up through enclosing conditions.

    ``x and all(x)`` guards on the positive test, ``not x or not all(x)`` on
    the negated one.
    """
    while node is not None:
        if isinstance(node, ast.BoolOp):
            positive = isinstance(node.op, ast.And)
            for sibling in node.values:
                if sibling is not child and tests_vector(sibling, vectors, positive):
                    return True
        child, node = node, parents.get(id(node))
    return False


def statement_guard(node, vectors, parents, blocks):
    """True when an earlier statement of the same block tested the vector."""
    while node is not None and not isinstance(node, ast.stmt):
        node = parents.get(id(node))
    if node is None or id(node) not in blocks:
        return False
    block, index = blocks[id(node)]
    for earlier in block[:index]:
        if isinstance(earlier, ast.Assert):
            if tests_vector(earlier.test, vectors):
                return True
        elif isinstance(earlier, ast.Expr) and isinstance(earlier.value, ast.Call):
            call = earlier.value
            name = call.func.attr if isinstance(call.func, ast.Attribute) else None
            if name == 'assertTrue' and call.args:
                if tests_vector(call.args[0], vectors):
                    return True
    return False


def statement_blocks(tree):
    blocks = {}
    for node in ast.walk(tree):
        for attribute in ('body', 'orelse', 'finalbody'):
            block = getattr(node, attribute, None)
            if not isinstance(block, list) or not block or not isinstance(block[0], ast.stmt):
                continue
            for index, statement in enumerate(block):
                blocks[id(statement)] = (block, index)
    return blocks


def unsigned_gates(tree, location):
    """Every ``all(...)`` over signature results with no non-empty test."""
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    blocks = statement_blocks(tree)
    bound = bound_vectors(tree)
    for node in ast.walk(tree):
        if (not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name)
                or node.func.id != 'all' or len(node.args) != 1):
            continue
        argument = node.args[0]
        source = results_source(argument)
        if source is not None:
            vectors = {ast.unparse(argument), source}
        elif isinstance(argument, ast.Name) and argument.id in bound:
            vectors = {argument.id, bound[argument.id]}
        else:
            continue
        if condition_guard(parents.get(id(node)), node, vectors, parents):
            continue
        if statement_guard(node, vectors, parents, blocks):
            continue
        yield f'{location}{node.lineno}'


def string_constants(tree):
    names = {}
    for node in ast.walk(tree):
        value = getattr(node, 'value', None)
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for target in targets:
                if isinstance(target, ast.Name):
                    names[target.id] = value
    return names


def driver_sources(tree):
    """String constants executed as Python: ``exec``/``eval``/``compile``/``-c``."""
    names = string_constants(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = (function.attr if isinstance(function, ast.Attribute)
                else function.id if isinstance(function, ast.Name) else None)
        if name not in CALL_NAMES:
            continue
        candidates = []
        if name in ('exec', 'eval', 'compile') and node.args:
            candidates.append(node.args[0])
        for argument in node.args:
            if not isinstance(argument, (ast.List, ast.Tuple)):
                continue
            for index, item in enumerate(argument.elts[:-1]):
                if isinstance(item, ast.Constant) and item.value == '-c':
                    candidates.append(argument.elts[index + 1])
        for candidate in candidates:
            if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                yield candidate.value, candidate.lineno
            elif isinstance(candidate, ast.Name) and candidate.id in names:
                yield names[candidate.id].value, names[candidate.id].lineno


def unsigned_gates_in(path):
    text = path.read_text(encoding='utf-8')
    if 'verify_with_results' not in text:
        return
    tree = ast.parse(text, filename=str(path))
    yield from unsigned_gates(tree, '')
    for source, line in driver_sources(tree):
        try:
            driver = ast.parse(source)
        except SyntaxError:
            continue
        yield from unsigned_gates(driver, f'driver@{line}:')


class SignatureGuardTests(unittest.TestCase):
    def test_every_signature_check_rejects_an_unsigned_transaction(self):
        offenders = []
        for path in tracked_python():
            relative = str(path.relative_to(ROOT))
            if relative in DEMONSTRATIONS:
                continue
            offenders.extend(f'{relative}:{where}' for where in unsigned_gates_in(path))
        self.assertEqual(offenders, [], 'test the signature vector before all() of its results')

    def test_the_sweep_accepts_only_a_non_empty_test(self):
        def gates(source):
            return list(unsigned_gates(ast.parse(source), ''))
        self.assertEqual(gates('assert all(tx.verify_with_results())\n'), ['1'])
        self.assertEqual(gates('assert tx.signatures and all(tx.verify_with_results())\n'), [])
        self.assertEqual(gates('if not tx.signatures or not all(tx.verify_with_results()):\n'
                               '    raise ValueError\n'), [])
        self.assertEqual(gates('if tx.signatures or all(tx.verify_with_results()):\n'
                               '    pass\n'), ['1'])
        self.assertEqual(gates('checks = tx.verify_with_results()\n'
                               'assert not checks\n'
                               'assert all(checks)\n'), ['3'])

    def test_the_guard_is_what_makes_an_empty_vector_false(self):
        class Stripped:
            signatures = []
            def verify_with_results(self): return []
        stripped = Stripped()
        self.assertTrue(all(stripped.verify_with_results()))
        self.assertFalse(stripped.signatures and all(stripped.verify_with_results()))
        held = stripped.verify_with_results()
        self.assertTrue(all(held))
        self.assertFalse(held and all(held))


if __name__ == '__main__':
    unittest.main()
