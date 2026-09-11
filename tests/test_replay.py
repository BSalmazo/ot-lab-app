#!/usr/bin/env python3
"""Replay and record (MODERNISATION_PLAN item 5): a recorded run replays deterministically on frame time.

Builds a synthetic OPC UA recording (the raw tshark field lines the extractor would see) shaped like
the bench: an observe window of two clean fill/drain cycles, then a learn cycle and an evaluate cycle
each with the bench's holds (rise to 50, hold, rise to 80, hold, fall), a status handle flipping every
sample, and supervisory writes to one node. Replays it with `--replay`, twice, and checks:

- the acceptance sequence RISING, STABLE, RISING, STABLE, FALLING in evaluate, with held values 50.0
  and 80.0 and no flicker;
- a write during RISING is COHERENT (learned), during a hold and during FALLING INCOHERENT;
- the two runs' events.jsonl are byte-identical (frame clock, no wall time);
- a live run (tshark stood in by a pipe) records its input under capture/, and replaying that record
  reproduces the same verdicts.

Deterministic; `python3 tests/test_replay.py`.
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

from otlab_core.extractors.opcua import _FIELDS

_IDX = {name: i for i, name in enumerate(_FIELDS)}
_failures = []

SERVER, CLIENT = ("10.0.0.5", "4840"), ("10.0.0.9", "48000")
DT = 0.1
T0 = 1000.0


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


def row(**kw):
    cols = [""] * len(_FIELDS)
    for field, value in kw.items():
        key = "opcua." + field
        cols[_IDX[key] if key in _IDX else _IDX[field]] = str(value)
    return "\t".join(cols)


def pub(t, handle, value):
    """A PublishResponse from the server carrying one MonitoredItemNotification."""
    typed = {"Float": f"{value}"} if handle == 1 else {"Int16": str(int(value))}
    return row(**{"frame.time_epoch": f"{t:.6f}", "frame.protocols": "eth:ethertype:ip:tcp:opcua",
                  "ip.src": SERVER[0], "tcp.srcport": SERVER[1], "ip.dst": CLIENT[0], "tcp.dstport": CLIENT[1],
                  "transport.type": "MSG", "servicenodeid.numeric": "829", "ClientHandle": str(handle),
                  "variant.has_value": "0x0a" if handle == 1 else "0x04", **typed})


def write(t, node, value):
    """A WriteRequest from the client to one node (AttributeId marks the write item)."""
    return row(**{"frame.time_epoch": f"{t:.6f}", "frame.protocols": "eth:ethertype:ip:tcp:opcua",
                  "ip.src": CLIENT[0], "tcp.srcport": CLIENT[1], "ip.dst": SERVER[0], "tcp.dstport": SERVER[1],
                  "transport.type": "MSG", "servicenodeid.numeric": "673",
                  "nodeid.numeric": f"1875154555,0,{node}", "AttributeId": "13",
                  "variant.has_value": "0x0a", "Float": f"{value}"})


class Timeline:
    """Emits rows in frame order: every sample slot carries the Level (handle 1) when it moves, and the
    status (handle 2) flip; during a hold only the status flips (report by exception: Level silent)."""

    def __init__(self):
        self.t = T0
        self.rows = []
        self.marks = {}

    def mark(self, name):
        self.marks[name] = self.t

    def ramp(self, start, end, step=0.5):
        v = start
        while (step > 0 and v < end) or (step < 0 and v > end):
            v = round(v + step, 3)
            self.rows.append(pub(self.t, 1, v))
            self.rows.append(pub(self.t + DT / 2, 2, len(self.rows) % 2))
            self.t += DT

    def hold(self, seconds):
        for _ in range(int(seconds / DT)):
            self.rows.append(pub(self.t, 2, len(self.rows) % 2))
            self.t += DT

    def write(self, node, value):
        self.rows.append(write(self.t + DT / 4, node, value))

    def bench_cycle(self, writes):
        """rise 0->50, hold, rise 50->80, hold, fall 80->0. ``writes`` maps a segment name to (node, value)."""
        self.mark("rise1")
        self._segment(lambda: self.ramp(0, 50), writes.get("rise1"))
        self.mark("hold1")
        self._segment(lambda: self.hold(6), writes.get("hold1"))
        self.mark("rise2")
        self._segment(lambda: self.ramp(50, 80), writes.get("rise2"))
        self.mark("hold2")
        self._segment(lambda: self.hold(6), writes.get("hold2"))
        self.mark("fall")
        self._segment(lambda: self.ramp(80, 0, -0.5), writes.get("fall"))
        self.mark("end")

    def _segment(self, run, w):
        """Run a segment; a write, if any, lands in its middle."""
        before = len(self.rows)
        run()
        if w is not None:
            mid = before + (len(self.rows) - before) // 2
            t_mid = float(self.rows[mid].split("\t", 1)[0])
            self.rows.insert(mid, write(t_mid + DT / 4, *w))


def build_recording():
    tl = Timeline()
    tl.mark("observe")
    tl.ramp(0, 100); tl.ramp(100, 0, -0.5); tl.ramp(0, 100); tl.ramp(100, 0, -0.5)    # 80 s, no holds
    tl.mark("learn")
    tl.bench_cycle({"rise1": (45, 40.0), "rise2": (45, 40.0)})
    # a second learn cycle so the grammar sees the write twice in RISING (fraction 1.0 either way)
    tl.bench_cycle({"rise1": (45, 40.0)})
    tl.mark("evaluate")
    tl.bench_cycle({"rise1": (45, 40.0), "hold2": (45, 40.0), "fall": (45, 40.0)})
    return tl


def write_capture(root, tl):
    cap = os.path.join(root, "capture")
    os.makedirs(cap)
    with open(os.path.join(cap, "probe.tsv"), "w") as f:
        f.write("\t".join(["eth:ethertype:ip:tcp:opcua", CLIENT[0], CLIENT[1], SERVER[0], SERVER[1]]) + "\n")
        f.write("\t".join(["eth:ethertype:ip:tcp", CLIENT[0], CLIENT[1], SERVER[0], SERVER[1]]) + "\n")
    with open(os.path.join(cap, "claimed.json"), "w") as f:
        json.dump(["opcua"], f)
    with open(os.path.join(cap, "opcua.tsv"), "w") as f:
        f.write("\n".join(tl.rows) + "\n")
    return cap


def replay(cap, runs_dir, observe, learn):
    out = io.StringIO()
    err = io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        rc = obs.main(["--replay", cap, "--observe", str(observe), "--learn", str(learn), "--runs-dir", runs_dir])
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    run = sorted(os.listdir(runs_dir))[-1]
    path = os.path.join(runs_dir, run)
    with open(os.path.join(path, "events.jsonl")) as f:
        events = [json.loads(line) for line in f]
    with open(os.path.join(path, "run.json")) as f:
        manifest = json.load(f)
    return rc, events, manifest, path, out.getvalue(), err.getvalue()


def dedup(seq):
    out = []
    for x in seq:
        if not out or x != out[-1]:
            out.append(x)
    return out


def main():
    tl = build_recording()
    observe_s = tl.marks["learn"] - tl.marks["observe"]
    learn_s = tl.marks["evaluate"] - tl.marks["learn"]
    print(f"[replay] recording: {len(tl.rows)} rows, observe {observe_s:.0f}s, learn {learn_s:.0f}s, "
          f"evaluate {tl.marks['end'] - tl.marks['evaluate']:.0f}s")

    with tempfile.TemporaryDirectory() as tmp:
        cap = write_capture(tmp, tl)
        rc1, ev1, man1, path1, out1, err1 = replay(cap, os.path.join(tmp, "runs1"), observe_s, learn_s)
        check("replay exits 0 at end of input", rc1 == 0, f"(rc={rc1})")
        check("manifest records a replay source on the frame clock",
              man1.get("clock") == "frame" and man1.get("source", {}).get("kind") == "replay", f"(={man1.get('source')})")
        stages = [e["stage"] for e in ev1 if e["type"] == "stage"]
        check("observe -> learn -> evaluate all reached on frame time", stages == ["observe", "learn", "evaluate"],
              f"(={stages})")
        silos = [e for e in ev1 if e["type"] == "silo"]
        check("one evaluable silo, the server endpoint",
              len(silos) == 1 and silos[0]["endpoint"] == "10.0.0.5:4840" and silos[0]["evaluable"] is True, f"(={silos})")
        state = [e for e in ev1 if e["type"] == "state_signal_discovered"]
        check("the Level handle is the discovered state signal", state and state[0]["key"] == "opcua:sub:1", f"(={state})")

        # --- the acceptance sequence in evaluate ---
        i_eval = next(i for i, e in enumerate(ev1) if e["type"] == "stage" and e["stage"] == "evaluate")
        phases = dedup([e["phase"] for e in ev1[i_eval:] if e["type"] == "phase"])
        check("evaluate phase sequence is RISING, STABLE, RISING, STABLE, FALLING (no flicker)",
              phases == ["RISING", "STABLE", "RISING", "STABLE", "FALLING"], f"(={phases})")
        # The last bench_cycle built is the evaluate cycle, so its marks bound the two holds.
        m = tl.marks
        level = [(e["frame_ts"], e["value"]) for e in ev1[i_eval:]
                 if e["type"] == "variable_value" and e["key"] == "opcua:sub:1"]

        def during(a, b):
            return [v for t, v in level if a <= t < b]

        def last_before(a):
            prior = [v for t, v in level if t < a]
            return prior[-1] if prior else None

        hold1, hold2 = during(m["hold1"], m["rise2"]), during(m["hold2"], m["fall"])
        check("through hold 1 the state value is exactly 50.0 (no 0/1 bleed from the status handle)",
              last_before(m["hold1"]) == 50.0 and set(hold1) <= {50.0}, f"(hold1 values={sorted(set(hold1))})")
        check("through hold 2 the state value is exactly 80.0",
              last_before(m["hold2"]) == 80.0 and set(hold2) <= {80.0}, f"(hold2 values={sorted(set(hold2))})")
        status_only = [v for t, v in level if v in (0.0, 1.0) and (m["hold1"] <= t < m["rise2"] or m["hold2"] <= t < m["fall"])]
        check("the status handle never reaches the state feed during a hold", not status_only)

        verdicts = [e for e in ev1 if e["type"] == "verdict"]
        results = [v["result"] for v in verdicts]
        check("three writes judged in evaluate: COHERENT (rising), INCOHERENT (hold), INCOHERENT (falling)",
              results == ["COHERENT", "INCOHERENT", "INCOHERENT"], f"(={results})")
        check("verdict ts is frame time (equals the write's frame_ts, rounded)",
              all(abs(v["ts"] - v["frame_ts"]) < 0.001 for v in verdicts))
        check("verdict target and value are the written node and value",
              all(v["target"] == "ns=0;i=45" and v["value"] == 40.0 for v in verdicts))

        # --- determinism ---
        rc2, ev2, man2, path2, _o, _e = replay(cap, os.path.join(tmp, "runs2"), observe_s, learn_s)
        with open(os.path.join(path1, "events.jsonl"), "rb") as f:
            b1 = f.read()
        with open(os.path.join(path2, "events.jsonl"), "rb") as f:
            b2 = f.read()
        check("two replays of the same recording produce byte-identical events.jsonl", b1 == b2 and rc2 == 0,
              f"(len {len(b1)} vs {len(b2)})")
        check("stdout stream equals the recorded events.jsonl", out1.encode() == b1)

        # --- input shorter than the observe window ---
        rc3, ev3, man3, _p, _o, err3 = replay(cap, os.path.join(tmp, "runs3"), observe_s * 10, learn_s)
        check("input ending inside the observe window exits 2 and says so",
              rc3 == 2 and "ended during the observe window" in err3, f"(rc={rc3})")

    # --- record on a live run, then replay the record ---
    print("[record] live run (pipe stands in for tshark) -> capture/ -> replay")
    with tempfile.TemporaryDirectory() as tmp:
        probe_rows = ("\t".join(["eth:ethertype:ip:tcp:opcua", CLIENT[0], CLIENT[1], SERVER[0], SERVER[1]]) + "\n").encode()
        data = ("\n".join(tl.rows) + "\n").encode()

        # tshark is stood in by `cat <file>`: a real child on a real pipe, so the selector sees EOF (a
        # regular file never reports EOF through kqueue, and the payload exceeds a pipe buffer).
        paths = {}
        for name, payload in (("probe", probe_rows), ("opcua", data)):
            paths[name] = os.path.join(tmp, f"{name}.tsv")
            with open(paths[name], "wb") as f:
                f.write(payload)
        real_popen = obs.subprocess.Popen

        def fake_popen(cmd, **kw):
            which = "opcua" if "opcua.Float" in cmd else "probe"
            return real_popen(["cat", paths[which]], stdout=obs.subprocess.PIPE, bufsize=0)

        obs.subprocess.Popen = fake_popen
        err = io.StringIO()
        real_err, sys.stderr = sys.stderr, err
        try:
            rc = obs.main(["--iface", "x", "--probe", "1", "--observe", "1", "--learn", "1",
                           "--respawn-retries", "0", "--runs-dir", os.path.join(tmp, "live")])
        finally:
            sys.stderr = real_err
            obs.subprocess.Popen = real_popen
        live_run = os.path.join(tmp, "live", sorted(os.listdir(os.path.join(tmp, "live")))[-1])
        cap = os.path.join(live_run, "capture")
        with open(os.path.join(cap, "opcua.tsv"), "rb") as f:
            recorded = f.read()
        with open(os.path.join(cap, "claimed.json")) as f:
            claimed = json.load(f)
        check("live run records every raw line the extractor received, byte for byte", recorded == data,
              f"(rc={rc}, {len(recorded)} vs {len(data)} bytes)")
        check("live run records the probe lines and the claimed layers", claimed == ["opcua"]
              and os.path.getsize(os.path.join(cap, "probe.tsv")) == len(probe_rows))
        rc4, ev4, man4, _p, _o, _e = replay(live_run, os.path.join(tmp, "runs4"), observe_s, learn_s)
        results4 = [e["result"] for e in ev4 if e["type"] == "verdict"]
        check("replaying the live record reproduces the verdicts",
              rc4 == 0 and results4 == ["COHERENT", "INCOHERENT", "INCOHERENT"], f"(={results4})")
        check("a replay run does not record itself again (recorded: None)", man4.get("recorded") is None
              or not os.path.exists(os.path.join(man4["recorded"], "opcua.tsv")))

    print()
    if _failures:
        print(f"REPLAY TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("REPLAY TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
