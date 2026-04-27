# TRP - Context, Motivation, Objectives, and Early Thesis

## 1. Context

I am developing an OT cybersecurity monitoring platform focused on Modbus/TCP communication visibility and operator-oriented situational awareness. The current system includes:

- a backend and dashboard,
- an agent capable of traffic capture and protocol parsing,
- a checkpoint toolkit for evidence collection and objective evaluation.

The broader context is industrial environments with multiple PLCs and HMIs, segmented Ethernet networks, and strict operational constraints.

## 2. Motivation

Industrial monitoring solutions often fail in practice when they ignore network visibility constraints and deployment realities. In switched OT networks, passive monitoring quality depends on physical/logical traffic observability. This motivates a research plan that links detection logic to architecture and deployment engineering.

## 3. Problem Statement

How can I evolve a lab-validated Modbus/TCP monitoring stack into a scalable OT research framework that:

- preserves protocol-level detection quality,
- works under realistic network visibility constraints,
- supports multi-cell deployments,
- and produces evidence suitable for scientific validation?

## 4. Research Objectives

### O1 - Architectural objective
Design a distributed monitoring architecture with edge sensors and central correlation suitable for real OT topologies.

### O2 - Visibility objective
Define and validate deployment patterns (SPAN, TAP, inline) that guarantee packet observability for target traffic.

### O3 - Detection objective
Improve event quality and operator comprehension (clear alerts, low duplication, meaningful summaries).

### O4 - Performance objective
Characterize timeliness and stability under different polling rates, traffic volumes, and interface configurations.

### O5 - Validation objective
Build a reproducible experimental protocol with objective pass/fail criteria and complete evidence traceability.

## 5. Early Thesis Statement

I hypothesize that protocol-aware distributed monitoring with explicit network-visibility engineering provides a feasible and scientifically robust path to OT cybersecurity monitoring that is both operationally practical and empirically verifiable.
