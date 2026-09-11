"""The observer's notion of "now".

Every timing decision in the observer (the observe and learn window deadlines, the hold-resampler's
silence clock, the ``ts`` stamped on emitted events) reads one clock object instead of the wall
clock directly. Live capture uses ``LiveClock`` (wall time, as before). Replay uses ``FrameClock``,
which advances only when a captured frame is handed to it, so a recorded run replays identically
however fast the machine is: same input, same decisions, same output.
"""

from __future__ import annotations

import time
from typing import Optional


class LiveClock:
    """Wall-clock time. The live observer's clock."""

    kind = "wall"

    def now(self) -> float:
        return time.time()


class FrameClock:
    """Frame time: ``now()`` is the timestamp of the latest frame handed to ``advance``.

    Never goes backwards (a frame stamped earlier than the current time does not rewind it), and
    reports ``None`` until the first frame arrives, so a caller can tell "no frame yet" from a real
    time. The replay driver advances it once per frame, before that frame is dispatched, so the
    deadlines and the resampler see the same time the extractor's event carries.
    """

    kind = "frame"

    def __init__(self, start: Optional[float] = None):
        self._t = start

    def now(self) -> Optional[float]:
        return self._t

    def advance(self, t: float) -> None:
        if t is None:
            return
        if self._t is None or t > self._t:
            self._t = float(t)
