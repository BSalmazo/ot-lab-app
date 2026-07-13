#!/usr/bin/env python3
"""Validation test for Degrau 4 (level 4a): behavioural state-signal discovery.

Reproduces the validated result on representative OPC UA traffic: the classifier identifies the
level (telemetry) flow as STATE, the write flow as COMMAND (role hint), and the read flow as
CONSTANT_METADATA — with exactly ONE flow discovered as the state signal, the telemetry one.

Deterministic (seeded); runnable as `python3 tests/test_discover.py`.
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from otlab_core.contract import NormalizedEvent
from otlab_core.engine.discover import classify_flows
from otlab_core.extractors.opcua import OpcUaExtractor

_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


def build_level(step=0.167, turns=(0, 100, 40, 60, 40), noise=0.01, seed=0):
    """A filling/draining level: piecewise-linear with 3 reversals, range ~100, step ~0.167."""
    rnd = random.Random(seed)
    vals = []
    cur = float(turns[0])
    for nxt in turns[1:]:
        direction = 1 if nxt > cur else -1
        while (direction > 0 and cur < nxt) or (direction < 0 and cur > nxt):
            cur += direction * step
            vals.append(cur + rnd.gauss(0, noise))
        cur = float(nxt)
    return vals


def publish_event(t, samples):
    return NormalizedEvent(
        timestamp=t, protocol="OPCUA/binary", op="PUBLISH_RESPONSE", direction="response",
        target=None, value=(samples[0] if samples else None),
        raw={"float_samples": list(samples), "value_is_float": True, "variant_type": 0x0A},
    )


def write_event(t, target, value):
    return NormalizedEvent(
        timestamp=t, protocol="OPCUA/binary", op="WRITE_REQUEST", direction="request",
        target=target, value=value, raw={"variant_type": 0x0A},
    )


def read_event(t, value):
    return NormalizedEvent(
        timestamp=t, protocol="OPCUA/binary", op="READ_RESPONSE", direction="response",
        target=None, value=value, raw={},
    )


def build_events():
    events = []
    # telemetry: the level, batched 2 Float samples per PublishResponse (as seen live)
    level = build_level()
    t = 0.0
    for i in range(0, len(level), 2):
        events.append(publish_event(t, level[i:i + 2]))
        t += 0.2
    # command: a single operator write to ns=4;i=23 (value 80) -> static, role hint command
    events.append(write_event(t, "ns=4;i=23", 80.0))
    # read: metadata reads returning a constant value
    for _ in range(10):
        t += 0.2
        events.append(read_event(t, 8.0))
    return events


def main():
    ex = OpcUaExtractor()
    flows = ex.group_flows(build_events())
    result = classify_flows(flows)

    print("Flows (features + verdict):")
    by_key = {}
    for v in result.flows:
        by_key[v.key] = v
        f = v.features
        print(f"  {v.verdict:17s} {v.key:24s} role={v.role_hint:9s} "
              f"n={f.n} unique={f.unique_values} range={f.value_range:.2f} "
              f"median_step={f.median_step:.4f} reversals={f.reversals}")
    print(f"\nstate_flow_key = {result.state_flow_key}\n")

    tele = by_key.get("opcua:publish:telemetry")
    write = by_key.get("opcua:write:ns=4;i=23")
    read = by_key.get("opcua:read")

    check("telemetry flow present", tele is not None)
    check("write flow present (ns=4;i=23)", write is not None)
    check("read flow present", read is not None)

    if tele:
        f = tele.features
        check("telemetry verdict == STATE", tele.verdict == "STATE")
        check("telemetry unique_values > 1000", f.unique_values > 1000, f"(={f.unique_values})")
        check("telemetry range ~= 100", 95.0 <= f.value_range <= 105.0, f"(={f.value_range:.2f})")
        check("telemetry median_step ~= 0.167", 0.14 <= f.median_step <= 0.19, f"(={f.median_step:.4f})")
        check("telemetry reversals >= 1 (measured ~3)", f.reversals >= 1, f"(={f.reversals})")
        check("telemetry datatype == Float (certain, from variant 0x0a)",
              tele.datatype == "Float" and tele.datatype_certain is True,
              f"(={tele.datatype}, certain={tele.datatype_certain})")
    if write:
        check("write verdict == COMMAND", write.verdict == "COMMAND")
        check("write is static (1 unique)", write.features.unique_values == 1)
        check("write datatype == Float (certain)", write.datatype == "Float" and write.datatype_certain)
    if read:
        check("read verdict == CONSTANT_METADATA", read.verdict == "CONSTANT_METADATA")
        check("read is static (<=5 unique)", read.features.unique_values <= 5)
        check("read datatype uncertain (no variant on the wire)", read.datatype_certain is False)

    states = result.state_flows()
    check("exactly ONE flow discovered as STATE", len(states) == 1, f"(={[s.key for s in states]})")
    check("the STATE flow is the telemetry (level) flow",
          result.state_flow_key == "opcua:publish:telemetry")

    print()
    if _failures:
        print(f"DISCOVER TEST: FAIL ({len(_failures)} check(s): {_failures})")
        return 1
    print("DISCOVER TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
