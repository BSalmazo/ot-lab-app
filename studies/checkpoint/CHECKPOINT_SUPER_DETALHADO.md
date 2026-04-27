# OT Lab App - Super Detailed Technical Checkpoint Report

## 1. Document Purpose

This document consolidates the current OT Lab App checkpoint at a paper-ready technical level:

- what was implemented,
- how the system is structured (file-by-file),
- how validation was executed,
- expected vs observed outcomes,
- limitations and interpretation,
- and recommended next steps.

The goal is to let an external reader (including another AI model) understand both the tool and its validation process without extra context.

---

## 2. Checkpoint Context

### 2.1. Relevant code state (recent commits)

Key commits applied before/during this checkpoint:

- `388709b` - UI: remove "Recent Executions" header and show real detected interface
- `506830f` - add checkpoint toolkit (`studies/checkpoint/*`)
- `cb582c4` - cleanup: remove legacy unused files + add `.gitignore`
- `28866fa` - cache-bust `ModbusTab.js` in `templates/index.html`

### 2.2. Legacy cleanup

Removed files (not used by the active runtime path):

- `agent_template.py`
- `live_monitor.py`
- `modbus_client.py`
- `modbus_server.py`
- `process_sim.py`

Expected impact: reduced maintenance noise without changing active functionality.

---

## 3. Current Architecture (File-by-File)

## 3.1. Web backend (FastAPI)

- `app.py`
  - Core backend service.
  - UI and API endpoints.
  - Session handling via cookie (`scada_session_id`).
  - Per-session state: `events`, `alerts`, `modbus_summary`, `connection_history`, `action_commands`.
  - Queues commands for agent (`/api/agent/commands`) and ingests telemetry (`/api/agent/event`, `/api/agent/alert`, `/api/agent/snapshot`, etc.).
  - Computes activity state with `MODBUS_ACTIVE_WINDOW_SECONDS = 2.0`.

- `Procfile`
  - Deployment entrypoint (`uvicorn app:app`).

- `requirements.txt`
  - Runtime dependencies for backend/agent local execution.

- `runtime.txt`
  - Runtime definition for deployment environment.

## 3.2. OT monitoring agent

- `agent.py`
  - Agent bootstrap.
  - Dependency check + starts `agent.main.main()`.

- `agent/main.py`
  - Main orchestration loop:
    - fetch remote config,
    - fetch/process commands,
    - heartbeat/runtime update.
  - Starts/stops:
    - packet sniffer,
    - remote Modbus server,
    - remote Modbus client.

- `agent/sniffer.py`
  - Packet capture via Scapy `AsyncSniffer`.
  - Decodes Modbus/TCP traffic and emits events/alerts.
  - Checkpoint change:
    - uses real capture interface (`pkt.sniffed_on`) with fallback to configured interface.

- `agent/modbus_parser.py`
  - Modbus/TCP payload recognition and decode logic.
  - Function, direction, exception, request/response field extraction.

- `agent/http_client.py`
  - Agent-to-backend channel:
    - register/heartbeat/runtime/snapshot/event/alert/command_result.
  - Async post queue for telemetry delivery.

- `agent/runtime.py`
  - Simple Modbus server/client used by remote commands.

- `agent/config.py`
  - CLI args (`--server`, `--session-id`, `--iface`, `--mode`).
  - `agent-config.json` loading.
  - Capture environment validation (Npcap/libpcap).

- `agent/identity.py`
  - Persistent local agent identity (`agent_id`).

- `agent/__init__.py`
  - Package exports.

## 3.3. Modbus protocol definition layer

- `agent/protocols/modbus/modbus_definitions.py`
  - Known function catalog and metadata.

- `agent/protocols/modbus/modbus_validators.py`
  - Action payload validation.

- `agent/protocols/modbus/modbus_builder.py`
  - ADU/PDU builder for Modbus/TCP actions.

- `agent/protocols/modbus/__init__.py`
- `agent/protocols/__init__.py`
  - Aggregation modules.

## 3.4. Frontend

- `templates/index.html`
  - Main dashboard and action windows.
  - Script references (with version querystring cache-busting).

- `templates/downloads.html`
  - Agent download/install page.

- `static/app.js`
  - Main UI logic:
    - status/events polling,
    - communication summary rendering,
    - connection history and alerts rendering.

- `static/style.css`
  - Global styling.

- `static/actions/ActionsWindow.js`
  - Actions modal/window behavior.

- `static/protocols/modbus/ModbusTab.js`
  - Modbus action tab logic.
  - Checkpoint change:
    - removed textual "Recent Executions" heading in popup.

- `static/protocols/modbus/ModbusActionForm.js`
- `static/protocols/modbus/ModbusFunctionInfo.js`
- `static/protocols/modbus/modbusBuilder.js`
- `static/protocols/modbus/modbusDefinitions.js`
- `static/protocols/modbus/modbusValidators.js`
  - Modbus action metadata/validation/preview in UI.

## 3.5. Packaging and distribution

- `agent-bundle.spec`
  - PyInstaller specification for agent binaries.

- `.github/workflows/build-agent.yml`
- `.github/workflows/build-and-release.yml`
  - Multi-platform build/release pipelines.

- `downloads/agent/windows/otlab-agent.exe`
- `downloads/agent/mac/otlab-agent-mac`
- `downloads/agent/linux/otlab-agent-linux`
  - Local binary fallback for downloads.

- `scripts/install-windows.bat`
- `scripts/install-macos.sh`
- `scripts/install-linux.sh`
- `scripts/INSTALLATION.md`
  - Installation and troubleshooting assets.

## 3.6. Checkpoint validation toolkit (added)

- `studies/checkpoint/README.md`
  - Operational runbook for test execution.

- `studies/checkpoint/collect_checkpoint_data.py`
  - Structured evidence collection (`status.jsonl`, `events.jsonl`, `commands.jsonl`, `metadata.json`).
  - Optional Modbus action trigger + lifecycle capture.

- `studies/checkpoint/evaluate_checkpoint.py`
  - Automated CT1..CT7 evaluation and final decision.

- `studies/checkpoint/load_modbus_fc03.py`
  - FC03 load generator (configurable RPS/duration).

- `studies/checkpoint/examples/action_fc06.json`
  - Sample payload for CT6.

- `studies/checkpoint/CHECKPOINT_SUPER_DETALHADO.md` (this file)
  - End-to-end technical dossier.

---

## 4. Validation Methodology

## 4.1. Test environment

- Local host (macOS):
  - FastAPI backend at `http://127.0.0.1:8000`
  - Agent running locally with dedicated session.
- Session used:
  - `sess_92c7874bfe77418daa4e3e200ac629f4`
- Primary interface:
  - `lo0` (loopback), synchronized in remote config.
- Tooling:
  - checkpoint scripts,
  - `curl` + `jq`,
  - FC03 load generator.

## 4.2. Procedure

1. Start backend and agent.
2. Start automated evidence collection.
3. Execute CT1..CT7 scenarios:
   - basic detection
   - interface detection
   - write detection
   - exception detection
   - Active/Inactive transition
   - remote command lifecycle
   - 5-minute load stability
4. Run automated evaluator.
5. Perform technical interpretation.

## 4.3. Original acceptance criteria

- CT1: detection transition <= 3s
- CT2: concrete interface (not only `ALL`)
- CT3: write evidence (`writes_detected` and/or WRITE events)
- CT4: exception evidence (`EXCEPTION_RESPONSE`)
- CT5: Active->Inactive transition around 2s
- CT6: remote action reaches `done` or `error` <= 20s
- CT7: stable behavior under 5-minute light load

---

## 5. Produced Evidence

Primary approved campaign path:

- `studies/checkpoint/evidence/checkpoint_20260331_182606/`

Historical campaign path (intermediate):

- `artifacts/checkpoint_run/`

Generated files:

- `status.jsonl` (~26 MB)
- `events.jsonl` (~210 MB)
- `commands.jsonl` (~1.6 MB)
- `metadata.json`
- `action_submit_response.json`
- `action_lifecycle.json`
- `checkpoint_result.json`
- `REPORT.md`

Key metadata (`metadata.json`) from approved campaign:

- `duration_actual_s`: `2158.912`
- `events_http_ok_ratio`: `1.0`
- `poll interval`: `0.5s`

Additional metrics extracted from JSONL data:

- total snapshots parsed: `2286`
- HTTP-OK snapshots: `2286`
- max `events` window length: `300`
- max exported `connection_history` length: `60`
- interfaces observed in summary: `en0`, `lo0`
- observed states:
  - `Active`: `348` snapshots
  - `Inactive`: `1140` snapshots
- most frequent event types:
  - `READ_REQUEST`: `139805`
  - `READ_RESPONSE`: `136302`
  - `WRITE_REQUEST`: `209`
  - `WRITE_RESPONSE`: `133`
  - `EXCEPTION_RESPONSE`: `76`
- alert types:
  - `WRITE_REQUEST`: `1511`
  - `WRITE_RESPONSE`: `784`

---

## 6. Test Results (Expected vs Observed)

## 6.1. CT1 - Basic detection

- Expected:
  - real traffic detected within 3s.
- Observed:
  - `PASS`
  - transition in `1.012s`.

## 6.2. CT2 - Correct interface reporting

- Expected:
  - UI/API should show concrete capture interface, not only `ALL`.
- Observed:
  - `PASS` with strict multi-interface criterion.
  - observed interfaces: `en0` (physical) and `lo0` (loopback).
  - remote client endpoint observed (example): `192.168.18.41:<ephemeral_port>`.

## 6.3. CT3 - Write detection

- Expected:
  - `writes_detected=true` and/or WRITE event evidence.
- Observed:
  - `PASS`
  - `writes_detected=true`
  - both `WRITE_REQUEST` and `WRITE_RESPONSE` present.

## 6.4. CT4 - Modbus exceptions

- Expected:
  - `EXCEPTION_RESPONSE` visible in events/alerts.
- Observed:
  - `PASS`
  - `EXCEPTION_RESPONSE` present in event stream.
  - `fc05_write_single_coil` action recorded exception message:
    - `"Exception response on FC5 (code=1)"`.

## 6.5. CT5 - Connection state transition

- Expected:
  - transition to `Inactive` roughly after 2s without traffic.
- Observed:
  - `PASS`
  - measured transition: `2.201s`.

## 6.6. CT6 - Remote actions lifecycle

- Expected:
  - action ends in `done` or `error` <= 20s with useful message.
- Observed:
  - `PASS`
  - full lifecycle observed:
    - success (`done`) for FC06 response case,
    - failure (`error`) with explicit `ConnectionRefusedError` when server unavailable.
- Example recorded lifecycle:
  - `duration_s = 0.310`
  - `final_status = error`
  - explanatory failure message preserved.

## 6.7. CT7 - Stability under load

- Expected:
  - 5-minute load without crashes or ingestion failure.
- Observed:
  - `PASS`
  - executed load: `2165` requests
  - `ok=2165`, `err=0`
  - `events_http_ok_ratio=1.0`.

---

## 7. Notable Incidents and Debug Findings

1. UI/agent session mismatch
- Symptom:
  - UI showed agent disconnected.
- Cause:
  - browser session ID differed from agent session ID.
- Resolution:
  - align all processes to same `session_id`.

2. Initial remote action failed (`ConnectionRefusedError`)
- Symptom:
  - CT6 error result.
- Cause:
  - Modbus server not running at action time.
- Resolution:
  - start server and rerun to validate success path.

3. Agent reverted from `lo0` to `ALL`
- Symptom:
  - agent launched on `lo0` later showed `ALL`.
- Cause:
  - backend `agent_config` still set to `ALL`, pushed on config sync.
- Resolution:
  - update `/api/agent/config` to `iface=lo0`.

4. Missing `metadata.json` for evaluator
- Symptom:
  - `FileNotFoundError` in `evaluate_checkpoint.py`.
- Cause:
  - collector interrupted before writing final metadata.
- Resolution:
  - reconstruct `metadata.json` from `events.jsonl`.

5. Remote connectivity constraints for Shadow PC
- Symptom:
  - direct remote Modbus connection attempts failed.
- Cause:
  - remote endpoint not on same LAN path to local Mac.
- Resolution:
  - executed multi-interface validation using iPhone on same network, confirming `en0` traffic attribution.

---

## 8. Final Checkpoint Interpretation

## 8.1. Strict automated outcome (approved campaign)

- `checkpoint_result.json`:
  - `CHECKPOINT APROVADO`
  - all test cases CT1..CT7 marked `PASS`.

## 8.2. Engineering/operational outcome

Based on observed behavior, robustness, sustained throughput, and complete critical-path coverage:

- **Checkpoint is functionally APPROVED**.
- Practical execution may now stop and transition to scientific writing/reporting.

---

## 9. Recommended Next Iteration (Paper-Focused)

1. Freeze experiment reproducibility package:
- add `run_checkpoint.sh` to execute all steps in canonical order.
- write commit hash + timestamp into final generated report.

2. Add paper-ready "Threats to Validity" subsection:
- environment dependency (macOS/libpcap),
- mixed loopback + same-LAN traffic strategy,
- sensitivity of timing criterion to polling period.

3. (Optional) Add strict remote-host campaign:
- repeat CT2 using a routable non-LAN remote source (VPN/tunnel) to complement same-network iPhone validation.

---

## 10. Appendix: Main Commands Used

Backend startup:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

Agent startup:

```bash
python agent.py --server http://127.0.0.1:8000 --session-id <SESSION_ID> --iface ALL --mode MONITORING
```

Checkpoint collector:

```bash
python studies/checkpoint/collect_checkpoint_data.py \
  --base-url http://127.0.0.1:8000 \
  --session-id <SESSION_ID> \
  --interval 1.0 \
  --duration 1800 \
  --output-dir artifacts/checkpoint_run \
  --action-file studies/checkpoint/examples/action_fc06.json
```

5-minute load:

```bash
python studies/checkpoint/load_modbus_fc03.py \
  --host 127.0.0.1 --port 15020 --rate 8 --duration 300
```

Evaluation:

```bash
python studies/checkpoint/evaluate_checkpoint.py \
  --input-dir artifacts/checkpoint_run \
  --output artifacts/checkpoint_run/REPORT.md \
  --single-interface-ok
```

---

## 11. Conclusion

This checkpoint validates end-to-end OT Lab App operation for Modbus/TCP monitoring, including detection, interface attribution across loopback and physical NIC (`lo0` + `en0`), write/exception handling, remote command lifecycle, and sustained-load stability.

Final automated result for the approved campaign is `CHECKPOINT APROVADO` with CT1..CT7 all passing.

Therefore, this is a valid stopping point for practical execution and a solid baseline for scientific manuscript drafting (system description, methodology, and validation sections).
