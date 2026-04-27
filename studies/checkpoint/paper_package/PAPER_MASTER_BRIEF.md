# OT Lab App - Paper Master Brief

## Study Goal

Validate an OT monitoring stack for Modbus/TCP that performs end-to-end:

- traffic capture,
- protocol parsing,
- interface attribution,
- connection-state tracking,
- remote action execution,
- exception handling,
- and stability under sustained load.

## System Under Test

- Web backend: FastAPI (`app.py`)
- Agent: Scapy-based monitor (`agent/main.py`, `agent/sniffer.py`, `agent/modbus_parser.py`)
- Frontend dashboard: HTML/JS (`templates/index.html`, `static/app.js`)
- Validation toolkit: `studies/checkpoint/*`

## Final Validated Campaign

- Run ID: `checkpoint_20260331_182606`
- Session ID: `sess_92c7874bfe77418daa4e3e200ac629f4`
- Poll interval: `0.5s`
- Effective duration: `2158.912s`
- Evaluator verdict: `CHECKPOINT APROVADO`

## Final CT Outcomes

- CT1 Basic detection: PASS
- CT2 Interface correctness: PASS (`en0` + `lo0` observed)
- CT3 Write detection: PASS
- CT4 Exception detection: PASS
- CT5 Active/Inactive transition: PASS
- CT6 Remote action lifecycle: PASS
- CT7 Stability under sustained load: PASS

## Core Quantitative Evidence (Approved Run)

- Event snapshots: `2786` (100% HTTP-OK)
- Interface counts: `lo0=2070`, `en0=716`
- State counts: `Inactive=2380`, `Active=406`
- Event type counts:
  - `READ_REQUEST=246461`
  - `READ_RESPONSE=241187`
  - `WRITE_REQUEST=892`
  - `EXCEPTION_RESPONSE=346`
- Command lifecycle (latest): all `done` (`3/3`)

## Main Scientific Claim Supported by Data

The tool reaches functional validation for Modbus/TCP monitoring in a realistic mixed-interface scenario (loopback + physical NIC) with complete acceptance criteria (CT1..CT7) satisfied in the final campaign.

## Files to cite for evidence

- `studies/checkpoint/evidence/checkpoint_20260331_182606/REPORT.md`
- `studies/checkpoint/evidence/checkpoint_20260331_182606/checkpoint_result.json`
- `studies/checkpoint/evidence/checkpoint_20260331_182606/derived_metrics.json`
- `studies/checkpoint/CHECKPOINT_SUPER_DETALHADO.md`
