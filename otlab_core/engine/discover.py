"""State-signal discovery via behavioural flow classification (Degrau 4, level 4a).

Protocol-agnostic CORE. Given a set of ``Flow`` objects (a stream of (timestamp, value) samples
plus a protocol-supplied ``role_hint``), it classifies each flow behaviourally and identifies which
flow — if any — is the process STATE signal, so the observer can DISCOVER the state variable
instead of being told a NodeId / register.

The classification criterion is behavioural and lives here; each ProtocolExtractor enriches the
decision with protocol-specific role hints that REINFORCE, not replace, it. There is no
protocol-specific logic in this module.

Verdicts:
  STATE             — a continuously-varying process variable (many distinct values, small
                      per-sample step relative to its range, at least one direction reversal).
  COMMAND           — an operator-written control point (by role hint).
  CONSTANT_METADATA — a static read/telemetry value (by role hint + very few distinct values).
  AMBIGUOUS         — none of the above.

DEBT D5 (thresholds): STATE_MIN_UNIQUE / STATE_STEP_FRAC / STATE_MIN_REVERSALS are fixed defaults.
Deriving them from observation (as Level-2 does for the phase params) is future work.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

# --- classification thresholds (DEBT D5: fixed defaults; future work to derive from observation) ---
STATE_MIN_UNIQUE = 30       # a state signal takes many distinct values
STATE_STEP_FRAC = 0.1       # its per-sample step is small relative to its total range
STATE_MIN_REVERSALS = 1     # and it turns around at least once (fills then drains)

# --- behavioural detector internals (dimensionless / self-relative; no absolute scale) ---
_CONST_MAX_UNIQUE = 5       # "very few distinct values" for CONSTANT_METADATA
_SMOOTH_SPAN = 5            # moving-average span before reversal sign detection
_DEADBAND_STEP_FRAC = 0.5   # reversal deadband, relative to the flow's own median step


@dataclass
class Flow:
    """One identified stream of samples, tagged by the extractor with a role hint."""

    key: str
    samples: List[Tuple[float, float]]     # (timestamp, value) in order
    role_hint: str = "unknown"             # "telemetry" | "command" | "read" | "unknown"
    # The wire data type, WHEN the protocol declares it (OPC UA Variant Type). None when the
    # protocol carries no type (Modbus) or the variant is unmapped; datatype_certain says which.
    datatype: Optional[str] = None
    datatype_certain: bool = False
    # Observed server endpoint "ip:port" for this flow, when the extractor supplies it (from
    # traffic). Protocol-neutral: the discovery layer just carries it through for the UI.
    server: Optional[str] = None


@dataclass
class FlowFeatures:
    n: int
    unique_values: int
    value_range: float
    median_step: float
    reversals: int


@dataclass
class FlowVerdict:
    key: str
    role_hint: str
    features: FlowFeatures
    verdict: str                            # STATE | COMMAND | CONSTANT_METADATA | AMBIGUOUS
    datatype: Optional[str] = None          # carried through from the Flow (protocol-declared type)
    datatype_certain: bool = False


@dataclass
class DiscoveryResult:
    flows: List[FlowVerdict]
    state_flow_key: Optional[str]           # the discovered state signal, or None

    def state_flows(self) -> List[FlowVerdict]:
        return [f for f in self.flows if f.verdict == "STATE"]


def _moving_average(series: List[float], span: int) -> List[float]:
    out: List[float] = []
    dq: deque = deque(maxlen=span)
    for x in series:
        dq.append(x)
        out.append(sum(dq) / len(dq))
    return out


def _reversals(values: List[float], median_step: float) -> int:
    if len(values) < 3:
        return 0
    sm = _moving_average(values, _SMOOTH_SPAN)
    deadband = median_step * _DEADBAND_STEP_FRAC
    rev = 0
    last = 0
    for a, b in zip(sm, sm[1:]):
        d = b - a
        if d > deadband:
            s = 1
        elif d < -deadband:
            s = -1
        else:
            s = 0
        if s == 0:
            continue
        if last != 0 and s != last:
            rev += 1
        last = s
    return rev


def _features(values: List[float]) -> FlowFeatures:
    n = len(values)
    unique = len(set(values))
    value_range = (max(values) - min(values)) if values else 0.0
    steps = [abs(b - a) for a, b in zip(values, values[1:])]
    median_step = statistics.median(steps) if steps else 0.0
    reversals = _reversals(values, median_step)
    return FlowFeatures(n=n, unique_values=unique, value_range=value_range,
                        median_step=median_step, reversals=reversals)


def _classify(feat: FlowFeatures, role_hint: str) -> str:
    if (
        feat.unique_values > STATE_MIN_UNIQUE
        and feat.value_range > 0
        and feat.median_step < feat.value_range * STATE_STEP_FRAC
        and feat.reversals >= STATE_MIN_REVERSALS
    ):
        return "STATE"
    if role_hint == "command":
        return "COMMAND"
    if role_hint in ("read", "telemetry") and feat.unique_values <= _CONST_MAX_UNIQUE:
        return "CONSTANT_METADATA"
    return "AMBIGUOUS"


def classify_flows(flows: Sequence[Flow]) -> DiscoveryResult:
    """Classify each flow behaviourally and identify the STATE signal (if any).

    The result is auditable: every flow's measured features and verdict are returned, so a human
    can see the numbers behind each decision. If more than one flow qualifies as STATE, the one
    with the most distinct values is reported as the state signal.
    """
    verdicts: List[FlowVerdict] = []
    for fl in flows:
        values = [float(v) for _, v in fl.samples]
        feat = _features(values)
        verdicts.append(FlowVerdict(key=fl.key, role_hint=fl.role_hint, features=feat,
                                    verdict=_classify(feat, fl.role_hint),
                                    datatype=fl.datatype, datatype_certain=fl.datatype_certain))
    states = [v for v in verdicts if v.verdict == "STATE"]
    state_key = max(states, key=lambda v: v.features.unique_values).key if states else None
    return DiscoveryResult(flows=verdicts, state_flow_key=state_key)
