# TRP - State-of-the-Art Positioning and Gap Map

## 1. Practical baseline in industry

Typical OT monitoring approaches include:

- centralized passive sensors,
- switch-mirror-based NIDS,
- appliance-based inline inspection,
- protocol-specific asset discovery and anomaly detection.

## 2. Common limitations observed in practice

- Implicit assumption that all relevant traffic is visible from one monitor.
- Weak integration between deployment topology and detection validity.
- Limited reproducible reporting of validation methodology.
- Alert outputs that are technically rich but operationally hard to interpret.

## 3. Gap addressed by this research

This research explicitly combines:

1. protocol-aware Modbus/TCP monitoring,
2. deployment visibility constraints as first-class variables,
3. evidence-driven checkpoint validation,
4. progressive architecture transition toward distributed sensing.

## 4. Planned contribution dimensions

- **C1 (Architecture):** edge sensor + central correlation model for multi-cell OT.
- **C2 (Methodology):** reproducible checkpoint-to-field validation pipeline.
- **C3 (Operational UX):** concise alert semantics that preserve technical traceability.
- **C4 (Engineering evidence):** quantitative evaluation of timeliness, interface attribution, and stability.

## 5. Scope boundaries for TRP

In-scope:

- Modbus/TCP visibility and event-level security telemetry.
- Passive monitoring plus controlled remote action pathways used in validation.

Out-of-scope (initial phases):

- Full active prevention/blocking.
- Universal protocol coverage beyond defined roadmap.
- Enterprise SOC integration as a primary research objective (treated as future integration).
