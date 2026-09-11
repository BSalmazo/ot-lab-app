# Run record and events

Every run writes `<runs-dir>/<run_id>/` (default `./runs`, `/var/lib/liscere/runs` on the Pi). The
run id is `<UTC timestamp>Z-<8 hex>`.

| File | Content |
|---|---|
| `run.json` | Manifest: `run_id`, `version`, `args`, `source` (`kind` live, pcap or replay, plus `iface` or `path`), `clock` (wall or frame), `python`, `tshark`, `config` (path, exclude_flows, state_signal), `stabilisation_parameters` (k, epsilon), `human_hours` (fill in by hand), `started_at`, `observe_ended_at`, `learn_ended_at`, `ended_at`, `exit_code`, `verdicts`, and per silo under `silos`: `protocol`, `evaluable`, `reason`, `state_key`, `observe_events`, `calibration`, `grammar`, `stabilisation`, `cycles`. |
| `events.jsonl` | The full event stream, byte-identical to stdout. |
| `verdicts.jsonl` | One record per verdict: the `verdict` event plus `run_id` and `seq`. |
| `capture/` | `probe.tsv`, `claimed.json`, `<layer>.tsv`: the raw tshark field lines consumed, for exact replay. |
| `silos/<endpoint>/` | `profile.json` (calibrated phase config) and `grammar.json` (the learn document), for `--resume-from`. |

## Event reference

Every event carries `type`, `ts` (wall time live, frame time in replay) and, for per-silo events,
`silo` (the server endpoint).

| type | Fields | Meaning |
|---|---|---|
| `stage` | `stage` (observe, learn, evaluate), `seconds` | Pipeline stage transition. |
| `protocol_seen` | `protocol`, `port` | An extractor was started for a claimed layer. |
| `silo` | `endpoint`, `evaluable`, `reason`, `protocol`, `kind` (unreadable, unevaluable, not_run), `peer` | A (protocol, server endpoint) unit and whether it can be judged. |
| `capture` | `protocol`, `state` (lost, resumed, permanently_lost), `down_s`, `attempt`, `tries` | Capture availability of one protocol. |
| `flow_found` | `key`, `role_hint`, `endpoint` | A flow the extractor grouped in the observe window. |
| `variable_found` | `key`, `nature` (STATE, COMMAND, CONSTANT_METADATA, AMBIGUOUS, EXCLUDED), `datatype`, `datatype_certain`, `features` (unique_values, value_range, median_step, reversals), `late`, `excluded` | A classified variable; a write later re-announces a non-command key as COMMAND with `late: true`. |
| `state_signal_discovered` | `key` | The flow chosen (or pinned) as the process state signal. |
| `phase` | `state_key`, `phase`, `confidence`, `level`, `frame_ts` | Inferred phase changed. |
| `variable_value` | `key`, `value`, `frame_ts` | Current value of a variable (de-duplicated). |
| `grammar_learned` | `target`, `phase` | A write was learned into the grammar. |
| `learning_progress` | `cycle`, `t`, `targets`, `writes_observed`, `max_fraction_change`, `coherent_set_changed`, `stable_cycles`, `stable`, `k`, `epsilon` | Grammar snapshot at a completed learn cycle. |
| `verdict` | `target`, `phase`, `result`, `rule`, `value`, `datatype`, `datatype_certain`, `client`, `frame_ts`, `confidence`, `transitioning`, `learned_fraction`, `reason` | A judged write with its evidence. |

## Verdict record

```json
{"run_id": "20260911T140000Z-1a2b3c4d", "seq": 7, "type": "verdict", "silo": "10.0.0.5:4840",
 "target": "ns=4;i=45", "value": 40.0, "datatype": "Float", "datatype_certain": true,
 "client": "10.0.0.9:48000", "frame_ts": 1789132000.123, "phase": "STABLE", "confidence": 1.0,
 "transitioning": false, "result": "INCOHERENT", "rule": "OBS-R009", "learned_fraction": 0.0,
 "reason": "write to ns=4;i=45 in phase STABLE was never observed during learning for this target; incoherent with learned grammar",
 "ts": 1789132000.123}
```

Results: COHERENT, UNUSUAL, INCOHERENT, UNCERTAIN. Rules: OBS-R005 phase unsettled, OBS-R006
target never seen, OBS-R007 coherent, OBS-R008 rare, OBS-R009 incoherent.
