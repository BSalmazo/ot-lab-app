# TRP - Commented Workplan and Gantt (Draft)

## Planning horizon

This draft assumes a 24-month research execution horizon after TRP approval.

## Work packages

### WP1 - Foundation and literature consolidation (M1-M3)

- Consolidate related work on OT IDS, Modbus monitoring, deployment architectures, and validation methods.
- Finalize research questions and hypotheses.
- Deliverable: TRP-aligned literature and gap matrix.

### WP2 - Architecture design and instrumentation (M2-M6)

- Define distributed sensor + central aggregator design.
- Specify event schema, secure transport, and observability metrics.
- Deliverable: architecture specification and prototype integration plan.

### WP3 - Sensor engineering and data pipeline (M5-M10)

- Implement edge-sensor mode and robust telemetry flow.
- Add buffering/retry and health metrics.
- Deliverable: multi-sensor ingestion prototype.

### WP4 - Detection quality and UX refinement (M8-M13)

- Improve alert semantics, deduplication, and explainability.
- Evaluate operator-facing clarity and actionability.
- Deliverable: revised detection/alert model with evaluation report.

### WP5 - Experimental campaigns (M11-M18)

- Controlled lab experiments across polling rates, loads, and topologies.
- Comparative runs for interface/visibility strategies.
- Deliverable: reproducible datasets and quantitative results.

### WP6 - Realistic deployment pilots (M16-M21)

- Pilot in representative industrial-like topology with segmented cells.
- Validate deployment guidance (SPAN/TAP/sensor placement).
- Deliverable: deployment playbook + pilot evidence.

### WP7 - Consolidation, writing, and defense preparation (M20-M24)

- Integrate results, discuss limitations, and finalize thesis narrative.
- Deliverable: thesis manuscript drafts and publication-ready artifacts.

## Gantt (text form)

- M1-M3: WP1
- M2-M6: WP2
- M5-M10: WP3
- M8-M13: WP4
- M11-M18: WP5
- M16-M21: WP6
- M20-M24: WP7

## Dependency notes

- WP2 is prerequisite for stable WP3 scope.
- WP3 and WP4 partially overlap by design.
- WP5 requires a stable data pipeline and baseline detection semantics.
- WP6 depends on validated lab protocol from WP5.
