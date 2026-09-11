"""Run every script-style test file under tests/ as a subprocess and require exit code 0.

The existing tests are standalone scripts that print PASS/FAIL per check and exit non-zero on any
failure. This wrapper lets `pytest` run them all without rewriting them, and shows each script's
output on failure. The scripts remain runnable by hand: `python tests/test_x.py`.
"""

import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = sorted(p for p in HERE.glob("test_*.py") if p.name != "test_scripts.py")


@pytest.mark.parametrize("script", SCRIPTS, ids=[p.name for p in SCRIPTS])
def test_script_passes(script):
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=300)
    tail = (result.stdout[-4000:] + result.stderr[-4000:]) if result.returncode else ""
    assert result.returncode == 0, f"{script.name} exited {result.returncode}\n{tail}"
