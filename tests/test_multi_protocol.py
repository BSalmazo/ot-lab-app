#!/usr/bin/env python3
"""Multi-protocol observer test (phase 2b): probe -> claimed layers -> N tshark -> one selector loop.

The 2b change is running MORE THAN ONE extractor at once, discovered from the wire rather than
configured. This pins the seams that only exist with N>1, none of which the single-protocol tests
reach:

  1. probe_layers classifies a no-filter capture into CLAIMED wire layers (extractors to run) and
     UNCLAIMED application traffic (surfaced as unreadable endpoints), and claims NOTHING when only
     transport plumbing is on the wire.
  2. multiplex routes each stdout line to the extractor that OWNS its fd -- two protocols in flight
     at once land in the right parser, demuxed by file descriptor.
  3. _Run over N extractors reports a claimed extractor that yields NO evaluable silo ("claimed X,
     no evaluable silo") while the OTHER extractor still proceeds to learn+evaluate -- the honesty
     requirement that a claimed-but-empty protocol is reported, not silently absent.
  4. main() with nothing claimed exits cleanly (rc=2, a message) and does NOT hang.

Real extractors throughout (Modbus, OPC UA, S7), driven by pipe-backed fake tshark processes so the
real parse_line / coalesce / selector loop run. Deterministic; `python3 tests/test_multi_protocol.py`.
"""
import io
import json
import os
import random
import sys
import types

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "scripts"))

from otlab_core.contract import NormalizedEvent
from otlab_core.extractors.modbus import ModbusExtractor
from otlab_core.extractors.opcua import OpcUaExtractor
from otlab_core.extractors.s7 import S7CommExtractor
import liscere_observe as obs

_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


class FakeProc:
    """A stand-in for a tshark subprocess: a real pipe carrying `data` bytes then EOF, so the probe
    and the multiplex (both os.read the raw fd) see a genuine file descriptor."""
    def __init__(self, data):
        self._r, w = os.pipe()
        os.write(w, data)     # small enough to fit the pipe buffer; closing the write end -> EOF
        os.close(w)
        self.stdout = types.SimpleNamespace(fileno=lambda: self._r)
    def poll(self):
        return 0
    def terminate(self):
        try:
            os.close(self._r)
        except OSError:
            pass
    def wait(self, timeout=None):
        pass
    def kill(self):
        pass


def tsv(rows):
    return b"".join(("\t".join(str(c) for c in r) + "\n").encode() for r in rows)


# --- protocol line builders (real tshark field orders) --------------------------------------------

_MB_COLS = ["ts", "proto", "src", "sport", "dst", "dport", "trans", "unit", "func", "ref_write",
            "read_ref", "word_cnt", "bit_cnt", "regval", "bitval", "exc", "regnum", "data"]
_MB_PROTO = "eth:ethertype:ip:tcp:mbtcp:modbus"

_S7_COLS = ["ts", "proto", "src", "sport", "dst", "dport", "rosctr", "pduref", "func", "itemcount",
            "area", "db", "addr_byte", "addr_bit", "transp_size", "item_length", "returncode",
            "data_transportsize", "data_length", "resp_data", "data_data"]
_S7_ISO = "eth:ethertype:ip:tcp:tpkt:cotp"


def mb_line(ts, **kw):
    kw.setdefault("ts", f"{ts:.3f}"); kw.setdefault("proto", _MB_PROTO)
    return [kw.get(c, "") for c in _MB_COLS]


def s7_line(**kw):
    return [kw.get(c, "") for c in _S7_COLS]


# --- OPC UA synthetic events (same shape as the other observer tests) -----------------------------

def pub(t, samples, server):
    return NormalizedEvent(timestamp=t, protocol="OPCUA/binary", op="PUBLISH_RESPONSE",
                           direction="response", target=None, server=server, client="10.0.0.9:5000",
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


def triangle_pubs(server):
    evs, t, level = [], 0.0, triangle()
    for i in range(0, len(level), 2):
        evs.append(pub(t, level[i:i + 2], server)); t += 0.2
    return evs


def stages(buf):
    return [json.loads(l)["stage"] for l in buf.getvalue().splitlines()
            if json.loads(l).get("type") == "stage"]


def silos(buf):
    return [json.loads(l) for l in buf.getvalue().splitlines() if json.loads(l).get("type") == "silo"]


def main():
    # === 1) probe_layers: classify a no-filter capture ============================================
    # A capture carrying Modbus + OPC UA (both claimed) and HTTP (no extractor), plus a bare-TCP
    # plumbing frame that must be ignored. Driven through a pipe-backed fake tshark.
    probe_rows = [
        [_MB_PROTO, "10.0.0.9", "50000", "10.0.0.5", "502"],                       # modbus  (claimed)
        ["eth:ethertype:ip:tcp:opcua", "10.0.0.9", "51000", "10.0.0.6", "4840"],   # opcua   (claimed)
        ["eth:ethertype:ip:tcp:http", "10.0.0.9", "52000", "10.0.0.7", "80"],      # http    (unclaimed)
        ["eth:ethertype:ip:tcp", "10.0.0.9", "53000", "10.0.0.5", "502"],          # bare TCP -> plumbing
    ]
    real_popen = obs.subprocess.Popen
    obs.subprocess.Popen = lambda *a, **k: FakeProc(tsv(probe_rows))
    pbuf = io.StringIO()
    pem = obs.Emitter(enabled=True, out=pbuf)
    try:
        claimed, unclaimed = obs.probe_layers("x", 1, log=lambda *a: None, emitter=pem)
    finally:
        obs.subprocess.Popen = real_popen
    check("probe claims exactly the wire layers seen on application traffic",
          claimed == {"modbus", "opcua"}, f"(={sorted(claimed)})")
    check("probe reports the unclaimed HTTP endpoint (server = lower port, :80)",
          "10.0.0.7:80" in unclaimed and "http" in unclaimed["10.0.0.7:80"], f"(={unclaimed})")
    psilos = silos(pbuf)
    check("the unclaimed endpoint surfaces once as a non-evaluable (unreadable) silo",
          len(psilos) == 1 and psilos[0]["endpoint"] == "10.0.0.7:80"
          and psilos[0]["evaluable"] is False, f"(={psilos})")

    # probe with ONLY transport plumbing on the wire -> nothing claimed (drives main()'s clean exit).
    obs.subprocess.Popen = lambda *a, **k: FakeProc(tsv([["eth:ethertype:ip:tcp", "1.1.1.1", "5", "2.2.2.2", "9"]]))
    try:
        claimed_none, _ = obs.probe_layers("x", 1, log=lambda *a: None)
    finally:
        obs.subprocess.Popen = real_popen
    check("probe claims nothing when only transport plumbing is on the wire", claimed_none == set(),
          f"(={claimed_none})")

    # === 2) multiplex routes each fd's lines to the extractor that owns it =========================
    # Modbus and S7 in flight at once: each fd carries its OWN protocol, and every line must reach the
    # right parser. A write on each -> exactly one event per protocol, attributed to its extractor.
    mb, s7 = ModbusExtractor(), S7CommExtractor()
    mb_data = tsv([mb_line(0.0, src="10.0.0.9", sport=50000, dst="10.0.0.5", dport=502,
                           trans=2, unit=1, func=6, ref_write=10, regval=77)])
    s7_data = tsv([s7_line(proto=f"{_S7_ISO}:s7comm", src="10.0.0.9", sport=51000, dst="10.0.0.8",
                           dport=102, rosctr=1, pduref=1, func=5, itemcount=1, area=0x84, db=1,
                           addr_byte=0, transp_size=0x05, resp_data="00:64")])
    sources = [obs._Source(mb, FakeProc(mb_data)), obs._Source(s7, FakeProc(s7_data))]
    seen = []
    obs.multiplex(sources, on_event=lambda ext, e: seen.append((ext.name, e.server, e.target)),
                  emitter=None, log=lambda *a: None)
    by_ext = {name: (server, target) for name, server, target in seen}
    check("both protocols produced exactly one event each (routed, not crossed)", len(seen) == 2,
          f"(={seen})")
    check("the Modbus line reached the Modbus extractor with its own server/target",
          by_ext.get("MODBUS/TCP") == ("10.0.0.5:502", "modbus:hr:10"), f"(={by_ext.get('MODBUS/TCP')})")
    check("the S7 line reached the S7 extractor with its own server/target",
          by_ext.get("S7COMM") == ("10.0.0.8:102", "s7:db1:0"), f"(={by_ext.get('S7COMM')})")

    # === 3) _Run over N extractors: one evaluable, one claimed-but-empty ===========================
    # OPC UA sees a full cycling level (evaluable); Modbus sees only writes (a flow, but no state
    # signal -> no evaluable silo). The barren extractor must be REPORTED, and the run must still
    # advance to learn on the strength of the evaluable one -- not exit rc=2.
    opc = OpcUaExtractor()
    rsources = [types.SimpleNamespace(extractor=opc), types.SimpleNamespace(extractor=mb)]
    rbuf = io.StringIO()
    rem = obs.Emitter(enabled=True, out=rbuf)
    args = types.SimpleNamespace(observe=1.0, learn=1.0, grammar=None, profile=None)
    run = obs._Run(rsources, args, rem, log=lambda *a: None)

    for e in triangle_pubs("10.0.0.6:4840"):
        run.dispatch(opc, e)                                  # OPC UA: an evaluable silo
    for i in range(3):
        run.dispatch(mb, ModbusExtractor().parse_line(
            mb_line(i * 0.2, src="10.0.0.9", sport=50000, dst="10.0.0.5", dport=502,
                    trans=2, unit=1, func=6, ref_write=10, regval=77)))   # Modbus: writes, no state
    run.observe_end = 0.0                                     # force the observe deadline into the past
    run.tick()

    reports = [(s["endpoint"], s["reason"]) for s in silos(rbuf)
               if s.get("reason") and "no evaluable silo" in s["reason"]]
    check("the claimed-but-empty Modbus extractor is reported, not silently absent",
          any("MODBUS/TCP" in (r or "") for _ep, r in reports), f"(={reports})")
    check("the evaluable OPC UA silo was built and is evaluable",
          any(s.endpoint == "10.0.0.6:4840" and s.evaluable for s in run.evaluable))
    check("one extractor empty does NOT sink the run: it proceeds to learn (not rc=2 exit)",
          run.phase == "learn" and not run.stop and run.rc == 0,
          f"(phase={run.phase}, stop={run.stop}, rc={run.rc})")
    check("observe -> learn announced (evaluate still ahead)", stages(rbuf) == ["observe", "learn"],
          f"(={stages(rbuf)})")

    # === 4) main() with nothing claimed exits cleanly (no hang) ===================================
    real_probe = obs.probe_layers
    obs.probe_layers = lambda iface, secs, log=print, emitter=None: (set(), {})
    ebuf = io.StringIO()
    _real_stderr, sys.stderr = sys.stderr, ebuf
    try:
        rc = obs.main(["--iface", "x", "--probe", "1", "--observe", "1", "--learn", "1"])
    finally:
        sys.stderr = _real_stderr
        obs.probe_layers = real_probe
    check("no claimed protocol -> main returns 2 (does not hang, does not raise)", rc == 2, f"(rc={rc})")
    check("no claimed protocol -> a human message explains why", "no known protocol" in ebuf.getvalue())

    print()
    if _failures:
        print(f"MULTI-PROTOCOL TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("MULTI-PROTOCOL TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
