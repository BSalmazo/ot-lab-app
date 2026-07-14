#!/usr/bin/env python3
"""Validation test for Level-2 phase-parameter auto-calibration.

Covers two regimes and the anti-jitter guarantee, after the scale-consistency change (thresholds
derive from the observed RATE and a smoothed-series noise floor, not raw per-sample deltas):

- OPC UA regime (smooth float): the derived integer parameters are unchanged (window=20,
  reversal_n=5, stable_n~=29-30, conf_full~=0.167). slope_rising moves from the old noise-dominated
  0.084 to the rate-dominated ~0.050; a tracker-equivalence check asserts the phase decisions are
  effectively identical (the regression guard).
- Modbus regime (quantised integer staircase): calibration now yields an ACTIONABLE slope_rising
  and the tracker reports RISING/FALLING through a quantised ramp (the exact live failure fixed).
- Anti-jitter: a flat jittery segment under a calibrated config must stay STABLE (no false RISING).

Deterministic (seeded); runnable as `python3 tests/test_autocalibrate.py`.
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from otlab_core.config import PhaseConfig
from otlab_core.engine.phase_tracker import PhaseTracker
from otlab_core.engine.autocalibrate import (
    calibrate_phase_config,
    _moving_average,
    _move,
    _noise,
    _half_cycle_period,
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
    print(f"    measured: dt={r.dt:.3f} move={r.move:.3f} noise={r.noise:.4f} rate={r.rate:.4f} "
          f"noise_smooth={r.noise_smooth:.4f} P={r.period_samples:.0f} reversals={r.reversals}")
    print(f"    derived : window={c.window} reversal_n={c.reversal_n} stable_n={c.stable_n} "
          f"slope={c.slope_rising:.4f} conf_full={c.conf_full_slope:.3f}")
    # measured intermediates near the validated targets
    check("dt ~= 0.10", abs(r.dt - 0.10) < 0.01)
    check("move ~= 0.167 (raw diagnostic)", 0.160 <= r.move <= 0.173)
    check("noise ~= 0.028 (raw diagnostic)", 0.024 <= r.noise <= 0.033)
    # scale-consistent statistics: for a smooth float ramp, rate ~= move and noise_smooth < noise
    check("rate ~= move for a smooth float ramp", abs(r.rate - r.move) < 0.01,
          f"(rate={r.rate:.4f} move={r.move:.4f})")
    check("noise_smooth < raw noise", r.noise_smooth < r.noise, f"(={r.noise_smooth:.4f})")
    check("P ~= 588", 580 <= r.period_samples <= 596)
    check("reversals >= 4", r.reversals >= 4)
    # derived params near the manually-tuned ~10 Hz profile (period-derived chain is unchanged)
    check("window == 20", c.window == 20)
    check("reversal_n == 5", c.reversal_n == 5)
    check("stable_n in {29,30}", c.stable_n in (29, 30))
    # slope_rising is now RATE-dominated (~0.3*rate ~= 0.050), not the old noise-dominated 0.084.
    # Equivalence to the old profile is asserted at the tracker level in
    # test_opcua_regime_equivalence; here we just pin it to the rate-derived value.
    check("slope_rising is rate-dominated (~= 0.3*rate)", abs(c.slope_rising - 0.3 * r.rate) < 0.006,
          f"(slope={c.slope_rising:.4f}, 0.3*rate={0.3 * r.rate:.4f})")
    check("slope_rising stays a safe fraction of rate", 0.25 * r.rate <= c.slope_rising <= 0.6 * r.rate,
          f"(={c.slope_rising:.4f})")
    check("slope_falling == -slope_rising", c.slope_falling == -c.slope_rising)
    check("conf_full_slope ~= rate ~= 0.167", 0.160 <= c.conf_full_slope <= 0.173)
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


def _old_formula_config(levels, period):
    """The config the PRE-change formula would have produced (raw per-sample move/noise)."""
    move = _move(levels)
    noise = _noise(levels)
    slope = max(3.0 * noise, 0.3 * move)
    return PhaseConfig(
        window=max(3, round(period / 30)), slope_rising=slope, slope_falling=-slope,
        reversal_n=max(2, round(period / 120)), stable_n=max(3, round(period / 20)),
        conf_full_slope=move,
    )


def _phases(config, levels):
    tr = PhaseTracker(config)
    return [tr.update(float(x))[0] for x in levels]


def build_staircase():
    """Modbus bench signal: integer staircase 0..100..0..100..0, +1 per ~3 samples, dt=0.1."""
    seq, v, d = [], 0.0, 1.0 / 3.0
    for turn in (100, 0, 100, 0):
        while (d > 0 and v < turn) or (d < 0 and v > turn):
            seq.append(int(round(v)))
            v += d
        d = -d
    return [(i * 0.1, x) for i, x in enumerate(seq)]


def test_opcua_regime_equivalence():
    """REGRESSION (a-i): on the OPC UA float regime the new config must decide phases the same as
    the old formula. slope_rising itself moves (0.084 noise-dominated -> ~0.050 rate-dominated), so
    equivalence is asserted at the DECISION level, tolerance = >=99% identical tracker phases."""
    print("test_opcua_regime_equivalence:")
    samples = build_triangle()
    levels = [v for _, v in samples]
    r = calibrate_phase_config(samples)
    period = r.period_samples
    old = _old_formula_config(levels, period)
    po, pn = _phases(old, levels), _phases(r.config, levels)
    agree = sum(a == b for a, b in zip(po, pn)) / len(po)
    print(f"    old slope={old.slope_rising:.4f} conf={old.conf_full_slope:.4f}  |  "
          f"new slope={r.config.slope_rising:.4f} conf={r.config.conf_full_slope:.4f}")
    check("tracker phase agreement old-vs-new >= 99% (tolerance)", agree >= 0.99, f"(={agree:.4f})")
    check("both configs classify the ramp (RISING and FALLING present, no all-STABLE)",
          "RISING" in pn and "FALLING" in pn)
    check("conf_full near-equal old-vs-new (<0.01)", abs(old.conf_full_slope - r.config.conf_full_slope) < 0.01)


def test_modbus_staircase():
    """FIX (a-ii + end-to-end c): the quantised integer staircase must calibrate to a threshold in
    the actionable band 0.05..0.20 and the tracker must report RISING through the ramp and FALLING
    after the reversal (the exact live failure: old formula gave slope_rising~=0.876 >> windowed
    slope, dead STABLE).

    With the noise floor measured at WINDOW scale, the period-3 quantisation ripple cancels (the
    tracker's window-length regression averages it away), so slope_rising drops from the 0.22 the
    shorter span-5 smoothing left to ~0.145 -- inside 0.05..0.20, with ~56% margin below the 0.33
    windowed slope."""
    print("test_modbus_staircase:")
    samples = build_staircase()
    r = calibrate_phase_config(samples)
    c = r.config
    print(f"    rate={r.rate:.4f} noise_smooth={r.noise_smooth:.4f} "
          f"slope_rising={c.slope_rising:.4f} conf_full={c.conf_full_slope:.4f} window={c.window}")
    check("staircase discovered as measurable", r.measurable)
    check("conf_full_slope == rate (~0.33)", c.conf_full_slope == r.rate and 0.30 <= r.rate <= 0.36,
          f"(={r.rate:.4f})")
    check("slope_rising in the actionable band 0.05..0.20", 0.05 <= c.slope_rising <= 0.20,
          f"(={c.slope_rising:.4f})")
    check("slope_rising below the windowed rate (tracker can act, with margin)",
          c.slope_rising < r.rate, f"(slope={c.slope_rising:.4f} < rate={r.rate:.4f})")
    check("slope_rising well below the OLD dead value 0.876", c.slope_rising < 0.30,
          f"(={c.slope_rising:.4f})")
    # end-to-end: the tracker must leave STABLE and track the quantised ramp
    levels = [v for _, v in samples]
    ph = _phases(c, levels)
    rise, fall = ph[50:290], ph[350:590]   # first half rises 0->100, second half falls 100->0
    check("tracker reports RISING during the quantised ramp",
          rise.count("RISING") >= 0.9 * len(rise), f"(RISING {rise.count('RISING')}/{len(rise)})")
    check("tracker reports FALLING after the reversal",
          fall.count("FALLING") >= 0.9 * len(fall), f"(FALLING {fall.count('FALLING')}/{len(fall)})")
    check("tracker is NOT stuck in STABLE (the live failure)", "STABLE" not in rise[20:])


def test_noisy_flat_no_false_rising():
    """ANTI-JITTER (a-iii): a config calibrated on a real noisy process must reject jitter -- a flat
    segment with the process's own sensor noise (and 4x it) must stay STABLE, no false RISING."""
    print("test_noisy_flat_no_false_rising:")
    r = calibrate_phase_config(build_triangle())
    check("noise floor retained: slope_rising >= 3*noise_smooth",
          r.config.slope_rising >= 3.0 * r.noise_smooth - 1e-9, f"(={r.config.slope_rising:.4f})")
    rnd = random.Random(99)
    for label, sigma, max_ramp in [("same jitter", 0.026, 0.01), ("4x jitter", 0.10, 0.05)]:
        flat = [50.0 + rnd.gauss(0, sigma) for _ in range(1500)]
        ph = _phases(r.config, flat)
        ramp_frac = (ph.count("RISING") + ph.count("FALLING")) / len(ph)
        check(f"flat + {label}: no false ramp (RISING+FALLING < {int(max_ramp*100)}%)",
              ramp_frac < max_ramp, f"(={ramp_frac:.3f})")


def main():
    import tempfile
    tmp = os.path.join(tempfile.mkdtemp(), "auto_profile.json")
    test_reproduces_validated_profile()
    test_opcua_regime_equivalence()
    test_modbus_staircase()
    test_noisy_flat_no_false_rising()
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
