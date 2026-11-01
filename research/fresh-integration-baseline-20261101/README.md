# Fresh integration baseline regression

`integrate.py` historically built only the composed candidate and compared it
with the checked-in historical score. That is useful for exploration, but it
cannot prove preservation of a match added by current upstream after that saved
score was recorded.

This packet retains the smallest witness. The historical score and composed
candidate both miss window `0x100`, so the historical comparison reports no
loss and appears acceptable. Current upstream's base matches that same window
both normalized and raw. Comparing fresh base and candidate builds therefore
reports both losses and rejects the composition.

The fixture does not claim a real Pump score or modify the scorer. It is a
synthetic membership fixture admitted by the maintained comparison rules. The
executable regression test additionally proves that historical `integrate.py`
invokes exactly one candidate build. The new fresh-control tests prove that the
paired path invokes isolated base and candidate builds and refuses these losses.

Base revision: `638fce2a34c8433177f5b778f39cf79e70c971d4`.

Reproduce the byte-identical result and verify its semantic claims:

```sh
python -B research/fresh-integration-baseline-20261101/reproduce.py \
  > /tmp/fresh-integration-results.json
cmp research/fresh-integration-baseline-20261101/results.json \
  /tmp/fresh-integration-results.json
python -B research/fresh-integration-baseline-20261101/verify.py
python -B reconstruction/harness/test_integrate_fresh_regression.py
```
