# Qualification record — seed 20261101

The final bounded campaign was produced from implementation commit
`d84ebbe0cca77febd97187215cd6924b39053756` on upstream base
`53ec7fb538c1bce0571eab731ab925d8530c40fa`.

Program identities:

- captured original: `7a19affaa2c6187e04a5fe0ea298cee81fa5eb8c24cfcc30d85f615e89f7b02d`;
- fresh identity candidate: `6f439f1e539ec8156aba443e9a464eedee0996c88720b3eae789e148e4fef7be`;
- matching unstripped image: `54344822ddef0bd4b1f7b4e8a6c80e5aae7e7fb804c5c82c67e7ebff369e9229`.

The candidate was built after the final rebase with the identity recipe and two
compiler jobs. Its source manifest is
`124807d11a9266f52253c2e4a369a50d6ded4f1a7e097be0befed9889d7e5374`;
the ordered linker script is
`6954d9901b005312be6e7f980cb5e74a1440c174547c2040a881acf2e5c25b45`;
the pinned `rust-lld` is
`a4d765c822f68b267e87765771da87a0fd4d640d9df27884bd10c0750d1b58f4`.
The build completed with runtime validation and no stack warnings. The campaign
independently bound the exact candidate bytes rather than trusting its path.

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
`bc8772891e6548ea4cc5aa4667d95aa883ef0404eaa7203672606ea5e1a2561a`.

Candidate summary SHA-256:
`2c4c5c67f54c8028a9d92886dd51ee8881900edc541d1e13b3eee24106340fa9`.

The independent verifier loaded every receipt and re-executed all 64 cases in
each report. Both verifications returned zero failures and zero warnings. The 8
unsupported cases were excluded from equivalence counts. No candidate
counterexample was found, so no on-chain vulnerability or broad semantic
equivalence is claimed, and the automatic reducer was qualified through its
deterministic positive/negative regression controls rather than a fabricated
program mismatch.

The maintained 388-case single-transaction differential also reported no
mismatches for this exact candidate. The nine-scenario stateful suite executed
20 transactions per image with no behavioral, oracle, invariant, or
infrastructure failures; its independent deterministic replay verified. The new
package passed 32 tests, the stateful package passed 10 tests, and the optimized
Python admission package passed 3 tests.

The retained local reports are:

- `~/.cache/pumpfun-reconstruction/daybreak-admin-cto-fuzzer/main-53ec7f-original-control-seed-20261101-n64`;
- `~/.cache/pumpfun-reconstruction/daybreak-admin-cto-fuzzer/main-53ec7f-candidate-seed-20261101-n64`.

They are intentionally not committed: each contains compressed full account and
transaction receipts, while this record remains compact and reproducible.
