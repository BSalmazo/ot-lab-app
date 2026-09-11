#!/usr/bin/env python3
"""Compatibility shim. The observer now lives in liscere/observe.py and is installed as the console
script liscere-observe. Running this file still works, and importing it (import liscere_observe) yields
the real liscere.observe module object, so existing tests and command lines are unchanged."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import liscere.observe as _module  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(_module.main())

sys.modules[__name__] = _module
