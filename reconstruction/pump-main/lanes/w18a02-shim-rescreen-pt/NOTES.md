# w18a02-shim-rescreen-pt: zero-instruction cell re-screen on the outline main

Target `0x22a10` `admin_cto::execute`, pinned at 27,400 B. Ledger: approach `rescreen-pt`, keys `0x22a10-w18-*-rescreen-pt-*`.

Slim pre-opt oracle (w17a06 capraw/preslim/fx) on main 5de7f22de (M 2,322, sig 51c18f636169). Size-neutral combination of
screened cells; every source edit reproduces its IR cell's signature.

| cell | source | dM | dB |
| --- | --- | ---: | ---: |
| kn:wallet:30904 | wallet owner test written as a plain `!=` (drops the volatile compare-in-memory shim) | +5 | -32 |
| kn:validate:28089 | holder-arm `new.is_some()` without the passthrough (shim peeled) | +1 | -8 |
| vl:sweep:33023 | sweep token `amount` read through `read_volatile` | +2 | +16 |
| pt:execute:29823 | passthrough on the accounts slot at `pf=` (`transmute`, a raw cast loses `readonly` on `ctx`) | 0 | +24 |
