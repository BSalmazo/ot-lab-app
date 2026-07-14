"""Observer configuration.

Process-specific knobs live here as configuration, not as constants buried in the extractor or
the engine. Defaults reproduce the LTR-2026-03 tank values so nothing changes for the existing
setup, but they are now overridable per deployment.

As of the consolidation step, ``PhaseTracker`` reads its six phase-inference parameters from
``PhaseConfig`` (defaults = LTR-2026-03). The runtime / lean observer selects a profile — the
built-in defaults, or a named JSON such as ``profiles/opcua_tank_10hz.json`` loaded via
``PhaseConfig.from_json`` — without editing engine code. The engine only READS these numbers; it
never sets them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from typing import Optional


@dataclass
class PhaseConfig:
    """Phase-inference parameters.

    Defaults = the LTR-2026-03 tank values (~1 Hz Modbus), so ``PhaseTracker()`` with no config
    behaves exactly as before. These parameters are rate/dynamics-specific: the right values
    depend on the process's sampling cadence and how fast it fills / drains.

    DEBT D3 / Level-2 (auto-calibration): today these are set MANUALLY — the defaults here, or a
    named profile such as ``profiles/opcua_tank_10hz.json`` loaded via ``from_json``. Level-2
    auto-calibration will DERIVE them from observed sample cadence (``frame.time_epoch`` deltas)
    and process dynamics, replacing hand-tuned profiles. Not implemented here; the engine only
    reads this config and never sets these numbers itself.
    """

    window: int = 8
    slope_rising: float = 0.25
    slope_falling: float = -0.25
    reversal_n: int = 3
    stable_n: int = 6
    conf_full_slope: float = 1.0
    conf_hold: float = 0.9

    @classmethod
    def from_dict(cls, data: dict) -> "PhaseConfig":
        """Build from a dict, ignoring unknown keys (e.g. a ``description`` field in a profile)."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def from_json(cls, path) -> "PhaseConfig":
        """Load a named phase profile from a JSON file (see ``profiles/``)."""
        with open(path) as f:
            data = json.load(f)
        return cls.from_dict(data)


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
class ModbusConfig:
    """Modbus/TCP extractor configuration."""

    #: Which holding register carries the process state signal, e.g. 2 for HR[2].
    #: None => treat every FC3 read-response value as a state sample (valid when a single
    #: register is polled; see the single-polled-register note in modbus.py). The behavioural
    #: classifier still keys a separate flow per register regardless of this value.
    state_signal_register: Optional[int] = None

    #: Write-coalescing window, in seconds. Consecutive WRITE_REQUEST events with an IDENTICAL
    #: (target register, value) are folded into a single COMMAND event (first timestamp wins; a
    #: repeat_count is kept) while the run stays within this window. This removes protocol-level
    #: repetition: an HMI holding a button re-issues the same write every ~230 ms at the wire,
    #: which is one operator action, not many.
    #:
    #: This 1.0 s is the FALLBACK default. When OBSERVE calibration measures the process, the
    #: orchestrator (scripts/liscere_observe.py) DERIVES the window from the observed half-cycle:
    #:
    #:     coalesce_window_s = clamp(observed_half_cycle_seconds / 20, 0.5, 5.0)
    #:
    #: where observed_half_cycle_seconds = calibration period_samples * inter-sample dt. Rationale:
    #: the 0.5 s floor sits above the ~230 ms HMI repetition cadence, so a held button still
    #: coalesces; the 5.0 s ceiling bounds very slow processes; taking 1/20 of a half-cycle keeps
    #: the window well below the phase duration, so coalescing never spans a phase reversal. The
    #: default here is used only when no calibration period is available.
    coalesce_window_s: float = 1.0


@dataclass
class ObserverConfig:
    phase: PhaseConfig = field(default_factory=PhaseConfig)
    opcua: OpcUaConfig = field(default_factory=OpcUaConfig)
    modbus: ModbusConfig = field(default_factory=ModbusConfig)
