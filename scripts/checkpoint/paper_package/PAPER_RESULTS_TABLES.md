# Results Tables (Approved Campaign)

Run: `checkpoint_20260331_182606`

## Table 1 - Acceptance Criteria Outcomes (CT1..CT7)

| Test Case | Status | Observed Evidence |
|---|---|---|
| CT1 Basic detection | PASS | Transition observed in `0.879s` (threshold `<= 3.0s`) |
| CT2 Interface correctness | PASS | Interfaces observed: `en0`, `lo0` |
| CT3 Write detection | PASS | `writes_detected=true` and WRITE events observed |
| CT4 Exception detection | PASS | `EXCEPTION_RESPONSE` observed in events/alerts |
| CT5 State transition | PASS | Active->Inactive observed in `2.201s` |
| CT6 Remote actions | PASS | Final status `done`, duration `0.335s` |
| CT7 Stability | PASS | `events_http_ok_ratio=1.0000`, `duration_actual_s=2158.912` |

## Table 2 - Campaign Metadata

| Metric | Value |
|---|---|
| Session ID | `sess_92c7874bfe77418daa4e3e200ac629f4` |
| Base URL | `http://127.0.0.1:8000` |
| Duration (s) | `2158.912` |
| Poll interval (s) | `0.5` |
| Snapshot samples | `2472` |
| Events HTTP OK ratio | `1.0` |

## Table 3 - Observability and Throughput

| Metric | Value |
|---|---|
| Event snapshots total | `2786` |
| Event snapshots HTTP-OK | `2786` |
| Status samples total | `2789` |
| Command samples total | `2786` |
| Max events window size | `300` |
| Max connection history window size | `60` |

## Table 4 - Interface and State Distribution

| Distribution | Counts |
|---|---|
| Interface counts | `lo0=2070`, `en0=716` |
| State counts | `Inactive=2380`, `Active=406` |
| Summary detected counts | `True=2786` |

## Table 5 - Event and Alert Composition

| Type | Count |
|---|---:|
| READ_REQUEST | 246,461 |
| READ_RESPONSE | 241,187 |
| WRITE_REQUEST | 892 |
| EXCEPTION_RESPONSE | 346 |

Alert types:

| Alert Type | Count |
|---|---:|
| WRITE_REQUEST | 892 |

## Table 6 - Command Outcome Distribution (Latest History Snapshot)

| Category | Counts |
|---|---|
| Command status | `done=3` |
| Function distribution | `fc06_write_single_register=2`, `fc05_write_single_coil=1` |

## Interpretation Notes

- The approved campaign demonstrates complete CT coverage with no failed acceptance criteria.
- Multi-interface validation was completed (`lo0` + `en0`).
- Exception handling was observed and passed acceptance checks.
