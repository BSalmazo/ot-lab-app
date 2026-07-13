#!/usr/bin/env python3
"""End-to-end pipeline test for the passive observer (no tshark).

Exercises the orchestration in scripts/liscere_observe.py on synthetic OPC UA events:
DISCOVER the state flow -> CALIBRATE phase params -> LEARN the grammar -> EVALUATE coherence.
Deterministic; runnable as `python3 tests/test_observer_pipeline.py`.
"""
import os
import random
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "scripts"))

from otlab_core.contract import NormalizedEvent
from otlab_core.extractors.opcua import OpcUaExtractor
import liscere_observe as obs

_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


def pub(t, samples):
    return NormalizedEvent(timestamp=t, protocol="OPCUA/binary", op="PUBLISH_RESPONSE",
                           direction="response", target=None,
                           value=(samples[0] if samples else None),
                           raw={"float_samples": list(samples), "value_is_float": True})


def wr(t, target, value):
    return NormalizedEvent(timestamp=t, protocol="OPCUA/binary", op="WRITE_REQUEST",
                           direction="request", target=target, value=value, raw={})


def triangle(step=0.167, turns=(0, 100, 40, 60, 40), noise=0.01, seed=0):
    rnd = random.Random(seed)
    vals = []
    cur = float(turns[0])
    for nxt in turns[1:]:
        d = 1 if nxt > cur else -1
        while (d > 0 and cur < nxt) or (d < 0 and cur > nxt):
            cur += d * step
            vals.append(cur + rnd.gauss(0, noise))
        cur = float(nxt)
    return vals


def ramp_events(start_level, n_pub, direction, t0, step=0.167, write_at=(), target="ns=4;i=23"):
    """Publishes (2 samples each) moving the level, with writes inserted at given publish indices."""
    events = []
    lvl = start_level
    t = t0
    for i in range(n_pub):
        s1 = lvl + direction * step
        s2 = s1 + direction * step
        lvl = s2
        events.append(pub(t, [s1, s2]))
        t += 0.2
        if i in write_at:
            events.append(wr(t, target, 80.0))
            t += 0.2
    return events, lvl, t


def main():
    ex = OpcUaExtractor()

    # OBSERVE window: a filling/draining level (for discovery + calibration).
    # Even half-cycles (599 samples each) so the derived period is unambiguous -> window=20.
    observe_events = []
    t = 0.0
    level = triangle(turns=(0, 100, 0, 100, 0))
    for i in range(0, len(level), 2):
        observe_events.append(pub(t, level[i:i + 2]))
        t += 0.2

    print("discover_and_calibrate:")
    calib, disc, state_flow = obs.discover_and_calibrate(ex, observe_events, log=lambda *a: None)
    check("state signal discovered", calib is not None and disc.state_flow_key == "opcua:publish:telemetry")
    check("calibration measurable", calib is not None and calib.measurable)
    check("calibrated window == 20", calib is not None and calib.config.window == 20,
          f"(={calib.config.window if calib else None})")
    check("phase config DERIVED from traffic (conf_full ~= move 0.167, not default 1.0)",
          calib is not None and abs(calib.config.conf_full_slope - 0.167) < 0.02,
          f"(={calib.config.conf_full_slope:.3f})")

    cfg = calib.config

    # LEARN window: level rises; operator writes ns=4;i=23 during RISING
    print("run_learn:")
    learn_events, _, _ = ramp_events(0.0, 40, +1, 0.0, write_at=(25, 30, 35))
    doc = obs.run_learn(ex, learn_events, obs.PhaseTracker(cfg))
    g = doc.get("grammar", {})
    check("grammar learned for ns=4;i=23", "ns=4;i=23" in g)
    if "ns=4;i=23" in g:
        check("ns=4;i=23 learned coherent in RISING",
              "RISING" in g["ns=4;i=23"].get("learned_coherent_phases", []),
              f"(dist={list(g['ns=4;i=23'].get('phase_distribution', {}).keys())})")

    # EVALUATE window: rise -> write (COHERENT), then fall -> write (INCOHERENT)
    print("run_evaluate:")
    ev = []
    e1, lvl, t = ramp_events(0.0, 30, +1, 0.0)         # climb to RISING
    ev += e1
    ev.append(wr(t, "ns=4;i=23", 80.0)); t += 0.2      # write during RISING
    e2, lvl, t = ramp_events(lvl, 45, -1, t)           # descend to FALLING
    ev += e2
    ev.append(wr(t, "ns=4;i=23", 80.0))                # write during FALLING
    verdicts = obs.run_evaluate(ex, ev, obs.PhaseTracker(cfg), g)
    check("two writes judged", len(verdicts) == 2, f"(={len(verdicts)})")
    if len(verdicts) == 2:
        check("write during RISING -> COHERENT", verdicts[0].verdict == "COHERENT",
              f"(={verdicts[0].verdict})")
        check("write during FALLING -> INCOHERENT", verdicts[1].verdict == "INCOHERENT",
              f"(={verdicts[1].verdict})")

    print()
    if _failures:
        print(f"OBSERVER PIPELINE TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("OBSERVER PIPELINE TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
