# Configuration

`liscere-observe --config liscere.toml`. Default: `/etc/liscere/liscere.toml`, then `./liscere.toml`,
when one exists; otherwise nothing is declared and every run behaves as discovered.

```toml
--8<-- "deploy/pi/liscere.toml"
```

Excluded flows are removed from the state-signal candidates but still reported as
`variable_found` with `nature: EXCLUDED`, so the exclusion is visible in every run. A pinned
`state_signal` replaces the most-distinct-values rule; a pin that names a flow not observed makes
the silo non-evaluable with a reason, never a silent fallback. The configuration used is recorded
in `run.json` under `config`.

Windows and the interface are arguments, not configuration: on the Pi they live in
`/etc/liscere/observe.env`.
