#!/usr/bin/env python3
"""Liscere discovery-map TUI.

Reads the observer's JSON-Lines stream on stdin (from `liscere_observe.py --emit-json`) and renders
it live with `rich`. It is a mirror of the engine's discovery stream: it hardcodes NOTHING about the
process, protocol, phases, or flows. It renders whatever events arrive — one protocol or three,
whatever phase name is reported, whatever flows are found. Event types it does not organise are shown
in an "other events" area rather than dropped, so we can see if the engine emits something new.

Pure presentation: no engine imports, no tshark, no assumptions.

Run (on the Pi):
    sudo -E venv/bin/python scripts/liscere_observe.py --iface eth0 --observe 60 --learn 120 \\
        2>/dev/null | venv/bin/python scripts/liscere_ui.py
Or against a recorded stream:
    cat sample.jsonl | python scripts/liscere_ui.py
"""

import json
import sys
from collections import OrderedDict, deque

from rich.console import Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

# Cosmetic-only styling for the known vocabulary. Unknown values fall back to a neutral style,
# so the UI never assumes a fixed set (a new verdict/result/phase just renders plainly).
VERDICT_STYLES = {"STATE": "bold green", "COMMAND": "cyan", "CONSTANT_METADATA": "dim", "AMBIGUOUS": "yellow"}
RESULT_STYLES = {"COHERENT": "green", "INCOHERENT": "bold red", "UNCERTAIN": "yellow"}


def _fmt(x):
    if isinstance(x, float):
        return f"{x:g}"
    return "?" if x is None else str(x)


class DiscoveryModel:
    """Accumulated view state, updated one event at a time. Knows only what the events carry."""

    def __init__(self):
        self.protocols = OrderedDict()   # label -> {"port": ..., "flows": OrderedDict(key -> flow)}  (left map)
        self.current_protocol = None
        # key -> {nature, datatype, datatype_certain, features, value, phase}  (VARIABLES table)
        self.variables = OrderedDict()
        self.verdicts = deque(maxlen=10)
        self.grammar = deque(maxlen=5)
        self.others = deque(maxlen=8)
        self.event_count = 0
        self.ended = False

    # -- event intake -----------------------------------------------------
    def update(self, ev):
        self.event_count += 1
        t = ev.get("type")
        handler = {
            "protocol_seen": self._protocol_seen,
            "flow_found": self._flow_found,
            "variable_found": self._variable_found,
            "variable_value": self._variable_value,
            "state_signal_discovered": self._state_signal,
            "phase": self._phase,
            "verdict": self._verdict,
            "grammar_learned": self._grammar,
        }.get(t)
        if handler:
            handler(ev)
        else:
            self.others.append(ev)   # unknown/unorganised type — shown, never dropped

    def _protocol_seen(self, ev):
        label = str(ev.get("protocol", "?"))
        self.protocols.setdefault(label, {"port": ev.get("port"), "flows": OrderedDict()})
        self.current_protocol = label

    def _current_proto(self):
        if self.current_protocol in self.protocols:
            return self.protocols[self.current_protocol]
        if not self.protocols:                      # a flow before any protocol: be graceful
            self.protocols["(unknown)"] = {"port": None, "flows": OrderedDict()}
            self.current_protocol = "(unknown)"
        return self.protocols[self.current_protocol]

    def _flow_found(self, ev):
        self._current_proto()["flows"].setdefault(
            str(ev.get("key", "?")),
            {"role_hint": ev.get("role_hint"), "verdict": None, "features": None, "state": False},
        )

    def _find_flow(self, key):
        for p in self.protocols.values():
            if key in p["flows"]:
                return p["flows"][key]
        return None

    def _ensure_var(self, key):
        return self.variables.setdefault(key, {
            "nature": None, "datatype": None, "datatype_certain": False,
            "features": None, "value": None, "phase": None,
        })

    def _variable_found(self, ev):
        key = str(ev.get("key"))
        # left map: attach nature + features to the flow node (create if unseen)
        fl = self._find_flow(key)
        if fl is None:
            self._flow_found({"key": key, "role_hint": None})
            fl = self._find_flow(key)
        fl["verdict"] = ev.get("nature")
        fl["features"] = ev.get("features") or {}
        # VARIABLES table row
        v = self._ensure_var(key)
        v["nature"] = ev.get("nature")
        v["datatype"] = ev.get("datatype")
        v["datatype_certain"] = bool(ev.get("datatype_certain"))
        v["features"] = ev.get("features") or {}

    def _variable_value(self, ev):
        # update the value column (create a minimal row if the variable wasn't announced yet)
        self._ensure_var(str(ev.get("key")))["value"] = ev.get("value")

    def _state_signal(self, ev):
        fl = self._find_flow(str(ev.get("key")))
        if fl is not None:
            fl["state"] = True

    def _phase(self, ev):
        key = ev.get("state_key")
        if key is not None:                          # phase belongs to a specific state variable
            self._ensure_var(str(key))["phase"] = ev.get("phase")

    def _verdict(self, ev):
        self.verdicts.append(ev)

    def _grammar(self, ev):
        self.grammar.append(ev)


# -- rendering ------------------------------------------------------------

def build_tree(model):
    tree = Tree(Text("DISCOVERY MAP", style="bold"))
    if not model.protocols:
        tree.add(Text("waiting for protocol…", style="dim"))
    for label, proto in model.protocols.items():
        head = Text(label, style="bold white")
        if proto["port"] is not None:
            head.append(f"  (port {proto['port']})", style="dim")
        pnode = tree.add(head)
        if not proto["flows"]:
            pnode.add(Text("waiting for flows…", style="dim"))
        for key, fl in proto["flows"].items():
            pnode.add(_flow_label(key, fl))
    return tree


def _flow_label(key, fl):
    t = Text()
    if fl["state"]:
        t.append("★ ", style="bold yellow")
    t.append(key)
    if fl["verdict"]:
        t.append("  → ")
        t.append(fl["verdict"], style=VERDICT_STYLES.get(fl["verdict"], "white"))
        f = fl["features"] or {}
        if f:
            t.append(
                f"   uniq={_fmt(f.get('unique_values'))} range={_fmt(f.get('value_range'))}"
                f" step={_fmt(f.get('median_step'))} rev={_fmt(f.get('reversals'))}",
                style="dim",
            )
    else:
        t.append(f"   [hint: {_fmt(fl['role_hint'])}]", style="dim")
    if fl["state"]:
        t.append("   state signal", style="bold yellow")
    return t


# Glyph per nature (cosmetic only). Unknown nature -> "?".
NATURE_SYMBOL = {"STATE": "●", "COMMAND": "○", "CONSTANT_METADATA": "·", "AMBIGUOUS": "?"}


def build_variables(model):
    # One row per discovered variable. Everything is driven by events: variable_found builds rows,
    # variable_value updates the value, phase updates the phase for the matching state variable.
    tbl = Table(expand=True, show_edge=False, header_style="bold")
    tbl.add_column("", width=1)                    # symbol
    tbl.add_column("key", overflow="fold")
    tbl.add_column("nature")
    tbl.add_column("datatype")
    tbl.add_column("value", justify="right")
    tbl.add_column("phase")
    if not model.variables:
        tbl.add_row("", Text("discovering…", style="dim"), "", "", "", "")
    for key, v in model.variables.items():
        nature = v["nature"]
        style = VERDICT_STYLES.get(nature, "white")
        sym = Text(NATURE_SYMBOL.get(nature, "?"), style=style)
        dtype = v["datatype"] if (v["datatype_certain"] and v["datatype"]) else "?"
        if nature == "CONSTANT_METADATA":
            value = "—"
        else:
            value = _fmt(v["value"]) if v["value"] is not None else "—"
        phase = _fmt(v["phase"]) if (nature == "STATE" and v["phase"]) else "—"
        tbl.add_row(sym, key, Text(_fmt(nature), style=style), dtype, value, phase)
    return Panel(tbl, title="VARIABLES", border_style="magenta")


def build_events(model):
    # Newest-first feed (most recent verdict on top). The 'rule' field still arrives in the event
    # data (kept in model.verdicts); it is simply not shown here — the observer's emission is unchanged.
    tbl = Table(expand=True, show_edge=False, header_style="bold")
    tbl.add_column("target", overflow="fold")
    tbl.add_column("phase")
    tbl.add_column("result")
    for v in reversed(model.verdicts):
        tbl.add_row(
            _fmt(v.get("target")), _fmt(v.get("phase")),
            Text(_fmt(v.get("result")), style=RESULT_STYLES.get(v.get("result"), "white")),
        )
    if not model.verdicts:
        tbl.add_row(Text("no events yet", style="dim"), "", "")
    return Panel(tbl, title="Events", border_style="cyan")


def build_grammar(model):
    if not model.grammar:
        return None
    t = Text()
    for g in model.grammar:
        t.append(f"learned {_fmt(g.get('target'))} → {_fmt(g.get('phase'))}\n", style="green")
    return Panel(t, title="grammar (learn)", border_style="green")


def build_others(model):
    if not model.others:
        return None
    t = Text()
    for o in model.others:
        rest = {k: val for k, val in o.items() if k != "type"}
        t.append(f"{o.get('type', '?')}: {json.dumps(rest)[:70]}\n", style="yellow")
    return Panel(t, title="other events (unorganised)", border_style="yellow")


def render(model):
    layout = Layout()
    status = "ENDED" if model.ended else "LIVE"
    live_parts = [build_variables(model), build_events(model)]
    for extra in (build_grammar(model), build_others(model)):
        if extra is not None:
            live_parts.append(extra)

    # VARIABLES (right) is now the primary panel, so give it the wider share.
    layout.split_row(Layout(name="map", ratio=2), Layout(name="live", ratio=3))
    layout["map"].update(Panel(build_tree(model), border_style="cyan"))
    layout["live"].update(
        Panel(Group(*live_parts), title=f"{status}  ·  {model.event_count} events",
              border_style=("green" if not model.ended else "dim"))
    )
    return layout


def main():
    model = DiscoveryModel()
    with Live(render(model), refresh_per_second=8, screen=False) as live:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if not isinstance(ev, dict) or "type" not in ev:
                    raise ValueError("not an event object")
            except Exception:
                model.others.append({"type": "<malformed line>", "raw": line[:70]})
                live.update(render(model))
                continue
            model.update(ev)
            live.update(render(model))
        model.ended = True
        live.update(render(model))   # freeze the final frame on stream end
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
