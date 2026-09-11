"""Protocol-neutral learned evaluation core (brought in from LTR-2026-03).

Three pieces, none of which know anything about a protocol:

- ``PhaseTracker``   : infers operational phase from a scalar state-signal trajectory;
- ``GrammarLearner`` : learns a structural target->phase grammar from observation;
- ``evaluate``       : grades a write against a learned grammar (COHERENT/UNUSUAL/INCOHERENT/UNCERTAIN).

Plus ``calibrate_phase_config`` (derives the PhaseConfig from the observed state series) and
``classify_flows`` (discovers which flow is the state signal). The observer in ``liscere.observe``
drives these directly: it feeds every state sample to the tracker and every write to the learner or
the evaluator.
"""

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
