#!/usr/bin/env python3
"""Compatibility shim. The probe now lives in liscere/probe.py and is installed as the console
script liscere-probe. Running this file still works, and importing it (import liscere_probe) yields
the real liscere.probe module object, so existing tests and command lines are unchanged."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import liscere.probe as _module  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(_module.main())

sys.modules[__name__] = _module
