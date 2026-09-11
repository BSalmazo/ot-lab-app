"""Process cycles and grammar stabilisation, in the observer layer (MODERNISATION_PLAN item 8).

Two pure, protocol-neutral helpers the observer drives from the phase stream. Neither touches the
engine: ``PhaseTracker`` still infers phases and ``GrammarLearner`` still counts writes exactly as
before. These only watch.

``CycleTracker`` counts completed process cycles from the de-duplicated phase sequence. It assumes
the process is periodic in its phase sequence (the bench: RISING, STABLE, RISING, STABLE, FALLING,
then again) and finds the period as the smallest p for which the sequence repeats with lag p. The
first cycle is only known once it starts repeating, so the count is 0 until the second cycle begins;
that is honest, not late: a single cycle cannot show a period. If the sequence later stops
repeating with that period, the tracker reports the break and stops counting rather than guessing.

``Stabilisation`` snapshots the grammar at each completed cycle and measures how much it moved: the
largest change in any target's phase fractions (a target absent from the previous snapshot counts as
a full change), and whether the set of learned coherent phases changed for any target. The model is
called stable after K consecutive cycles with no set change and a fraction change below epsilon.
K and epsilon are research parameters: they are stored in every run's record and printed beside
cycles_to_stable, never silently assumed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class CycleTracker:
    """Counts completed process cycles from the de-duplicated phase sequence.

    A prefix can look periodic before the real cycle has shown itself (RISING, STABLE, RISING reads as
    period 2 until the FALLING arrives), so a period is only CONFIRMED once the sequence has repeated
    it twice in full. Until then no cycle is reported; at confirmation the boundaries already passed
    are reported together, each with the snapshot taken when that phase change happened, so nothing
    is lost by confirming late. After confirmation each further repetition is one cycle; a phase that
    breaks the pattern marks the tracker broken and counting stops rather than guessing.
    """

    def __init__(self):
        self.phases: List[str] = []       # de-duplicated phase sequence since the first known phase
        self.marks: List[Any] = []        # (t, snapshot) per entry of ``phases``
        self.period: Optional[int] = None
        self.cycles = 0
        self.broken = False
        self.boundaries: List[float] = []
        self._reported = 0                # index up to which boundaries have been reported

    def update(self, phase: str, t: float, snapshot=None) -> List[Dict[str, Any]]:
        """Feed the current phase (every sample; de-duplicated here). ``snapshot`` is called only on a
        phase change and its result kept with that mark. Returns the cycles completed by this call,
        each {"cycle", "t", "snapshot"}; usually empty, one, or several at confirmation."""
        if phase in (None, "UNKNOWN") or self.broken:
            return []
        if self.phases and self.phases[-1] == phase:
            return []
        self.phases.append(phase)
        self.marks.append((t, snapshot() if snapshot is not None else None))
        n = len(self.phases)
        if self.period is None:
            p = self._confirmed_period()
            if p is None:
                return []
            self.period = p
        elif self.phases[-1] != self.phases[-1 - self.period]:
            self.broken = True
            return []
        out = []
        p = self.period
        # every index i with i % p == 0 and i > 0 is the first phase of a new repetition: a boundary
        for i in range(self._reported + 1, n):
            if i % p == 0:
                self.cycles += 1
                tb, snap = self.marks[i]
                self.boundaries.append(tb)
                out.append({"cycle": self.cycles, "t": tb, "snapshot": snap})
        self._reported = n - 1
        return out

    def _confirmed_period(self) -> Optional[int]:
        seq = self.phases
        n = len(seq)
        for p in range(2, n):
            if (n - 1) % p == 0 and (n - 1) >= 2 * p and all(seq[i] == seq[i - p] for i in range(p, n)):
                return p
        return None

    def summary(self) -> Dict[str, Any]:
        return {"cycles": self.cycles, "period_phases": self.period, "broken": self.broken,
                "sequence": self.phases[: (self.period or len(self.phases))], "boundaries": list(self.boundaries)}


def grammar_distance(prev: Dict[str, Any], cur: Dict[str, Any]) -> Dict[str, Any]:
    """Largest L1 change in any target's phase fractions between two grammar exports, and whether
    the learned coherent phase set changed for any target. A target absent from ``prev`` counts as a
    change of 1.0 (all its mass is new)."""
    max_change = 0.0
    set_changed = False
    for target, entry in cur.items():
        pdist = (prev.get(target) or {}).get("phase_distribution", {})
        cdist = entry.get("phase_distribution", {})
        phases = set(pdist) | set(cdist)
        l1 = sum(abs(cdist.get(ph, {}).get("fraction", 0.0) - pdist.get(ph, {}).get("fraction", 0.0)) for ph in phases)
        if target not in prev:
            l1 = 1.0
        max_change = max(max_change, l1)
        if (prev.get(target) or {}).get("learned_coherent_phases") != entry.get("learned_coherent_phases"):
            set_changed = True
    for target in prev:
        if target not in cur:
            set_changed = True
            max_change = 1.0
    return {"max_fraction_change": round(max_change, 4), "coherent_set_changed": set_changed}


class Stabilisation:
    def __init__(self, k: int = 3, epsilon: float = 0.05):
        self.k = int(k)
        self.epsilon = float(epsilon)
        self.history: List[Dict[str, Any]] = []
        self.prev: Optional[Dict[str, Any]] = None
        self.streak = 0
        self.stable_at_cycle: Optional[int] = None
        self.stable_at_t: Optional[float] = None
        self.start_t: Optional[float] = None

    def start(self, t: float) -> None:
        self.start_t = t

    def on_cycle(self, cycle: int, grammar: Dict[str, Any], t: float, writes_observed: int) -> Dict[str, Any]:
        """Record a completed cycle's grammar snapshot. Returns the progress record for that cycle."""
        if self.prev is None:
            d = {"max_fraction_change": None, "coherent_set_changed": None}
            self.streak = 0
        else:
            d = grammar_distance(self.prev, grammar)
            change = d["max_fraction_change"]
            if not d["coherent_set_changed"] and change is not None and change < self.epsilon:
                self.streak += 1
            else:
                self.streak = 0
        self.prev = grammar
        rec = {"cycle": cycle, "t": t, "targets": len(grammar), "writes_observed": writes_observed,
               **d, "stable_cycles": self.streak, "stable": self.streak >= self.k}
        if rec["stable"] and self.stable_at_cycle is None:
            self.stable_at_cycle = cycle
            self.stable_at_t = t
        self.history.append(rec)
        return rec

    @property
    def stable(self) -> bool:
        return self.stable_at_cycle is not None

    def summary(self) -> Dict[str, Any]:
        return {
            "k": self.k, "epsilon": self.epsilon,
            "cycles_observed": len(self.history),
            "cycles_to_stable": self.stable_at_cycle,
            "wall_seconds_to_stable": (round(self.stable_at_t - self.start_t, 3)
                                       if self.stable_at_t is not None and self.start_t is not None else None),
            "writes_observed": self.history[-1]["writes_observed"] if self.history else 0,
            "history": self.history,
        }
