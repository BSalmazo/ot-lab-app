# Liscere Scenario Pack for OT Lab

This directory contains the initial Liscere Modbus/TCP baseline scenario pack implemented on top of the existing OT Lab architecture.

The execution path remains the current OT Lab path:

```text
FUXA / API action
→ runtime monitor-proxy
→ OpenPLC
→ tshark-based protocol decoding
→ FastAPI backend
→ semantic policy decision
→ evidence export
```

This is not a complete benchmark suite. It is a reproducible baseline for evaluating:

```text
protocol operation × automation artefact × operational context × policy constraint → expected decision
```

The initial scenario set is intentionally small and aligned with the current Modbus/TCP process model and observed semantic policy already implemented in OT Lab.

## Included scenarios

- `SCN-MODBUS-SETPOINT-001`
- `SCN-MODBUS-SETPOINT-002`
- `SCN-MODBUS-CONFIG-001`
- `SCN-MODBUS-UNKNOWN-001`

## Scenario file format and runner selection rule

The canonical preferred format for runner execution is JSON.

- JSON scenario files are the canonical execution format used by the runner.
- YAML scenario files are kept as human-readable mirrors and documentation-friendly equivalents.
- When both JSON and YAML versions exist for the same logical scenario, the runner must de-duplicate by `scenario_id`.
- The deterministic selection rule is: prefer JSON over YAML/YML for the same `scenario_id`.

This keeps the execution path predictable while still preserving readable scenario definitions for inspection, publication, and discussion.

## Purpose

The pack provides a minimal, reproducible baseline for showing that OT Lab can:

1. execute protocol-valid Modbus/TCP actions through the monitored path;
2. observe the corresponding protocol operation with the existing tshark-based monitor;
3. relate the operation to a known or unknown automation artefact;
4. evaluate the action against the current semantic policy baseline; and
5. export structured evidence for later analysis.

## Current semantic baseline

The scenarios are aligned with the currently implemented observed-policy outcomes:

- `OBS-R000` -> `ALLOW`
- `OBS-R001` -> `ALERT`
- `OBS-R002` -> `ALERT`
- `OBS-R003` -> `ALERT`

No production enforcement is introduced here. This pack evaluates the current OT Lab baseline as implemented today.
