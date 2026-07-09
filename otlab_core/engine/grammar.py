"""Operational grammar learning (brought in from LTR-2026-03, protocol-neutral).

``GrammarLearner`` observes normal operation and learns, per control target, the distribution of
operational phases in which that target is written — a structural artefact->phase association,
never a value baseline. It is a protocol-neutral port of ``liscere_learn.py``: where the original
keyed on Modbus register numbers and carried a register_name, this keys on an opaque ``target``
(an int register today, an OPC UA node id tomorrow) and carries no protocol-specific naming.

Only writes seen under a confidently-known, non-transition phase are learned, so the grammar is
not contaminated by ambiguous moments. ``export()`` turns counts into fractions; the produced
grammar is the same JSON shape LTR-2026-03 published (minus the protocol-specific register_name).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict

# Only learn from writes seen under a confidently-known, non-transition phase.
LEARN_MIN_CONFIDENCE = 0.6

# Fraction at/above which a phase is considered to "belong" to a target.
BELONGS_MIN_FRACTION = 0.1


class GrammarLearner:
    def __init__(self, min_confidence: float = LEARN_MIN_CONFIDENCE):
        self.min_confidence = min_confidence
        # counts[target][phase] = number of writes seen in that phase
        self.counts: Dict[Any, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.skipped_low_confidence = 0

    def observe(self, target, phase: str, confidence: float, transitioning: bool) -> bool:
        """Record one observed write. Returns True if it was learned, False if skipped."""
        if transitioning or confidence < self.min_confidence or phase == "UNKNOWN":
            self.skipped_low_confidence += 1
            return False
        self.counts[target][phase] += 1
        return True

    def export(self) -> Dict[str, Any]:
        """Turn observed (target, phase) counts into a learned grammar (fractions)."""
        grammar: Dict[str, Any] = {}
        for target, phase_counts in self.counts.items():
            total = sum(phase_counts.values())
            phases = {}
            for ph, c in phase_counts.items():
                phases[ph] = {"count": c, "fraction": round(c / total, 3)}
            belongs = sorted(
                [ph for ph, info in phases.items() if info["fraction"] >= BELONGS_MIN_FRACTION]
            )
            grammar[str(target)] = {
                "target": target,
                "total_writes_observed": total,
                "phase_distribution": phases,
                "learned_coherent_phases": belongs,
            }
        return grammar

    def export_document(self) -> Dict[str, Any]:
        """The full learned-grammar document, including learning metadata."""
        return {
            "min_confidence_for_learning": self.min_confidence,
            "writes_skipped_low_confidence": self.skipped_low_confidence,
            "grammar": self.export(),
        }
