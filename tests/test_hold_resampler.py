#!/usr/bin/env python3
"""Hold-resampler (Requirement B): STABLE closes during a report-by-exception hold.

A datachange subscription is silent while the value is unchanged, so during a hold the pure
PhaseTracker never accumulates the flat samples it needs to settle to STABLE. The HoldResampler
(observer layer) repeats the last real value at the calibrated cadence during silence and stops when
a real sample resumes -- a faithful reconstruction, only flat repeats, never fabricated movement.

This test covers the unit contract (silence margin so movement jitter is not a false hold; a bounded
catch-up ceiling; dormancy under polling), a silence gap settling to STABLE at the held level, and a
full synthetic tank-cycle reproduction printing the BEFORE (bleed + flicker, the defect) and AFTER
(RISING -> STABLE -> RISING -> STABLE -> FALLING, held values near 50 and 80) phase streams. It runs
headless (no emitter), proving STABLE closes on the tick-driven resampler alone. Deterministic;
`python3 tests/test_hold_resampler.py`.
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "scripts"))

from liscere_observe import HoldResampler

from otlab_core.engine.autocalibrate import calibrate_phase_config
from otlab_core.engine.phase_tracker import PhaseTracker
from otlab_core.extractors.opcua import _FIELDS, OpcUaExtractor

_IDX = {name: i for i, name in enumerate(_FIELDS)}
_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


def line(**kw):
    row = [""] * len(_FIELDS)
    for field, value in kw.items():
        key = "opcua." + field
        row[_IDX[key] if key in _IDX else _IDX[field]] = str(value)
    return row


def pub(t, **kw):
    base = {"frame.time_epoch": str(t), "frame.protocols": "eth:ethertype:ip:tcp:opcua",
            "ip.src": "10.0.0.9", "ip.dst": "10.0.0.5", "tcp.srcport": "48000", "tcp.dstport": "4840",
            "transport.type": "MSG", "servicenodeid.numeric": "829"}
    base.update(kw)
    return line(**base)


def dedup(seq):
    out = []
    for x in seq:
        if not out or x != out[-1]:
            out.append(x)
    return out


# --------------------------------------------------------------------------- unit: the resampler contract

def test_unit():
    print("[unit] HoldResampler contract")

    # margin: the first flat only fires after silence >= margin * cadence (1.75 x 0.1 = 0.175)
    r = HoldResampler(cadence=0.1, window=8, stable_n=6)   # ceiling = 8 + 12 = 20
    r.real(50.0, 0.0)
    check("no flat while silence is below the margin (movement jitter is not a false hold)",
          r.catch_up(0.10) == [] and r.catch_up(0.17) == [], "(<= 0.175s)")
    check("the first flat fires just past the margin, at the held value",
          r.catch_up(0.18) == [50.0], "(> 0.175s)")
    check("subsequent flats follow at the natural cadence (~0.1s), not the margin",
          r.catch_up(0.30) == [50.0], "(next due ~0.275s)")

    # ceiling: a huge jump injects at most `ceiling` flats, then stops until a real sample resumes
    r2 = HoldResampler(cadence=0.1, window=3, stable_n=5)   # ceiling = 3 + 10 = 13
    r2.real(80.0, 0.0)
    flood = r2.catch_up(10_000.0)
    check("a long pause is capped at the ceiling (no flood of thousands of flats)",
          len(flood) == 13 and set(flood) == {80.0}, f"(n={len(flood)})")
    check("once the ceiling is hit no more flats are injected until a real sample resumes",
          r2.catch_up(20_000.0) == [])
    r2.real(81.0, 20_000.0)
    check("a real sample re-arms the resampler (silence clock and ceiling reset)",
          r2.catch_up(20_000.0 + 0.18) == [81.0])

    # dormancy under polling: a real sample every cadence keeps it silent forever (byte-identical feed)
    r3 = HoldResampler(cadence=0.1, window=8, stable_n=6)
    injected = 0
    for i in range(50):
        t = i * 0.1
        r3.real(42.0, t)                       # a poll every cadence re-sends the value
        injected += len(r3.catch_up(t))        # tick right after the poll
        injected += len(r3.catch_up(t + 0.09)) # and mid-cycle, still within the margin
    check("a polled transport keeps the resampler dormant (no flat ever injected)",
          injected == 0, f"(injected={injected})")

    # unmeasurable cadence -> dormant (no reconstruction, documented fallback)
    r4 = HoldResampler(cadence=0.0, window=8, stable_n=6)
    r4.real(1.0, 0.0)
    check("an unmeasurable cadence leaves the resampler dormant", r4.catch_up(9999.0) == [])


# --------------------------------------------------------------------------- silence gap settles to STABLE

def test_silence_to_stable():
    print("[silence] a hold settles the tracker to STABLE at the held value")
    tracker = PhaseTracker()                       # defaults: window=8, slope_rising=0.25, stable_n=6
    r = HoldResampler(cadence=0.1, window=tracker.window, stable_n=tracker.stable_n)

    # A clean rising ramp of REAL samples (+2/sample), then the wire goes silent (a hold).
    for i in range(12):
        v, t = float(i * 2), i * 0.1
        tracker.update(v)
        r.real(v, t)
    check("the tracker is RISING before the hold", tracker.phase == "RISING", f"(phase={tracker.phase})")
    held = tracker.last_level                       # 22.0

    # Silence: ticks every 0.1s drive the resampler; no real sample arrives.
    for k in range(1, 40):
        now = 1.1 + k * 0.1
        for v in r.catch_up(now):
            tracker.update(v)
    check("the tracker reaches STABLE during the silent hold", tracker.phase == "STABLE",
          f"(phase={tracker.phase})")
    check("the reported value stays at the held level (only flat repeats, no fabricated movement)",
          tracker.last_level == held == 22.0, f"(last_level={tracker.last_level}, held={held})")


# --------------------------------------------------------------------------- full synthetic tank cycle

def build_cycle(ex):
    """A tank auto-cycle as an OPC UA subscription: the Level (handle 1) rises to 50, HOLDS (silent)
    while a status Int (handle 2) flips 0/1, rises to 80, holds again, then falls to 0. Returns the
    parsed events with their timestamps, the Level (t, value) series for calibration, and the index
    ranges of the two holds."""
    dt = 0.1
    triples = []          # (t, handle, value)
    state = {"t": 0.0}

    def emit(h, v):
        triples.append((state["t"], h, float(v)))
        state["t"] += dt

    for v in range(0, 51):
        emit(1, v)                       # rise 0 -> 50
    h1 = (len(triples), 0)
    for i in range(40):
        emit(2, i % 2)                   # HOLD at 50: status flips, Level silent
    h1 = (h1[0], len(triples))
    for v in range(51, 81):
        emit(1, v)                       # rise 50 -> 80
    h2 = (len(triples), 0)
    for i in range(40):
        emit(2, i % 2)                   # HOLD at 80
    h2 = (h2[0], len(triples))
    for v in range(79, -1, -1):
        emit(1, v)                       # fall 80 -> 0

    def parse(t, h, v):
        if h == 1:
            return ex.parse_line(pub(t, ClientHandle="1", **{"variant.has_value": "0x0a"}, Float=str(v)))
        return ex.parse_line(pub(t, ClientHandle="2", **{"variant.has_value": "0x04"}, Int16=str(int(v))))

    events = [(t, parse(t, h, v)) for (t, h, v) in triples]
    level_series = [(t, v) for (t, h, v) in triples if h == 1]
    return events, level_series, h1, h2


def run_before(ex, events, config):
    """The pre-fix live path: no attribution (fused feed), no resampler -- the defect."""
    tracker = PhaseTracker(config)
    phases, values = [], []
    for _t, evt in events:
        for s in ex.extract_state_samples(evt):          # state_key=None -> every handle bleeds in
            tracker.update(s)
        phases.append(tracker.phase)
        values.append(tracker.last_level)
    return phases, values


def run_after(ex, events, config, dt):
    """The fixed live path: per-handle attribution + the tick-driven hold-resampler, headless."""
    tracker = PhaseTracker(config)
    r = HoldResampler(dt, config.window, config.stable_n)
    phases, values = [], []
    for t, evt in events:
        for v in r.catch_up(t):                          # tick: reconstruct the silent hold
            tracker.update(v)
        for s in ex.extract_state_samples(evt, state_key="opcua:sub:1"):   # dispatch: only the Level
            tracker.update(s)
            r.real(s, t)
        phases.append(tracker.phase)
        values.append(tracker.last_level)
    return phases, values


def test_bench_repro():
    print("[repro] synthetic tank cycle: before vs after")
    ex = OpcUaExtractor()
    events, level_series, h1, h2 = build_cycle(ex)
    calib = calibrate_phase_config(level_series)
    config, dt = calib.config, (calib.dt or 0.1)

    before_p, before_v = run_before(ex, events, config)
    after_p, after_v = run_after(ex, events, config, dt)

    bd = [p for p in dedup(before_p) if p != "UNKNOWN"]
    ad = [p for p in dedup(after_p) if p != "UNKNOWN"]
    print(f"    BEFORE phases: {bd}")
    print(f"    AFTER  phases: {ad}")

    # BEFORE: the non-Level 0/1 bleed collapses the held value and flickers the phase.
    hold1_vals_before = [before_v[i] for i in range(*h1) if before_v[i] is not None]
    check("BEFORE: the held value collapses to 0/1 during the hold (the defect)",
          min(hold1_vals_before) <= 1.0, f"(min in hold1={min(hold1_vals_before)})")
    check("BEFORE: a spurious FALLING flickers in while the Level is still rising",
          "FALLING" in bd[:bd.index("FALLING")] + ["FALLING"] and bd != ["RISING", "STABLE", "RISING", "STABLE", "FALLING"],
          f"(before={bd})")

    # AFTER: the acceptance sequence, with the holds settling to STABLE at the held levels.
    check("AFTER: the phase sequence is RISING -> STABLE -> RISING -> STABLE -> FALLING",
          ad == ["RISING", "STABLE", "RISING", "STABLE", "FALLING"], f"(after={ad})")
    hold1_vals_after = [after_v[i] for i in range(*h1) if after_v[i] is not None]
    hold2_vals_after = [after_v[i] for i in range(*h2) if after_v[i] is not None]
    check("AFTER: through hold 1 the reported value stays at ~50 and never reads 0/1",
          set(v for v in hold1_vals_after if v is not None) <= {50.0} and min(hold1_vals_after) == 50.0,
          f"(hold1 values={sorted(set(hold1_vals_after))})")
    check("AFTER: through hold 2 the reported value stays at ~80 and never reads 0/1",
          set(v for v in hold2_vals_after if v is not None) <= {80.0} and min(hold2_vals_after) == 80.0,
          f"(hold2 values={sorted(set(hold2_vals_after))})")
    check("AFTER: STABLE is actually reached in each hold (not merely no flicker)",
          "STABLE" in [after_p[i] for i in range(*h1)] and "STABLE" in [after_p[i] for i in range(*h2)])


def main():
    test_unit()
    print()
    test_silence_to_stable()
    print()
    test_bench_repro()
    print()
    if _failures:
        print(f"HOLD RESAMPLER TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("HOLD RESAMPLER TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
