"""Thin adapter from a NormalizedEvent stream to the learned core (Phase 1C).

``LearnedEngineAdapter`` wires PhaseTracker + GrammarLearner + graded evaluator together over a
stream of ``NormalizedEvent`` objects. It consumes only protocol-neutral fields
(``state_signal_value`` to drive phase inference, and ``(op, target, value)`` to judge writes) —
never Modbus-specific fields.

IMPORTANT: this adapter is deliberately NOT wired into the live OT Lab passive path in Phase 1.
The existing declarative rules in ``app.py`` remain the default and current verdicts are
unchanged. This module exists so the protocol-neutral core can be exercised and, in a later
explicit step, switched into the passive path.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..config import PhaseConfig
from ..contract import NormalizedEvent
from .evaluator import Verdict, evaluate
from .grammar import GrammarLearner
from .phase_tracker import PhaseTracker


class LearnedEngineAdapter:
    def __init__(
        self,
        grammar: Optional[Dict[str, Any]] = None,
        learning: bool = False,
        phase_config: Optional[PhaseConfig] = None,
    ):
        # phase_config is produced by Level-2 auto-calibration (autocalibrate.learn_phase_config)
        # during the learn first pass, then passed here for the evaluate phase. None => LTR-03
        # defaults, so existing callers are unchanged.
        self.tracker = PhaseTracker(phase_config)
        self.grammar = grammar or {}
        self.learning = learning
        self.learner = GrammarLearner() if learning else None

    def observe_event(self, evt: NormalizedEvent) -> Optional[Verdict]:
        """Feed one event. Returns a Verdict for a judged write, else None.

        - Events carrying a reconstructed state signal advance phase inference.
        - Write requests are either learned (learning mode) or judged (evaluation mode).
        """
        if evt.state_signal_value is not None:
            # DEBT D4: consumer reads only the newest sample; a response may carry multiple state samples
            # (extract_state_samples returns all, in order). Feeding all samples to the PhaseTracker is
            # required for correct phase inference, but is deferred: how many samples per event and at what
            # cadence they are fed ties into automatic rate/parameter self-calibration (a separate research
            # task). Do NOT wire multi-sample feeding here until that design is settled. For now the Phase-3
            # lean observer feeds samples directly, bypassing this adapter.
            self.tracker.update(evt.state_signal_value)
            return None

        if evt.op == "WRITE_REQUEST" and evt.target is not None:
            phase = self.tracker.phase
            conf = self.tracker.confidence
            trans = self.tracker.transitioning
            if self.learning and self.learner is not None:
                self.learner.observe(evt.target, phase, conf, trans)
                return None
            return evaluate(self.grammar, evt.target, phase, conf, trans)

        return None

    def export_grammar(self) -> Dict[str, Any]:
        """Export the learned grammar document (learning mode only)."""
        if self.learner is None:
            raise RuntimeError("adapter is not in learning mode")
        return self.learner.export_document()
