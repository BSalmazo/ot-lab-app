#!/usr/bin/env python3
"""OPC UA subscription-publish split: one flow per ClientHandle, generic variant reading.

A Siemens HMI reads PLC tags via an OPC UA subscription. Each PublishResponse batches an array of
MonitoredItemNotification -- (ClientHandle, DataValue{Variant}) -- and the dissector emits them as
PARALLEL, positionally-aligned lists under -E occurrence=a (opcua.ClientHandle, opcua.variant.has_value
= the per-item Variant Type byte, and the value in the type's own field). The extractor must:

  - split into ONE flow per handle (opcua:sub:<handle>), not fuse them into one serpentining signal;
  - read the value GENERICALLY by dispatching on the Variant Type -- Float, Double, every Int/UInt
    width, Byte/SByte, Boolean -- never hardcoding Float + Int32;
  - keep both notifications when a handle appears twice (they are two genuine samples, not one twice);
  - REPORT a variant type it cannot read yet (the handle still surfaces), never silently drop it;
  - leave the explicit Read/Write path working.

No domain knowledge: the handle number is the honest key. Deterministic; `python3 tests/test_opcua_subscription.py`.
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "scripts"))

from otlab_core.extractors.opcua import OpcUaExtractor, _FIELDS

_IDX = {name: i for i, name in enumerate(_FIELDS)}
_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


def line(**kw):
    """Build a tshark TSV column list from field-name kwargs (e.g. Float='22.8', ClientHandle='1,7')."""
    row = [""] * len(_FIELDS)
    for field, value in kw.items():
        row[_IDX["opcua." + field] if ("opcua." + field) in _IDX else _IDX[field]] = str(value)
    return row


def pub(**kw):
    base = {"frame.time_epoch": "1.0", "frame.protocols": "eth:ethertype:ip:tcp:opcua",
            "ip.src": "10.0.0.9", "ip.dst": "10.0.0.5", "tcp.srcport": "48000", "tcp.dstport": "4840",
            "transport.type": "MSG", "servicenodeid.numeric": "829"}    # 829 = PublishResponse
    base.update(kw)
    return line(**base)


def flows_by_key(ex, events):
    return {f.key: f for f in ex.group_flows(events)}


def main():
    ex = OpcUaExtractor()

    # === 1) two handles, MIXED variant types -> two flows, typed correctly, NOT one fused ==========
    # handle 1 -> Float 22.8001 ; handle 7 -> Int32 45. Parallel lists: opcua.Float holds only the
    # Float item, opcua.Int32 only the Int32 item; ClientHandle + variant.has_value zip by position.
    e = ex.parse_line(pub(ClientHandle="1,7", **{"variant.has_value": "0x0a,0x06"}, Float="22.8001", Int32="45"))
    items = e.raw["sub_items"]
    check("a PublishResponse is split into per-item (handle, variant, value)",
          [(i["handle"], i["variant_type"], i["value"]) for i in items] == [(1, 0x0a, 22.8001), (7, 0x06, 45)],
          f"(={[(i['handle'], i['variant_type'], i['value']) for i in items]})")
    fl = flows_by_key(ex, [e])
    check("two flows emitted, keyed by ClientHandle (not one fused publish:telemetry)",
          set(fl) == {"opcua:sub:1", "opcua:sub:7"}, f"(={sorted(fl)})")
    check("handle 1 is the Float value, typed Float",
          fl["opcua:sub:1"].samples == [(1.0, 22.8001)] and fl["opcua:sub:1"].datatype == "Float"
          and fl["opcua:sub:1"].datatype_certain)
    check("handle 7 is the Int32 value (an int, not a float), typed Int32",
          fl["opcua:sub:7"].samples == [(1.0, 45.0)] and fl["opcua:sub:7"].datatype == "Int32"
          and fl["opcua:sub:7"].datatype_certain and isinstance(items[1]["value"], int),
          f"(={fl['opcua:sub:7'].samples}, dt={fl['opcua:sub:7'].datatype})")

    # === 2) GENERIC dispatch beyond Float+Int32: Float, UInt16, Double in one publish =============
    # The actual integer variant type on the bench was unconfirmed and must be DISCOVERED per item,
    # not assumed. Prove three different variant types read from their own fields.
    e2 = ex.parse_line(pub(ClientHandle="1,2,3", **{"variant.has_value": "0x0a,0x05,0x0b"},
                           Float="22.80", UInt16="7", Double="3.14159"))
    fl2 = flows_by_key(ex, [e2])
    check("three variant types (Float, UInt16, Double) each read from their OWN field",
          fl2["opcua:sub:1"].samples == [(1.0, 22.80)] and fl2["opcua:sub:2"].samples == [(1.0, 7.0)]
          and fl2["opcua:sub:3"].samples == [(1.0, 3.14159)],
          f"(1={fl2['opcua:sub:1'].samples}, 2={fl2['opcua:sub:2'].samples}, 3={fl2['opcua:sub:3'].samples})")
    check("their declared datatypes are read generically (UInt16 + Double, not coerced to Float/Int32)",
          fl2["opcua:sub:2"].datatype == "UInt16" and fl2["opcua:sub:3"].datatype == "Double")

    # === 2b) the per-type cursor handles INTERLEAVED types (same type non-contiguous) =============
    # handles 1,2,1 with types Float,Int32,Float: item0 -> Float[0], item2 -> Float[1], item1 -> Int32[0].
    e3 = ex.parse_line(pub(ClientHandle="1,2,1", **{"variant.has_value": "0x0a,0x06,0x0a"},
                           Float="10.0,30.0", Int32="20"))
    fl3 = flows_by_key(ex, [e3])
    check("interleaved types zip correctly by a per-type cursor",
          fl3["opcua:sub:1"].samples == [(1.0, 10.0), (1.0, 30.0)] and fl3["opcua:sub:2"].samples == [(1.0, 20.0)],
          f"(1={fl3['opcua:sub:1'].samples}, 2={fl3['opcua:sub:2'].samples})")

    # === 2c) the bench-confirmed handle/type STRUCTURE (values are synthetic) ======================
    # The ClientHandle and variant.has_value arrays below are REAL bench data (a 180s full-cycle
    # capture): ClientHandle = 1,2,6,7,7 ; variant.has_value = 0x0a,0x04,0x04,0x0a,0x0a
    # (Float, Int16, Int16, Float, Float) -- the PLC's Real tags are Float (0x0a), its Int tags are
    # Int16 (0x04), NOT Int32 (a live -e opcua.Int32 was empty). The VALUES here (30.46/30.34/45.66,
    # 128/7) are INVENTED -- the bench message gave arrays only, no values -- chosen to exercise the
    # positional zip and per-item dispatch. This is a unit test on the real wire STRUCTURE.
    eb = ex.parse_line(pub(ClientHandle="1,2,6,7,7", **{"variant.has_value": "0x0a,0x04,0x04,0x0a,0x0a"},
                           Float="30.46,30.34,45.66", Int16="128,7"))   # Floats: items 0,3,4; Int16s: 1,2
    check("bench structure: (handle, variant, value) zip positionally per item",
          [(i["handle"], i["variant_type"], i["value"]) for i in eb.raw["sub_items"]]
          == [(1, 0x0a, 30.46), (2, 0x04, 128), (6, 0x04, 7), (7, 0x0a, 30.34), (7, 0x0a, 45.66)],
          f"(={[(i['handle'], i['variant_type'], i['value']) for i in eb.raw['sub_items']]})")
    flb = flows_by_key(ex, [eb])
    check("bench structure: the Int tags read as Int16 (0x04 -> opcua.Int16), an int not a float",
          flb["opcua:sub:2"].samples == [(1.0, 128.0)] and flb["opcua:sub:2"].datatype == "Int16"
          and flb["opcua:sub:6"].samples == [(1.0, 7.0)] and flb["opcua:sub:6"].datatype == "Int16"
          and isinstance(eb.raw["sub_items"][1]["value"], int))
    check("bench structure: the Real tags read as Float, and handle 7's two Float updates are both kept",
          flb["opcua:sub:1"].datatype == "Float" and flb["opcua:sub:7"].datatype == "Float"
          and [v for _t, v in flb["opcua:sub:7"].samples] == [30.34, 45.66], f"(7={flb['opcua:sub:7'].samples})")

    # === 3) the DUPLICATION: a handle appearing twice = two genuine samples, never one twice ======
    e4 = ex.parse_line(pub(ClientHandle="1,1,7,7", **{"variant.has_value": "0x0a,0x0a,0x0a,0x0a"},
                           Float="22.8001,22.9001,45.6687,45.6587"))
    fl4 = flows_by_key(ex, [e4])
    vals1 = [v for _t, v in fl4["opcua:sub:1"].samples]
    vals7 = [v for _t, v in fl4["opcua:sub:7"].samples]
    check("a handle seen twice keeps BOTH distinct samples in wire order (two real updates)",
          vals1 == [22.8001, 22.9001] and vals7 == [45.6687, 45.6587], f"(1={vals1}, 7={vals7})")
    all_vals = vals1 + vals7
    check("no value is emitted twice (each notification read exactly once)",
          len(all_vals) == len(set(all_vals)) == 4, f"(={all_vals})")

    # === 4) an unhandled variant type is REPORTED (the handle surfaces), not silently dropped =====
    # 0x0e = Guid: no numeric field this extractor reads. The handle must still appear, with its type
    # id shown, so a signal of a type we cannot read yet is never hidden.
    e5 = ex.parse_line(pub(ClientHandle="5", **{"variant.has_value": "0x0e"}))
    check("the unhandled variant id is recorded on the event (reported), not dropped",
          e5.raw["unhandled_variants"] == [0x0e] and e5.raw["sub_items"][0]["unhandled"] is True,
          f"(={e5.raw['unhandled_variants']})")
    fl5 = flows_by_key(ex, [e5])
    check("the unreadable handle still yields its flow, labelled with the unknown variant id",
          "opcua:sub:5" in fl5 and fl5["opcua:sub:5"].datatype == "variant:0x0e"
          and not fl5["opcua:sub:5"].datatype_certain and fl5["opcua:sub:5"].samples == [],
          f"(={fl5.get('opcua:sub:5') and (fl5['opcua:sub:5'].datatype, fl5['opcua:sub:5'].samples)})")

    # an array variant (bit 7 set) is also reported, not misread as a scalar
    e6 = ex.parse_line(pub(ClientHandle="9", **{"variant.has_value": "0x8a"}, Float="1.0,2.0,3.0"))
    check("an ARRAY variant is reported as unhandled, not misaligned into scalar samples",
          e6.raw["sub_items"][0]["unhandled"] is True and flows_by_key(ex, [e6])["opcua:sub:9"].samples == [])

    # === 5) the explicit Read / Write path still works (must not break) ===========================
    rd = ex.parse_line(line(**{"frame.time_epoch": "2.0", "frame.protocols": "eth:ethertype:ip:tcp:opcua",
                               "ip.src": "10.0.0.5", "ip.dst": "10.0.0.9", "tcp.srcport": "4840",
                               "tcp.dstport": "48000", "transport.type": "MSG", "servicenodeid.numeric": "634",
                               "nodeid.numeric": "847832407,0,23", "nodeid.nsindex": "0,4",
                               "Float": "47.5", "variant.has_value": "0x0a"}))
    check("ReadResponse still parses: op, target NodeId, Float value, state sample",
          rd.op == "READ_RESPONSE" and rd.target == "ns=4;i=23" and rd.value == 47.5
          and ex.extract_state_samples(rd) == [47.5], f"(op={rd.op}, target={rd.target}, value={rd.value})")
    wr = ex.parse_line(line(**{"frame.time_epoch": "3.0", "frame.protocols": "eth:ethertype:ip:tcp:opcua",
                               "ip.src": "10.0.0.9", "ip.dst": "10.0.0.5", "tcp.srcport": "48000",
                               "tcp.dstport": "4840", "transport.type": "MSG", "servicenodeid.numeric": "673",
                               "nodeid.numeric": "847832407,0,23", "nodeid.nsindex": "0,4",
                               "Int32": "80", "variant.has_value": "0x06"}))
    check("WriteRequest still parses: op, target, Int32 value, command flow + datatype",
          wr.op == "WRITE_REQUEST" and wr.target == "ns=4;i=23" and wr.value == 80
          and ex.write_datatype(wr) == ("Int32", True)
          and flows_by_key(ex, [wr]).get("opcua:write:ns=4;i=23").role_hint == "command",
          f"(op={wr.op}, target={wr.target}, value={wr.value})")

    # a legacy / no-ClientHandle publish still falls back to the single fused flow (unchanged)
    e7 = ex.parse_line(pub(Float="47.1,47.2", **{"variant.has_value": "0x0a"}))   # no ClientHandle
    fl7 = flows_by_key(ex, [e7])
    check("a publish with no ClientHandle falls back to the single fused publish:telemetry flow",
          set(fl7) == {"opcua:publish:telemetry"} and [v for _t, v in fl7["opcua:publish:telemetry"].samples] == [47.1, 47.2],
          f"(={sorted(fl7)})")

    print()
    if _failures:
        print(f"OPCUA SUBSCRIPTION TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("OPCUA SUBSCRIPTION TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
