# TRP - Real OT Deployment Architecture (From Lab Prototype to Field-Ready Research)

## 1. Core deployment reality

A passive monitor can only analyze traffic it can see. In switched Ethernet, PLC-HMI unicast traffic is not visible to an arbitrary third host unless visibility mechanisms are engineered.

## 2. Why this matters

In realistic plants, the monitor is usually not running on the HMI or PLC. Therefore, architecture must guarantee observability by design.

## 3. Viable visibility patterns

### A) SPAN / Port Mirroring

- Mirror PLC/HMI ports to the sensor port.
- Cost-effective and common in managed switches.
- Not universally available on all switches.

### B) Network TAP

- Hardware-level copy of traffic.
- Higher capture reliability, especially for forensics.
- Additional hardware cost.

### C) Inline gateway/sensor

- Traffic crosses the sensor path.
- Strong visibility and optional control opportunities.
- Higher operational complexity and risk.

## 4. Proposed research architecture

### Edge layer (distributed sensors)

- One lightweight sensor per OT cell/area (or per critical segment).
- Local packet capture + protocol parse + minimal buffering.
- Secure telemetry forwarding to central node.

### Central layer (aggregation and analytics)

- Event ingestion from multiple sensors.
- Correlation, deduplication, alert enrichment.
- Unified dashboard and evidence export.

## 5. Minimal hardware profile for test environments

- Managed switch with port mirroring.
- PLC + SCADA/HMI endpoint.
- Dedicated sensor host with at least two NICs:
  - capture NIC (monitor-only),
  - management/uplink NIC.

## 6. Architecture implications for TRP

The research plan should evaluate not only detection logic but also deployment topology classes:

- single-cell mirrored setup,
- multi-cell distributed sensors,
- constrained environments without mirroring (documented limitations).
