#!/usr/bin/env python3
"""Respawn / capture-supervisor test: the observer must survive a tshark death and resume.

The weekend soak lost a Modbus tshark (rc=1) hours into a continuous evaluate. The guarantee this
pins: a child death is survived, not fatal -- respawn under a bounded backoff -- and the critical
state (phase tracker, grammar, calibration, silos) lives in PYTHON on the Silo, untouched by a
respawn; only tshark's dissector state is lost and rebuilds itself. While one protocol's capture is
down, the others keep being judged without pause.

Coverage:
  1. Supervisor policy (deterministic, injected clock): death -> lost + scheduled respawn; respawn on
     backoff; resume on first bytes with the right downtime; give-up after N tries -> permanently lost.
  2. The state guarantee: a silo's tracker and grammar are the SAME objects after a respawn, and a
     write after resume is judged against the pre-death grammar.
  3. A timed window (observe/learn) extends by the downtime rather than calibrating short.
  4. multiplex: a survivor's stream is unbroken while another source dies and respawns; the backoff
     does not busy-loop; give-up with no runnable source left -> rc=3.

Deterministic where it can be; the few real-time loop checks use short, generous bounds.
`python3 tests/test_respawn.py`.
"""
import io
import json
import os
import random
import sys
import threading
import time
import types

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "scripts"))

from otlab_core.contract import NormalizedEvent
from otlab_core.extractors.opcua import OpcUaExtractor
import liscere_observe as obs

SRV = "10.0.0.9:4840"
_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


class Proc:
    """A tshark stand-in over a real pipe. `data` bytes are written up front; with close=True the write
    end is closed (EOF after data -> a dead tshark), else it stays open (a live capture) and can be
    fed more via feed()."""
    def __init__(self, data=b"", close=True):
        self._r, self._w = os.pipe()
        if data:
            os.write(self._w, data)
        if close:
            os.close(self._w)
            self._w = None
        self.stdout = types.SimpleNamespace(fileno=lambda: self._r)

    def feed(self, data):
        os.write(self._w, data)

    def poll(self):
        return None if self._w is not None else 1     # live -> running; closed -> exited rc=1

    def terminate(self):
        for fd in (self._r, self._w):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def wait(self, timeout=None):
        pass

    def kill(self):
        pass


class CountingSelector(obs.selectors.DefaultSelector):
    count = 0
    def select(self, timeout=None):
        CountingSelector.count += 1
        return super().select(timeout)


def captures(buf):
    return [json.loads(l) for l in buf.getvalue().splitlines() if json.loads(l).get("type") == "capture"]


# --- OPC UA synthetic signal (evaluable silo), same shape as the other observer tests -------------

def pub(t, samples, server=SRV):
    return NormalizedEvent(timestamp=t, protocol="OPCUA/binary", op="PUBLISH_RESPONSE",
                           direction="response", target=None, server=server, client="10.0.0.1:5000",
                           value=(samples[0] if samples else None),
                           raw={"float_samples": list(samples), "value_is_float": True, "variant_type": 0x0A})


def triangle(step=0.167, turns=(0, 100, 0, 100, 0), noise=0.01, seed=0):
    rnd = random.Random(seed)
    vals, cur = [], float(turns[0])
    for nxt in turns[1:]:
        d = 1 if nxt > cur else -1
        while (d > 0 and cur < nxt) or (d < 0 and cur > nxt):
            cur += d * step
            vals.append(cur + rnd.gauss(0, noise))
        cur = float(nxt)
    return vals


def observe_pubs():
    evs, t, lv = [], 0.0, triangle()
    for i in range(0, len(lv), 2):
        evs.append(pub(t, lv[i:i + 2])); t += 0.2
    return evs


def write_evt(t, val):
    return NormalizedEvent(timestamp=t, protocol="OPCUA/binary", op="WRITE_REQUEST", direction="request",
                           target="ns=4;i=23", value=val, server=SRV, client="10.0.0.1:5000",
                           raw={"variant_type": 0x0A})


def run_to_evaluate(emitter):
    """Drive a _Run (no tshark) through observe -> learn -> evaluate on one OPC UA silo, returning the
    run with a live evaluable silo carrying a learned grammar."""
    src = types.SimpleNamespace(extractor=OpcUaExtractor())
    args = types.SimpleNamespace(observe=1.0, learn=1.0, grammar=None, profile=None)
    run = obs._Run([src], args, emitter, log=lambda *a: None)
    for e in observe_pubs():
        run.dispatch(src.extractor, e)
    run.observe_end = 0.0
    run.tick()                                   # -> learn
    # Learn: feed a rising state series so the tracker holds a real phase, and a write AT that phase,
    # so the grammar actually records ns=4;i=23 (a write at UNKNOWN would learn nothing).
    t, lvl = 10.0, 0.0
    for i in range(30):
        s1 = lvl + 0.167; s2 = s1 + 0.167; lvl = s2
        run.dispatch(src.extractor, pub(t, [s1, s2])); t += 0.2
        if i == 20:
            run.dispatch(src.extractor, write_evt(t, 80.0)); t += 0.2
    run.learn_end = 0.0
    run.tick()                                   # -> evaluate
    return run, src.extractor


def main():
    # === 1) Supervisor policy: death -> backoff respawn -> resume (deterministic clock) ===========
    cbuf = io.StringIO()
    em = obs.Emitter(enabled=True, out=cbuf)
    made, resumed_downtime = [], []
    sup = obs._Supervisor(spawn=lambda ext: made.append(Proc(b"", close=False)) or made[-1],
                          log=lambda *a: None, emitter=em, on_resume=resumed_downtime.append,
                          retries=36, backoff_cap=30.0, base=1.0, grace=0.0)
    src = obs._Source(OpcUaExtractor(), Proc(b"", close=True))     # a dead tshark
    dead_proc_r = src.fd

    sup.note_death(src, now=100.0)
    check("death emits a 'lost' capture event and schedules a respawn",
          [c["state"] for c in captures(cbuf)] == ["lost"] and sup.pending() and sup.is_degraded())
    sup.service(now=100.5)                        # before the 1s backoff -> nothing yet
    check("respawn does NOT fire before the backoff elapses", src.fd == dead_proc_r and not made)
    fresh = sup.service(now=101.0)               # backoff (1s) elapsed -> respawn
    check("respawn fires once the backoff elapses (fresh proc, same source object)",
          fresh == [src] and len(made) == 1 and src.fd == made[0].stdout.fileno())
    check("the respawned source keeps the SAME extractor (its Python state survives)",
          src.extractor is not None)
    sup.mark_alive(src, now=101.4)               # first bytes after respawn -> resumed
    caps = captures(cbuf)
    check("first bytes after respawn emit 'resumed' with the downtime",
          [c["state"] for c in caps] == ["lost", "resumed"]
          and abs(caps[1]["down_s"] - 1.4) < 1e-6, f"(={caps})")
    check("the downtime is reported to on_resume (feeds window extension)",
          resumed_downtime == [round(1.4, 3)] or (resumed_downtime and abs(resumed_downtime[0] - 1.4) < 1e-6),
          f"(={resumed_downtime})")
    check("after resume the supervisor is no longer degraded/pending",
          not sup.pending() and not sup.is_degraded())

    # === 2) Give-up after the configured retries -> permanently lost ==============================
    gbuf = io.StringIO()
    gem = obs.Emitter(enabled=True, out=gbuf)
    gsup = obs._Supervisor(spawn=lambda ext: Proc(b"", close=True),   # always dies again
                           log=lambda *a: None, emitter=gem, retries=2, backoff_cap=0.0, base=0.0, grace=0.0)
    gsrc = obs._Source(OpcUaExtractor(), Proc(b"", close=True))
    t = 0.0
    for _ in range(5):                           # death, respawn, death, ... until give-up
        gsup.note_death(gsrc, now=t); t += 0.001
        for s in gsup.service(now=t):            # respawn if due (backoff 0)
            pass
        t += 0.001
        if not gsup.pending():
            break
    gcaps = [c["state"] for c in captures(gbuf)]
    check("give-up after retries -> a 'permanently_lost' capture event",
          "permanently_lost" in gcaps, f"(={gcaps})")
    check("a permanently-lost source is neither pending nor degraded (it will not respawn forever)",
          not gsup.pending() and not gsup.is_degraded())

    # === 3) The state guarantee: tracker + grammar survive a respawn, verdict uses the old grammar =
    vbuf = io.StringIO()
    vem = obs.Emitter(enabled=True, out=vbuf)
    run, extractor = run_to_evaluate(vem)
    check("reached continuous evaluate with one evaluable silo", run.phase == "evaluate" and run.evaluable)
    silo = run.evaluable[0]
    tracker_before, grammar_before = silo.tracker, silo.grammar
    learned_targets = set(grammar_before.keys())
    check("a grammar was learned before the (simulated) death", bool(learned_targets), f"(={learned_targets})")

    # Respawn this silo's extractor through the supervisor -- the Silo is never handed to it.
    rsup = obs._Supervisor(spawn=lambda ext: Proc(b"", close=False), log=lambda *a: None,
                           emitter=vem, grace=0.0)
    rsrc = obs._Source(extractor, Proc(b"", close=True))
    rsup.note_death(rsrc, now=0.0)
    rsup.service(now=99.0)                        # respawn
    rsup.mark_alive(rsrc, now=99.1)              # resumed
    check("silo.tracker is the SAME object after a respawn (not reset)", silo.tracker is tracker_before)
    check("silo.grammar is the SAME object after a respawn (not reset)", silo.grammar is grammar_before)
    check("the respawned source carries the SAME extractor object (S7 _pending etc. preserved)",
          rsrc.extractor is extractor and extractor is silo.extractor)

    before = len([l for l in vbuf.getvalue().splitlines() if json.loads(l).get("type") == "verdict"])
    run.dispatch(extractor, write_evt(200.0, 80.0))   # a write AFTER resume
    verdicts = [json.loads(l) for l in vbuf.getvalue().splitlines() if json.loads(l).get("type") == "verdict"]
    check("a write after resume is judged (a verdict is produced), against the pre-death grammar",
          len(verdicts) == before + 1 and verdicts[-1]["target"] in learned_targets, f"(={verdicts[-1:]})")

    # === 4) A timed window EXTENDS by the downtime rather than calibrating short ===================
    wbuf = io.StringIO()
    wem = obs.Emitter(enabled=True, out=wbuf)
    wsrc = types.SimpleNamespace(extractor=OpcUaExtractor())
    wargs = types.SimpleNamespace(observe=1.0, learn=1.0, grammar=None, profile=None)
    wrun = obs._Run([wsrc], wargs, wem, log=lambda *a: None)
    for e in observe_pubs():
        wrun.dispatch(wsrc.extractor, e)
    degraded = {"v": True}
    wrun.degraded = lambda: degraded["v"]
    wrun.observe_end = 0.0                        # deadline already past...
    obs_end_before, learn_end_before = wrun.observe_end, wrun.learn_end
    wrun.tick()
    check("a timed window does NOT end while capture is degraded (would calibrate short)",
          wrun.phase == "observe", f"(phase={wrun.phase})")
    wrun.extend_window(3.0)                       # a 3s outage just ended
    check("the window (and the downstream learn window) are extended by the downtime",
          wrun.observe_end == obs_end_before + 3.0 and wrun.learn_end == learn_end_before + 3.0)
    degraded["v"] = False
    wrun.observe_end = 0.0                        # re-past after the extension, no longer degraded
    wrun.tick()
    check("once no longer degraded the window ends and the run advances", wrun.phase in ("learn", "evaluate"),
          f"(phase={wrun.phase})")

    # === 5) multiplex: a survivor's stream is unbroken while another dies and respawns =============
    ext_a = types.SimpleNamespace(name="MODBUS/TCP",
                                  parse_line=lambda c: NormalizedEvent(
                                      timestamp=0.0, protocol="MODBUS/TCP", op="PUBLISH_RESPONSE",
                                      direction="response", target=None, server="A", client="c",
                                      value=1.0, raw={}))
    ext_b = types.SimpleNamespace(name="OPCUA/binary", parse_line=lambda c: None)
    proc_a = Proc(b"", close=False)              # survivor: fed by a thread throughout
    src_a = obs._Source(ext_a, proc_a)
    src_b = obs._Source(ext_b, Proc(b"", close=True))   # dies at once
    sbuf = io.StringIO()
    sem = obs.Emitter(enabled=True, out=sbuf)
    ssup = obs._Supervisor(spawn=lambda ext: Proc(b"L\n", close=False),   # B resumes and produces
                           log=lambda *a: None, emitter=sem, base=0.3, backoff_cap=0.3, grace=0.1)
    seen_a = []
    stop = threading.Event()

    def feed_a():
        i = 0
        while not stop.is_set() and i < 30:
            try:
                proc_a.feed(f"A{i}\n".encode())
            except OSError:
                break
            i += 1
            time.sleep(0.04)                     # ~30 lines over ~1.2s, spanning B's downtime

    fed = threading.Thread(target=feed_a, daemon=True)
    fed.start()
    t0 = time.time()
    obs.multiplex([src_a, src_b], on_event=lambda ext, e: seen_a.append(ext.name) if ext is ext_a else None,
                  emitter=sem, log=lambda *a: None, supervisor=ssup, until=lambda: time.time() - t0 > 1.1)
    stop.set(); fed.join(timeout=1.0)
    scaps = [c["state"] for c in captures(sbuf)]
    check("the survivor kept producing throughout the other's death+respawn (unbroken)",
          len(seen_a) >= 8, f"(A events={len(seen_a)})")
    check("meanwhile the dead source was reported lost and then resumed",
          scaps[:2] == ["lost", "resumed"], f"(={scaps})")
    check("the survivor's coalescer was never reset by the other's death (same object)",
          src_a.coalescer is not None)

    # === 5b) the outage window is genuinely ABSENT after resume -- not fabricated or backfilled ====
    # The honest counterpart to "survivor unbroken": we resume, we do NOT pretend the dark window was
    # covered. Bytes that never crossed the wire cannot appear, and a partial frame straddling the
    # death is dropped WITH the old buffer -- never stitched to the first post-resume bytes, which
    # would forge a frame ("HALF" + "2" -> "HALF2") that no capture ever saw.
    seen = []
    gext = types.SimpleNamespace(name="G", parse_line=lambda cols: NormalizedEvent(
        timestamp=0.0, protocol="G", op="READ_RESPONSE", direction="response", target=cols[0],
        server="g", client="c", value=None, raw={}))
    gsrc2 = obs._Source(gext, Proc(b"1\nHALF", close=True))   # "1" delivered; "HALF" straddles the death
    osup = obs._Supervisor(spawn=lambda ext: Proc(b"2\n", close=False),   # resumes and emits "2"
                           log=lambda *a: None, emitter=None, base=0.1, backoff_cap=0.1, grace=0.1)
    t0 = time.time()
    obs.multiplex([gsrc2], on_event=lambda ext, e: seen.append(e.target), emitter=None,
                  log=lambda *a: None, supervisor=osup, until=lambda: time.time() - t0 > 0.6)
    check("resume neither backfills the outage nor stitches a fabricated frame across it "
          "(only the real pre/post frames survive; the straddling partial is dropped)",
          seen == ["1", "2"], f"(={seen})")

    # === 6) The backoff does not busy-loop (bounded selects with a source down) ===================
    bsrc = obs._Source(types.SimpleNamespace(name="X", parse_line=lambda c: None), Proc(b"", close=True))
    bsup = obs._Supervisor(spawn=lambda ext: Proc(b"", close=False),   # respawns quiet-but-live
                           log=lambda *a: None, emitter=None, base=0.5, backoff_cap=0.5, grace=0.2)
    real_sel = obs.selectors.DefaultSelector
    obs.selectors.DefaultSelector = CountingSelector
    CountingSelector.count = 0
    t0 = time.time()
    try:
        obs.multiplex([bsrc], on_event=lambda *a: None, emitter=None, log=lambda *a: None,
                      supervisor=bsup, until=lambda: time.time() - t0 > 0.9)
    finally:
        obs.selectors.DefaultSelector = real_sel
    check("a source down and backing off BLOCKS on select (bounded iterations, not a spin)",
          CountingSelector.count < 20, f"(selects in ~0.9s = {CountingSelector.count}; a spin would be 1000s)")

    # === 7) main(): give-up with no runnable source left -> rc=3, permanently lost reported ========
    real_probe, real_spawn = obs.probe_layers, obs._spawn
    obs.probe_layers = lambda iface, secs, log=print, emitter=None: ({"modbus"}, {})
    obs._spawn = lambda ext, iface, reset_after=None: Proc(b"", close=True)   # always dies
    ebuf = io.StringIO()
    _es, _eo = sys.stderr, sys.stdout
    sys.stderr, sys.stdout = ebuf, io.StringIO()
    try:
        rc = obs.main(["--iface", "x", "--probe", "1", "--observe", "60", "--learn", "60",
                       "--respawn-retries", "2", "--respawn-backoff-cap", "0.01"])
    finally:
        sys.stderr, sys.stdout = _es, _eo
        obs.probe_layers, obs._spawn = real_probe, real_spawn
    check("a capture that exhausts its retries -> main returns 3 (no runnable source remains)", rc == 3,
          f"(rc={rc})")
    check("give-up is explained on stderr", "capture lost" in ebuf.getvalue())

    print()
    if _failures:
        print(f"RESPAWN TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("RESPAWN TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
