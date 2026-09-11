#!/usr/bin/env python3
"""UI render-loop: fixed-cadence redraw decoupled from event arrival, with a persisted final frame.

The terminal UI (scripts/liscere_ui.py) flickered because it redrew inline (rich Live screen=False),
so the panel borders and rule lines tore, worse during event bursts. The fix draws to the alternate
screen (screen=True, atomic frames) and redraws on a fixed 6 fps timer (auto_refresh=False), driven
single-threaded from the read loop, so a burst of events only mutates the in-memory model and the
screen coalesces to the latest state.

This test drives main() with fake Live/Console (no real terminal) over a burst of ~1500 telemetry
events that follow an INCOHERENT verdict, and asserts: Live is configured screen=True /
auto_refresh=False; the burst produced only a handful of redraws (coalesced, not one per event); the
final frame is reprinted to the normal screen AFTER Live exits (so stopping the capture does not wipe
it); and the verdict survives to that final frame (the verdicts deque is separate from telemetry, so
a burst never evicts it). Deterministic; `python3 tests/test_ui_render_loop.py`.
"""
import io
import json
import os
import sys
import threading

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "scripts"))

import liscere_ui as ui
from rich.console import Console as RealConsole

_failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    if not cond:
        _failures.append(name)


class FakeLive:
    instances = []

    def __init__(self, renderable, refresh_per_second=None, screen=None, auto_refresh=None):
        self.screen = screen
        self.auto_refresh = auto_refresh
        self.refresh_per_second = refresh_per_second
        self.updates = 0
        FakeLive.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, renderable, refresh=False):
        self.updates += 1     # one redraw


class FakeConsole:
    """Renders whatever it is asked to print to text, so the final frame can be inspected."""
    prints = []

    def print(self, renderable):
        buf = io.StringIO()
        RealConsole(file=buf, width=180, height=44).print(renderable)
        FakeConsole.prints.append(buf.getvalue())


def build_burst():
    """A stage + silo, an INCOHERENT verdict, then a large telemetry burst AFTER the verdict."""
    ep = "10.0.0.5:4840"
    lines = [
        {"type": "stage", "stage": "evaluate"},
        {"type": "silo", "endpoint": ep, "evaluable": True, "protocol": "OPCUA/binary", "peer": "10.0.0.9"},
        {"type": "variable_found", "silo": ep, "key": "opcua:sub:1", "nature": "STATE",
         "datatype": "Float", "datatype_certain": True},
        {"type": "verdict", "silo": ep, "target": "ns=4;i=23", "phase": "STABLE", "result": "INCOHERENT"},
    ]
    # 1500 telemetry updates AFTER the verdict: a burst that must not evict the verdict nor cause a
    # burst of redraws. Values wander so each is a genuine change.
    for i in range(1500):
        lines.append({"type": "variable_value", "silo": ep, "key": "opcua:sub:1", "value": 50.0 + (i % 7)})
    return len(lines), ("\n".join(json.dumps(x) for x in lines) + "\n").encode()


def run_main_over_burst():
    n_events, data = build_burst()

    FakeLive.instances = []
    FakeConsole.prints = []
    render_calls = [0]
    orig_render = ui.render

    def counting_render(model):
        render_calls[0] += 1
        return orig_render(model)     # exercise the real render path (builds the real Layout)

    r_fd, w_fd = os.pipe()

    def feeder():
        try:
            os.write(w_fd, data)      # may block if the burst exceeds the pipe buffer; main drains it
        finally:
            os.close(w_fd)            # EOF -> main returns

    old_stdin = sys.stdin
    saved = (ui.Live, ui.Console, ui.render)
    ui.Live, ui.Console, ui.render = FakeLive, FakeConsole, counting_render
    sys.stdin = os.fdopen(r_fd, "rb", buffering=0)
    t = threading.Thread(target=feeder)
    t.start()
    try:
        rc = ui.main([])
    finally:
        t.join()
        try:
            sys.stdin.close()
        except Exception:
            pass
        sys.stdin = old_stdin
        ui.Live, ui.Console, ui.render = saved
    return rc, n_events, render_calls[0]


def main():
    rc, n_events, render_calls = run_main_over_burst()
    live = FakeLive.instances[0] if FakeLive.instances else None
    redraws = live.updates if live else -1

    print(f"[run] events fed={n_events}  redraws(live.update)={redraws}  render() calls={render_calls}")

    check("main() exits cleanly (rc == 0)", rc == 0, f"(rc={rc})")
    check("Live uses the alternate screen (screen=True), no inline tearing",
          live is not None and live.screen is True, f"(screen={getattr(live, 'screen', None)})")
    check("Live auto_refresh is OFF (single-threaded, timer-driven redraws only)",
          live is not None and live.auto_refresh is False, f"(auto_refresh={getattr(live, 'auto_refresh', None)})")
    check("Live refresh_per_second is the fixed 6 fps cadence",
          live is not None and live.refresh_per_second == 6, f"(={getattr(live, 'refresh_per_second', None)})")

    # coalescing: a ~1500-event burst must produce only a handful of redraws, not one per event.
    check("a burst coalesces: redraws are bounded by the timer, not the event count",
          0 < redraws < 40 and redraws < n_events // 20,
          f"(redraws={redraws} vs events={n_events})")

    # final-frame persistence: the frame is reprinted ONCE to the normal screen after Live exits.
    check("the final frame is reprinted to the normal screen after Live closes (persists on exit)",
          len(FakeConsole.prints) == 1, f"(prints={len(FakeConsole.prints)})")

    final = FakeConsole.prints[0] if FakeConsole.prints else ""
    check("the run's end state (ENDED) is on the persisted final frame",
          "ENDED" in final, "(status label)")
    check("the INCOHERENT verdict survives the burst and is on the persisted final frame",
          "INCOHERENT" in final, "(verdict retained, not lost to coalescing)")

    print()
    if _failures:
        print(f"UI RENDER LOOP TEST: FAIL ({len(_failures)}: {_failures})")
        return 1
    print("UI RENDER LOOP TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
