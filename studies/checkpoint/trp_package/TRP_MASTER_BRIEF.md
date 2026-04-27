# OT Lab App - TRP Master Brief

## Research Context

Operational Technology (OT) networks increasingly expose industrial control assets (PLCs, HMIs, engineering workstations, SCADA servers) to cyber risk. Many real plants still lack continuous, protocol-aware visibility at cell/area level, especially for legacy and heterogeneous installations.

The project targets this gap through an OT-focused monitoring stack for Modbus/TCP, with practical deployment constraints as a first-class design requirement.

## Motivation

The current checkpoint validates core technical feasibility in a controlled but realistic mixed-interface environment. The next scientific step is to transform a lab-validated monitor into a deployable research framework that remains effective in real industrial topologies.

A critical insight from practice is that a passive monitor can only detect traffic it can physically observe. In switched industrial Ethernet networks, unicast PLC-HMI traffic is not visible to an arbitrary third host unless visibility mechanisms (SPAN/TAP/inline) are provided.

## Preliminary Thesis Statement (Early)

A distributed OT monitoring architecture, combining protocol-aware edge sensors and centralized correlation, can provide reliable and actionable Modbus/TCP cybersecurity visibility in real industrial networks, while preserving practical deployability constraints and acceptable operational overhead.

## What is already demonstrated

From the approved checkpoint run (`checkpoint_20260331_182606`):

- End-to-end Modbus/TCP capture and parsing.
- Correct concrete interface attribution in mixed-interface operation (`en0` and `lo0`).
- Correct detection of reads, writes, exceptions, and connection state transitions.
- Functional remote action lifecycle.
- Stable operation under sustained light load.
- Full acceptance of CT1..CT7 in evaluator output.

## What remains to be researched

- Real deployment architecture across multiple PLC/HMI segments.
- Sensor placement and visibility guarantees in switched OT networks.
- Data pipeline scalability (multi-sensor ingestion, buffering, correlation).
- Alert quality and explainability for operators.
- Reproducible field-oriented validation protocol.

## Strategic direction for TRP

The TRP should position the checkpoint as validated preliminary work and then propose a structured research plan around:

1. architecture evolution (single monitor -> distributed sensors),
2. deployment constraints and observability guarantees,
3. detection quality and performance at scale,
4. formal validation and reproducibility.
