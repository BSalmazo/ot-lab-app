#!/usr/bin/env python3
"""Report endpoints that carry captured-but-unreadable traffic.

The bench has a TLS-wrapped S7 channel at 192.168.1.12:102. Its frames are captured (S7's filter is
"tcp port 102"), tshark emits a TSV line for each with data.data and the endpoints populated, and
parse_line drops them at the wire_layer gate. This exercises the fix: S7's opaque_endpoint recognises
such a frame precisely (ISO-on-TCP transport, no s7comm layer, data.data first byte 0x72 S7CommPlus /
0x16-0x17 TLS), and the observer -- asking null-safe, holding no protocol knowledge -- reports the
endpoint ONCE as a non-evaluable silo, never as an event and never routed to a tracker.

Two layers: S7.opaque_endpoint directly, and the real observer capture loop driven by a fake tshark
process (so parse_line, _report_opaque and the Emitter dedup all run).

Deterministic; runnable as `python3 tests/test_opaque_endpoints.py`.
"""
import io
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "scripts"))

from otlab_core.extractors.s7 import S7CommExtractor
import liscere_observe as obs

# S7 _FIELDS order (21 columns).
_COLS = [
    "ts", "proto", "src", "sport", "dst", "dport", "rosctr", "pduref", "func",
    "itemcount", "area", "db", "addr_byte", "addr_bit", "transp_size", "item_length",
    "returncode", "data_transportsize", "data_length", "resp_data", "data_data",
]
ISO = "eth:ethertype:ip:tcp:tpkt:cotp"          # ISO-on-TCP transport, no application layer yet
PLC_OPAQUE = "192.168.1.12"                     # the TLS-wrapped S7 endpoint
PLC_READABLE = "192.168.1.9"

_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


def line(**kw):
    return [str(kw.get(c, "")) for c in _COLS]


def tls_frame(server=PLC_OPAQUE, first="16"):
    # server ip on port 102, client on an ephemeral port; payload starts with a TLS record byte
    return line(proto=f"{ISO}:data", src="192.168.1.13", sport=54000, dst=server, dport=102,
                data_data=f"{first}:03:01:00:2a")


def s7commplus_frame(server=PLC_OPAQUE):
    return line(proto=f"{ISO}:data", src="192.168.1.13", sport=54001, dst=server, dport=102,
                data_data="72:01:00:00")


def bare_tcp_frame():
    return line(proto="eth:ethertype:ip:tcp", src="192.168.1.13", sport=54000, dst=PLC_OPAQUE, dport=102)


def write_job_frame():
    # A readable single-frame S7 write (ROSCTR=1 Job, func=0x05) -> a WRITE_REQUEST event.
    return line(proto=f"{ISO}:s7comm", src="192.168.1.10", sport=51000, dst=PLC_READABLE, dport=102,
                rosctr=1, pduref=1, func=5, itemcount=1, area=0x84, db=1, addr_byte=0,
                transp_size=0x05, resp_data="00:64")


class FakeProc:
    """A stand-in for the tshark subprocess: a finite line iterator, no-op teardown."""
    def __init__(self, lines):
        self.stdout = iter(lines)
    def terminate(self):
        pass
    def wait(self, timeout=None):
        pass
    def kill(self):
        pass


def main():
    ex = S7CommExtractor()

    # --- 1) S7.opaque_endpoint decides precisely --------------------------------------------------
    ep_tls = ex.opaque_endpoint(tls_frame(first="16"))
    check("TLS-on-102 -> (endpoint, reason)", ep_tls is not None and ep_tls[0] == "192.168.1.12:102")
    check("TLS reason states what was seen (transport + first byte)",
          ep_tls is not None and "TLS record" in ep_tls[1] and "0x16" in ep_tls[1],
          f"({ep_tls[1] if ep_tls else None})")
    ep_plus = ex.opaque_endpoint(s7commplus_frame())
    check("S7CommPlus (0x72) -> (endpoint, reason)",
          ep_plus is not None and ep_plus[0] == "192.168.1.12:102" and "S7CommPlus" in ep_plus[1]
          and "0x72" in ep_plus[1])
    check("bare TCP (no tpkt/cotp) -> None", ex.opaque_endpoint(bare_tcp_frame()) is None)
    check("cotp/data but no payload -> None (transport plumbing)",
          ex.opaque_endpoint(line(proto=f"{ISO}:data", src="192.168.1.13", sport=54000,
                                   dst=PLC_OPAQUE, dport=102)) is None)
    check("first byte 0x32 (readable s7comm header) -> None (not claimed opaque)",
          ex.opaque_endpoint(tls_frame(first="32")) is None)
    check("frame carrying the s7comm layer -> None (parse_line's job, not opaque)",
          ex.opaque_endpoint(write_job_frame()) is None)

    # --- 2) the observer capture loop: report once, event flow unaffected -------------------------
    # 689-frames-one-event, in miniature: two opaque frames for one endpoint, a bare TCP frame that
    # yields nothing, and a readable write that must still produce an event.
    raw_lines = ["\t".join(cols) + "\n" for cols in
                 (tls_frame(), tls_frame(), bare_tcp_frame(), write_job_frame())]
    real_spawn = obs._spawn
    obs._spawn = lambda extractor, iface: FakeProc(raw_lines)
    buf = io.StringIO()
    em = obs.Emitter(enabled=True, out=buf)
    try:
        events = obs.capture_events(S7CommExtractor(), "eth0", 60, log=lambda *a: None, emitter=em)
    finally:
        obs._spawn = real_spawn

    silos = [json.loads(l) for l in buf.getvalue().splitlines() if json.loads(l).get("type") == "silo"]
    check("exactly ONE silo event across many opaque frames (announced once)", len(silos) == 1,
          f"(={len(silos)})")
    check("the opaque silo is the right endpoint, not evaluable, with a reason",
          len(silos) == 1 and silos[0]["endpoint"] == "192.168.1.12:102"
          and silos[0]["evaluable"] is False and bool(silos[0]["reason"]))
    check("a bare TCP frame produced no silo event", all(s["endpoint"] != "192.168.1.13:54000" for s in silos))
    check("the readable write still produced its event (flow unaffected)",
          len(events) == 1 and events[0].op == "WRITE_REQUEST" and events[0].target == "s7:db1:0",
          f"(events={[ (e.op, e.target) for e in events]})")
    check("the opaque endpoint never became a readable event",
          all(e.server != "192.168.1.12:102" for e in events))

    print()
    if _failures:
        print(f"OPAQUE ENDPOINTS TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("OPAQUE ENDPOINTS TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
