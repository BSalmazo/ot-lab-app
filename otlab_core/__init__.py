"""Liscere engine: the protocol-abstracted passive observer core.

- ``contract``   : the ``NormalizedEvent`` data contract (protocol-neutral event shape);
- ``extractors`` : the ``ProtocolExtractor`` interface and the OPC UA, Modbus/TCP and S7comm
                   passive extractors (everything protocol-specific lives here);
- ``engine``     : the protocol-neutral learned core (PhaseTracker, auto-calibration, state-signal
                   discovery, grammar learner, graded evaluator), ported from LTR-2026-03;
- ``wire``       : dissector-chain parsing shared by the probe and the observer.

The programs that drive this package (liscere-observe, liscere-probe, liscere-ui, liscere-metrics)
live in the ``liscere`` package. The engine knows no protocol and no process: protocol facts stay
in the extractors, process facts are discovered from traffic or declared in configuration.
"""

__all__ = ["contract", "extractors", "engine"]
