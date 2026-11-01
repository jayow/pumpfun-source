#!/usr/bin/env python3
"""Deterministic execution-backed reduction for Admin CTO counterexamples."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from case_model import case_identity, validate_case  # noqa: E402

SCHEMA = "pump-admin-cto-counterexample-reduction-v1"
STEP_OPTIONALS = (
    "new", "new_none", "holder", "fee", "wrong", "readonly",
    "replace_accounts", "drop_accounts", "instruction_mutation",
)


def _leaf_count(value) -> int:
    if isinstance(value, dict):
        return sum(_leaf_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_leaf_count(item) for item in value)
    return 1


def _numeric_score(value) -> int:
    if isinstance(value, dict):
        return sum(_numeric_score(item) for item in value.values())
    if isinstance(value, list):
        return sum(_numeric_score(item) for item in value)
    return value if type(value) is int and value >= 0 else 0


def complexity(case: dict) -> dict:
    optional = sum(sum(key in step for key in STEP_OPTIONALS) for step in case["steps"])
    config = {key: value for key, value in case["config"].items() if key != "mints"}
    step_fields = [{key: value for key, value in step.items() if key not in ("name", "op")}
                   for step in case["steps"]]
    values = [config, case["initial_mutations"], step_fields]
    return {
        "modified_accounts": len({row.get("target") for row in case["initial_mutations"]}),
        "modified_fields": sum(_leaf_count(value) for value in values),
        "instruction_sequence_length": len(case["steps"]),
        "optional_arguments": optional,
        "numeric_score": sum(_numeric_score(value) for value in values),
    }


def complexity_tuple(case: dict) -> tuple[int, ...]:
    value = complexity(case)
    return tuple(value[key] for key in (
        "modified_accounts", "modified_fields", "instruction_sequence_length",
        "optional_arguments", "numeric_score",
    ))


def mismatch_property(result: dict) -> dict | None:
    """Return the exact observable divergence the reducer must retain."""
    if result.get("classification") != "different_behavior":
        return None
    pairs = []
    left = {row["step"]: row for row in result["original"]["receipts"]}
    right = {row["step"]: row for row in result["candidate"]["receipts"]}
    for comparison in result.get("comparison", []):
        if comparison["mismatch_fields"]:
            name = comparison["step"]
            pairs.append({
                "step": name,
                "fields": sorted(comparison["mismatch_fields"]),
                "original_outcome": left[name]["outcome"],
                "candidate_outcome": right[name]["outcome"],
            })
    top = sorted(field for field in result.get("mismatch_fields", []) if ":" not in field)
    return {"top_level_fields": top, "steps": pairs}


def _with_identity(case: dict) -> dict:
    changed = copy.deepcopy(case)
    changed["case_id"] = case_identity(changed)
    return validate_case(changed)


def _smaller_numbers(value: int):
    if type(value) is not int or value <= 0:
        return []
    return list(dict.fromkeys(item for item in (0, 1, value // 2) if item < value))


def immediate_reductions(case: dict) -> list[dict]:
    """Enumerate the stable, local reduction neighborhood for one case."""
    candidates = []

    def add(changed):
        try:
            value = _with_identity(changed)
        except (KeyError, TypeError, ValueError):
            return
        if complexity_tuple(value) < complexity_tuple(case):
            candidates.append(value)

    for index in range(len(case["initial_mutations"])):
        changed = copy.deepcopy(case)
        del changed["initial_mutations"][index]
        add(changed)
    if len(case["steps"]) > 1:
        for index in range(len(case["steps"])):
            changed = copy.deepcopy(case)
            del changed["steps"][index]
            add(changed)
    for index, step in enumerate(case["steps"]):
        for key in STEP_OPTIONALS:
            if key in step:
                changed = copy.deepcopy(case)
                del changed["steps"][index][key]
                add(changed)
        for key in ("readonly", "replace_accounts"):
            for item in range(len(step.get(key, []))):
                changed = copy.deepcopy(case)
                del changed["steps"][index][key][item]
                if not changed["steps"][index][key]:
                    del changed["steps"][index][key]
                add(changed)
        for key in ("new", "fee", "drop_accounts"):
            for smaller in _smaller_numbers(step.get(key)):
                changed = copy.deepcopy(case)
                changed["steps"][index][key] = smaller
                add(changed)
        mutation = step.get("instruction_mutation")
        if mutation and type(mutation.get("value")) is int:
            for smaller in _smaller_numbers(mutation["value"]):
                changed = copy.deepcopy(case)
                changed["steps"][index]["instruction_mutation"]["value"] = smaller
                add(changed)
    for index, mutation in enumerate(case["initial_mutations"]):
        for key in ("value", "offset"):
            for smaller in _smaller_numbers(mutation.get(key)):
                changed = copy.deepcopy(case)
                changed["initial_mutations"][index][key] = smaller
                add(changed)
    for key, value in case["config"].items():
        if key != "mints":
            changed = copy.deepcopy(case)
            del changed["config"][key]
            add(changed)
        for smaller in _smaller_numbers(value):
            changed = copy.deepcopy(case)
            changed["config"][key] = smaller
            add(changed)
    unique = {row["case_id"]: row for row in candidates}
    return [unique[key] for key in sorted(unique)]


def reduce_case(case: dict, evaluate, *, max_trials: int = 500) -> dict:
    """Greedily reduce a mismatch and audit its final local neighborhood."""
    if type(max_trials) is not int or max_trials < 1:
        raise ValueError("max_trials must be positive")
    original = validate_case(copy.deepcopy(case))
    first = evaluate(original)
    target = mismatch_property(first)
    if target is None:
        raise ValueError("initial case does not demonstrate different behavior")
    cache = {original["case_id"]: first}
    attempts, accepted, current = [], [], original

    def trial(candidate):
        if candidate["case_id"] in cache:
            return cache[candidate["case_id"]], False
        if len(cache) >= max_trials + 1:
            return None, False
        result = evaluate(candidate)
        cache[candidate["case_id"]] = result
        return result, True

    while True:
        changed = False
        for candidate in immediate_reductions(current):
            result, executed = trial(candidate)
            if result is None:
                break
            kept = mismatch_property(result) == target
            attempts.append({"case_id": candidate["case_id"], "executed": executed,
                             "preserved_property": kept,
                             "classification": result.get("classification")})
            if kept:
                accepted.append({"from": current["case_id"], "to": candidate["case_id"],
                                 "complexity": complexity(candidate)})
                current = candidate
                changed = True
                break
        if not changed:
            break

    unchecked, preserving = [], []
    for candidate in immediate_reductions(current):
        result, executed = trial(candidate)
        if result is None:
            unchecked.append(candidate["case_id"])
            continue
        kept = mismatch_property(result) == target
        attempts.append({"case_id": candidate["case_id"], "executed": executed,
                         "preserved_property": kept,
                         "classification": result.get("classification"), "audit": True})
        if kept:
            preserving.append(candidate["case_id"])
    one_minimal = not unchecked and not preserving
    return {
        "schema": SCHEMA,
        "original_case_id": original["case_id"],
        "reduced_case_id": current["case_id"],
        "mismatch_property": target,
        "original_complexity": complexity(original),
        "reduced_complexity": complexity(current),
        "executions": len(cache),
        "accepted_reductions": accepted,
        "attempts": attempts,
        "minimality": {
            "criterion": "one-minimal over immediate_reductions() neighborhood",
            "demonstrated": one_minimal,
            "unchecked_case_ids": unchecked,
            "preserving_case_ids": preserving,
        },
        "case": current,
        "result": cache[current["case_id"]],
    }
