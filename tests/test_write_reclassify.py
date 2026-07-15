#!/usr/bin/env python3
"""A write reclassifies a key discovery mislabelled as a non-command nature.

The bug: a read-only register the HMI only DISPLAYS is classified CONSTANT_METADATA during observe
(role=telemetry, unique=1, range=0 -- correct given the evidence). When a WRITE_REQUEST to it later
arrives, that is protocol PROOF it is a command, but the Emitter's announce-once suppression kept the
METADATA label forever: the map showed it as METADATA and omitted it from VARIABLES while the grammar
judged it correctly. A write must outrank a behavioural classification inferred from a window.

This exercises the Emitter's announce/reclassify seam directly (where the suppression lived): a key
announced as CONSTANT_METADATA, then written, is re-announced ONCE as a late COMMAND with the write's
datatype; subsequent writes do not re-announce; and the discovered STATE signal is NEVER reclassified
by a write.

Deterministic; runnable as `python3 tests/test_write_reclassify.py`.
"""
import io
import json
import os
import sys
from types import SimpleNamespace

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "scripts"))

import liscere_observe as obs

_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


def verdict(key, nature, datatype="uint16", certain=False):
    """A minimal FlowVerdict stand-in: only the fields Emitter.variable_found reads."""
    feats = SimpleNamespace(unique_values=1, value_range=0.0, median_step=0.0, reversals=0)
    return SimpleNamespace(key=key, verdict=nature, datatype=datatype, datatype_certain=certain, features=feats)


def emitted(buf):
    return [json.loads(l) for l in buf.getvalue().splitlines()]


def main():
    print("WRITE RECLASSIFIES A NON-COMMAND KEY")

    # 1) METADATA discovered, then written -> re-announced once as a late COMMAND with the datatype.
    buf = io.StringIO()
    em = obs.Emitter(enabled=True, out=buf)
    em.variable_found(verdict("modbus:hr:0", "CONSTANT_METADATA"))   # observe: read-only display
    em.command_found("modbus:hr:0", "uint16", True)                  # a write arrives (learn/evaluate)
    em.command_found("modbus:hr:0", "uint16", True)                  # a repeat write
    vf = [e for e in emitted(buf) if e["type"] == "variable_found" and e["key"] == "modbus:hr:0"]
    check("announced CONSTANT_METADATA, then reclassified to COMMAND",
          [e["nature"] for e in vf] == ["CONSTANT_METADATA", "COMMAND"],
          f"(natures={[e['nature'] for e in vf]})")
    check("reclassification is a late command with the write's datatype",
          len(vf) == 2 and vf[1].get("late") is True
          and vf[1]["datatype"] == "uint16" and vf[1]["datatype_certain"] is True)
    check("subsequent write does NOT re-announce", len(vf) == 2)

    # 2) The STATE signal is never reclassified by a write (guard).
    buf2 = io.StringIO()
    em2 = obs.Emitter(enabled=True, out=buf2)
    em2.variable_found(verdict("modbus:hr:5", "STATE"))
    em2.state_signal_discovered("modbus:hr:5")
    em2.command_found("modbus:hr:5", "uint16", True)                 # a write to the state variable
    late_state = [e for e in emitted(buf2) if e["type"] == "variable_found" and e.get("late")]
    check("a write to the STATE signal does NOT reclassify it", late_state == [],
          f"(late={late_state})")

    # 3) A key already surfaced as COMMAND is not re-announced (repeat suppression unchanged); a
    #    brand-new written key is still announced once (the original late-command behaviour).
    buf3 = io.StringIO()
    em3 = obs.Emitter(enabled=True, out=buf3)
    em3.variable_found(verdict("modbus:hr:2", "COMMAND"))            # discovered as a command
    em3.command_found("modbus:hr:2", "uint16", True)                # write -> suppressed (already COMMAND)
    em3.command_found("modbus:hr:9", None, False)                   # never seen -> announced once
    em3.command_found("modbus:hr:9", None, False)                   # repeat -> suppressed
    vf3 = [e for e in emitted(buf3) if e["type"] == "variable_found"]
    check("a discovered COMMAND is not re-announced by a write",
          len([e for e in vf3 if e["key"] == "modbus:hr:2" and e.get("late")]) == 0)
    check("a brand-new written key is announced once as a late COMMAND",
          [e["nature"] for e in vf3 if e["key"] == "modbus:hr:9"] == ["COMMAND"])

    # 4) Reclassification is per-silo (tag-scoped): a write to silo A's key does not touch silo B's.
    buf4 = io.StringIO()
    base = obs.Emitter(enabled=True, out=buf4)
    a, b = base.bind("10.0.0.1:502"), base.bind("10.0.0.2:502")
    a.variable_found(verdict("modbus:hr:0", "CONSTANT_METADATA"))
    b.variable_found(verdict("modbus:hr:0", "CONSTANT_METADATA"))
    a.command_found("modbus:hr:0", "uint16", True)                  # write on silo A only
    per_silo = [(e.get("silo"), e["nature"]) for e in emitted(buf4)
                if e["type"] == "variable_found" and e["key"] == "modbus:hr:0"]
    check("reclassification is scoped to the written silo (A reclassified, B still METADATA)",
          per_silo == [("10.0.0.1:502", "CONSTANT_METADATA"), ("10.0.0.2:502", "CONSTANT_METADATA"),
                       ("10.0.0.1:502", "COMMAND")],
          f"(={per_silo})")

    print()
    if _failures:
        print(f"WRITE RECLASSIFY TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("WRITE RECLASSIFY TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
