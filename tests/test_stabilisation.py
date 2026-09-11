#!/usr/bin/env python3
"""Persistence and stabilisation (MODERNISATION_PLAN item 8).

Unit checks on CycleTracker (period detection on the bench sequence and a triangle, a broken
sequence) and Stabilisation (fraction change, coherent-set change, K consecutive cycles), then an
integration run over a bench-shaped recording: learning_progress events per cycle, the stabilisation
record in run.json with K and epsilon beside cycles_to_stable, the per-silo grammar and profile files,
--learn-until-stable ending the learn window early, and --resume-from skipping learning on a second
run. Deterministic; `python3 tests/test_stabilisation.py`.
"""
import io
import json
import os
import sys
import tempfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "scripts"))
sys.path.insert(0, os.path.join(_REPO, "tests"))

import liscere_observe as obs
from liscere.learning import CycleTracker, Stabilisation, grammar_distance
from test_replay import Timeline, write_capture

_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


def feed(tracker, seq):
    completed = []
    for i, ph in enumerate(seq):
        if tracker.update(ph, float(i)):
            completed.append(i)
    return completed


def test_cycles():
    print("[cycles] period detection")
    bench = ["RISING", "STABLE", "RISING", "STABLE", "FALLING"] * 3 + ["RISING"]
    ct = CycleTracker()
    done = feed(ct, bench)
    check("bench sequence: period 5, three cycles completed at the start of each repetition",
          ct.period == 5 and ct.cycles == 3 and done == [5, 10, 15], f"(period={ct.period}, cycles={ct.cycles}, at={done})")
    tri = CycleTracker()
    feed(tri, ["RISING", "FALLING"] * 4)
    check("triangle: period 2, three cycles", tri.period == 2 and tri.cycles == 3)
    dup = CycleTracker()
    feed(dup, ["RISING", "RISING", "UNKNOWN", "STABLE", "STABLE", "RISING", "STABLE", "RISING"])
    check("repeated and UNKNOWN phases are de-duplicated and ignored", dup.phases[:4] == ["RISING", "STABLE", "RISING", "STABLE"])
    br = CycleTracker()
    feed(br, ["RISING", "FALLING", "RISING", "FALLING", "RISING", "STABLE", "RISING"])
    check("a sequence that stops repeating is reported broken and stops counting",
          br.broken and br.cycles == 2, f"(broken={br.broken}, cycles={br.cycles})")
    one = CycleTracker()
    feed(one, ["RISING", "STABLE", "FALLING"])
    check("no period is claimed before the sequence repeats", one.period is None and one.cycles == 0)


def g(**targets):
    """grammar export shape: {target: {phase_distribution: {phase: {fraction}}, learned_coherent_phases}}"""
    out = {}
    for t, dist in targets.items():
        out[t] = {"phase_distribution": {ph: {"fraction": fr} for ph, fr in dist.items()},
                  "learned_coherent_phases": sorted(ph for ph, fr in dist.items() if fr >= 0.1)}
    return out


def test_stabilisation():
    print("[stabilisation] distance and K consecutive cycles")
    d = grammar_distance(g(a={"RISING": 1.0}), g(a={"RISING": 0.9, "STABLE": 0.1}))
    check("fraction change is the L1 distance (0.2) and the coherent set changed (STABLE joined)",
          abs(d["max_fraction_change"] - 0.2) < 1e-9 and d["coherent_set_changed"])
    d2 = grammar_distance(g(a={"RISING": 1.0}), g(a={"RISING": 1.0}, b={"FALLING": 1.0}))
    check("a new target counts as a full change", d2["max_fraction_change"] == 1.0 and d2["coherent_set_changed"])
    st = Stabilisation(k=2, epsilon=0.05)
    st.start(0.0)
    r1 = st.on_cycle(1, g(a={"RISING": 1.0}), 10.0, 3)
    r2 = st.on_cycle(2, g(a={"RISING": 0.97, "STABLE": 0.03}), 20.0, 6)   # small drift, set unchanged
    r3 = st.on_cycle(3, g(a={"RISING": 0.96, "STABLE": 0.04}), 30.0, 9)
    check("first snapshot has no reference; two quiet cycles then declare stable at cycle 3",
          r1["max_fraction_change"] is None and r2["stable_cycles"] == 1 and r3["stable"] and st.stable_at_cycle == 3,
          f"(r2={r2['stable_cycles']}, r3={r3['stable']})")
    s = st.summary()
    check("summary carries K, epsilon, cycles_to_stable and wall seconds",
          s["k"] == 2 and s["epsilon"] == 0.05 and s["cycles_to_stable"] == 3 and s["wall_seconds_to_stable"] == 30.0)
    st2 = Stabilisation(k=2, epsilon=0.05)
    st2.on_cycle(1, g(a={"RISING": 1.0}), 1.0, 1)
    st2.on_cycle(2, g(a={"RISING": 1.0}), 2.0, 2)
    r = st2.on_cycle(3, g(a={"RISING": 0.5, "FALLING": 0.5}), 3.0, 4)
    check("a set change resets the streak", r["stable_cycles"] == 0 and not st2.stable)


def build():
    tl = Timeline()
    tl.mark("observe")
    tl.ramp(0, 100); tl.ramp(100, 0, -0.5); tl.ramp(0, 100); tl.ramp(100, 0, -0.5)
    tl.mark("learn")
    for _ in range(4):
        tl.bench_cycle({"rise1": (45, 40.0), "rise2": (45, 40.0)})
    tl.mark("evaluate")
    tl.bench_cycle({"rise1": (45, 40.0), "fall": (45, 40.0)})
    return tl


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    real = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        rc = obs.main(argv)
    finally:
        sys.stdout, sys.stderr = real
    return rc, out.getvalue(), err.getvalue()


def test_integration():
    print("[integration] replay: learning_progress, persisted files, learn-until-stable, resume")
    tl = build()
    observe_s = tl.marks["learn"] - tl.marks["observe"]
    learn_s = tl.marks["evaluate"] - tl.marks["learn"]
    with tempfile.TemporaryDirectory() as tmp:
        cap = write_capture(tmp, tl)
        runs = os.path.join(tmp, "runs")
        rc, out, err = run(["--replay", cap, "--observe", str(observe_s), "--learn", str(learn_s),
                            "--learn-until-stable", "--stable-cycles", "2", "--stable-epsilon", "0.05", "--runs-dir", runs])
        run1 = os.path.join(runs, sorted(os.listdir(runs))[-1])
        events = [json.loads(line) for line in out.splitlines()]
        prog = [e for e in events if e["type"] == "learning_progress"]
        check("run completes", rc == 0, f"(rc={rc})")
        check("one learning_progress event per completed learn cycle, carrying K and epsilon",
              len(prog) >= 3 and all(e["k"] == 2 and e["epsilon"] == 0.05 for e in prog), f"(n={len(prog)})")
        check("the grammar is stable by the third cycle (set unchanged, fractions unchanged)",
              any(e["stable"] for e in prog) and prog[-1]["cycle"] == 3, f"(cycles={[e['cycle'] for e in prog]})")
        check("learn ended early on stability", "ending the learn window early" in err)
        with open(os.path.join(run1, "run.json")) as f:
            man = json.load(f)
        silo = man["silos"]["10.0.0.5:4840"]
        st = silo.get("stabilisation", {})
        check("run.json holds the stabilisation record with K, epsilon, cycles_to_stable, wall seconds, writes",
              st.get("k") == 2 and st.get("epsilon") == 0.05 and st.get("cycles_to_stable") == 3
              and st.get("wall_seconds_to_stable") and st.get("writes_observed") == 6, f"(={ {k: st.get(k) for k in ('k','epsilon','cycles_to_stable','wall_seconds_to_stable','writes_observed')} })")
        check("run.json records the parameters at top level and a human_hours field to fill in",
              man.get("stabilisation_parameters") == {"k": 2, "epsilon": 0.05} and "human_hours" in man)
        check("stderr prints cycles_to_stable next to K and epsilon",
              "stable after 3 cycle(s) (K=2, epsilon=0.05)" in err)
        sdir = os.path.join(run1, "silos", "10.0.0.5_4840")
        check("per-silo grammar.json and profile.json are written in the run directory",
              os.path.isfile(os.path.join(sdir, "grammar.json")) and os.path.isfile(os.path.join(sdir, "profile.json")))
        with open(os.path.join(sdir, "grammar.json")) as f:
            gdoc = json.load(f)
        check("grammar.json is the learn document with the stabilisation summary",
              "ns=0;i=45" in gdoc.get("grammar", {}) and gdoc.get("stabilisation", {}).get("cycles_to_stable") == 3)
        v1 = [e["result"] for e in events if e["type"] == "verdict"]
        check("evaluation still ran after the early end of learn", len(v1) >= 2, f"(={v1})")

        # resume: a second run skips learning and judges on the saved grammar
        rc2, out2, err2 = run(["--replay", cap, "--observe", str(observe_s), "--learn", str(learn_s),
                               "--resume-from", run1, "--runs-dir", os.path.join(tmp, "runs2")])
        events2 = [json.loads(line) for line in out2.splitlines()]
        stages2 = [e["stage"] for e in events2 if e["type"] == "stage"]
        v2 = [e for e in events2 if e["type"] == "verdict"]
        check("--resume-from skips the learn stage", rc2 == 0 and stages2 == ["observe", "evaluate"], f"(={stages2})")
        check("resume loaded the grammar and profile", "grammar with 1 target(s) and profile loaded" in err2)
        check("resumed run judges every write from the learn cycles onwards on the saved grammar",
              len(v2) > len(v1) and v2[0]["result"] == "COHERENT", f"(n={len(v2)})")
        check("a write during FALLING is INCOHERENT on the resumed grammar", "INCOHERENT" in {e["result"] for e in v2})


def main():
    test_cycles()
    print()
    test_stabilisation()
    print()
    test_integration()
    print()
    if _failures:
        print(f"STABILISATION TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("STABILISATION TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
