#!/usr/bin/env python3
"""Independently verify the retained stale-baseline correction receipt."""
from __future__ import annotations

import json
from pathlib import Path

from reproduce import canonical, reproduce


HERE = Path(__file__).resolve().parent


def main() -> int:
    expected = canonical(reproduce()).encode()
    retained = (HERE / "results.json").read_bytes()
    if retained != expected:
        raise SystemExit("results.json does not match a fresh recomputation")
    result = json.loads(retained)
    historical = result["historical_control"]
    fresh = result["fresh_control"]
    if not historical["accepted"] or historical["comparison"]["normalized_losses"]:
        raise SystemExit("historical witness no longer demonstrates the gap")
    if fresh["accepted"] or fresh["decision"] != "reject":
        raise SystemExit("fresh control did not reject the regression")
    if fresh["comparison"]["normalized_losses"] != ["0x100"]:
        raise SystemExit("fresh control did not identify normalized loss 0x100")
    if fresh["comparison"]["raw_losses"] != ["0x100"]:
        raise SystemExit("fresh control did not identify raw loss 0x100")
    print("verified fresh-integration stale-baseline correction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
