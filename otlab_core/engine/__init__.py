"""Protocol-neutral learned evaluation core (brought in from LTR-2026-03).

Three pieces, none of which know anything about a protocol:

- ``PhaseTracker``   : infers operational phase from a scalar state-signal trajectory;
- ``GrammarLearner`` : learns a structural target->phase grammar from observation;
- ``evaluate``       : grades a write against a learned grammar (COHERENT/UNUSUAL/INCOHERENT/UNCERTAIN).

``LearnedEngineAdapter`` bridges these to a NormalizedEvent stream. In Phase 1 the core is landed
as a module + adapter only; it is NOT wired into the live passive path (the declarative rules in
app.py remain the default), so current OT Lab behaviour is unchanged.
"""

from .adapter import LearnedEngineAdapter
from .autocalibrate import (
    CalibrationResult,
    calibrate_phase_config,
    learn_phase_config,
    write_profile,
)
from .discover import (
    DiscoveryResult,
    Flow,
    FlowFeatures,
    FlowVerdict,
    classify_flows,
)
from .evaluator import Verdict, evaluate, phase_fraction
from .grammar import GrammarLearner
from .phase_tracker import PhaseTracker

__all__ = [
    "PhaseTracker",
    "GrammarLearner",
    "evaluate",
    "phase_fraction",
    "Verdict",
    "LearnedEngineAdapter",
    "calibrate_phase_config",
    "learn_phase_config",
    "write_profile",
    "CalibrationResult",
    "classify_flows",
    "Flow",
    "FlowFeatures",
    "FlowVerdict",
    "DiscoveryResult",
]
