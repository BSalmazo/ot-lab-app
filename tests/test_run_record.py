#!/usr/bin/env python3
"""Verdict record (MODERNISATION_PLAN item 6): every verdict carries its evidence, every run
leaves a record on disk.

Covers: the verdict event fields (value, datatype, client, frame_ts, confidence, transitioning,
learned_fraction, reason) on top of the pre-record shape (target, phase, result, rule); frame_ts on
phase and variable_value events; the RunRecord directory (run.json manifest, events.jsonl mirroring
the stream byte for byte, verdicts.jsonl with run_id and seq); the sink working with stdout emission
disabled; --no-run-dir leaving no directory behind. Deterministic; `python3 tests/test_run_record.py`.
"""
import io
import json
import os
import sys
import tempfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "scripts"))

import liscere_observe as obs

from otlab_core.contract import NormalizedEvent
from otlab_core.extractors.opcua import OpcUaExtractor

_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


def pub(t, samples):
    return NormalizedEvent(timestamp=t, protocol="OPCUA/binary", op="PUBLISH_RESPONSE", direction="response",
                           client="10.0.0.9:48000", server="10.0.0.5:4840", target=None,
                           value=(samples[0] if samples else None),
                           raw={"float_samples": list(samples), "value_is_float": True, "variant_type": 0x0A})


def wr(t, target, value):
    return NormalizedEvent(timestamp=t, protocol="OPCUA/binary", op="WRITE_REQUEST", direction="request",
                           client="10.0.0.9:48000", server="10.0.0.5:4840", target=target, value=value,
                           raw={"variant_type": 0x0A})


def ramp(start, n, direction, t0, step=0.167, write_at=(), target="ns=4;i=23"):
    events, lvl, t = [], start, t0
    for i in range(n):
        s1 = lvl + direction * step
        s2 = s1 + direction * step
        lvl = s2
        events.append(pub(t, [s1, s2]))
        t += 0.2
        if i in write_at:
            events.append(wr(t, target, 40.0))
            t += 0.2
    return events, lvl, t


def stream(buf):
    return [json.loads(line) for line in buf.getvalue().splitlines()]


def main():
    ex = OpcUaExtractor()

    # Calibrate from an observe window (two clean fill/drain cycles), as the observer does.
    observe, t0, lvl0 = [], 0.0, 0.0
    for direction in (+1, -1, +1, -1):
        e, lvl0, t0 = ramp(lvl0, 300, direction, t0)
        observe += e
    calib, _disc, _flow = obs.discover_and_calibrate(ex, observe, log=lambda *a: None)
    check("calibration from the observe window is measurable", calib is not None and calib.measurable)
    cfg = calib.config

    # A grammar that says ns=4;i=23 is written during RISING only.
    learn_events, _, _ = ramp(0.0, 40, +1, 0.0, write_at=(25, 30, 35))
    grammar = obs.run_learn(ex, learn_events, obs.PhaseTracker(cfg)).get("grammar", {})

    # Evaluate: rise -> write (COHERENT), fall -> write (INCOHERENT); record everything.
    ev, lvl, t = ramp(0.0, 30, +1, 0.0)
    ev.append(wr(t, "ns=4;i=23", 40.0)); t += 0.2
    e2, lvl, t = ramp(lvl, 45, -1, t)
    ev += e2
    ev.append(wr(t, "ns=4;i=23", 40.0))

    print("[record] run directory, manifest, events and verdicts")
    with tempfile.TemporaryDirectory() as tmp:
        rec = obs.RunRecord(tmp)
        rec.update(args={"iface": "x"}, source={"kind": "test"}, clock="wall")
        buf = io.StringIO()
        em = obs.Emitter(enabled=True, out=buf, sink=rec.sink)
        verdicts = obs.run_evaluate(ex, ev, obs.PhaseTracker(cfg), grammar, emitter=em.bind("10.0.0.5:4840"),
                                    state_key="opcua:publish:telemetry")
        rec.close(0)

        check("two verdicts returned", len(verdicts) == 2, f"(={len(verdicts)})")
        events = stream(buf)
        vev = [e for e in events if e["type"] == "verdict"]
        check("two verdict events emitted", len(vev) == 2)
        needed = {"target", "phase", "result", "rule", "value", "datatype", "datatype_certain", "client",
                  "frame_ts", "confidence", "transitioning", "learned_fraction", "reason", "silo", "ts"}
        check("verdict event carries every evidence field", all(needed <= set(e) for e in vev),
              f"(missing={[sorted(needed - set(e)) for e in vev]})")
        check("verdict value is the written value", all(e["value"] == 40.0 for e in vev))
        check("verdict datatype comes from the extractor (Float, certain)",
              all(e["datatype"] == "Float" and e["datatype_certain"] is True for e in vev))
        check("verdict client and frame_ts come from the write event",
              all(e["client"] == "10.0.0.9:48000" and isinstance(e["frame_ts"], float) for e in vev))
        check("results are COHERENT then INCOHERENT", [e["result"] for e in vev] == ["COHERENT", "INCOHERENT"],
              f"(={[e['result'] for e in vev]})")
        check("reason text present and learned_fraction numeric or None",
              all(e["reason"] and (e["learned_fraction"] is None or isinstance(e["learned_fraction"], float))
                  for e in vev))

        ph = [e for e in events if e["type"] == "phase"]
        vv = [e for e in events if e["type"] == "variable_value"]
        check("phase events carry frame_ts", bool(ph) and all("frame_ts" in e for e in ph))
        check("variable_value events carry frame_ts", bool(vv) and all("frame_ts" in e for e in vv))

        with open(os.path.join(rec.path, "events.jsonl")) as f:
            disk = f.read()
        check("events.jsonl is byte-identical to the stdout stream", disk == buf.getvalue())
        with open(os.path.join(rec.path, "verdicts.jsonl")) as f:
            vrec = [json.loads(line) for line in f]
        check("verdicts.jsonl has one record per verdict with run_id and seq",
              [v["seq"] for v in vrec] == [1, 2] and all(v["run_id"] == rec.run_id for v in vrec))
        check("verdicts.jsonl record equals the stream event plus run_id and seq",
              all({k: v for k, v in r.items() if k not in ("run_id", "seq")} == e for r, e in zip(vrec, vev)))
        with open(os.path.join(rec.path, "run.json")) as f:
            man = json.load(f)
        check("run.json manifest has version, timestamps, args, source, exit code and verdict count",
              {"run_id", "version", "started_at", "ended_at", "args", "source", "clock", "exit_code",
               "verdicts"} <= set(man) and man["exit_code"] == 0 and man["verdicts"] == 2,
              f"(keys={sorted(man)})")
        check("run id is timestamp plus 8 hex", len(rec.run_id) == 16 + 1 + 8 and rec.run_id[15] == "Z")

    print("[record] the sink is independent of stdout emission")
    with tempfile.TemporaryDirectory() as tmp:
        rec2 = obs.RunRecord(tmp)
        quiet = io.StringIO()
        em2 = obs.Emitter(enabled=False, out=quiet, sink=rec2.sink)
        obs.run_evaluate(ex, ev, obs.PhaseTracker(cfg), grammar, emitter=em2, state_key="opcua:publish:telemetry")
        rec2.close(0)
        with open(os.path.join(rec2.path, "verdicts.jsonl")) as f:
            n = sum(1 for _ in f)
        check("--no-emit-json still records verdicts on disk", n == 2 and quiet.getvalue() == "", f"(n={n})")

    print("[record] --no-run-dir")
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            real_probe = obs.probe_layers
            obs.probe_layers = lambda iface, secs, log=print, emitter=None: (set(), {})
            try:
                rc = obs.main(["--no-run-dir", "--iface", "x", "--probe", "1"])
            finally:
                obs.probe_layers = real_probe
            check("main() with --no-run-dir leaves no runs/ directory", rc == 2 and not os.path.exists("runs"))
            obs.probe_layers = lambda iface, secs, log=print, emitter=None: (set(), {})
            try:
                rc = obs.main(["--iface", "x", "--probe", "1", "--runs-dir", "rec"])
            finally:
                obs.probe_layers = real_probe
            runs = os.listdir("rec")
            with open(os.path.join("rec", runs[0], "run.json")) as f:
                man = json.load(f)
            check("main() records even a run that found nothing (exit 2 in the manifest)",
                  rc == 2 and len(runs) == 1 and man["exit_code"] == 2 and man["source"]["iface"] == "x")
        finally:
            os.chdir(cwd)

    print()
    if _failures:
        print(f"RUN RECORD TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("RUN RECORD TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
