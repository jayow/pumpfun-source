# Donation Relay multi-donor stateful differential

## Coverage audit

The merged `donation-relay-stateful` fixture owns one donor keypair and one
source token account. Its repeated donation case therefore exercises shared
state only through one authority. `donation-relay-xtx-sequences` reuses that
fixture: its two- and three-instruction transactions repeat the same donor and
the same source account. Neither package establishes isolation or conservation
across independently signed donor accounts, configurations, or mints.

This package fills that boundary without modifying either predecessor. It uses
three deterministic test-owned Ed25519 donors, a distinct fee payer, canonical
SPL token accounts, and canonical epoch/debouncer/destination PDAs. Each donor
appearing in a transaction supplies its own real signature. Signature and
blockhash checks remain enabled in two independent persistent LiteSVMs.

The production relayer key is not retained. Successful whitelist mutation,
epoch closure, distribution, and swap settlement remain unsupported fixtures;
only a genuine non-production signer rejection is exercised. No signer bit or
PDA signature is fabricated.

## Qualified lifecycle inventory

All cases use fresh transaction identities and preserve each VM between the
listed steps. A failed step is followed by a full watched-account rollback
check before the next step executes.

| Case | Persistent sequence | Captured-program result |
| --- | --- | --- |
| `same_epoch_a_then_b` | A deposits, then B deposits | both succeed |
| `interleaved_a_b_a` | A, B, then A again | all succeed |
| `three_donors_one_transaction` | A + B + C atomically | succeeds with three donor signatures |
| `a_success_b_fail_c_success` | A, invalid B, C | success / `6009` / success |
| `atomic_rollback_invalid_third` | A + B + invalid C atomically | `6009`; all three changes roll back |
| `different_balance_boundaries` | exact, overdrawn, zero | success / token `1` / success |
| `authority_substitution` | A signs for B's source, then B retries | `2015` / success |
| `separate_configurations` | A/config A, B/config B | both succeed; PDA state remains isolated |
| `separate_mints` | A/mint X, B/mint Y | both succeed; supply domains remain isolated |
| `zero_and_u64_boundary` | zero, `u64::MAX`, one | success / token `1` / success |
| `rejected_close_between_donors` | A, outsider close, B | success / `2012` / success |
| `failed_donor_retry` | A, overdrawn B, fresh B retry | success / token `1` / success |

The qualified run contains 12 equal cases, 28 unique signed transactions, 60
individual signatures, and 11 cases with at least one original-positive
transaction. It found zero strict original/candidate mismatches. The production
relayer-only closure/distribution and Jupiter settlement families are recorded
as unsupported scope boundaries and do not increase the passing count.

## Evidence and invariants

For every step the producer retains the signed transaction and message bytes,
exact errors, non-compute logs, events, return data, inner instructions, fee,
and complete before/after snapshots of watched accounts. The independent
verifier decodes the signed transaction again and validates its signatures,
compiled instructions, donor/source ownership, canonical PDAs and ATAs,
blockhash uniqueness, and sequence continuity.

Accounting is recomputed independently for each donor and domain. Checks cover
source and destination token deltas, debouncer aggregates, epoch state, rent,
payer fees, per-mint conservation, untouched-domain isolation, full failed-step
rollback, and final state. Seven negative controls corrupt a signature, token
balance, debouncer aggregate, event, outcome, owner, and case count; all seven
must be rejected.

Qualified immutable inputs:

- audit/build base: `aab087f32d0ec96c723a4628582cfc2410bb2cc5`
- final upstream synchronization: `5e05222b54c4549cbec293160bb5fc65b4921792`
- captured Relay ELF: `367f07a62e3f74d358187d175cf3228848e81a56c781a9a75b9df7fed1268563`
- captured SPL Token ELF: `8190d3f7ceb6cb7a7a8d8924bff89f9f611e15ce1f806f2b6237f3311a98f697`
- captured ATA ELF: `6804554e69fd3a58caa191dc4a58f4c67223d30ca28ab8987f39fc18d2f7374d`
- source-built candidate: `810df19bdae4af7540a844f8873bf09196cb5e42a867b77e3e007f3f1363dd92`
- fixture identity: `eb0ee9a92bc2959245b2bbf8fafb1a4774478407c8f79cac407f15e641c53dc2`

## Reproduction

The builder uses the local pinned macOS arm64 platform-tools v1.44, the locked
dependency graph, and an isolated target directory. If the pinned Cargo cache
has not yet been populated, fetch that exact lockfile once with the same Cargo
binary; the authenticated build itself remains offline.

```sh
sdk="$HOME/.cache/solana/v1.44/platform-tools"
out="$HOME/.cache/pumpfun-reconstruction/donation-relay-multidonor/repro"

"$sdk/rust/bin/cargo" fetch --locked \
  --manifest-path programs/donation-relay/Cargo.toml
python -B reconstruction/verification/donation-relay-multidonor/build_candidate.py \
  --root "$PWD" --sdk "$sdk" --output "$out/build"

uv run --offline --python 3.12 --with solders==0.27.1 python -B \
  reconstruction/verification/donation-relay-multidonor/producer.py \
  --root "$PWD" --candidate "$out/build/donation_relay_candidate.so" \
  --build-receipt "$out/build/build-receipt.json" \
  --output "$out/report.json"
uv run --offline --python 3.12 --with solders==0.27.1 python -B \
  reconstruction/verification/donation-relay-multidonor/verify.py \
  --root "$PWD" --candidate "$out/build/donation_relay_candidate.so" \
  --report "$out/report.json"
uv run --offline --python 3.12 --with solders==0.27.1 python -B \
  reconstruction/verification/donation-relay-multidonor/controls.py \
  --root "$PWD" --candidate "$out/build/donation_relay_candidate.so" \
  --report "$out/report.json" --output "$out/negative-controls.json"

uv run --offline --python 3.12 --with solders==0.27.1 python -B \
  -m unittest discover \
  -s reconstruction/verification/donation-relay-multidonor -p 'test_*.py'
```

Output publication is fail-closed: existing report, control, or build paths are
never replaced. Failed/interrupted build staging directories are retained for
recovery. No RPC or Solana cluster is contacted during execution.
