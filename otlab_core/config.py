"""Observer configuration (Phase 2).

Process-specific knobs live here as configuration, not as constants buried in the extractor or
the engine. Defaults reproduce the LTR-2026-03 tank values so nothing changes for the existing
setup, but they are now overridable per deployment.

DEBT D3 (recalibration): the phase-inference defaults below were tuned on the LTR-2026-03 tank at
a ~1 Hz Modbus poll. OPC UA telemetry on the lab runs at ~5 Hz (and the level is a Float, not an
integer), so WINDOW / slope thresholds / hysteresis will need recalibration before the learned
engine is trusted on the OPC UA channel. They are carried here, with those defaults, precisely so
that recalibration is a config change rather than a code edit.

NOTE: in Phase 2 these values are NOT yet injected into otlab_core.engine (engine/ is untouched and
still holds its own module-level defaults). This object is the intended single source of truth to
be threaded into the engine at the later, explicit enablement step; for now it governs the
extractor (state-signal identity, port) and stages the phase params for that step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PhaseConfig:
    """Phase-inference parameters (defaults = LTR-2026-03; see DEBT D3)."""

    window: int = 8
    slope_rising: float = 0.25
    slope_falling: float = -0.25
    reversal_n: int = 3
    stable_n: int = 6
    conf_full_slope: float = 1.0
    conf_hold: float = 0.9


@dataclass
class OpcUaConfig:
    """OPC UA extractor configuration."""

    #: TCP port carrying opc.tcp (configurable; OPC UA default is 4840).
    port: int = 4840
    #: Which NodeId is the process state signal, e.g. "ns=4;i=23".
    #: None => infer it as "the Float in read/publish responses" (valid for a single
    #: subscribed tag; see DEBT D1 in opcua.py for the multi-tag case).
    state_signal_node: Optional[str] = None


@dataclass
class ObserverConfig:
    phase: PhaseConfig = field(default_factory=PhaseConfig)
    opcua: OpcUaConfig = field(default_factory=OpcUaConfig)
