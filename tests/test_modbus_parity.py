#!/usr/bin/env python3
"""Modbus wire-dict behaviour test (self-contained; no tshark).

HISTORY: this file used to assert byte-for-byte parity with the retired legacy v2-dev parser
(scripts/tshark_runtime.py on the ``legacy-engine-v1`` tag). That parity was DELIBERATELY RETIRED
on the feature/modbus-extractor branch, because the field-resolution precedence changed to match
what real tshark (4.2.2 and 4.4.15) actually emits on the bench:

  - a READ_RESPONSE has no address in its PDU; the dissector pairs it in as ``modbus.regnum16``,
    while ``modbus.read_reference_num`` is EMPTY on responses. The legacy fixtures read the
    (empty) response reference and so did not reflect real capture output.
  - a write value is ``modbus.regval_uint16`` on 4.4.15, but only ``modbus.data`` (hex bytes) on
    4.2.2, so value resolution now falls back to parsing modbus.data.

The cases below therefore encode the NEW expected output on the REAL 4.4.15 field layout
(regnum16/regval_uint16 populated on responses, read_reference_num empty). The DROP cases
(non-Modbus, short line, missing src_ip, missing func) are unchanged: they still apply.

Run: python3 tests/test_modbus_parity.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from otlab_core.extractors.modbus import ModbusExtractor

# Field order mirrors otlab_core.extractors.modbus._FIELDS (0..17). Build lines by name so the
# column indices are never hand-counted.
_COLS = [
    "ts", "proto", "src", "sport", "dst", "dport", "trans", "unit", "func",
    "ref_write", "read_ref", "word_cnt", "bit_cnt", "regval", "bitval", "exc",
    "regnum", "data",
]
_TS = "1782846744.886"
_PROTO = "eth:ethertype:ip:tcp:mbtcp:modbus"


def L(**kw):
    kw.setdefault("ts", _TS)
    kw.setdefault("proto", _PROTO)
    return "\t".join(str(kw.get(c, "")) for c in _COLS)


def _wire(**over):
    """A wire dict with the fixed context and Modbus defaults, overridden per case."""
    base = {
        "session_id": "S1", "agent_id": "A1", "timestamp": 1782846744.886,
        "src_ip": None, "src_port": None, "dst_ip": None, "dst_port": None,
        "client": None, "server": None, "direction": "request",
        "transaction_id": None, "function_code": None, "unit_id": 1,
        "protocol": "MODBUS/TCP", "type": None, "register": None, "start_addr": None,
        "quantity": None, "value": None, "exception_code": None, "iface": "eth0", "summary": "",
    }
    base.update(over)
    base["start_addr"] = base["register"]   # to_wire_dict mirrors register into start_addr
    return base


HMI, PLC = "192.168.1.9", "192.168.1.12"

CASES = [
    # FC3 read REQUEST: poll HR[2], quantity 1. Start address is read_reference_num.
    {"name": "READ_REQUEST fc3 poll HR2",
     "line": L(src=HMI, sport=50000, dst=PLC, dport=502, trans=10, unit=1, func=3,
               read_ref=2, word_cnt=1),
     "expected": _wire(src_ip=HMI, src_port=50000, dst_ip=PLC, dst_port=502,
                       client=f"{HMI}:50000", server=f"{PLC}:502", direction="request",
                       transaction_id=10, function_code=3, type="READ_REQUEST",
                       register=2, quantity=1,
                       summary=f"FC3 read from {HMI}:50000 to {PLC}:502 | start=2 qty=1")},

    # FC3 read RESPONSE: HR[2]=45. Real 4.4.15 -> regnum16=2, regval_uint16=45, read_ref EMPTY.
    {"name": "READ_RESPONSE fc3 HR2=45",
     "line": L(src=PLC, sport=502, dst=HMI, dport=50000, trans=10, unit=1, func=3,
               regval=45, regnum=2),
     "expected": _wire(src_ip=PLC, src_port=502, dst_ip=HMI, dst_port=50000,
                       client=f"{HMI}:50000", server=f"{PLC}:502", direction="response",
                       transaction_id=10, function_code=3, type="READ_RESPONSE",
                       register=2, value=45,
                       summary=f"FC3 read from {PLC}:502 to {HMI}:50000 | start=2 qty=None")},

    # FC6 write HR[0]=90, value in regval_uint16 (tshark 4.4.15).
    # A write REQUEST's target/register is the flow-key STRING; the summary keeps the int register.
    {"name": "WRITE_REQ fc6 HR0=90 (regval)",
     "line": L(src=HMI, sport=50000, dst=PLC, dport=502, trans=11, unit=1, func=6,
               ref_write=0, regval=90),
     "expected": _wire(src_ip=HMI, src_port=50000, dst_ip=PLC, dst_port=502,
                       client=f"{HMI}:50000", server=f"{PLC}:502", direction="request",
                       transaction_id=11, function_code=6, type="WRITE_REQUEST",
                       register="modbus:hr:0", value=90,
                       summary=f"FC6 write from {HMI}:50000 to {PLC}:502 | register=0 value=90")},

    # FC6 write HR[1]=100, value only as modbus.data hex (tshark 4.2.2 fallback).
    {"name": "WRITE_REQ fc6 HR1=100 (data hex)",
     "line": L(src=HMI, sport=50000, dst=PLC, dport=502, trans=12, unit=1, func=6,
               ref_write=1, data="0064"),
     "expected": _wire(src_ip=HMI, src_port=50000, dst_ip=PLC, dst_port=502,
                       client=f"{HMI}:50000", server=f"{PLC}:502", direction="request",
                       transaction_id=12, function_code=6, type="WRITE_REQUEST",
                       register="modbus:hr:1", value=100,
                       summary=f"FC6 write from {HMI}:50000 to {PLC}:502 | register=1 value=100")},

    # FC5 write single coil, value in bitval (unchanged fallback after regval/data).
    {"name": "WRITE_REQ fc5 coil r2=1",
     "line": L(src=HMI, sport=50000, dst=PLC, dport=502, trans=13, unit=1, func=5,
               ref_write=2, bitval=1),
     "expected": _wire(src_ip=HMI, src_port=50000, dst_ip=PLC, dst_port=502,
                       client=f"{HMI}:50000", server=f"{PLC}:502", direction="request",
                       transaction_id=13, function_code=5, type="WRITE_REQUEST",
                       register="modbus:hr:2", value=1,
                       summary=f"FC5 write from {HMI}:50000 to {PLC}:502 | register=2 value=1")},

    # FC16 write-multiple RESPONSE: register 1, quantity 2.
    {"name": "WRITE_RESP fc16 reg1 qty2",
     "line": L(src=PLC, sport=502, dst=HMI, dport=50000, trans=14, unit=1, func=16,
               ref_write=1, word_cnt=2),
     "expected": _wire(src_ip=PLC, src_port=502, dst_ip=HMI, dst_port=50000,
                       client=f"{HMI}:50000", server=f"{PLC}:502", direction="response",
                       transaction_id=14, function_code=16, type="WRITE_RESPONSE",
                       register=1, quantity=2,
                       summary=f"FC16 write from {PLC}:502 to {HMI}:50000 | register=1 value=None")},

    # Exception 0x83: a real exception PDU carries no register (regnum/ref empty) -> register None.
    {"name": "EXCEPTION 0x83 (no register)",
     "line": L(src=PLC, sport=502, dst=HMI, dport=50000, trans=15, unit=1, func=131, exc=2),
     "expected": _wire(src_ip=PLC, src_port=502, dst_ip=HMI, dst_port=50000,
                       client=f"{HMI}:50000", server=f"{PLC}:502", direction="response",
                       transaction_id=15, function_code=131, type="READ_RESPONSE",
                       register=None, exception_code=2,
                       summary=f"FC131 read from {PLC}:502 to {HMI}:50000 | start=None qty=None")},

    # --- DROP cases (unchanged: these still apply) ---
    {"name": "NON-MODBUS drop",
     "line": L(proto="eth:ethertype:ip:tcp", src=HMI, sport=50000, dst=PLC, dport=443),
     "expected": None},
    {"name": "SHORT LINE drop", "line": "\t".join([_TS, "eth:tcp:modbus", HMI, "50000"]),
     "expected": None},
    {"name": "NO src_ip drop",
     "line": L(src="", sport=50000, dst=PLC, dport=502, trans=1, unit=1, func=3, read_ref=0, word_cnt=4),
     "expected": None},
    {"name": "NO func drop",
     "line": L(src=HMI, sport=50000, dst=PLC, dport=502, trans=1, unit=1, read_ref=0, word_cnt=4),
     "expected": None},
]


def main():
    ex = ModbusExtractor()
    failures = []
    for c in CASES:
        evt = ex.parse_line(c["line"].split("\t"))
        got = evt.to_wire_dict("S1", "A1", "eth0") if evt is not None else None
        ok = (got == c["expected"])
        shape = "None (dropped)" if c["expected"] is None else f'type={c["expected"]["type"]}'
        print(f"  {'OK ' if ok else 'DIFF'}  {c['name']:32s} -> {shape}")
        if not ok:
            failures.append(c["name"])
            print("     expected:", c["expected"])
            print("     got     :", got)
    print()
    print("MODBUS WIRE-DICT:", "ALL OK" if not failures else f"FAIL ({failures})")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
