"""Operational phase inference (brought in from LTR-2026-03, protocol-neutral).

``PhaseTracker`` infers the operational phase (RISING / FALLING / STABLE / unsettled) of a
process from the trajectory of a single scalar state signal, using a least-squares slope over a
sliding window plus hysteresis ("phase inertia") and a confidence value. It consumes plain floats
and knows nothing about Modbus, registers, or any protocol.

The six tuning parameters (window, slope thresholds, hysteresis counts, confidence scaling) are
NOT hardcoded here: they are read from a ``PhaseConfig`` (``otlab_core.config``). The defaults of
that config are the LTR-2026-03 values, so ``PhaseTracker()`` with no argument behaves exactly as
before. A later Level-2 module will PRODUCE a calibrated ``PhaseConfig`` from observed traffic;
this class does not change when that happens — it just reads the config it is given.
"""

from __future__ import annotations

from collections import deque
from typing import List, Optional, Tuple

from ..config import PhaseConfig


def _slope(samples: List[float]) -> float:
    n = len(samples)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(samples) / n
    num = sum((xs[i] - mx) * (samples[i] - my) for i in range(n))
    den = sum((xs[i] - mx) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return num / den


class PhaseTracker:
    def __init__(self, config: Optional[PhaseConfig] = None):
        cfg = config or PhaseConfig()
        self.config = cfg
        # Phase-inference parameters, sourced from config (defaults = LTR-2026-03).
        self.window = cfg.window
        self.slope_rising = cfg.slope_rising
        self.slope_falling = cfg.slope_falling
        self.reversal_n = cfg.reversal_n
        self.stable_n = cfg.stable_n
        self.conf_full_slope = cfg.conf_full_slope
        self.conf_hold = cfg.conf_hold

        self.levels = deque(maxlen=self.window)
        self.phase = "UNKNOWN"
        self.confidence = 0.0
        self.transitioning = False
        self.last_level = None
        self._reversal_dir = None
        self._reversal_count = 0
        self._flat_count = 0

    def _movement(self, slope: float) -> Optional[str]:
        if slope >= self.slope_rising:
            return "RISING"
        if slope <= self.slope_falling:
            return "FALLING"
        return None

    def update(self, level: float) -> Tuple[str, float, bool]:
        self.levels.append(level)
        self.last_level = level
        slope = _slope(list(self.levels))
        mag = min(abs(slope) / self.conf_full_slope, 1.0)
        move = self._movement(slope)

        if move is not None:
            self._flat_count = 0
            if self.phase in ("RISING", "FALLING") and move != self.phase:
                self.transitioning = True
                if self._reversal_dir == move:
                    self._reversal_count += 1
                else:
                    self._reversal_dir = move
                    self._reversal_count = 1
                self.confidence = mag * (self._reversal_count / self.reversal_n) * 0.5
                if self._reversal_count >= self.reversal_n:
                    self.phase = move
                    self.confidence = mag
                    self.transitioning = False
                    self._reversal_dir = None
                    self._reversal_count = 0
            else:
                self.phase = move
                self.confidence = mag
                self.transitioning = False
                self._reversal_dir = None
                self._reversal_count = 0
        else:
            self._reversal_dir = None
            self._reversal_count = 0
            self._flat_count += 1
            if self.phase in ("RISING", "FALLING"):
                if self._flat_count < self.stable_n:
                    self.transitioning = False
                    self.confidence = self.conf_hold
                else:
                    self.phase = "STABLE"
                    self.confidence = 1.0
                    self.transitioning = False
            elif self.phase == "STABLE":
                self.confidence = 1.0
                self.transitioning = False
            else:
                # UNKNOWN: needs a sustained flat run to become STABLE.
                if self._flat_count >= self.stable_n:
                    self.phase = "STABLE"
                    self.confidence = 1.0
                    self.transitioning = False
                else:
                    self.transitioning = True
                    self.confidence = 0.4

        return self.phase, self.confidence, self.transitioning
