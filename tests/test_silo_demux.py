#!/usr/bin/env python3
"""Silo demux test for the passive observer (phase 2a-1, no tshark).

Verifies that a single capture covering more than one server endpoint is demuxed into independent
silos, each discovered + calibrated from ITS OWN events, and that a silo too sparse to calibrate is
reported (not silently dropped). Also pins the single-silo invariant that makes "nothing changed"
testable: a one-endpoint stream partitions to exactly one bucket equal to the input, so main() takes
the original single-silo path.

Synthetic OPC UA events (same shape as test_observer_pipeline.py) with evt.server set per silo.
Deterministic; runnable as `python3 tests/test_silo_demux.py`.
"""
import io
import json
import os
import random
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "scripts"))

from otlab_core.contract import NormalizedEvent
from otlab_core.extractors.opcua import OpcUaExtractor
import liscere_observe as obs

SRV_A, SRV_B = "192.168.1.9:4840", "192.168.1.12:4840"   # two OPC UA silos in one capture

_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


def pub(t, samples, server):
    return NormalizedEvent(timestamp=t, protocol="OPCUA/binary", op="PUBLISH_RESPONSE",
                           direction="response", target=None, server=server,
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


def triangle_pubs(server, seed=0):
    """A full two-cycle level signal -> discoverable + calibratable (measurable period)."""
    evs, t = [], 0.0
    level = triangle(seed=seed)
    for i in range(0, len(level), 2):
        evs.append(pub(t, level[i:i + 2], server))
        t += 0.2
    return evs


def short_pubs(server, n=4):
    """Too few samples to calibrate a phase period -> reported, not evaluated."""
    evs, t, lvl = [], 0.0, 0.0
    for _ in range(n):
        lvl += 0.334
        evs.append(pub(t, [lvl, lvl + 0.167], server))
        t += 0.2
    return evs


def silo_events(buf):
    return [json.loads(l) for l in buf.getvalue().splitlines() if json.loads(l).get("type") == "silo"]


def main():
    ex = OpcUaExtractor()

    # --- single-silo invariant: one endpoint -> one bucket, identical to input -------------------
    a_only = triangle_pubs(SRV_A)
    parts = obs.partition_by_server(a_only)
    check("single endpoint -> one bucket", list(parts.keys()) == [SRV_A], f"(keys={list(parts.keys())})")
    check("single bucket is the input unchanged", parts[SRV_A] == a_only)

    # --- N=1 byte-identical: RUN the silo code and compare its emitted stream to the pre-silo path.
    # The whole point of the rework: the silo path is exercised on every single-silo run, and its
    # output equals calling discover_and_calibrate directly (what the observer did before silos).
    def events_without_ts(buf):
        # `ts` is a wall-clock stamp emit() adds per event; it varies every run in BOTH the old and
        # new code, so the byte-identical claim is over event CONTENT, not the timestamp field.
        out = []
        for line in buf.getvalue().splitlines():
            e = json.loads(line)
            e.pop("ts", None)
            out.append(e)
        return out

    buf_ref = io.StringIO()
    obs.discover_and_calibrate(ex, a_only, profile_path=None, log=lambda *a: None,
                               emitter=obs.Emitter(enabled=True, out=buf_ref))
    buf_silo = io.StringIO()
    em_silo = obs.Emitter(enabled=True, out=buf_silo)
    silos1 = obs.build_silos(ex, a_only, em_silo, log=lambda *a: None)
    check("N=1 observe stream is byte-identical (content) to pre-silo discover_and_calibrate",
          events_without_ts(buf_silo) == events_without_ts(buf_ref),
          "(differs)" if events_without_ts(buf_silo) != events_without_ts(buf_ref) else "")
    check("N=1 emits NO silo() announcement", '"type": "silo"' not in buf_silo.getvalue())
    check("N=1 events carry NO silo tag", '"silo"' not in buf_silo.getvalue())
    check("N=1 silo binds an untagged view of the ONE run Emitter (shared de-dup, tag=None)",
          silos1[0].emitter._shared is em_silo._shared and silos1[0].emitter._tag is None)
    check("N=1 silo is evaluable", len(silos1) == 1 and silos1[0].evaluable)

    # --- the tag mechanism itself: one shared Emitter, per-silo tag stamped only when set ----------
    tbuf = io.StringIO()
    shared = obs.Emitter(enabled=True, out=tbuf)
    shared.bind("10.1.1.1:4840").variable_value("k", 5)
    shared.bind(None).variable_value("k", 5)
    tagged = [json.loads(l) for l in tbuf.getvalue().splitlines()]
    check("bound(tag) stamps the silo tag on events", tagged[0].get("silo") == "10.1.1.1:4840")
    check("bound(None) stamps NO silo tag (byte-identical shape)", "silo" not in tagged[1])

    # --- demux: two full silos + one sparse silo + one server-less event ------------------------
    evs_a = triangle_pubs(SRV_A, seed=1)
    evs_b = triangle_pubs(SRV_B, seed=2)
    evs_c = short_pubs("10.0.0.5:4840")                 # sparse -> not evaluable
    orphan = [pub(0.0, [1.0, 2.0], None)]               # no server -> unassignable
    combined = evs_a + evs_b + evs_c + orphan

    parts = obs.partition_by_server(combined)
    check("first-seen order preserved (A, B, C, None)",
          list(parts.keys()) == [SRV_A, SRV_B, "10.0.0.5:4840", None],
          f"(keys={list(parts.keys())})")
    check("no cross-contamination: A bucket holds only A's events",
          parts[SRV_A] == evs_a and parts[SRV_B] == evs_b)

    buf = io.StringIO()
    em = obs.Emitter(enabled=True, out=buf)
    silos = obs.build_silos(ex, combined, em, log=lambda *a: None)

    by_ep = {s.endpoint: s for s in silos}
    check("two real silos built (orphan not added to the silo list)", len(silos) == 3)
    check("silo A evaluable + own state_key", by_ep[SRV_A].evaluable and by_ep[SRV_A].state_key == "opcua:publish:telemetry")
    check("silo B evaluable + own state_key", by_ep[SRV_B].evaluable and by_ep[SRV_B].state_key == "opcua:publish:telemetry")
    check("silo A and B: independent trackers/calibrations, one shared Emitter, distinct tags",
          by_ep[SRV_A].tracker is not by_ep[SRV_B].tracker
          and by_ep[SRV_A].calib is not by_ep[SRV_B].calib
          and by_ep[SRV_A].emitter._shared is by_ep[SRV_B].emitter._shared      # one Emitter, one stdout
          and (by_ep[SRV_A].emitter._tag, by_ep[SRV_B].emitter._tag) == (SRV_A, SRV_B))
    check("sparse silo reported NOT evaluable, with a reason",
          not by_ep["10.0.0.5:4840"].evaluable and bool(by_ep["10.0.0.5:4840"].reason),
          f"(reason={by_ep['10.0.0.5:4840'].reason!r})")

    # Each real silo calibrates from ONLY its own events (compare to standalone discovery).
    calib_a, _d, sf_a = obs.discover_and_calibrate(ex, evs_a, log=lambda *a: None)
    check("silo A calibrated from its own partition alone",
          by_ep[SRV_A].state_flow is not None
          and len(by_ep[SRV_A].state_flow.samples) == len(sf_a.samples),
          f"(silo={len(by_ep[SRV_A].state_flow.samples)}, standalone={len(sf_a.samples)})")

    # Reporting: a silo() event per real silo + one for the server-less bucket, all honest.
    ev = silo_events(buf)
    endpoints = {(e["endpoint"], e["evaluable"]) for e in ev}
    check("silo events emitted for every silo incl. sparse and orphan",
          (SRV_A, True) in endpoints and (SRV_B, True) in endpoints
          and ("10.0.0.5:4840", False) in endpoints and (None, False) in endpoints,
          f"(={sorted(str(x) for x in endpoints)})")

    # --- late silo: one first seen in learn/evaluate is REPORTED once, never silently skipped ------
    lbuf = io.StringIO()
    lem = obs.Emitter(enabled=True, out=lbuf)
    reported = set()
    route = lambda ep: obs._route_or_report_late(lem, by_ep, reported, ep)
    check("known evaluable endpoint routes to its silo (no report)", route(SRV_A) is by_ep[SRV_A])
    check("observed-but-not-evaluable endpoint returns None (already reported at build)",
          route("10.0.0.5:4840") is None)
    check("brand-new endpoint returns None and is reported once", route("10.9.9.9:4840") is None)
    _ = route("10.9.9.9:4840")   # second sighting must NOT re-report
    late = [json.loads(l) for l in lbuf.getvalue().splitlines() if json.loads(l).get("type") == "silo"]
    check("exactly one late-silo report for the new endpoint (deduped, not skipped)",
          [e["endpoint"] for e in late] == ["10.9.9.9:4840"] and late[0]["evaluable"] is False,
          f"(={[e['endpoint'] for e in late]})")

    print()
    if _failures:
        print(f"SILO DEMUX TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("SILO DEMUX TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
