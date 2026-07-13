#!/usr/bin/env python3
"""Modbus flow-path test: field resolution, per-register grouping, state samples, coalescing.

Exercises the Modbus extractor's learned-pipeline surface on synthetic tshark TSV lines that
match the REAL 4.4.15 field layout (regnum16/regval_uint16 populated on responses,
read_reference_num empty). Covers: FC3 request/response register resolution per register, a
quantised staircase state series driving the PhaseTracker to RISING, FC6 write parsing, a
multi-register FC3 response, the modbus.data hex fallback, and write coalescing (burst collapse,
value-change break, gap break).

Deterministic; runnable as `python3 tests/test_modbus_flows.py`.
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "scripts"))   # for the orchestrator's derived-window helper

from otlab_core.config import ModbusConfig
from otlab_core.engine.discover import classify_flows
from otlab_core.engine.phase_tracker import PhaseTracker
from otlab_core.extractors.modbus import ModbusExtractor

_COLS = [
    "ts", "proto", "src", "sport", "dst", "dport", "trans", "unit", "func",
    "ref_write", "read_ref", "word_cnt", "bit_cnt", "regval", "bitval", "exc",
    "regnum", "data",
]
_PROTO = "eth:ethertype:ip:tcp:mbtcp:modbus"
HMI, PLC = "192.168.1.9", "192.168.1.12"

_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


def line(ts, **kw):
    kw.setdefault("ts", f"{ts:.3f}")
    kw.setdefault("proto", _PROTO)
    return "\t".join(str(kw.get(c, "")) for c in _COLS).split("\t")


def fc3_resp(ex, ts, regval, regnum):
    return ex.parse_line(line(ts, src=PLC, sport=502, dst=HMI, dport=50000, trans=1, unit=1,
                              func=3, regval=regval, regnum=regnum))


def fc6_write(ex, ts, reg, val=None, data=""):
    kw = dict(src=HMI, sport=50000, dst=PLC, dport=502, trans=2, unit=1, func=6, ref_write=reg)
    if val is not None:
        kw["regval"] = val
    if data:
        kw["data"] = data
    return ex.parse_line(line(ts, **kw))


def quantised_triangle():
    """0..100..0..100..0 stepping +1 roughly every third sample (2/3 of adjacent deltas == 0)."""
    seq, v, d = [], 0.0, 1.0 / 3.0
    for turn in (100, 0, 100, 0):
        while (d > 0 and v < turn) or (d < 0 and v > turn):
            seq.append(int(round(v)))
            v += d
        d = -d
    return seq


def main():
    ex = ModbusExtractor()

    # --- FC3 request/response register resolution ---
    req = ex.parse_line(line(0.0, src=HMI, sport=50000, dst=PLC, dport=502, trans=1, unit=1,
                             func=3, read_ref=2, word_cnt=1))
    resp = fc3_resp(ex, 0.1, regval=45, regnum=2)
    check("FC3 request register from read_reference_num", req.target == 2, f"(={req.target})")
    check("FC3 request quantity from word_cnt", req.quantity == 1, f"(={req.quantity})")
    check("FC3 response register from regnum16 (read_ref empty)", resp.target == 2, f"(={resp.target})")
    check("FC3 response value from regval_uint16", resp.value == 45, f"(={resp.value})")
    check("FC3 request produces no state sample", ex.extract_state_samples(req) == [])
    check("FC3 response state sample == value", ex.extract_state_samples(resp) == [45.0],
          f"(={ex.extract_state_samples(resp)})")
    check("variable_key keys per register", ex.variable_key(resp) == "modbus:hr:2",
          f"(={ex.variable_key(resp)})")

    # --- FC6 write parsing: regval and modbus.data hex fallback ---
    w_regval = fc6_write(ex, 1.0, reg=0, val=90)
    w_hex = fc6_write(ex, 1.1, reg=1, data="0064")
    check("FC6 write raw register from reference_num", w_regval.raw["register"] == 0,
          f"(={w_regval.raw['register']})")
    check("FC6 write target is the flow-key string", w_regval.target == "modbus:hr:0",
          f"(={w_regval.target})")
    check("FC6 write target aligns with variable_key",
          w_regval.target == ex.variable_key(w_regval), f"(={w_regval.target})")
    check("FC6 write value from regval_uint16", w_regval.value == 90, f"(={w_regval.value})")
    check("FC6 write value from modbus.data hex (0064 -> 100)", w_hex.value == 100, f"(={w_hex.value})")
    check("write produces no state sample", ex.extract_state_samples(w_regval) == [])

    # --- multi-register FC3 response: pair each value with its regnum16 ---
    multi = fc3_resp(ex, 2.0, regval="45,50,55", regnum="2,3,4")
    check("multi-register reg_values paired",
          multi.raw["reg_values"] == [(2, 45), (3, 50), (4, 55)], f"(={multi.raw['reg_values']})")
    check("multi-register state samples (all, in order)",
          ex.extract_state_samples(multi) == [45.0, 50.0, 55.0], f"(={ex.extract_state_samples(multi)})")

    # --- state_signal_register filter isolates one register ---
    ex_hr3 = ModbusExtractor(ModbusConfig(state_signal_register=3))
    multi3 = fc3_resp(ex_hr3, 2.1, regval="45,50,55", regnum="2,3,4")
    check("state_signal_register filters to HR[3] only",
          ex_hr3.extract_state_samples(multi3) == [50.0], f"(={ex_hr3.extract_state_samples(multi3)})")

    # --- group_flows + classification on a quantised poll + writes ---
    seq = quantised_triangle()
    events, t = [], 0.0
    for i, val in enumerate(seq):
        events.append(fc3_resp(ex, t, regval=val, regnum=2)); t += 0.1
        if i in (100, 300):
            events.append(fc6_write(ex, t, reg=0, val=100)); t += 0.1
        if i in (200,):
            events.append(fc6_write(ex, t, reg=1, val=200)); t += 0.1
    result = classify_flows(ex.group_flows(events))
    by_key = {v.key: v for v in result.flows}
    check("HR[2] poll discovered as STATE", result.state_flow_key == "modbus:hr:2",
          f"(={result.state_flow_key})")
    check("HR[0] write is COMMAND",
          by_key.get("modbus:hr:0") is not None and by_key["modbus:hr:0"].verdict == "COMMAND")
    check("HR[1] write is COMMAND",
          by_key.get("modbus:hr:1") is not None and by_key["modbus:hr:1"].verdict == "COMMAND")
    check("Modbus datatype always uncertain",
          all(v.datatype is None and v.datatype_certain is False for v in result.flows))

    # --- quantised staircase drives the PhaseTracker to RISING (not STABLE) ---
    tracker = PhaseTracker()
    for val in [int(round(i / 3.0)) for i in range(30)]:   # +1 every ~3 samples, monotonic rise
        tracker.update(float(val))
    check("quantised rise classified RISING (window slope, not adjacent deltas)",
          tracker.phase == "RISING", f"(={tracker.phase})")

    # --- write coalescing ---
    # A SHORT burst (span < window) collapses to one event.
    exc = ModbusExtractor(ModbusConfig(coalesce_window_s=1.0))
    short = [fc6_write(exc, i * 0.23, reg=0, val=100) for i in range(4)]   # span 0.69 s < 1.0 s
    out = list(exc.coalesce_writes(iter(short)))
    check("short burst (span < window) collapses to 1", len(out) == 1, f"(n={len(out)})")
    check("collapsed write keeps repeat_count", out and out[0].raw.get("repeat_count") == 4,
          f"(={out[0].raw.get('repeat_count') if out else None})")

    # A GAPLESS burst longer than the window splits into ceil(duration / window) events, so a phase
    # reversal inside a held button is not masked. 17 writes at 0.25 s span 4.0 s, window 1.0 s.
    import math
    excL = ModbusExtractor(ModbusConfig(coalesce_window_s=1.0))
    longburst = [fc6_write(excL, i * 0.25, reg=0, val=100) for i in range(17)]
    outL = list(excL.coalesce_writes(iter(longburst)))
    span = longburst[-1].timestamp - longburst[0].timestamp
    check("gapless burst longer than window splits (ceil(duration/window))",
          len(outL) == math.ceil(span / 1.0), f"(n={len(outL)}, expected {math.ceil(span/1.0)})")
    check("split preserves every repeat (counts sum to input)",
          sum(e.raw.get("repeat_count", 0) for e in outL) == 17,
          f"(sum={sum(e.raw.get('repeat_count', 0) for e in outL)})")

    exc2 = ModbusExtractor(ModbusConfig(coalesce_window_s=1.0))
    vch = [fc6_write(exc2, 0.0, reg=0, val=100), fc6_write(exc2, 0.2, reg=0, val=100),
           fc6_write(exc2, 0.4, reg=0, val=200)]
    out2 = list(exc2.coalesce_writes(iter(vch)))
    check("value change breaks the window", [e.value for e in out2] == [100, 200],
          f"(={[e.value for e in out2]})")

    exc3 = ModbusExtractor(ModbusConfig(coalesce_window_s=1.0))
    gap = [fc6_write(exc3, 0.0, reg=0, val=100), fc6_write(exc3, 0.2, reg=0, val=100),
           fc6_write(exc3, 1.5, reg=0, val=100)]
    out3 = list(exc3.coalesce_writes(iter(gap)))
    check("gap > window breaks into two events", len(out3) == 2, f"(n={len(out3)})")

    # --- calibration-derived coalesce window: clamp(half_cycle_s / 20, 0.5, 5.0) ---
    import liscere_observe as obs
    d = obs.derive_coalesce_window_s
    check("derived window: bench half-cycle 30 s -> 1.5 s",
          d(period_samples=300, dt=0.1, measurable=True, default=1.0) == 1.5,
          f"(={d(300, 0.1, True, 1.0)})")
    check("derived window: fast process clamps to floor 0.5 s",
          d(period_samples=50, dt=0.1, measurable=True, default=1.0) == 0.5,
          f"(={d(50, 0.1, True, 1.0)})")
    check("derived window: slow process clamps to ceiling 5.0 s",
          d(period_samples=2000, dt=0.1, measurable=True, default=1.0) == 5.0,
          f"(={d(2000, 0.1, True, 1.0)})")
    check("derived window: not measurable -> config default",
          d(period_samples=0, dt=0.0, measurable=False, default=1.0) == 1.0,
          f"(={d(0, 0.0, False, 1.0)})")

    print()
    if _failures:
        print(f"MODBUS FLOWS TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("MODBUS FLOWS TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
