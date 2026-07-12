#!/usr/bin/env python3
"""Validation test for Level-2 phase-parameter auto-calibration.

Reproduces the empirically-validated result: a process with dt~=0.10s, move~=0.167,
noise~=0.028, half-cycle period P~=588 samples must derive approximately the manually-tuned
~10 Hz OPC UA profile (window=20, reversal_n=5, stable_n~=29-30, slope~=+/-0.084,
conf_full~=0.167). Deterministic (seeded); runnable as `python3 tests/test_autocalibrate.py`.
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from otlab_core.config import PhaseConfig
from otlab_core.engine.autocalibrate import (
    calibrate_phase_config,
    _moving_average,
    unfold,
    learn_phase_config,
)

STEP = 0.167      # per-sample ramp magnitude -> move
HALF = 588        # half-cycle length in samples -> period P
CYCLES = 6
DT = 0.10         # per-sample interval -> dt (diagnostic only)
SIGMA = 0.026     # sensor noise; measured noise ~= 0.028 after realistic apex rounding
ROUND = 15        # apex rounding span (a real tank reverses smoothly, not instantaneously)

_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


def build_triangle(sigma=SIGMA, seed=0):
    """A realistic filling/draining process: rounded triangular level + sensor noise."""
    rnd = random.Random(seed)
    ideal = []
    lvl = 0.0
    direction = 1
    k = 0
    for _ in range(HALF * CYCLES):
        lvl = lvl + direction * STEP
        k += 1
        if k >= HALF:
            direction *= -1
            k = 0
        ideal.append(lvl)
    clean = _moving_average(ideal, ROUND)
    t = 0.0
    out = []
    for v in clean:
        out.append((t, v + rnd.gauss(0, sigma)))
        t += DT
    return out


def test_reproduces_validated_profile():
    print("test_reproduces_validated_profile:")
    r = calibrate_phase_config(build_triangle())
    c = r.config
    print(f"    measured: dt={r.dt:.3f} move={r.move:.3f} noise={r.noise:.4f} "
          f"P={r.period_samples:.0f} reversals={r.reversals}")
    print(f"    derived : window={c.window} reversal_n={c.reversal_n} stable_n={c.stable_n} "
          f"slope={c.slope_rising:.4f} conf_full={c.conf_full_slope:.3f}")
    # measured intermediates near the validated targets
    check("dt ~= 0.10", abs(r.dt - 0.10) < 0.01)
    check("move ~= 0.167", 0.160 <= r.move <= 0.173)
    check("noise ~= 0.028", 0.024 <= r.noise <= 0.033)
    check("P ~= 588", 580 <= r.period_samples <= 596)
    check("reversals >= 4", r.reversals >= 4)
    # derived params near the manually-tuned ~10 Hz profile
    check("window == 20", c.window == 20)
    check("reversal_n == 5", c.reversal_n == 5)
    check("stable_n in {29,30}", c.stable_n in (29, 30))
    check("slope_rising ~= 0.084", 0.075 <= c.slope_rising <= 0.095, f"(={c.slope_rising:.4f})")
    check("slope_falling == -slope_rising", c.slope_falling == -c.slope_rising)
    check("conf_full_slope ~= 0.167", 0.160 <= c.conf_full_slope <= 0.173)
    check("conf_hold kept at default", c.conf_hold == PhaseConfig().conf_hold)


def test_fallback_when_period_unmeasurable():
    print("test_fallback_when_period_unmeasurable:")
    # a monotonic ramp: no reversals -> fall back to LTR-03 defaults with a warning
    samples = [(i * DT, i * STEP) for i in range(200)]
    r = calibrate_phase_config(samples)
    check("measurable is False", r.measurable is False)
    check("config == default PhaseConfig", r.config == PhaseConfig())
    check("warning mentions 'period not measurable'",
          bool(r.warning) and "period not measurable" in r.warning)


def test_unfold_and_learn_helper(tmp_profile):
    print("test_unfold_and_learn_helper:")
    # messages that batch multiple samples each -> unfold expands them in order
    records = [(0.0, [1.0, 1.1]), (0.2, [1.2, 1.3]), (0.4, [1.4])]
    flat = unfold(records)
    check("unfold flattens in order",
          [v for _, v in flat] == [1.0, 1.1, 1.2, 1.3, 1.4])
    # learn_phase_config unfolds, calibrates and writes a profile that reloads to the same params
    triangle_records = [(t, [v]) for t, v in build_triangle()]
    res = learn_phase_config(triangle_records, profile_path=tmp_profile)
    reloaded = PhaseConfig.from_json(tmp_profile)
    check("written profile reloads to derived window", reloaded.window == res.config.window == 20)
    check("written profile reloads to derived slope", reloaded.slope_rising == res.config.slope_rising)


def main():
    import tempfile
    tmp = os.path.join(tempfile.mkdtemp(), "auto_profile.json")
    test_reproduces_validated_profile()
    test_fallback_when_period_unmeasurable()
    test_unfold_and_learn_helper(tmp)
    print()
    if _failures:
        print(f"AUTOCALIBRATE TEST: FAIL ({len(_failures)} check(s): {_failures})")
        return 1
    print("AUTOCALIBRATE TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
