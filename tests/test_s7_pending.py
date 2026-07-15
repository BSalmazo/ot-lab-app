#!/usr/bin/env python3
"""S7 pending-map conversation-scoping test.

Reproduces a correctness bug and its fix: the read Job -> Ack_Data pending map must be keyed by
(TCP conversation, pduref), not pduref alone. pduref is a 16-bit correlator scoped to ONE TCP
connection, so two conversations legitimately reuse the same value. Under a pduref-only key, the
second conversation's Read Job overwrites the first's remembered items and the first Ack_Data pops
the WRONG conversation's items -- emitting a correct-looking value against the wrong variable, while
the second Ack_Data finds an empty slot and is dropped.

The test drives two conversations (a client to two different PLCs) that share pduref=1 with DIFFERENT
request items, interleaved Job/Job/Ack/Ack, and asserts each Ack_Data pairs against its OWN
conversation's item. It FAILS on the pduref-only code and passes once the key includes the
conversation. Synthetic tshark TSV lines match the real 4.4.15 S7 field layout in s7.py's _FIELDS.

Deterministic; runnable as `python3 tests/test_s7_pending.py`.
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from otlab_core.extractors.s7 import S7CommExtractor

# Column names in _FIELDS order (21 columns, indices 0-20).
_COLS = [
    "ts", "proto", "src", "sport", "dst", "dport", "rosctr", "pduref", "func",
    "itemcount", "area", "db", "addr_byte", "addr_bit", "transp_size", "item_length",
    "returncode", "data_transportsize", "data_length", "resp_data", "data_data",
]
_PROTO = "eth:ethertype:ip:tcp:tpkt:cotp:s7comm"

ROSCTR_JOB, ROSCTR_ACK_DATA = 1, 3
FUNC_READ = 0x04
AREA_DB = 0x84
WL_INT = 0x05          # request-side WordLen -> signed 16-bit INT

CLIENT = "192.168.1.9"
PLC_A, PLC_B = "192.168.1.12", "192.168.1.13"   # one capture spanning two PLCs

_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


def line(ts, **kw):
    kw.setdefault("ts", f"{ts:.3f}")
    kw.setdefault("proto", _PROTO)
    return "\t".join(str(kw.get(c, "")) for c in _COLS).split("\t")


def read_job(ex, ts, client_port, plc, db, byte):
    """Client -> PLC Read Job for one DB.INT item under pduref=1. Returns None (no event yet)."""
    return ex.parse_line(line(ts, src=CLIENT, sport=client_port, dst=plc, dport=102,
                              rosctr=ROSCTR_JOB, pduref=1, func=FUNC_READ, itemcount=1,
                              area=AREA_DB, db=db, addr_byte=byte, transp_size=WL_INT))


def read_ack(ex, ts, client_port, plc, value_hex):
    """PLC -> client Ack_Data (opposite direction) carrying one success value under pduref=1."""
    return ex.parse_line(line(ts, src=plc, sport=102, dst=CLIENT, dport=client_port,
                              rosctr=ROSCTR_ACK_DATA, pduref=1, func=FUNC_READ, itemcount=1,
                              returncode=0xFF, resp_data=value_hex))


def only_item(evt):
    """(flow_key, value) of an event's single response item, or (None, None)."""
    items = evt.raw.get("items") if evt is not None else None
    if not items:
        return (None, None)
    key, value, *_rest = items[0]
    return (key, value)


def main():
    print("S7 PENDING-MAP CONVERSATION SCOPING")
    ex = S7CommExtractor()

    # Two conversations reuse pduref=1 with DIFFERENT items, interleaved Job/Job/Ack/Ack.
    #   Conv A: client:50001 <-> PLC_A, item s7:db1:0,  value bytes 00:64 -> INT 100
    #   Conv B: client:50002 <-> PLC_B, item s7:db2:10, value bytes 00:c8 -> INT 200
    read_job(ex, 1.0, 50001, PLC_A, db=1, byte=0)     # A remembers under (convA, 1)
    read_job(ex, 1.1, 50002, PLC_B, db=2, byte=10)    # B remembers under (convB, 1); must NOT evict A
    evt_a = read_ack(ex, 1.2, 50001, PLC_A, "00:64")  # must pop A's items, not B's
    evt_b = read_ack(ex, 1.3, 50002, PLC_B, "00:c8")  # must still find B's items

    key_a, val_a = only_item(evt_a)
    key_b, val_b = only_item(evt_b)

    # Conv A's Ack_Data must pair against A's own item (s7:db1:0 = 100), not B's (s7:db2:10).
    check("conv A Ack_Data emitted", evt_a is not None)
    check("conv A pairs its OWN item s7:db1:0", key_a == "s7:db1:0", f"(got {key_a})")
    check("conv A value is its own (INT 100)", val_a == 100, f"(got {val_a})")

    # Conv B's Ack_Data must still be paired (not dropped by a stolen/overwritten slot).
    check("conv B Ack_Data emitted (not dropped)", evt_b is not None)
    check("conv B pairs its OWN item s7:db2:10", key_b == "s7:db2:10", f"(got {key_b})")
    check("conv B value is its own (INT 200)", val_b == 200, f"(got {val_b})")

    # Regression guard: a single-conversation Job/Ack still pairs exactly as before.
    ex2 = S7CommExtractor()
    read_job(ex2, 2.0, 51000, PLC_A, db=5, byte=4)
    evt_c = read_ack(ex2, 2.1, 51000, PLC_A, "01:2c")   # INT 300
    key_c, val_c = only_item(evt_c)
    check("single conversation still pairs (s7:db5:4 = 300)",
          key_c == "s7:db5:4" and val_c == 300, f"(got {key_c}={val_c})")

    print()
    if _failures:
        print(f"S7 PENDING TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("S7 PENDING TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
