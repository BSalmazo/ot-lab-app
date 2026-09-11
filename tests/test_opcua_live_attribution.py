#!/usr/bin/env python3
"""OPC UA live per-variable attribution: the phase tracker is fed ONLY the discovered state signal.

Requirement A (the D1 / level-4b fix). A subscription PublishResponse batches several variables, each
a MonitoredItemNotification keyed by ClientHandle. In the LIVE feed the observer passes the discovered
state flow key (opcua:sub:<handle>) so extract_state_samples returns ONLY that handle's values. During
a hold the state signal is silent while OTHER handles keep publishing; without attribution their 0/1
values bleed into the held signal and collapse it (the reported defect). This test proves the bleed is
gone: only the state handle reaches the tracker, and a hold returns no samples (not another variable's).

It also fixes the fail-loud contract (a key that does not resolve yields nothing, never every handle)
and the D1 isolation (the merged opcua:publish:telemetry key never feeds the phase path). Polled
Modbus and S7 are asserted byte-identical under the new signature (dormant). Deterministic;
`python3 tests/test_opcua_live_attribution.py`.
"""
import os
import sys
from types import SimpleNamespace

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from otlab_core.engine.phase_tracker import PhaseTracker
from otlab_core.extractors.modbus import ModbusExtractor
from otlab_core.extractors.opcua import _FIELDS, OpcUaExtractor
from otlab_core.extractors.s7 import S7CommExtractor

_IDX = {name: i for i, name in enumerate(_FIELDS)}
_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


def line(**kw):
    row = [""] * len(_FIELDS)
    for field, value in kw.items():
        key = "opcua." + field
        row[_IDX[key] if key in _IDX else _IDX[field]] = str(value)
    return row


def pub(t, **kw):
    base = {"frame.time_epoch": str(t), "frame.protocols": "eth:ethertype:ip:tcp:opcua",
            "ip.src": "10.0.0.9", "ip.dst": "10.0.0.5", "tcp.srcport": "48000", "tcp.dstport": "4840",
            "transport.type": "MSG", "servicenodeid.numeric": "829"}    # 829 = PublishResponse
    base.update(kw)
    return line(**base)


def main():
    ex = OpcUaExtractor()
    STATE = "opcua:sub:1"   # discovery found handle 1 (the tank Level) as the state signal

    # A cycle: the Level (handle 1) rises, then HOLDS (silent, report-by-exception) while another
    # variable (handle 2) flips 0/1, then rises again. The 0/1 vs ~10..30 gap makes any bleed obvious.
    stream = [
        ex.parse_line(pub(1.0, ClientHandle="1,2", **{"variant.has_value": "0x0a,0x04"}, Float="10.0", Int16="0")),  # both
        ex.parse_line(pub(1.1, ClientHandle="1", **{"variant.has_value": "0x0a"}, Float="20.0")),                     # Level moves
        ex.parse_line(pub(1.2, ClientHandle="2", **{"variant.has_value": "0x04"}, Int16="1")),                        # HOLD: other -> 1
        ex.parse_line(pub(1.3, ClientHandle="2", **{"variant.has_value": "0x04"}, Int16="0")),                        # HOLD: other -> 0
        ex.parse_line(pub(1.4, ClientHandle="1", **{"variant.has_value": "0x0a"}, Float="30.0")),                     # Level moves
    ]

    # --- 1) live feed WITH attribution: only handle 1 reaches the tracker ---
    tracker = PhaseTracker()
    fed, per_event, held = [], [], []
    for evt in stream:
        s = ex.extract_state_samples(evt, state_key=STATE)
        per_event.append(s)
        for v in s:
            tracker.update(v)
            fed.append(v)
        held.append(tracker.last_level)
    check("only the state handle's values reach the tracker (no 0/1 bleed)",
          fed == [10.0, 20.0, 30.0], f"(fed={fed})")
    check("a hold returns NO state sample (another variable never substitutes)",
          per_event[2] == [] and per_event[3] == [], f"(hold C={per_event[2]}, D={per_event[3]})")
    check("through both holds the tracker's value stays the held Level (20.0), never 0/1",
          held[2] == 20.0 and held[3] == 20.0, f"(after holds: {held[2]}, {held[3]})")

    # --- 2) the BUG path (no attribution) bleeds every handle: shown explicitly for the record ---
    bleed = [v for evt in stream for v in ex.extract_state_samples(evt)]   # state_key=None, config unset
    check("without attribution every handle bleeds in (this is the defect being fixed)",
          bleed == [10.0, 0.0, 20.0, 1.0, 0.0, 30.0], f"(bleed={bleed})")

    # --- 3) each value is attributed to its OWN variable (discovery view unchanged) ---
    flows = {f.key: [v for _t, v in f.samples] for f in ex.group_flows(stream)}
    check("each variable keeps its own series (handle 1 = Level, handle 2 = other)",
          flows.get("opcua:sub:1") == [10.0, 20.0, 30.0] and flows.get("opcua:sub:2") == [0.0, 1.0, 0.0],
          f"(1={flows.get('opcua:sub:1')}, 2={flows.get('opcua:sub:2')})")

    # --- 4) explicit key resolution, fail loud (no silent leak, no silent starve) ---
    check("resolve_state_key maps a flow key and a bare handle to the int handle",
          ex.resolve_state_key("opcua:sub:7") == 7 and ex.resolve_state_key("7") == 7)
    check("resolve_state_key returns None for a key it cannot map",
          ex.resolve_state_key("modbus:hr:2") is None and ex.resolve_state_key("opcua:sub:x") is None
          and ex.resolve_state_key(None) is None)
    check("a state_key that does not resolve yields NOTHING, never every handle (fail-loud-safe)",
          ex.extract_state_samples(stream[0], state_key="opcua:sub:x") == [],
          f"(={ex.extract_state_samples(stream[0], state_key='opcua:sub:x')})")

    # --- 5) D1 isolation: the merged telemetry key exists but never feeds the phase path ---
    check("variable_key still returns the merged telemetry key for a publish (UI display only)",
          ex.variable_key(stream[2]) == "opcua:publish:telemetry")
    check("the phase feed used per-handle attribution, so the merged key never reached the tracker",
          0.0 not in fed and 1.0 not in fed)

    # --- 6) polled Modbus and S7 are byte-identical under the new signature (resampler-free, dormant) ---
    mb = ModbusExtractor()
    mb_evt = SimpleNamespace(op="READ_RESPONSE", raw={"exception_code": None, "reg_values": [(2, 55.0)]})
    check("Modbus extract_state_samples ignores state_key (byte-identical)",
          mb.extract_state_samples(mb_evt) == mb.extract_state_samples(mb_evt, state_key="opcua:sub:1")
          == mb.extract_state_samples(mb_evt, state_key="modbus:hr:2") == [55.0])
    s7 = S7CommExtractor()
    s7_evt = SimpleNamespace(op="READ_RESPONSE", raw={"items": [("s7:db1:0", 80.0, "REAL", True)]})
    check("S7 extract_state_samples ignores state_key (byte-identical)",
          s7.extract_state_samples(s7_evt) == s7.extract_state_samples(s7_evt, state_key="anything") == [80.0])

    print()
    if _failures:
        print(f"OPCUA LIVE ATTRIBUTION TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("OPCUA LIVE ATTRIBUTION TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
