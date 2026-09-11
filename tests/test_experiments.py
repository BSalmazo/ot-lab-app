#!/usr/bin/env python3
"""Experiments convention: every experiments/EXP-*/metrics.json names a run id and a capture.

Every number published from this repository must trace to a run id and a capture hash. This check
enforces the minimum: each experiment folder that carries a metrics.json has run_id and either a
non-empty captures list or an explicit "captures": [] with a README that says why.
`python3 tests/test_experiments.py`.
"""
import glob
import json
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


def main():
    folders = sorted(glob.glob(os.path.join(_REPO, "experiments", "EXP-*")))
    print(f"[experiments] {len(folders)} experiment folder(s)")
    for folder in folders:
        name = os.path.basename(folder)
        check(f"{name}: has a README.md", os.path.isfile(os.path.join(folder, "README.md")))
        for mpath in glob.glob(os.path.join(folder, "**", "metrics.json"), recursive=True):
            with open(mpath) as f:
                rep = json.load(f)
            check(f"{name}: {os.path.relpath(mpath, folder)} names a run id", bool(rep.get("run_id")))
            check(f"{name}: {os.path.relpath(mpath, folder)} names a capture (or an explicit empty list)",
                  isinstance(rep.get("captures"), list))
    manifest = os.path.join(_REPO, "experiments", "captures", "MANIFEST.json")
    with open(manifest) as f:
        m = json.load(f)
    check("captures/MANIFEST.json is well formed", m.get("schema") == 1 and isinstance(m.get("captures"), list))
    print()
    if _failures:
        print(f"EXPERIMENTS TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("EXPERIMENTS TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
