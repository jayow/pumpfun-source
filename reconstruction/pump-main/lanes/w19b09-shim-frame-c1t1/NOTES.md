# w19b09-shim-frame-c1t1: frame-slot naming outside validate (admin_cto 0x22a10, shim track)

Target `0x22a10` `admin_cto::execute`, `programs/pump/src/creator/admin_cto.rs`, pinned at 27,400 B. Ledger approach `framec1t1`.

## Gain 1: wallet event signer array at the original's -0x680

| | admin_cto | M | slots | bytes | normalized / raw | losses |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| before (main d493b88f4) | 0.69662 | 2,326 | 3,221 | 27,400 | 1034 / 1034 | |
| after | 0.69751 | 2,329 | 3,221 | 27,400 | 1034 / 1034 | none |

Window W2 (original 0x66f8, 0x6740, 0x6760): `event_signers.i` sits at -0x540 in FI53, the original has it at -0x680.
The original's -0x680 lies in the 40 B tail of main's `hkx` slot. The array is now written there through the `hkx` base
pointer, passed to `wallet`, which inlines. A 16 B `MaybeUninit` with `llvm.var.annotation` stays in `event_signers`'
place, so FI53 still hosts validate's 7 B target piece and no other offset moves. Calls stay 154 / 3. The source capture
has the llc probe's exact signature, so only the three tokens change.
