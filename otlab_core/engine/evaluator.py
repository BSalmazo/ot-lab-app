"""Graded coherence evaluation (brought in from LTR-2026-03, protocol-neutral).

Judges a write by the degree of coherence between the current inferred phase and the learned
phase distribution for the written target. Verdicts: COHERENT (allow), UNUSUAL (surface for
review), INCOHERENT (alert), plus UNCERTAIN when the phase itself is unsettled. This is a
protocol-neutral port of ``liscere_evaluator_step3.py``'s ``evaluate_write``: it consumes a
learned grammar and a normalized ``(target, phase, confidence, transitioning)``, no Modbus.

Rule ids OBS-R005 to OBS-R009 come from LTR-2026-03. OBS-R000 to OBS-R004 belonged to the retired
declarative engine (tag ``legacy-engine-v1``) and are not used here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

# Graded thresholds on the learned phase fraction for the current target.
COHERENT_MIN = 0.30            # fraction >= this => COHERENT
# 0 < fraction < COHERENT_MIN => UNUSUAL ; fraction == 0 => INCOHERENT

# Phase confidence below which we treat the phase itself as uncertain (LTR-2026-03 Step 2).
PHASE_MIN_CONFIDENCE = 0.6


@dataclass
class Verdict:
    verdict: str                     # COHERENT | UNUSUAL | INCOHERENT | UNCERTAIN
    rule: str                        # OBS-Rxxx (see module note on namespace collision)
    learned_fraction: Optional[float]
    reason: str


def phase_fraction(grammar: Dict[str, Any], target, phase: str) -> Optional[float]:
    """Learned fraction of writes to ``target`` that occurred in ``phase``.

    Returns None if the target was never seen during learning, 0.0 if the target was seen but
    never in this phase, else the learned fraction.
    """
    entry = grammar.get(str(target))
    if not entry:
        return None
    dist = entry.get("phase_distribution", {})
    info = dist.get(phase)
    if not info:
        return 0.0
    return info.get("fraction", 0.0)


def evaluate(grammar: Dict[str, Any], target, phase: str, confidence: float,
             transitioning: bool) -> Verdict:
    # Phase itself unsettled -> uncertainty dominates (Step 2 carry-over).
    if transitioning or phase == "UNKNOWN" or confidence < PHASE_MIN_CONFIDENCE:
        return Verdict("UNCERTAIN", "OBS-R005", None,
                       f"write to {target} while phase is unsettled "
                       f"(phase={phase}, confidence={confidence:.2f}); surfaced for review")

    frac = phase_fraction(grammar, target, phase)
    if frac is None:
        return Verdict("UNUSUAL", "OBS-R006", None,
                       f"write to {target} but this target was never observed during "
                       f"learning; surfaced for review")

    if frac >= COHERENT_MIN:
        return Verdict("COHERENT", "OBS-R007", round(frac, 3),
                       f"write to {target} in phase {phase} matches learned grammar "
                       f"(learned fraction={frac:.2f})")
    if frac > 0.0:
        return Verdict("UNUSUAL", "OBS-R008", round(frac, 3),
                       f"write to {target} in phase {phase} is rare in learned grammar "
                       f"(learned fraction={frac:.2f}); surfaced for review")
    return Verdict("INCOHERENT", "OBS-R009", 0.0,
                   f"write to {target} in phase {phase} was never observed during "
                   f"learning for this target; incoherent with learned grammar")
