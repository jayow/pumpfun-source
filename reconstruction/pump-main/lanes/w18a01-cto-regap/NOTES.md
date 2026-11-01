# w18a01-cto-regap: curve store order and repay placement (admin_cto 0x22a10)

Base main 864c2b511: 0.69901, M 2,334. This change: 0.70021, M 2,338, 27,400 B, 1034 / 1034, zero losses.

- Line 754: the volatile curve box reload at the creator store is gone. The store now follows `[r1+0x20]` directly and the `old_fee` load
  and spill follow it, as the original at 0x4b08-0x4b50 (+2).
- Its 8 B moved to a marked holder_key byte read at the `can_edit_creator_fee` block, so jumps spanning P1 keep their displacement.
- Two of the ten fills at line 312 moved: one to the 289 error path after the error is built, one to the sweep native arm before
  `ensure_rent_exempt` (+1 each, jump displacements).

Oracle, tools and the priced lever table are on `origin/swarm/w18a01-cto-regap`. Ledger approach `regap`.
