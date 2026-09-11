# Scenarios

A scenario labels supervisory writes in a recorded run with the verdict a domain expert expects, so
`liscere-metrics` can count false positives and false negatives. Files are TOML (read with the
Python standard library; no extra package on the Pi).

```
uv run liscere-metrics --run runs/<run_id> --scenario scenarios/SCN-BENCH-VALVEB-001.toml --out metrics.json
```

## Format

```toml
id = "SCN-BENCH-VALVEB-001"
description = "ValveB=40 during heating is INCOHERENT; during add-B it is COHERENT"
capture = "sha256:<hash from experiments/captures/MANIFEST.json>"
bench = "S7-1200 G2 five-phase batch, WinCC Unified, 7 tags over OPC UA"

[[labels]]
target = "ns=4;i=<ValveB node>"   # the target string as the verdict carries it
value = 40.0                      # optional: omit to match any value
rel_from = 420.0                  # seconds after the run's first frame (or absolute: from / to)
rel_to = 480.0
expected = "INCOHERENT"
note = "heating phase"
```

Counting (decided in MODERNISATION_PLAN item 7): the full expected-by-observed table is always
published; the headline uses the strict rule (only INCOHERENT is an alarm); the UNCERTAIN
abstention rate is reported explicitly; the alarm-inclusive and abstention-excluded rules are
shown as alternatives derived from the same table.

`TEMPLATE.toml` is the bench scenario with the fields still to be confirmed on the bench
(the ValveB node id and the phase windows of a recorded cycle).
