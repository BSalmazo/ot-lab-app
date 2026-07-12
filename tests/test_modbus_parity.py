#!/usr/bin/env python3
"""Modbus wire-parity test (self-contained; no dependency on the removed legacy engine).

The expected wire dicts below were captured from the GENUINE legacy v2-dev parser
(scripts/tshark_runtime.py, preserved on the `legacy-engine-v1` tag and the v2-dev branch)
before the legacy declarative engine was retired. This test asserts that otlab_core's
ModbusExtractor -> NormalizedEvent.to_wire_dict() reproduces them byte-for-byte, so the Modbus
passive path stays identical to the historical baseline. Vendoring the fixture keeps the test
self-contained: it needs neither the removed code nor any git fetch in CI.

Run: python3 tests/test_modbus_parity.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from otlab_core.extractors.modbus import ModbusExtractor

# Captured from v2-dev:scripts/tshark_runtime.py (legacy-engine-v1). Do not hand-edit.
CASES = [{'name': 'READ_REQUEST fc3',
  'line': '1782846744.886\teth:ethertype:ip:tcp:mbtcp:modbus\t192.168.1.9\t50000\t192.168.1.12\t502\t10\t1\t3\t\t0\t'
          '16\t\t\t\t',
  'expected': {'session_id': 'S1',
               'agent_id': 'A1',
               'timestamp': 1782846744.886,
               'src_ip': '192.168.1.9',
               'src_port': 50000,
               'dst_ip': '192.168.1.12',
               'dst_port': 502,
               'client': '192.168.1.9:50000',
               'server': '192.168.1.12:502',
               'direction': 'request',
               'transaction_id': 10,
               'function_code': 3,
               'unit_id': 1,
               'protocol': 'MODBUS/TCP',
               'type': 'READ_REQUEST',
               'register': 0,
               'start_addr': 0,
               'quantity': 16,
               'value': None,
               'exception_code': None,
               'iface': 'eth0',
               'summary': 'FC3 read from 192.168.1.9:50000 to 192.168.1.12:502 | start=0 qty=16'}},
 {'name': 'READ_RESPONSE fc3',
  'line': '1782846744.886\teth:ethertype:ip:tcp:mbtcp:modbus\t192.168.1.12\t502\t192.168.1.9\t50000\t10\t1\t3\t\t'
          '0\t\t\t8,0,0,0,0,12\t\t',
  'expected': {'session_id': 'S1',
               'agent_id': 'A1',
               'timestamp': 1782846744.886,
               'src_ip': '192.168.1.12',
               'src_port': 502,
               'dst_ip': '192.168.1.9',
               'dst_port': 50000,
               'client': '192.168.1.9:50000',
               'server': '192.168.1.12:502',
               'direction': 'response',
               'transaction_id': 10,
               'function_code': 3,
               'unit_id': 1,
               'protocol': 'MODBUS/TCP',
               'type': 'READ_RESPONSE',
               'register': 0,
               'start_addr': 0,
               'quantity': None,
               'value': 8,
               'exception_code': None,
               'iface': 'eth0',
               'summary': 'FC3 read from 192.168.1.12:502 to 192.168.1.9:50000 | start=0 qty=None'}},
 {'name': 'WRITE_REQ fc6 reg',
  'line': '1782846744.886\teth:ethertype:ip:tcp:mbtcp:modbus\t192.168.1.9\t50000\t192.168.1.12\t502\t11\t1\t6\t'
          '0\t\t\t\t90\t\t',
  'expected': {'session_id': 'S1',
               'agent_id': 'A1',
               'timestamp': 1782846744.886,
               'src_ip': '192.168.1.9',
               'src_port': 50000,
               'dst_ip': '192.168.1.12',
               'dst_port': 502,
               'client': '192.168.1.9:50000',
               'server': '192.168.1.12:502',
               'direction': 'request',
               'transaction_id': 11,
               'function_code': 6,
               'unit_id': 1,
               'protocol': 'MODBUS/TCP',
               'type': 'WRITE_REQUEST',
               'register': 0,
               'start_addr': 0,
               'quantity': None,
               'value': 90,
               'exception_code': None,
               'iface': 'eth0',
               'summary': 'FC6 write from 192.168.1.9:50000 to 192.168.1.12:502 | register=0 value=90'}},
 {'name': 'WRITE_REQ fc5 coil',
  'line': '1782846744.886\teth:ethertype:ip:tcp:mbtcp:modbus\t192.168.1.9\t50000\t192.168.1.12\t502\t12\t1\t5\t'
          '2\t\t\t\t\t1\t',
  'expected': {'session_id': 'S1',
               'agent_id': 'A1',
               'timestamp': 1782846744.886,
               'src_ip': '192.168.1.9',
               'src_port': 50000,
               'dst_ip': '192.168.1.12',
               'dst_port': 502,
               'client': '192.168.1.9:50000',
               'server': '192.168.1.12:502',
               'direction': 'request',
               'transaction_id': 12,
               'function_code': 5,
               'unit_id': 1,
               'protocol': 'MODBUS/TCP',
               'type': 'WRITE_REQUEST',
               'register': 2,
               'start_addr': 2,
               'quantity': None,
               'value': 1,
               'exception_code': None,
               'iface': 'eth0',
               'summary': 'FC5 write from 192.168.1.9:50000 to 192.168.1.12:502 | register=2 value=1'}},
 {'name': 'WRITE_RESP fc16',
  'line': '1782846744.886\teth:ethertype:ip:tcp:mbtcp:modbus\t192.168.1.12\t502\t192.168.1.9\t50000\t13\t1\t16\t1\t\t'
          '2\t\t\t\t',
  'expected': {'session_id': 'S1',
               'agent_id': 'A1',
               'timestamp': 1782846744.886,
               'src_ip': '192.168.1.12',
               'src_port': 502,
               'dst_ip': '192.168.1.9',
               'dst_port': 50000,
               'client': '192.168.1.9:50000',
               'server': '192.168.1.12:502',
               'direction': 'response',
               'transaction_id': 13,
               'function_code': 16,
               'unit_id': 1,
               'protocol': 'MODBUS/TCP',
               'type': 'WRITE_RESPONSE',
               'register': 1,
               'start_addr': 1,
               'quantity': 2,
               'value': None,
               'exception_code': None,
               'iface': 'eth0',
               'summary': 'FC16 write from 192.168.1.12:502 to 192.168.1.9:50000 | register=1 value=None'}},
 {'name': 'EXCEPTION 0x83',
  'line': '1782846744.886\teth:ethertype:ip:tcp:mbtcp:modbus\t192.168.1.12\t502\t192.168.1.9\t50000\t14\t1\t131\t\t'
          '0\t\t\t\t\t2',
  'expected': {'session_id': 'S1',
               'agent_id': 'A1',
               'timestamp': 1782846744.886,
               'src_ip': '192.168.1.12',
               'src_port': 502,
               'dst_ip': '192.168.1.9',
               'dst_port': 50000,
               'client': '192.168.1.9:50000',
               'server': '192.168.1.12:502',
               'direction': 'response',
               'transaction_id': 14,
               'function_code': 131,
               'unit_id': 1,
               'protocol': 'MODBUS/TCP',
               'type': 'READ_RESPONSE',
               'register': 0,
               'start_addr': 0,
               'quantity': None,
               'value': None,
               'exception_code': 2,
               'iface': 'eth0',
               'summary': 'FC131 read from 192.168.1.12:502 to 192.168.1.9:50000 | start=0 qty=None'}},
 {'name': 'NON-MODBUS drop',
  'line': '1782846744.886\teth:ethertype:ip:tcp\t192.168.1.9\t50000\t192.168.1.12\t443\t\t\t\t\t\t\t\t\t\t',
  'expected': None},
 {'name': 'SHORT LINE drop', 'line': '1782846744.886\teth:tcp:modbus\t192.168.1.9\t50000', 'expected': None},
 {'name': 'NO src_ip drop',
  'line': '1782846744.886\teth:ethertype:ip:tcp:mbtcp:modbus\t\t50000\t192.168.1.12\t502\t1\t1\t3\t\t0\t4\t\t\t\t',
  'expected': None},
 {'name': 'NO func drop',
  'line': '1782846744.886\teth:ethertype:ip:tcp:mbtcp:modbus\t192.168.1.9\t50000\t192.168.1.12\t502\t1\t1\t\t\t0\t'
          '4\t\t\t\t',
  'expected': None}]


def main():
    ex = ModbusExtractor()
    failures = []
    for c in CASES:
        evt = ex.parse_line(c["line"].split("\t"))
        got = evt.to_wire_dict("S1", "A1", "eth0") if evt is not None else None
        ok = (got == c["expected"])
        shape = "None (dropped)" if c["expected"] is None else f'type={c["expected"]["type"]}'
        print(f"  {'OK ' if ok else 'DIFF'}  {c['name']:22s} -> {shape}")
        if not ok:
            failures.append(c["name"])
            print("     expected:", c["expected"])
            print("     got     :", got)
    print()
    print("MODBUS PARITY:", "ALL IDENTICAL" if not failures else f"FAIL ({failures})")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
