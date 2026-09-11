#!/usr/bin/env python3
"""liscere-metrics (MODERNISATION_PLAN item 7): FP and FN from a run's verdict record and labelled scenarios.

Builds a run directory by hand (run.json, events.jsonl, verdicts.jsonl) with one verdict per case and a
scenario that labels them, then checks the table, the strict headline (A), the abstention rate, the
alternatives (B, C), unmatched verdicts, labels no verdict reached, value and window matching, and
relative times. Deterministic; `python3 tests/test_metrics.py`.
"""
import io
import json
import os
import sys
import tempfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from liscere import metrics

_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


T0 = 2000.0


def verdict(seq, t, target, value, result):
    return {"run_id": "r1", "seq": seq, "type": "verdict", "silo": "10.0.0.5:4840", "target": target,
            "value": value, "datatype": "Float", "datatype_certain": True, "client": "10.0.0.9:48000",
            "frame_ts": T0 + t, "phase": "RISING", "confidence": 1.0, "transitioning": False,
            "result": result, "rule": "OBS-R00x", "learned_fraction": None, "reason": "test", "ts": T0 + t}


def make_run(root):
    run = os.path.join(root, "run1")
    os.makedirs(run)
    vs = [
        verdict(1, 10, "ns=4;i=45", 40.0, "INCOHERENT"),   # labelled INCOHERENT -> TP
        verdict(2, 20, "ns=4;i=45", 40.0, "UNUSUAL"),      # labelled INCOHERENT -> FN under A, TP under B
        verdict(3, 30, "ns=4;i=45", 40.0, "UNCERTAIN"),    # labelled INCOHERENT -> FN under A, abstention
        verdict(4, 40, "ns=4;i=45", 40.0, "INCOHERENT"),   # labelled COHERENT   -> FP
        verdict(5, 50, "ns=4;i=45", 40.0, "COHERENT"),     # labelled COHERENT   -> TN
        verdict(6, 60, "ns=4;i=45", 99.0, "COHERENT"),     # value 99 does not match the label's 40 -> unmatched
        verdict(7, 70, "ns=4;i=46", 1.0, "COHERENT"),      # other node, no label -> unmatched
    ]
    with open(os.path.join(run, "verdicts.jsonl"), "w") as f:
        for v in vs:
            f.write(json.dumps(v) + "\n")
    with open(os.path.join(run, "events.jsonl"), "w") as f:
        f.write(json.dumps({"type": "phase", "phase": "RISING", "frame_ts": T0, "ts": T0}) + "\n")
        for v in vs:
            f.write(json.dumps(v) + "\n")
    with open(os.path.join(run, "run.json"), "w") as f:
        json.dump({"run_id": "r1", "version": "0.1.0", "source": {"kind": "replay", "path": "x"}, "clock": "frame"}, f)
    return run


SCENARIO = """
id = "SCN-TEST-001"
description = "one verdict per case"
capture = "sha256:abc"

[[labels]]
target = "ns=4;i=45"
value = 40.0
rel_from = 5.0
rel_to = 35.0
expected = "INCOHERENT"
note = "three writes expected incoherent"

[[labels]]
target = "ns=4;i=45"
value = 40.0
from = %f
to = %f
expected = "COHERENT"
note = "two writes expected coherent (absolute times)"

[[labels]]
target = "ns=4;i=45"
value = 40.0
rel_from = 500.0
rel_to = 600.0
expected = "INCOHERENT"
note = "a label no verdict reaches"
""" % (T0 + 35, T0 + 65)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        run = make_run(tmp)
        sc = os.path.join(tmp, "scn.toml")
        with open(sc, "w") as f:
            f.write(SCENARIO)
        out = os.path.join(tmp, "metrics.json")
        buf = io.StringIO()
        real = sys.stdout
        sys.stdout = buf
        try:
            rc = metrics.main(["--run", run, "--scenario", sc, "--out", out])
        finally:
            sys.stdout = real
        text = buf.getvalue()
        check("metrics exits 0", rc == 0, f"(rc={rc})")
        with open(out) as f:
            rep = json.load(f)
        check("report names the run id and the capture hash", rep["run_id"] == "r1" and rep["captures"] == ["sha256:abc"])
        s = rep["scenarios"][0]
        t = s["table"]
        check("table: expected INCOHERENT row is INCOHERENT 1, UNUSUAL 1, UNCERTAIN 1",
              t["INCOHERENT"] == {"COHERENT": 0, "UNUSUAL": 1, "INCOHERENT": 1, "UNCERTAIN": 1}, f"(={t['INCOHERENT']})")
        check("table: expected COHERENT row is INCOHERENT 1, COHERENT 1",
              t["COHERENT"] == {"COHERENT": 1, "UNUSUAL": 0, "INCOHERENT": 1, "UNCERTAIN": 0}, f"(={t['COHERENT']})")
        h = s["counts"]["headline"]
        check("headline A: TP 1, FN 2, FP 1, TN 1", (h["tp"], h["fn"], h["fp"], h["tn"]) == (1, 2, 1, 1), f"(={h})")
        check("headline A rates carry denominators: FP 1/2 = 50%, FN 2/3 = 66.7%",
              h["fp_denominator"] == 2 and abs(h["fp_rate"] - 0.5) < 1e-9
              and h["fn_denominator"] == 3 and abs(h["fn_rate"] - 2 / 3) < 1e-9)
        a = s["counts"]["abstention"]
        check("abstention: 1 UNCERTAIN of 5 matched = 20%", a["uncertain"] == 1 and abs(a["rate"] - 0.2) < 1e-9)
        b = s["counts"]["alternatives"]["B_alarm_inclusive"]
        check("alternative B counts UNUSUAL as an alarm: TP 2, FN 1", (b["tp"], b["fn"]) == (2, 1), f"(={b})")
        c = s["counts"]["alternatives"]["C_abstention_excluded"]
        check("alternative C drops UNCERTAIN from the denominators: FN 0 of 2", (c["fn"], c["fn_denominator"]) == (0, 2), f"(={c})")
        check("two verdicts no label covers (value 99, other node)",
              sorted(u["seq"] for u in s["unmatched_verdicts"]) == [6, 7], f"(={[u['seq'] for u in s['unmatched_verdicts']]})")
        check("one label no verdict reached (the 500..600 window)",
              [x["label"] for x in s["labels_without_verdict"]] == [2])
        check("relative and absolute windows both match", len(s["matched"]) == 5)
        check("text summary shows the table and the headline", "expected \\ observed" in text and "headline (A:" in text)
        tot = rep["total"]["counts"]["headline"]
        check("total equals the single scenario", (tot["tp"], tot["fn"], tot["fp"], tot["tn"]) == (1, 2, 1, 1))

        # a bad scenario is rejected with exit 2
        bad = os.path.join(tmp, "bad.toml")
        with open(bad, "w") as f:
            f.write('id = "x"\n[[labels]]\ntarget = "a"\nexpected = "MAYBE"\nfrom = 0\nto = 1\n')
        err = io.StringIO()
        real_err = sys.stderr
        sys.stderr = err
        try:
            rc2 = metrics.main(["--run", run, "--scenario", bad, "--quiet"])
        finally:
            sys.stderr = real_err
        check("an invalid expected value is rejected (exit 2)", rc2 == 2 and "expected" in err.getvalue())

    print()
    if _failures:
        print(f"METRICS TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("METRICS TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
