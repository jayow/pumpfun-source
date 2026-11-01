# PumpSwap multi-provider LP lifecycle

This package closes a specific runtime-verification gap: two distinct LP
authorities operate independently on one persistent PumpSwap pool.  Existing
pool-creation and liquidity packets exercise one signed liquidity owner per
fixture, and the retained multi-hop liquidity packets reuse one actor.  They
do not establish that LP shares, withdrawals, rollback, and authority checks
remain isolated across two providers over several transactions.

The producer uses local LiteSVM instances only.  It creates provider token
accounts through the captured Associated Token program, funds them through
the captured token program under a deterministic mint authority, and invokes
the captured PumpSwap `create_pool` instruction.  The pool PDA is never a
transaction signer; PumpSwap signs its own CPI calls with canonical seeds.
Provider A, provider B, and the fee payer are separate deterministic Ed25519
keypairs.

The same serialized, fully signed transactions are submitted to independent
original and candidate VMs.  Reports retain transaction bytes, signatures,
message/blockhash hashes, errors, events, inner instructions, return data,
compute units, and complete watched account snapshots.  `verify.py` derives
case membership, transaction identities, signatures, accounting, rollback,
and the differential result without trusting producer acceptance fields.

## Coverage boundary

The package is intentionally narrower than a claim of PumpSwap equivalence.
It covers only the listed deterministic lifecycle plans and their observed
captured-program outcomes.  An unsupported fixture or infrastructure failure
is never counted as an equivalent case.  PumpSwap's existing exact
meaningful-ELF checkpoint is independent of this runtime evidence; this
package does not change its bytecode completion percentage.

The discovery audit was performed at upstream commit
`2b9914e2ae2d1342ab135a9860c68c1ca9b42084`.  Relevant existing coverage:

- `scripts/pump_amm_create_differential.py`: real pool/LP/vault creation, one
  creator per reset fixture.
- `scripts/pump_amm_liquidity_differential.py`: one deposit or withdrawal per
  reset fixture with one synthetic LP owner.
- `research/amm-multi-hop-fee-table-split-liquidity-chains-20261029/`:
  persistent liquidity/trade chains, but one liquidity actor.
- `research/pump-amm-create-pool-initial-liquidity-20261030/` and
  `research/pump-amm-liquidity-rounding-boundaries-20261030/`: captured
  positive creation and arithmetic boundaries, still one LP authority.

No open upstream PR at the audit commit implemented a multi-provider LP
lifecycle package.

