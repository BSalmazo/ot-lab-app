#!/usr/bin/env python3
"""Deployment configuration (MODERNISATION_PLAN item 9): exclusions and a pinned state signal.

A third monitored item (handle 3) varies like a temperature and has more distinct values than the
Level, so plain discovery picks it as the state signal (the bench situation the Temperature
exclusion works around). With exclude_flows = ["opcua:sub:3"] discovery picks the Level; the excluded
flow is still reported, marked EXCLUDED; the configuration is recorded in run.json. A pinned
state_signal replaces the rule; a pin that names an unobserved flow makes the silo non-evaluable
with a reason. Bad files are rejected. `python3 tests/test_config.py`.
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
from test_replay import CLIENT, SERVER, Timeline, row, write_capture

_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


def temp_row(t, value):
    return row(**{"frame.time_epoch": f"{t:.6f}", "frame.protocols": "eth:ethertype:ip:tcp:opcua",
                  "ip.src": SERVER[0], "tcp.srcport": SERVER[1], "ip.dst": CLIENT[0], "tcp.dstport": CLIENT[1],
                  "transport.type": "MSG", "servicenodeid.numeric": "829", "ClientHandle": "3",
                  "variant.has_value": "0x0a", "Float": f"{value:.4f}"})


def build():
    tl = Timeline()
    tl.mark("observe")
    tl.ramp(0, 100); tl.ramp(100, 0, -0.5); tl.ramp(0, 100); tl.ramp(100, 0, -0.5)
    tl.mark("learn")
    tl.bench_cycle({"rise1": (45, 40.0), "rise2": (45, 40.0)})
    tl.mark("evaluate")
    tl.bench_cycle({"rise1": (45, 40.0), "fall": (45, 40.0)})
    # a temperature-like third item: follows the Level with a slow drift, so every sample is distinct
    extra = []
    for i, r in enumerate(tl.rows):
        cols = r.split("\t")
        if cols[26] == "1":                       # opcua.ClientHandle column of a Level publish
            t = float(cols[0]); level = float(cols[10])
            extra.append(temp_row(t + 0.01, 20.0 + 0.6 * level + 0.0001 * i))
    tl.rows = sorted(tl.rows + extra, key=lambda r: float(r.split("\t")[0]))
    return tl


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    real = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        rc = obs.main(argv)
    finally:
        sys.stdout, sys.stderr = real
    return rc, [json.loads(line) for line in out.getvalue().splitlines()], err.getvalue()


def main():
    tl = build()
    observe_s = tl.marks["learn"] - tl.marks["observe"]
    learn_s = tl.marks["evaluate"] - tl.marks["learn"]
    with tempfile.TemporaryDirectory() as tmp:
        cap = write_capture(tmp, tl)
        base = ["--replay", cap, "--observe", str(observe_s), "--learn", str(learn_s)]

        print("[config] without a file: discovery picks the wrong (more varied) signal")
        rc0, ev0, err0 = run(base + ["--runs-dir", os.path.join(tmp, "r0")])
        st0 = [e["key"] for e in ev0 if e["type"] == "state_signal_discovered"]
        check("handle 3 (temperature-like) wins plain discovery", st0 == ["opcua:sub:3"], f"(={st0})")

        print("[config] exclude_flows")
        cfg = os.path.join(tmp, "liscere.toml")
        with open(cfg, "w") as f:
            f.write('[observer]\nexclude_flows = ["opcua:sub:3"]\n')
        rc1, ev1, err1 = run(base + ["--config", cfg, "--runs-dir", os.path.join(tmp, "r1")])
        st1 = [e["key"] for e in ev1 if e["type"] == "state_signal_discovered"]
        check("with the exclusion the Level is the state signal", rc1 == 0 and st1 == ["opcua:sub:1"], f"(rc={rc1}, ={st1})")
        exc = [e for e in ev1 if e["type"] == "variable_found" and e.get("nature") == "EXCLUDED"]
        check("the excluded flow is still reported, marked EXCLUDED", len(exc) == 1 and exc[0]["key"] == "opcua:sub:3")
        check("the config is named in the log", "[config]" in err1 and "opcua:sub:3" in err1)
        run1 = os.path.join(tmp, "r1", sorted(os.listdir(os.path.join(tmp, "r1")))[-1])
        with open(os.path.join(run1, "run.json")) as f:
            man = json.load(f)
        check("run.json records the configuration used",
              man.get("config", {}).get("exclude_flows") == ["opcua:sub:3"] and man["config"]["path"] == cfg)
        phases = [e["phase"] for e in ev1 if e["type"] == "phase"]
        check("the acceptance sequence appears with the Level as state signal",
              "STABLE" in phases and "FALLING" in phases and "RISING" in phases)
        results = [e["result"] for e in ev1 if e["type"] == "verdict"]
        check("verdicts: COHERENT during rising, INCOHERENT during falling",
              results == ["COHERENT", "INCOHERENT"], f"(={results})")

        print("[config] pinned state_signal")
        with open(cfg, "w") as f:
            f.write('[observer]\nstate_signal = "opcua:sub:1"\n')
        rc2, ev2, err2 = run(base + ["--config", cfg, "--runs-dir", os.path.join(tmp, "r2")])
        st2 = [e["key"] for e in ev2 if e["type"] == "state_signal_discovered"]
        check("a pin selects the Level without an exclusion", rc2 == 0 and st2 == ["opcua:sub:1"] and "pinned" in err2)
        with open(cfg, "w") as f:
            f.write('[observer]\nstate_signal = "opcua:sub:9"\n')
        rc3, ev3, err3 = run(base + ["--config", cfg, "--runs-dir", os.path.join(tmp, "r3")])
        silos3 = [e for e in ev3 if e["type"] == "silo" and e.get("endpoint")]
        check("a pin that names an unobserved flow makes the silo non-evaluable with the reason",
              rc3 == 2 and silos3 and silos3[0]["evaluable"] is False and "opcua:sub:9" in (silos3[0]["reason"] or ""),
              f"(rc={rc3}, reason={silos3[0]['reason'] if silos3 else None})")

        print("[config] bad files")
        with open(cfg, "w") as f:
            f.write('[observer]\nexclude_flows = "opcua:sub:3"\n')
        rc4, _e, err4 = run(base + ["--config", cfg, "--no-run-dir"])
        check("a non-list exclude_flows is rejected with exit 2", rc4 == 2 and "exclude_flows" in err4)
        rc5, _e, err5 = run(base + ["--config", os.path.join(tmp, "missing.toml"), "--no-run-dir"])
        check("a missing config file is an error, not silently ignored", rc5 == 2 and "--config" in err5)

    print()
    if _failures:
        print(f"CONFIG TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("CONFIG TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
