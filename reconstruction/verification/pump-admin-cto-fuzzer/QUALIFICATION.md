# Qualification record — seed 20261101

The final bounded campaign was produced from implementation commit
`ebe41720ae5176bcbe4493e67ae17c0ebe0df04d` on upstream base
`638fce2a3470d2505522f7e73e416d56daef86c5`.

Program identities:

- captured original: `7a19affaa2c6187e04a5fe0ea298cee81fa5eb8c24cfcc30d85f615e89f7b02d`;
- retained provenance-bound candidate: `7251af18ef95ea907f5f6e2ec1bee80768a5671c672713bc902ed98e295a29ad`.

The candidate was accompanied by `pump-source-inputs-v3`, ordered-linker, pinned
`rust-lld`, and source/link receipts. The campaign independently bound the exact
candidate bytes rather than trusting the path.

## Results

Both final reports used root seed `20261101`, indices 0–63, and the same case
manifest.

| Measure | Original vs original | Original vs candidate |
|---|---:|---:|
| Generated cases | 64 | 64 |
| `equal_observed_behavior` | 56 | 56 |
| `different_behavior` | 0 | 0 |
| `unsupported_fixture` | 8 | 8 |
| `infrastructure_failure` | 0 | 0 |
| Transactions per image | 103 | 103 |
| All-success original sequences | 4 | 4 |
| Raw-log/compute diagnostic cases | 0 | 22 |
| Negative controls detected | 3/3 | 3/3 |
| Reduced counterexamples | 0 | 0 |

Control summary SHA-256:
`17bd46d0ea291516151b57e5afa07cc64a2657f20bcaac2494486ef77ccabe46`.

Candidate summary SHA-256:
`551a65829ff355522156f39c48d25473f3807b04c61a00c055495712255aa26d`.

The independent verifier loaded every receipt and re-executed all 64 cases in
each report. Both verifications returned zero failures and zero warnings. The 8
unsupported cases were excluded from equivalence counts. No candidate
counterexample was found, so no on-chain vulnerability or broad semantic
equivalence is claimed, and the automatic reducer was qualified through its
deterministic positive/negative regression controls rather than a fabricated
program mismatch.

The retained local reports are:

- `~/.cache/pumpfun-reconstruction/daybreak-admin-cto-fuzzer/final-original-control-seed-20261101-n64`;
- `~/.cache/pumpfun-reconstruction/daybreak-admin-cto-fuzzer/final-candidate-seed-20261101-n64`.

They are intentionally not committed: each contains compressed full account and
transaction receipts, while this record remains compact and reproducible.
