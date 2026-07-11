"""Level-2 automatic phase-parameter calibration.

Produces a ``PhaseConfig`` from a learn-window of ``(timestamp, state_sample)`` pairs, PURELY
statistically. It never calls ``PhaseTracker`` and never infers phase (that would be
chicken-and-egg); it only measures the observed signal and derives numbers.

Agnostic to protocol, cadence, and process scale: every derived parameter is a DIMENSIONLESS
ratio of the process's own measured half-cycle period ``P`` (in samples), or a direct function of
the measured per-sample ``move`` / ``noise``. There are NO absolute-time constants, NO assumed
sample rate, and NO values tuned to a specific testbed. The only literals are dimensionless
detector thresholds (a "is it moving" epsilon, a smoothing span, a sign deadband, a fit span).

This supersedes manually-authored phase profiles (e.g. ``profiles/opcua_tank_10hz.json``): during
learn mode a first pass observes the clean process, measures, and derives the ``PhaseConfig`` the
``PhaseTracker`` then consumes. ``PhaseTracker``'s inference logic is unchanged — this only
PRODUCES its config.
"""

from __future__ import annotations

import json
import statistics
from collections import deque
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from ..config import PhaseConfig

# --- dimensionless / signal-derived detector thresholds (no absolute time, no assumed rate) ---
_MOVE_EPS = 0.001       # |level delta| above which a sample counts as "moving"
_SMOOTH_SPAN = 5        # moving-average span to kill micro-jitter before sign detection
_SIGN_DEADBAND = 0.01   # |smoothed delta| below this is treated as flat (sign 0)
_FIT_SPAN = 10          # local window for linear-fit residuals (noise estimate)

_FALLBACK_WARNING = (
    "period not measurable; using defaults; extend the learn window to cover >=1.5 process cycles"
)


@dataclass
class CalibrationResult:
    config: PhaseConfig
    measurable: bool
    dt: float                # median positive inter-sample timestamp delta (diagnostic only)
    move: float              # median |level delta| over moving samples
    noise: float             # population stdev of local linear-fit residuals
    period_samples: float    # median half-cycle length, in samples
    reversals: int
    warning: Optional[str] = None


# --------------------------------------------------------------------------- helpers

def unfold(records: Sequence[Tuple[float, Sequence[float]]]) -> List[Tuple[float, float]]:
    """Flatten ``(t, [sample, ...])`` records into a flat ``[(t, sample), ...]`` list, in order.

    A single message may carry several state samples; each becomes its own ordered pair so the
    calibrator sees the true per-sample series.
    """
    out: List[Tuple[float, float]] = []
    for t, samples in records:
        for s in samples:
            out.append((float(t), float(s)))
    return out


def _ols(xs: List[float], ys: List[float]) -> Tuple[float, float]:
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    den = sum((xs[i] - mx) ** 2 for i in range(n))
    m = num / den if den else 0.0
    return m, my - m * mx


def _median_positive_dt(times: List[float]) -> float:
    deltas = [b - a for a, b in zip(times, times[1:]) if (b - a) > 0]
    return statistics.median(deltas) if deltas else 0.0


def _move(levels: List[float]) -> float:
    moving = [abs(b - a) for a, b in zip(levels, levels[1:]) if abs(b - a) > _MOVE_EPS]
    return statistics.median(moving) if moving else 0.0


def _noise(levels: List[float], span: int = _FIT_SPAN) -> float:
    """Population stdev of residuals from a rolling local linear fit (centered)."""
    n = len(levels)
    if n < span:
        return 0.0
    half = span // 2
    xs = list(range(span))
    resid: List[float] = []
    for c in range(half, n - half):
        window = levels[c - half:c - half + span]
        m, b = _ols(xs, window)
        resid.append(levels[c] - (m * half + b))
    return statistics.pstdev(resid) if len(resid) > 1 else 0.0


def _moving_average(series: List[float], span: int) -> List[float]:
    out: List[float] = []
    dq: deque = deque(maxlen=span)
    for x in series:
        dq.append(x)
        out.append(sum(dq) / len(dq))
    return out


def _half_cycle_period(levels: List[float]) -> Tuple[Optional[float], int]:
    """Median half-cycle length in samples, and the number of reversals found."""
    sm = _moving_average(levels, _SMOOTH_SPAN)
    signs: List[int] = []
    for a, b in zip(sm, sm[1:]):
        d = b - a
        if d > _SIGN_DEADBAND:
            signs.append(1)
        elif d < -_SIGN_DEADBAND:
            signs.append(-1)
        else:
            signs.append(0)
    reversals: List[int] = []
    last = 0
    for i, s in enumerate(signs):
        if s == 0:
            continue
        if last != 0 and s != last:
            reversals.append(i)
        last = s
    if len(reversals) < 2:
        return None, len(reversals)
    half_cycles = [b - a for a, b in zip(reversals, reversals[1:])]
    return statistics.median(half_cycles), len(reversals)


# --------------------------------------------------------------------------- calibrator

def calibrate_phase_config(samples: Sequence[Tuple[float, float]]) -> CalibrationResult:
    """Derive a ``PhaseConfig`` from ``(timestamp, level)`` samples (already unfolded)."""
    times = [float(t) for t, _ in samples]
    levels = [float(v) for _, v in samples]

    dt = _median_positive_dt(times)
    move = _move(levels)
    noise = _noise(levels)
    period, n_reversals = _half_cycle_period(levels)

    if period is None or period <= 0:
        return CalibrationResult(
            config=PhaseConfig(), measurable=False, dt=dt, move=move, noise=noise,
            period_samples=(period or 0.0), reversals=n_reversals, warning=_FALLBACK_WARNING,
        )

    # DERIVE — every parameter is a dimensionless ratio of P, or a function of move/noise.
    window = max(3, round(period / 30))
    reversal_n = max(2, round(period / 120))
    stable_n = max(3, round(period / 20))
    slope_rising = max(3.0 * noise, 0.3 * move)   # robust-to-noise rule

    config = PhaseConfig(
        window=window,
        slope_rising=slope_rising,
        slope_falling=-slope_rising,
        reversal_n=reversal_n,
        stable_n=stable_n,
        conf_full_slope=move,
        conf_hold=PhaseConfig().conf_hold,        # keep existing default
    )
    return CalibrationResult(
        config=config, measurable=True, dt=dt, move=move, noise=noise,
        period_samples=period, reversals=n_reversals,
    )


def learn_phase_config(
    records: Sequence[Tuple[float, Sequence[float]]],
    profile_path=None,
    description: Optional[str] = None,
) -> CalibrationResult:
    """Learn-mode first pass: unfold multi-sample records, calibrate, optionally write a profile.

    This is the entry point the learn path uses BEFORE any phase inference: it observes the clean
    learn window as ``(t, [sample, ...])`` records, derives a ``PhaseConfig`` purely statistically,
    and (if ``profile_path`` is given) writes it as a profile JSON. The caller then constructs the
    ``PhaseTracker`` from ``result.config`` for the subsequent evaluate phase.
    """
    samples = unfold(records)
    result = calibrate_phase_config(samples)
    if profile_path is not None:
        write_profile(result.config, profile_path, description=description)
    return result


def write_profile(config: PhaseConfig, path, description: Optional[str] = None) -> None:
    """Write a ``PhaseConfig`` to a profile JSON (same format ``PhaseConfig.from_json`` reads)."""
    data = {
        "description": description or (
            "Auto-generated by Level-2 calibration (otlab_core/engine/autocalibrate.py) from the "
            "learn-window traffic. Supersedes manually-tuned profiles."
        ),
        "window": config.window,
        "slope_rising": config.slope_rising,
        "slope_falling": config.slope_falling,
        "reversal_n": config.reversal_n,
        "stable_n": config.stable_n,
        "conf_full_slope": config.conf_full_slope,
        "conf_hold": config.conf_hold,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
