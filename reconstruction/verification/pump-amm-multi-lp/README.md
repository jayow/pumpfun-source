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

The discovery audit was refreshed at upstream commit
`747aae62863ecb052069999be020a7f49943e6a0`.  Relevant existing coverage:

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

## Qualified lifecycle matrix

The retained qualification executed 65 signed transactions against each image.
The captured original had 62 successful and three deliberately rejected
transactions.  All nine cases were `equal_observed_behavior`; there were no
candidate mismatches, unsupported fixtures, or infrastructure failures.

| case | captured result established |
| --- | --- |
| `two-provider-independent-withdrawals` | B deposits into A's pool; A and B withdraw their own shares |
| `deposit-trade-independent-withdraw` | B deposit, real `buy_v2`, then A withdrawal at changed reserves |
| `atomic-two-provider-deposits` | A and B deposit in one transaction signed by both providers and the fee payer |
| `atomic-valid-deposit-invalid-withdraw-rollback` | the second instruction fails and every non-fee account rolls back |
| `foreign-lp-authority-refused-then-owner-withdraws` | B cannot burn A's LP ATA; B can still withdraw its own shares |
| `over-withdraw-refused-then-partial-chain` | over-withdraw fails; two later partial withdrawals succeed |
| `tiny-deposits-and-rounding` | one- and two-unit LP deposits and one-unit withdrawals |
| `token2022-base-multi-provider` | Token-2022 base mint with classic wrapped-SOL quote and Token-2022 LP mint |
| `two-pool-provider-state-isolation` | operations on pool 2 leave pool 1, its mint, vaults, and provider positions unchanged |

On every established pool state, the verifier observes
`pool.lp_supply - lp_mint.supply == 100` and the two provider LP balances sum
to the LP mint supply.  It recomputes base/quote/reserve deltas from serialized
account bytes.  The trade case includes the buyback recipient when checking
quote-token conservation.  Failed transactions must preserve all watched
non-fee state exactly.

The independent verifier decodes the transaction rather than trusting receipt
signer lists, runs Ed25519 verification, checks unique transaction identities,
re-derives all pool/LP/ATA addresses, reconstructs the complete plan, decodes
account bytes, and recomputes classifications.  The tests prove refusal of
signature substitution, LP-supply and balance corruption, wrong reserves or
owners, missing/duplicate transactions, altered events or errors, forged case
counts, changed source/executable identities, and corrupt account encodings.

## Reproduce

Install the repository requirements in an isolated environment, then build
the current source.  The builder refuses an existing output directory, uses
at most two Cargo jobs, checks the 20 GiB free-space floor, and records source
identity before and after compilation.

```sh
python -m pip install -r requirements.txt
python reconstruction/verification/pump-amm-multi-lp/build_candidate.py \
  --output /tmp/pump-amm-multi-lp-build
python reconstruction/verification/pump-amm-multi-lp/lifecycle.py \
  --candidate /tmp/pump-amm-multi-lp-build/artifacts/pump_amm_recovered.so \
  --build-receipt /tmp/pump-amm-multi-lp-build/build-receipt.json \
  --output /tmp/pump-amm-multi-lp-report.json.gz
python reconstruction/verification/pump-amm-multi-lp/verify.py \
  --candidate /tmp/pump-amm-multi-lp-build/artifacts/pump_amm_recovered.so \
  --build-receipt /tmp/pump-amm-multi-lp-build/build-receipt.json \
  --report /tmp/pump-amm-multi-lp-report.json.gz --replay
python -m unittest discover \
  -s reconstruction/verification/pump-amm-multi-lp -p 'test_*.py' -v
```

Output publication is exclusive: an existing report is never replaced.  The
compressed report retains raw logs and full watched account states; it is
bounded to the fixed nine-case corpus.

## Qualification and limitation

The retained report is `qualification/report.json.gz` (SHA-256
`bd7f81de43c5c7c1461f23791b04dde1a757fa024570ffc1e3c9e7c7b30365a3`).
It binds source manifest
`da44d85497928a0e21d69c4162dfd6ad4d2caee1047bf9574073db4bb67addf1`
to candidate
`ebfac4444b969f9114e174acf485907fa541e1f862bc709d18b6d22407c711ed`
and the original
`6a7ad34596d83b4c435b32f458a56e718e9f82eb4ad38e4316616d15d24d8ff7`.

The ordinary pinned v1.44 `cargo build-sbf` artifact is not the repository's
specialized layout-closure artifact: its receipt honestly records
`exact_checkpoint_match: false`.  The separately maintained PumpSwap checkpoint
remains 785/785 raw-equal functions with meaningful-ELF SHA-256
`51a5f6a7fb5a812de7686e593ce6159f6ee59b15771a3d5d37ab661c46d64586`.
This package qualifies behavior of the current source build on the bounded
lifecycle corpus; it neither replaces that checkpoint nor claims finite tests
prove broad semantic equivalence.
