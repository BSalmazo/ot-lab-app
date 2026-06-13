# Complete Technical Dossier - Repository `02_labs` / OT Lab v2 (ChatGPT Input)

## 1) Dossier Purpose

This document provides an updated technical and research-oriented summary of the current `02_labs` practical work, with emphasis on the Docker-based `modbus_lab/ot_lab_app` v2 prototype.

It is intended to be used as reliable input for ChatGPT-assisted drafting of the Thesis Research Plan (TRP), especially Chapter 4 ("Preliminary Work and Feasibility Evidence") and Chapter 5 ("Research Plan and Evaluation Strategy").

This dossier updates the earlier archived `90_archive/legacy_dossiers/02_labs_dossier_for_chatgpt_legacy.md`, which mainly described the previous local-agent / Scapy-based v1 checkpoint. The present document reflects the current v2 direction:

- Docker-first OT/ICS laboratory topology;
- OpenPLC and FUXA integration;
- Modbus/TCP monitoring through `tshark`;
- monitor proxy between HMI and PLC;
- process-aware semantic decision layer;
- structured policy-decision evidence export.

---

## 2) Executive Summary of Current Practical Work

The current practical work implements an OT/ICS simulation and testing framework whose purpose is not only to observe industrial network traffic, but to interpret protocol-valid actions in relation to a declared process model.

The current v2 prototype includes:

- a Docker-based OT laboratory with fixed OT and DMZ subnets;
- an OpenPLC container acting as the PLC;
- a FUXA container acting as the HMI;
- a FastAPI web platform for observability, configuration, lab actions, and evidence export;
- a `tshark`-based runtime monitor for Modbus/TCP traffic capture and protocol dissection;
- an inline Modbus/TCP monitor proxy so HMI traffic can traverse the monitor before reaching the PLC;
- a simple but functional tank process model in Structured Text;
- process tag mapping from Modbus addresses to process-level names;
- an observed semantic policy layer that classifies write actions as `ALLOW` or `ALERT`;
- a "Policy Decisions" dashboard panel and JSON export mechanism;
- a preliminary direct-path versus monitored-path latency benchmark for the Modbus/TCP proxy path.

The most important validated outcome is that the prototype can now show the difference between packet visibility and process-aware interpretation. The same Modbus/TCP function code, FC06 Write Single Register, is classified differently depending on the target asset, value, and declared process model.

The latency evidence should be interpreted as preliminary feasibility evidence for the Docker laboratory, not as a deterministic real-time industrial guarantee.

---

## 3) Current Repository and Branch State

Repository focus:

- `modbus_lab/ot_lab_app`

Current development branch:

- `v2-dev`

Recent baseline commit observed during this dossier update:

- `638f3cb fix(v2): remove legacy agent.main import from package init`

Important note:

- The v1 production site and branch are intentionally kept separate.
- The current work is v2 development and should not be presented as the deployed production version.
- Official production site remains: `https://otlab.salmazo.org`

---

## 4) Current Docker-Based Architecture

The v2 lab is defined in:

- `modbus_lab/ot_lab_app/docker-compose.yml`

Current services:

| Service | Role | Default OT/DMZ address | Host access |
| --- | --- | --- | --- |
| `web` | FastAPI web platform | `10.20.0.10`, `10.30.0.10` | `localhost:8000` |
| `openplc` | PLC / Modbus server | `10.20.0.11` | OpenPLC UI `localhost:8081`, Modbus `localhost:1502` |
| `hmi` | FUXA HMI | `10.20.0.21` | `localhost:1881` |
| `runtime` | `tshark` monitor + Modbus proxy | `10.20.0.31`, `10.30.0.31` | proxy `localhost:15020` |

Network segmentation:

- OT subnet: `10.20.0.0/24`
- DMZ subnet: `10.30.0.0/24`

Intended lab flow:

```text
HMI -> Monitor proxy (OT side) -> PLC
Monitor/runtime -> Web platform (DMZ/API side)
```

Practical communication path:

- FUXA should connect to the monitor proxy, not directly to OpenPLC.
- Inside Docker, the HMI target is usually:
  - host: `runtime`
  - port: `15020`
- The runtime proxy forwards to:
  - host: `openplc`
  - port: `502`

Rationale:

- This approximates an inline monitoring/enforcement position for laboratory experimentation.
- It is not yet a production deployment design.
- For real OT deployment discussion, passive SPAN/TAP visibility and inline enforcement must be treated separately.

---

## 5) Current End-to-End Flow

1. Docker Compose starts the web platform, OpenPLC, FUXA, and runtime monitor.
2. The runtime registers with the web API using session `sess_v2_docker_local`.
3. The runtime starts:
   - a Modbus/TCP proxy on port `15020`;
   - a `tshark` capture loop.
4. FUXA communicates with OpenPLC through the proxy.
5. `tshark` observes Modbus/TCP packets and extracts protocol fields.
6. The runtime posts decoded events to the FastAPI backend.
7. The backend reconstructs:
   - Modbus events;
   - active connection history;
   - operational write actions;
   - semantic policy decisions.
8. The UI shows:
   - monitor, PLC, and HMI status;
   - active Modbus/TCP communication;
   - alerts / actions;
   - policy decisions;
   - network scan;
   - links to PLC and HMI.
9. The user can export policy decisions as JSON evidence.

---

## 6) Main Implemented Components

### 6.1 FastAPI Backend

Main file:

- `modbus_lab/ot_lab_app/app.py`

Main responsibilities:

- session-scoped state management;
- runtime monitor registration;
- Modbus event ingestion;
- connection summary and active-flow history;
- lab helper endpoints for Modbus read/write tests;
- monitor proxy target configuration;
- process tag mapping;
- observed semantic policy evaluation;
- policy-decision storage and export.

Important endpoints:

- `GET /api/status`
- `GET /api/events`
- `POST /api/agent/register`
- `POST /api/agent/events`
- `GET /api/v2/lab/topology`
- `GET /api/v2/lab/read-register`
- `POST /api/v2/lab/write-register`
- `GET /api/v2/lab/read-bool`
- `POST /api/v2/lab/write-bool`
- `GET /api/v2/policy-decisions`
- `POST /api/v2/policy-decisions/export`

### 6.2 Runtime Monitor

Main file:

- `modbus_lab/ot_lab_app/scripts/tshark_runtime.py`

Main responsibilities:

- wait for the web API to become reachable;
- register as `tshark` runtime;
- run a Modbus/TCP proxy;
- run `tshark` capture;
- parse relevant Modbus/TCP fields from `tshark`;
- batch and send observed events to the backend.

Key design choice:

- `tshark` is used instead of rebuilding a full protocol dissector from scratch.
- Current capture configuration focuses on standard Modbus/TCP ports and proxy traffic.
- This is appropriate for the current Modbus/TCP TRP evidence, while leaving room to add more industrial protocols later.

### 6.3 Docker Topology

Main files:

- `modbus_lab/ot_lab_app/docker-compose.yml`
- `modbus_lab/ot_lab_app/Dockerfile`
- `modbus_lab/ot_lab_app/docker/fuxa.Dockerfile`

Docker-based lab components:

- OpenPLC container;
- FUXA HMI container;
- web container;
- runtime monitor/proxy container;
- OT and DMZ bridge networks.

### 6.4 Web UI

Main files:

- `modbus_lab/ot_lab_app/templates/index.html`
- `modbus_lab/ot_lab_app/static/app.js`
- `modbus_lab/ot_lab_app/static/style.css`

Current UI features:

- local monitor status;
- visual OT/DMZ connection flow;
- active communication card;
- alert/action cards;
- policy decision table;
- network scan popup;
- PLC and HMI shortcut buttons;
- monitor configuration popup;
- tag mapping popup;
- exportable evidence.

### 6.5 Process / PLC Program

Main seed file:

- `modbus_lab/ot_lab_app/scripts/v2_seed/openplc_tank_v1.st`

Current simulated process:

- tank level from `0` to `100`;
- pump command;
- pump flow setpoint;
- valve command;
- valve flow setpoint;
- configurable high and low alarm setpoints;
- high and low alarm active signals;
- process constraints implemented in the PLC logic.

Important design principle:

- Basic physical limits and safety constraints belong in the PLC.
- The external monitor should not replace PLC safety logic.
- The research contribution is not "PLC has no logic"; it is detecting semantically suspicious or unauthorised protocol-valid actions around an otherwise reasonable PLC process.

### 6.6 FUXA HMI Backup / Configuration

Relevant seed and backup files:

- `modbus_lab/ot_lab_app/scripts/v2_seed/fuxa_tank_v1_tags.md`
- `modbus_lab/ot_lab_app/scripts/v2_seed/fuxa_project_recovered_latest.json`
- `modbus_lab/ot_lab_app/scripts/v2_seed/project_fuxa_db_recovered_latest.db`
- `modbus_lab/ot_lab_app/scripts/v2_seed/backups/*`

Additional user-exported FUXA files were supplied during development:

- `Tank.json`
- `Tank-2.json`

These should be kept as practical backup artefacts for restoring the FUXA configuration and view.

---

## 7) Current Process Model and Tag Map

The current Modbus/TCP tank process uses the following semantic map:

Coils:

| Modbus coil | PLC location | Semantic name |
| ---: | --- | --- |
| `0` | `%QX0.0` | `PUMP_CMD` |
| `1` | `%QX0.1` | `VALVE_CMD` |
| `2` | `%QX0.2` | `ALARM_HI_ACTIVE` |
| `3` | `%QX0.3` | `ALARM_LO_ACTIVE` |

Holding registers:

| Register | PLC location | Semantic name |
| ---: | --- | --- |
| `1` | `%QW1` | `PUMP_FLOW_SP` |
| `2` | `%QW2` | `VALVE_FLOW_SP` |
| `3` | `%QW3` | `ALARM_HI_SP` |
| `4` | `%QW4` | `ALARM_LO_SP` |
| `6` | `%QW6` | `LEVEL_AI` |

Important note:

- The tag map can be edited or uploaded through the UI.
- If no semantic tag map is supplied, raw Modbus addresses can still be observed, but semantic interpretation is weaker.

---

## 8) Current Semantic Policy Layer

Current observed policy identifier:

- `observed_modbus_tank_v1`

Current policy version:

- `0.1.0`

Current operating mode:

- observe mode;
- policy produces `ALLOW` or `ALERT`;
- it does not yet enforce blocking in the live proxy path.

Current observed-policy rules:

| Rule | Decision | Meaning |
| --- | --- | --- |
| `OBS-R000` | `ALLOW` | Observed action is mapped to the process model and remains inside the initial semantic policy. |
| `OBS-R001` | `ALERT` | Setpoint write is outside the declared process envelope, currently `0..100`. |
| `OBS-R002` | `ALERT` | Write targets a sensitive process configuration parameter. |
| `OBS-R003` | `ALERT` | Write targets an address not mapped to the declared process model. |
| `OBS-R004` | `ALLOW` | Sensitive configuration write during an active maintenance window. |
| `OBS-R005` | `ALERT` | Direct write to alarm-state output. |

Key contribution demonstrated:

- Protocol-valid traffic is not sufficient to determine whether an action is contextually legitimate.
- The same Modbus function code can be normal or suspicious depending on process target and declared semantics.

---

## 9) Validated Evidence - Chapter 4 Test Package

Main evidence log:

- `modbus_lab/ot_lab_app/studies/chapter4_modbus_tcp_test_log.md`

Exported policy-decision evidence:

- `modbus_lab/ot_lab_app/studies/evidence/otlab-policy-decisions-2026-06-08T18-22-31-770Z.json`

Latency benchmark evidence:

- `modbus_lab/ot_lab_app/studies/evidence/modbus_latency_persistent_20260609T120706Z.json`
- `modbus_lab/ot_lab_app/studies/evidence/modbus_latency_persistent_20260609T120706Z.csv`

Screenshots:

- `modbus_lab/ot_lab_app/studies/evidence/screenshots/chapter4_modbus_tcp/Screenshot 2026-06-08 at 14.41.21.png`
- `modbus_lab/ot_lab_app/studies/evidence/screenshots/chapter4_modbus_tcp/Screenshot 2026-06-08 at 14.48.06.png`
- `modbus_lab/ot_lab_app/studies/evidence/screenshots/chapter4_modbus_tcp/Screenshot 2026-06-08 at 14.50.00.png`
- `modbus_lab/ot_lab_app/studies/evidence/screenshots/chapter4_modbus_tcp/Screenshot 2026-06-08 at 14.54.04.png`
- `modbus_lab/ot_lab_app/studies/evidence/screenshots/chapter4_modbus_tcp/Screenshot 2026-06-08 at 14.57.40.png`
- `modbus_lab/ot_lab_app/studies/evidence/screenshots/chapter4_modbus_tcp/Screenshot 2026-06-08 at 14.59.43.png`
- `modbus_lab/ot_lab_app/studies/evidence/screenshots/chapter4_modbus_tcp/Screenshot 2026-06-08 at 15.25.12.png`
- `modbus_lab/ot_lab_app/studies/evidence/screenshots/chapter4_modbus_tcp/Screenshot 2026-06-08 at 20.22.26.png`

### 9.1 Baseline Monitoring Test

Result:

- PASS

Evidence:

- Modbus/TCP traffic observed.
- Normal reads observed.
- FC01 and FC03 observed.
- Active communication shown in dashboard.
- Interface shown by dashboard: `any`.
- Runtime/agent log interface: `eth0` before fallback / capture adaptation.
- Connection active between HMI/monitor and PLC.

Interpretation:

- The platform can observe live Modbus/TCP traffic in the Docker OT lab.

### 9.2 Write Action Detection Test

Result:

- PASS

Representative command:

```bash
curl -s -X POST "http://localhost:8000/api/v2/lab/write-register?session_id=sess_v2_docker_local" \
  -H "Content-Type: application/json" \
  -d '{"register":1,"value":42,"unit_id":1}' | python3 -m json.tool
```

Observed reconstruction:

- Function: FC06 Write Single Register.
- Register: `1`.
- Semantic asset: `PUMP_FLOW_SP`.
- Value: `42`.
- Dashboard action generated.

Interpretation:

- The platform reconstructs a protocol-level write into an operational action.

### 9.3 Invalid / Unmapped Address Test

Result:

- PASS, with semantic interpretation added after initial observation.

Representative command:

```bash
curl -s -X POST "http://localhost:8000/api/v2/lab/write-register?session_id=sess_v2_docker_local" \
  -H "Content-Type: application/json" \
  -d '{"register":65000,"value":123,"unit_id":1}' | python3 -m json.tool
```

Observed result:

- PLC returned Modbus exception code `2` in the lab helper path.
- Monitor reconstructed the attempted write.
- Policy decision: `ALERT`.
- Rule: `OBS-R003`.
- Asset: `HR65000`.

Interpretation:

- The monitor can detect a write attempt to an unmapped address and classify it relative to the declared process model.

### 9.4 Policy-Style Scenario Results

Final validated export contains four policy decisions:

| Scenario | Action | Asset | Value | Decision | Rule |
| --- | --- | --- | ---: | --- | --- |
| Valid setpoint | FC06 write register | `PUMP_FLOW_SP` | 50 | ALLOW | `OBS-R000` |
| Out-of-range setpoint | FC06 write register | `PUMP_FLOW_SP` | 150 | ALERT | `OBS-R001` |
| Sensitive configuration | FC06 write register | `ALARM_HI_SP` | 5 | ALERT | `OBS-R002` |
| Unmapped address | FC06 write register | `HR65000` | 123 | ALERT | `OBS-R003` |

This is the strongest current TRP evidence because it demonstrates:

1. protocol visibility;
2. operational action reconstruction;
3. semantic process-aware judgement;
4. structured traceability through rule identifiers and exportable JSON.

### 9.5 Latency Test: Direct Path vs Monitored Path

Result:

- PASS

Purpose:

- Estimate the response-time overhead introduced by the monitored Modbus/TCP proxy path.
- Produce preliminary evidence for TRP discussion about OT timing sensitivity, availability, and the need to avoid disruptive security controls.

Important validity note:

- At the beginning of the latency test session, the OpenPLC web container was running, but the PLC runtime / Modbus server was not yet accepting connections on port `502`.
- Those initial attempts returned connection failures and were discarded.
- The latency results below were collected only after OpenPLC runtime was activated and connectivity was confirmed.
- Therefore, the reported latency results are valid for the active OpenPLC runtime state.

Connectivity confirmation after OpenPLC activation:

```text
openplc 502 OK
runtime 15020 OK
10.20.0.11 502 OK
10.20.0.31 15020 OK
```

Test paths:

| Path | Communication Route | Endpoint Used by Test Client |
| --- | --- | --- |
| Direct path | client -> OpenPLC | `127.0.0.1:1502` |
| Monitored path | client -> runtime proxy -> OpenPLC | `127.0.0.1:15020` |

Benchmark script:

- `modbus_lab/ot_lab_app/scripts/benchmark_modbus_latency.py`

Command:

```bash
python3 scripts/benchmark_modbus_latency.py \
  --requests 500 \
  --warmup 25 \
  --connection-mode persistent \
  --timeout 2 \
  --start-addr 1 \
  --quantity 6 \
  --json-out studies/evidence/modbus_latency_persistent_20260609T120706Z.json \
  --csv-out studies/evidence/modbus_latency_persistent_20260609T120706Z.csv
```

Workload:

| Parameter | Value |
| --- | --- |
| Modbus operation | FC03 Read Holding Registers |
| Unit ID | `1` |
| Start address | `1` |
| Quantity | `6` |
| Warmup requests per path | `25` |
| Measured requests per path | `500` |
| TCP mode | Persistent connection |
| Timeout | `2 s` |

Measured results:

| Path | Requests | OK | Failures | Avg (ms) | Median (ms) | p95 (ms) | p99 (ms) | Min (ms) | Max (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Direct | 500 | 500 | 0 | 0.190 | 0.172 | 0.278 | 0.489 | 0.124 | 1.500 |
| Monitored | 500 | 500 | 0 | 0.296 | 0.219 | 0.713 | 1.420 | 0.178 | 2.478 |

Observed overhead introduced by the monitored path:

| Metric | Overhead |
| --- | ---: |
| Average | 0.106 ms |
| Median | 0.047 ms |
| p95 | 0.434 ms |
| p99 | 0.931 ms |

Interpretation:

- The monitored path introduced measurable latency, but the observed median overhead was below `0.05 ms` and the p95 overhead was below `0.5 ms` in this Docker-based laboratory test.
- No failures or timeouts were observed in the valid 500-request direct-path run or in the valid 500-request monitored-path run.
- This supports preliminary feasibility for a low-overhead observation/proxy architecture in the simulated Modbus/TCP lab.
- This does not prove deterministic real-time suitability for physical industrial networks. Future work should repeat the test under longer duration, higher request volumes, controlled polling intervals, multiple concurrent clients, loaded host conditions, and additional protocols.

Suggested Chapter 4 wording:

> A preliminary latency benchmark was performed after confirming that the OpenPLC runtime was active and accepting Modbus/TCP connections. The direct client-to-PLC path was compared with the monitored path through the runtime proxy using 500 persistent FC03 read requests per path. The direct path achieved an average response time of 0.190 ms and median of 0.172 ms, while the monitored path achieved an average of 0.296 ms and median of 0.219 ms. No failures or timeouts were observed. The resulting median overhead of approximately 0.047 ms suggests that the current prototype introduces low overhead in the Docker-based laboratory setting, although this result should be interpreted as preliminary feasibility evidence rather than a deterministic real-time guarantee.

---

## 10) What Is Already Demonstrated

Already demonstrated in the current v2 prototype:

- Docker-based OT/DMZ lab topology;
- HMI-to-PLC communication through a monitor proxy;
- OpenPLC and FUXA integration;
- `tshark`-based Modbus/TCP observation;
- active communication detection and disappearance when the PLC path is unavailable;
- FC01 / FC03 read observation;
- FC06 write observation;
- protocol address to process-tag mapping;
- structured operational-action reconstruction;
- basic observed semantic policy;
- policy-decision dashboard panel;
- policy-decision JSON export;
- preliminary direct-versus-monitored Modbus/TCP latency benchmark;
- zero observed failures/timeouts in the valid 500-request persistent latency test for both direct and monitored paths;
- evidence suitable for Chapter 4 preliminary feasibility.

---

## 11) Current Limitations and Boundaries

The current prototype should be presented accurately:

- The semantic policy layer currently operates primarily in observation/alert mode.
- It does not yet enforce blocking in the live proxy path for all scenarios.
- Current protocol evidence is Modbus/TCP only.
- The current policy model is manually declared and simple.
- The current process is a single tank model, not yet a library of industrial profiles.
- Docker networking approximates OT/DMZ segmentation but is not equivalent to a physical OT network with real switches, SPAN/TAP ports, PLC hardware, and deterministic timing.
- `tshark` provides protocol dissection, but process-aware interpretation still depends on a process model, tag map, and semantic rules.
- The current UI is sufficient for TRP evidence, but not yet a complete operational product.
- The latency benchmark is valid for the active Docker lab state, but should not be presented as proof of deterministic real-time industrial performance.
- Initial latency attempts made before OpenPLC runtime was active produced connection failures; these attempts were discarded and are not used as evidence.

Important thesis framing:

- The contribution is not "detecting Modbus packets"; Wireshark/tshark already does that.
- The contribution is connecting observed OT protocol actions to process context and producing explainable semantic decisions.
- PLC safety logic remains essential and should not be replaced by the monitor.
- The research question is about an additional process-aware cyber-physical security layer.

---

## 12) Proposed Next Research Steps

Recommended next practical steps:

1. Stabilise the Chapter 4 demonstration baseline.
2. Commit the current v2 evidence state.
3. Add a small policy/evidence report view that can export Markdown/JSON together.
4. Move from observe-only decisions toward selective enforcement in the proxy.
5. Define one realistic attack scenario based on ATT&CK for ICS and real industrial logic:
   - not simply "write invalid value";
   - instead, protocol-valid and PLC-accepted action that is dangerous in process context.
6. Expand the process model slightly:
   - mode/state;
   - operating phase;
   - expected command sequence;
   - operator/engineering context.
7. Add a second protocol later only after Modbus/TCP has a clean end-to-end evidence chain.

Candidate next scenario:

- "Authorised-looking setpoint or alarm-threshold change during an unsafe operating phase."

Why this is better than trivial invalid writes:

- a good PLC may clamp or reject impossible values;
- a real attack often uses valid credentials, valid protocol requests, and valid addresses;
- the research value is detecting contextual illegitimacy, not merely malformed packets.

---

## 13) File-by-File Inventory - Current v2-Relevant Files

### Core application

- `app.py`
  - FastAPI backend, session state, lab endpoints, semantic policy decisions, policy export.
- `requirements.txt`
  - Python dependencies.
- `Dockerfile`
  - Python image with `tshark`, `tcpdump`, `libpcap`, and Python dependencies.
- `docker-compose.yml`
  - OT/DMZ topology with web, runtime, OpenPLC, and FUXA.

### Runtime and protocol observation

- `scripts/tshark_runtime.py`
  - runtime monitor, `tshark` execution, event parsing, Modbus proxy.
- `scripts/benchmark_modbus_latency.py`
  - direct-path versus monitored-path Modbus/TCP latency benchmark script.

### Frontend

- `templates/index.html`
  - dashboard structure and floating windows.
- `static/app.js`
  - UI polling, render logic, network scan, monitor config, alerts, policy decisions, export.
- `static/style.css`
  - dashboard, cards, policy decision table, visual styling.

### v2 process and seed artefacts

- `scripts/v2_seed/openplc_tank_v1.st`
  - OpenPLC Structured Text tank process.
- `scripts/v2_seed/fuxa_tank_v1_tags.md`
  - notes for FUXA tag mapping.
- `scripts/v2_seed/fuxa_project_recovered_latest.json`
  - recovered FUXA configuration backup.
- `scripts/v2_seed/project_fuxa_db_recovered_latest.db`
  - recovered FUXA database backup.
- `scripts/v2_seed/backup_lab_state.sh`
  - backup helper script.
- `scripts/v2_seed/backups/*`
  - saved backup snapshots.

### Research evidence

- `studies/chapter4_modbus_tcp_test_log.md`
  - current Chapter 4 test log and interpretation.
- `studies/evidence/otlab-policy-decisions-2026-06-08T18-22-31-770Z.json`
  - exported semantic policy decision evidence.
- `studies/evidence/modbus_latency_persistent_20260609T120706Z.json`
  - exported latency benchmark summary and raw samples for direct and monitored paths.
- `studies/evidence/modbus_latency_persistent_20260609T120706Z.csv`
  - latency benchmark samples in tabular form.
- `studies/checkpoint/*`
  - previous checkpoint evidence and writing packages from the earlier phase.

### Historical / v1-related context

- `90_archive/legacy_dossiers/02_labs_dossier_for_chatgpt_legacy.md`
  - previous dossier, focused on the earlier validated checkpoint implementation.
- `studies/checkpoint/trp_package/*`
  - TRP package from the previous checkpoint campaign.
- `studies/checkpoint/paper_package/*`
  - paper-oriented package from the previous checkpoint campaign.

---

## 14) Suggested ChatGPT Prompt

Use this updated dossier as the primary self-contained source. Supporting evidence files may be attached if needed for verification:

```text
Based on the attached updated technical dossier (`otlab_v2_chapter4_dossier.md`), help draft the TRP sections on preliminary work and evaluation strategy in formal academic English. Treat the dossier as the primary self-contained source; use raw evidence files only if they are also attached for verification.

Clearly separate:
1. what was already implemented and validated in v2;
2. what is only a prototype limitation;
3. what remains future research.

The main argument is not that the platform detects Modbus/TCP packets, because tshark/Wireshark already do that. The main argument is that the platform reconstructs OT protocol actions as process-level operations and applies an initial semantic policy that can distinguish allowed, suspicious, and unmapped actions based on process context.

Do not invent results. Use only the evidence provided:
- baseline Modbus/TCP monitoring;
- write action detection;
- invalid/unmapped address observation;
- policy decisions OBS-R000 through OBS-R003;
- exported policy-decision JSON;
- preliminary direct-path versus monitored-path latency benchmark;
- zero observed failures/timeouts in the valid 500-request persistent latency test;
- dashboard screenshots.

Write with a PhD-level tone and connect the prototype to OT/ICS security concepts such as Purdue-style segmentation, process-aware monitoring, protocol-valid malicious actions, ATT&CK for ICS, NIST SP 800-82, and IEC 62443, without claiming standards compliance.
```

---

## 15) Optional Supporting Files

This dossier is intended to be self-contained for writing Chapter 4. The files below are optional and are recommended only when raw evidence or implementation verification is required.

Primary document:

1. `modbus_lab/ot_lab_app/studies/dossiers/otlab_v2_chapter4_dossier.md`

Optional verification files:

2. `modbus_lab/ot_lab_app/studies/chapter4_modbus_tcp_test_log.md`
3. `modbus_lab/ot_lab_app/studies/evidence/otlab-policy-decisions-2026-06-08T18-22-31-770Z.json`
4. `modbus_lab/ot_lab_app/studies/evidence/modbus_latency_persistent_20260609T120706Z.json`
5. `modbus_lab/ot_lab_app/studies/evidence/modbus_latency_persistent_20260609T120706Z.csv`
6. `modbus_lab/ot_lab_app/docker-compose.yml`
7. `modbus_lab/ot_lab_app/scripts/tshark_runtime.py`
8. `modbus_lab/ot_lab_app/scripts/benchmark_modbus_latency.py`
9. `modbus_lab/ot_lab_app/scripts/v2_seed/openplc_tank_v1.st`
10. `modbus_lab/ot_lab_app/app.py`
11. Screenshot: `modbus_lab/ot_lab_app/studies/evidence/screenshots/chapter4_modbus_tcp/Screenshot 2026-06-08 at 20.22.26.png`

Optional historical context:

12. `90_archive/legacy_dossiers/02_labs_dossier_for_chatgpt_legacy.md`
13. `modbus_lab/ot_lab_app/studies/checkpoint/trp_package/README_FOR_CHATGPT_TRP.md`
14. `modbus_lab/ot_lab_app/studies/checkpoint/CHECKPOINT_SUPER_DETALHADO.md`

---

## 16) One-Sentence Current Claim

The current OT Lab v2 prototype demonstrates that Modbus/TCP traffic can be observed, reconstructed as process-level operations, classified through an initial semantic policy, and monitored through a proxy path that introduced low preliminary latency overhead in the validated Docker laboratory benchmark.
