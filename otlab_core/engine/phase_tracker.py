"""Operational phase inference (brought in from LTR-2026-03, protocol-neutral).

``PhaseTracker`` infers the operational phase (FILLING / DRAINING / STABLE / unsettled) of a
process from the trajectory of a single scalar state signal, using a least-squares slope over a
sliding window plus hysteresis ("phase inertia") and a confidence value. It is a verbatim port
of the tracker validated in LTR-2026-03 (Steps 2/3): it consumes plain floats and knows nothing
about Modbus, registers, or any protocol.

The tuning constants below are the values LTR-2026-03 fixed empirically for the tank testbed;
they are process-dependent (see that report, section 8.3) and are exposed here as module-level
constants so a later phase can make them configurable per process.
"""

from __future__ import annotations

from collections import deque
from typing import List, Optional, Tuple

# --- Phase inference tuning (LTR-2026-03 Step 2/3) ---
WINDOW = 8              # samples used for the slope regression
SLOPE_RISING = 0.25     # slope >= this => rising movement
SLOPE_FALLING = -0.25   # slope <= this => falling movement
REVERSAL_N = 3          # consecutive opposite-direction samples to confirm a reversal
STABLE_N = 6            # consecutive flat samples (no movement) to settle into STABLE
CONF_FULL_SLOPE = 1.0   # |slope| at/above which moving confidence is maximal
CONF_HOLD = 0.9         # confidence held during a short pause within a moving phase


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
    def __init__(self):
        self.levels = deque(maxlen=WINDOW)
        self.phase = "UNKNOWN"
        self.confidence = 0.0
        self.transitioning = False
        self.last_level = None
        self._reversal_dir = None
        self._reversal_count = 0
        self._flat_count = 0

    def _movement(self, slope: float) -> Optional[str]:
        if slope >= SLOPE_RISING:
            return "FILLING"
        if slope <= SLOPE_FALLING:
            return "DRAINING"
        return None

    def update(self, level: float) -> Tuple[str, float, bool]:
        self.levels.append(level)
        self.last_level = level
        slope = _slope(list(self.levels))
        mag = min(abs(slope) / CONF_FULL_SLOPE, 1.0)
        move = self._movement(slope)

        if move is not None:
            self._flat_count = 0
            if self.phase in ("FILLING", "DRAINING") and move != self.phase:
                self.transitioning = True
                if self._reversal_dir == move:
                    self._reversal_count += 1
                else:
                    self._reversal_dir = move
                    self._reversal_count = 1
                self.confidence = mag * (self._reversal_count / REVERSAL_N) * 0.5
                if self._reversal_count >= REVERSAL_N:
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
            if self.phase in ("FILLING", "DRAINING"):
                if self._flat_count < STABLE_N:
                    self.transitioning = False
                    self.confidence = CONF_HOLD
                else:
                    self.phase = "STABLE"
                    self.confidence = 1.0
                    self.transitioning = False
            elif self.phase == "STABLE":
                self.confidence = 1.0
                self.transitioning = False
            else:
                # UNKNOWN: needs a sustained flat run to become STABLE.
                if self._flat_count >= STABLE_N:
                    self.phase = "STABLE"
                    self.confidence = 1.0
                    self.transitioning = False
                else:
                    self.transitioning = True
                    self.confidence = 0.4

        return self.phase, self.confidence, self.transitioning
