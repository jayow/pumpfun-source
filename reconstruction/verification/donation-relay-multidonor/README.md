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

The qualified report, case inventory, hashes, and reproduction commands are
added after original-positive and source-built-candidate execution.
