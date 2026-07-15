#!/usr/bin/env python3
"""Modbus write-coalescing must be scoped per server endpoint.

Reproduces a correctness bug and its fix: coalesce_writes keyed its run on (register, value) alone.
evt.target is the flow key "modbus:hr:<n>", which is silo-relative and carries no endpoint, so two
Modbus servers written with the SAME register and the SAME value inside the coalesce window folded
into one event -- the second was dropped as a repeat, with no error and no log (the same class as the
S7 pduref collision). One extractor sees every Modbus endpoint (its filter is "tcp"), so this is
reachable whenever two servers share a register+value; a second synthetic PLC mirroring the layout
(HR[0], HR[1] both written 100) triggers it exactly.

The test drives two endpoints writing register 0 = value 100 within the window and asserts both
writes survive as separate events with their own evt.server. It FAILS on the pre-fix code (the second
write is folded away) and passes once the coalescing key includes the endpoint. Synthetic tshark TSV
lines match the Modbus field layout in modbus.py; convention follows tests/test_s7_pending.py.

Deterministic; runnable as `python3 tests/test_modbus_coalesce_endpoint.py`.
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from otlab_core.extractors.modbus import ModbusExtractor

# Modbus _FIELDS order (18 columns), as in tests/test_modbus_flows.py.
_COLS = [
    "ts", "proto", "src", "sport", "dst", "dport", "trans", "unit", "func",
    "ref_write", "read_ref", "word_cnt", "bit_cnt", "regval", "bitval", "exc",
    "regnum", "data",
]
_PROTO = "eth:ethertype:ip:tcp:mbtcp:modbus"
HMI = "192.168.1.9"
PLC_A, PLC_B = "192.168.1.12", "192.168.1.13"   # two Modbus servers, identical register layout

_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


def line(ts, **kw):
    kw.setdefault("ts", f"{ts:.3f}")
    kw.setdefault("proto", _PROTO)
    return "\t".join(str(kw.get(c, "")) for c in _COLS).split("\t")


def fc6_write(ex, ts, plc, reg, val):
    """A single-register write (FC6) to `plc`. Server endpoint is the PLC on port 502."""
    return ex.parse_line(line(ts, src=HMI, sport=50000, dst=plc, dport=502,
                              trans=2, unit=1, func=6, ref_write=reg, regval=val))


def main():
    print("MODBUS WRITE-COALESCING IS PER ENDPOINT")

    # Two servers, SAME register (0), SAME value (100), well inside the coalesce window.
    ex = ModbusExtractor()
    window = ex.config.coalesce_window_s
    a = fc6_write(ex, 0.00, PLC_A, reg=0, val=100)
    b = fc6_write(ex, 0.10, PLC_B, reg=0, val=100)   # 0.1s < window -> would fold under the old key
    check("both writes parsed as WRITE_REQUEST", a is not None and b is not None
          and a.op == "WRITE_REQUEST" and b.op == "WRITE_REQUEST")
    check("parsed with distinct server endpoints",
          a.server == "192.168.1.12:502" and b.server == "192.168.1.13:502",
          f"(a={a.server}, b={b.server})")
    check("the two writes are within the coalesce window", (0.10 - 0.00) <= window)

    out = list(ex.coalesce_writes([a, b]))
    check("both writes SURVIVE (not folded across endpoints)", len(out) == 2, f"(got {len(out)})")
    check("each surviving write keeps its own endpoint",
          {e.server for e in out} == {"192.168.1.12:502", "192.168.1.13:502"},
          f"(servers={sorted(e.server for e in out)})")
    check("each is register 0 = 100 (nothing mis-assigned)",
          all(e.target == "modbus:hr:0" and e.value == 100 for e in out))
    check("neither was counted as a repeat of the other",
          all(e.raw.get("repeat_count") == 1 for e in out),
          f"(repeats={[e.raw.get('repeat_count') for e in out]})")

    # Regression guard: genuine wire repetition from ONE endpoint still coalesces.
    ex2 = ModbusExtractor()
    r1 = fc6_write(ex2, 0.00, PLC_A, reg=0, val=100)
    r2 = fc6_write(ex2, 0.10, PLC_A, reg=0, val=100)   # same endpoint, register, value -> one action
    same = list(ex2.coalesce_writes([r1, r2]))
    check("same-endpoint repeat still folds to one event (coalescing intact)",
          len(same) == 1 and same[0].raw.get("repeat_count") == 2,
          f"(len={len(same)}, repeat={same[0].raw.get('repeat_count') if same else None})")

    print()
    if _failures:
        print(f"MODBUS COALESCE ENDPOINT TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("MODBUS COALESCE ENDPOINT TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
