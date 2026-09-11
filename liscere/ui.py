#!/usr/bin/env python3
"""Liscere discovery-map TUI.

Reads the observer's JSON-Lines stream on stdin (from `liscere_observe.py --emit-json`) and renders
it live with `rich`. It is a mirror of the engine's discovery stream: it hardcodes NOTHING about the
process, protocol, phases, or flows. It renders whatever events arrive: one protocol or three,
whatever phase name is reported, whatever flows are found. Event types it does not organise are shown
in an "other events" area rather than dropped, so we can see if the engine emits something new.

Pure presentation: no engine imports, no tshark, no assumptions.

Run (on the Pi):
    sudo -E venv/bin/python scripts/liscere_observe.py --iface eth0 --observe 60 --learn 120 \\
        2>/dev/null | venv/bin/python scripts/liscere_ui.py
Or against a recorded stream:
    cat sample.jsonl | python scripts/liscere_ui.py
"""

import argparse
import json
import os
import select
import sys
import time
from collections import OrderedDict, deque

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from liscere import __version__

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

    def __init__(self, verbose=False):
        # The silo (server endpoint) is the unit. Each readable silo owns its own flows; two silos
        # with the same flow key never merge. Everything routes by the event's "silo" tag, read from
        # the event. Each silo gets a short GLOBAL id ([1], [2], ...) in discovery order, so the map
        # is the legend and the tables can reference a silo by id instead of a long endpoint.
        self.silos = OrderedDict()       # endpoint -> {"id", "peer", "flows": OrderedDict(key->flow)}
        self._next_silo_id = 1
        # Three distinct kinds of "not evaluated", never conflated (each key -> (display, reason)):
        #   unreadable  -- traffic no extractor can read (a vantage boundary).
        #   unevaluable -- read perfectly, could not be calibrated (a data problem).
        #   not_run     -- claimed by the probe but the operator chose not to run it (--only): a scope
        #                  decision, neither a blindness nor a data problem. Shown so the run says
        #                  plainly what it saw and did not run.
        # Keyed so an endpoint-less finding (a claimed-but-empty extractor) does not collide with
        # another on one line. A silo whose flows WERE discovered but has no state signal is NOT here
        # -- it stays a map root, marked unevaluable in place (see _silo).
        self.unreadable = OrderedDict()
        self.unevaluable = OrderedDict()
        self.not_run = OrderedDict()
        # Capture AVAILABILITY per protocol -- protocol -> (state, detail). A tshark can die and be
        # respawned mid-run; this surfaces the blip so the map never pretends a protocol is being
        # watched when its capture is down. "resumed"/None clears back to steady-state.
        self.capture_status = OrderedDict()
        self.protocol = None             # run-wide protocol label (fallback only; each silo carries its own)
        # (silo, key) -> {nature, datatype, datatype_certain, features, value, phase}  (VARIABLES table)
        self.variables = OrderedDict()
        self.verdicts = deque(maxlen=10)
        self.grammar = deque(maxlen=5)
        self.others = deque(maxlen=8)
        self.event_count = 0
        self.ended = False
        self.verbose = verbose           # -v: developer view (engine diagnostics on the map)
        self.stage = None                # pipeline stage: "observe" | "learn" | "evaluate" | None
        self.stage_seconds = None        # window duration for the current stage (observe/learn), else None
        self.stage_started = None        # UI wall-clock at which the current timed stage began

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
            "stage": self._stage,
            "silo": self._silo,
            "capture": self._capture,
        }.get(t)
        if handler:
            handler(ev)
        else:
            self.others.append(ev)   # unknown/unorganised type: shown, never dropped

    # -- silo routing -----------------------------------------------------
    def _silo_of(self, ev, endpoint_hint=None):
        """The silo (endpoint) an event belongs to, READ from the event -- never inferred. The
        observer tags every per-silo event with its silo, so the "silo" tag is authoritative; the
        endpoint hint (which flow_found also carries) is a fallback only."""
        tag = ev.get("silo")
        if tag is not None:
            return str(tag)
        return str(endpoint_hint) if endpoint_hint else None

    def _ensure_silo(self, endpoint):
        if not endpoint:
            return None
        if endpoint not in self.silos:
            self.silos[endpoint] = {"id": self._next_silo_id, "peer": None, "protocol": None,
                                    "evaluable": True, "reason": None, "flows": OrderedDict()}
            self._next_silo_id += 1
        return self.silos[endpoint]

    def silo_id(self, endpoint):
        """The short global id for a silo endpoint, e.g. "[1]" (the map is the legend); "?" if unseen."""
        s = self.silos.get(endpoint)
        return f"[{s['id']}]" if s else "?"

    def _ensure_var(self, silo, key):
        return self.variables.setdefault((silo, key), {
            "nature": None, "datatype": None, "datatype_certain": False,
            "features": None, "value": None, "phase": None, "late": False,
        })

    def _protocol_seen(self, ev):
        # One extractor per run today, so a single label annotates the silos it produced.
        self.protocol = str(ev.get("protocol", "?"))

    def _flow_found(self, ev):
        # flow_found carries its silo both as the "silo" tag (N>1) and as "endpoint" (the flow's
        # server), so it can create/route its silo even at N=1 before the silo() announcement arrives.
        silo = self._ensure_silo(self._silo_of(ev, endpoint_hint=ev.get("endpoint")))
        if silo is None:
            self.others.append(ev)
            return
        silo["flows"].setdefault(
            str(ev.get("key", "?")),
            {"role_hint": ev.get("role_hint"), "verdict": None, "features": None,
             "state": False, "late": False},
        )

    def _variable_found(self, ev):
        endpoint = self._silo_of(ev)
        key = str(ev.get("key"))
        # A late command (surfaced after observe) is classified from protocol semantics only, not
        # behaviourally; the "*" marker in the UI keeps that distinct from a discovered variable.
        late = bool(ev.get("late"))
        # left map: attach nature + features to the flow node in this silo (create if unseen)
        silo = self.silos.get(endpoint) if endpoint else None
        if silo is not None:
            fl = silo["flows"].setdefault(
                key, {"role_hint": None, "verdict": None, "features": None, "state": False, "late": False})
            fl["verdict"] = ev.get("nature")
            fl["features"] = ev.get("features") or {}   # null for late commands -> {} (no feature line)
            fl["late"] = late
        # VARIABLES table row, keyed by (silo, key)
        v = self._ensure_var(endpoint, key)
        v["nature"] = ev.get("nature")
        v["datatype"] = ev.get("datatype")
        v["datatype_certain"] = bool(ev.get("datatype_certain"))
        v["features"] = ev.get("features") or {}
        v["late"] = late

    def _variable_value(self, ev):
        # update the value column (create a minimal row if the variable wasn't announced yet)
        self._ensure_var(self._silo_of(ev), str(ev.get("key")))["value"] = ev.get("value")

    def _state_signal(self, ev):
        silo = self.silos.get(self._silo_of(ev))
        key = str(ev.get("key"))
        if silo is not None and key in silo["flows"]:
            silo["flows"][key]["state"] = True

    def _phase(self, ev):
        key = ev.get("state_key")
        if key is not None:                          # phase belongs to a specific state variable
            self._ensure_var(self._silo_of(ev), str(key))["phase"] = ev.get("phase")

    def _verdict(self, ev):
        self.verdicts.append({**ev, "silo": self._silo_of(ev)})   # stamp the resolved silo for display

    def _grammar(self, ev):
        self.grammar.append(ev)

    def _stage(self, ev):
        self.stage = ev.get("stage")
        secs = ev.get("seconds")
        self.stage_seconds = secs
        # Count down from the UI's own receipt of the stage event (robust for live and recorded
        # playback alike, and free of observer/UI clock-sync). Timed stages only (observe/learn).
        self.stage_started = time.time() if secs is not None else None

    def _silo(self, ev):
        # A silo announcement, carrying its OWN protocol label. evaluable=True -> a DISCOVERY MAP root
        # (with its peer and protocol). evaluable=False -> a finding that exists but cannot be
        # evaluated; ``kind`` says which section it belongs in and the two are never merged:
        #   "unreadable"  -> UNREADABLE ENDPOINTS (traffic no extractor can read; a vantage boundary).
        #   "unevaluable" -> read but not calibratable (a data problem), shown separately.
        #   "not_run"     -> claimed but excluded by --only (a scope decision), shown separately again.
        endpoint = ev.get("endpoint")
        protocol = ev.get("protocol")
        reason = ev.get("reason")
        kind = ev.get("kind") or "unevaluable"
        ep = str(endpoint) if endpoint else None

        if ev.get("evaluable"):
            silo = self._ensure_silo(ep)
            if silo is not None:
                if protocol:
                    silo["protocol"] = protocol
                if ev.get("peer"):
                    silo["peer"] = ev.get("peer")
            return

        if kind == "unreadable":
            self._add_finding(self.unreadable, ep, reason)
            return
        if kind == "not_run":
            # A whole claimed layer the operator chose not to run: no endpoint. Show its protocol label
            # so it reads as "MODBUS -- claimed but not run", key by label so two never collapse.
            self.not_run[protocol or reason] = (protocol or "(layer)", reason)
            return

        # Unevaluable. If its flows were already discovered (a map root exists), it is a silo that
        # exists and cannot be evaluated -- mark it IN PLACE, never also list it below. Same endpoint
        # in two places was the incoherence. Only an unevaluable finding with NO map root (an
        # endpoint-less "claimed X, no evaluable silo", or an endpoint whose flows were never seen)
        # goes to the separate UNEVALUABLE section.
        silo = self.silos.get(ep) if ep else None
        if silo is not None:
            silo["evaluable"] = False
            silo["reason"] = reason
            if protocol:
                silo["protocol"] = protocol
            return
        self._add_finding(self.unevaluable, ep, reason)

    @staticmethod
    def _add_finding(bucket, endpoint, reason):
        # Key by endpoint when present (one line for hundreds of frames); key an endpoint-less finding
        # by its reason so two of them (e.g. two claimed-but-empty protocols) never collapse onto one.
        if endpoint:
            key, display = endpoint, endpoint
        else:
            key, display = f"(no endpoint):{reason}", "(no endpoint)"
        bucket[key] = (display, reason)

    def _capture(self, ev):
        # Availability of one protocol's capture. "lost"/"permanently_lost" flag a degraded/dead
        # capture; "resumed" clears back to steady-state (keeping a brief note of the downtime).
        proto, state = ev.get("protocol", "?"), ev.get("state")
        if state == "resumed":
            self.capture_status[proto] = ("resumed", f"resumed after {ev.get('down_s', '?')}s")
        elif state == "permanently_lost":
            self.capture_status[proto] = ("permanently_lost",
                                          f"permanently lost after {ev.get('tries', '?')} respawns")
        else:  # "lost"
            self.capture_status[proto] = ("lost", "capture lost -- respawning")


# -- rendering ------------------------------------------------------------

def _strip_prefix(key):
    """Drop the leading protocol segment of a flow key ("modbus:hr:2" -> "hr:2"). Generic -- the
    first colon-delimited segment, whatever it is -- because the silo header already names the
    protocol, so repeating it on every key is noise. Keys without a ":" are returned unchanged."""
    key = str(key)
    return key.split(":", 1)[1] if ":" in key else key


def _finding_section(tree, title, title_style, glyph, glyph_style, text_style, findings, need_gap):
    """One compact section (UNREADABLE / UNEVALUABLE): a header, then one line per finding
    "<glyph> <endpoint>   <reason>". Returns True if it rendered (so the next gap is decided)."""
    if not findings:
        return False
    if need_gap:
        tree.add(Text(""))
    node = tree.add(Text(title, style=title_style))
    for _key, (display, reason) in findings.items():
        t = Text(glyph, style=glyph_style)
        t.append(str(display), style=text_style)
        if reason:
            t.append(f"   {reason}", style=f"dim {text_style}")
        node.add(t)
    return True


_CAPTURE_STYLE = {"lost": "bold yellow", "permanently_lost": "bold red", "resumed": "green"}
_CAPTURE_GLYPH = {"lost": "◐ ", "permanently_lost": "✖ ", "resumed": "● "}


def build_tree(model):
    tree = Tree(Text("DISCOVERY MAP", style="bold"))
    # Capture-availability banner FIRST: a degraded/dead capture is the most urgent thing to see, and
    # the monitor must never look healthy while a protocol is dark. Steady-state protocols show nothing.
    for proto, (state, detail) in model.capture_status.items():
        style = _CAPTURE_STYLE.get(state, "yellow")
        line = Text(_CAPTURE_GLYPH.get(state, "◐ "), style=style)
        line.append(f"{proto}: {detail}", style=style)
        tree.add(line)
    if model.capture_status:
        tree.add(Text(""))
    if not model.silos and not model.unreadable and not model.unevaluable and not model.not_run:
        tree.add(Text("discovering…", style="dim"))
    silos = list(model.silos.items())
    for i, (endpoint, silo) in enumerate(silos):
        # Silo header "[id] LABEL (port)", then the addresses as bare IPs on their own lines (server
        # first, then peer(s), no label), then the flows -- no blank INSIDE a silo. Silos are
        # separated by one blank line BETWEEN them. The LABEL is the silo's OWN protocol (each silo
        # is labelled by the extractor that parsed it), falling back to the run-wide one only if a
        # silo was created by flow_found before its announcement arrived.
        server_ip, _, port = str(endpoint).rpartition(":")
        head = Text(f"[{silo['id']}] ", style="bold yellow")
        head.append(silo.get("protocol") or model.protocol or "?", style="bold white")
        if port:
            head.append(f" ({port})", style="dim")
        snode = tree.add(head)
        snode.add(Text(server_ip or str(endpoint), style="cyan"))
        for p in (silo.get("peer") or "").split(", "):
            if p.strip():
                snode.add(Text(p.strip(), style="dim"))
        # A silo whose flows were discovered but which has no state signal exists and cannot be
        # evaluated: it stays a map root, marked here in place -- never duplicated into a list below.
        if silo.get("evaluable") is False:
            note = Text("⚠ unevaluable", style="yellow")
            if silo.get("reason"):
                note.append(f"   {silo['reason']}", style="dim yellow")
            snode.add(note)
        for key, fl in silo["flows"].items():
            snode.add(_flow_label(key, fl, model.verbose))
        if i < len(silos) - 1:
            tree.add(Text(""))                                     # one blank line between silos
    # Three separate sections, kept distinct: unreadable (a vantage boundary), unevaluable (a data
    # problem), and not-run (a scope decision). Conflating any of them would erase a distinction the
    # observer rests on -- each is a different reason a protocol on the wire is not being evaluated.
    r1 = _finding_section(
        tree, "UNREADABLE ENDPOINTS  ·  captured but no extractor can read", "bold red",
        "⊘ ", "bold red", "red", model.unreadable, need_gap=bool(silos))
    r2 = _finding_section(
        tree, "UNEVALUABLE  ·  read but no state signal to calibrate", "bold yellow",
        "⚠ ", "bold yellow", "yellow", model.unevaluable, need_gap=bool(silos) or r1)
    _finding_section(
        tree, "NOT RUN  ·  claimed by the probe, excluded by --only", "bold blue",
        "⊙ ", "bold blue", "blue", model.not_run, need_gap=bool(silos) or r1 or r2)
    return tree


def _flow_label(key, fl, verbose=False):
    """Product view: `key   NATURE[*] [★]` -- what the variable is, nothing more. Developer view (-v)
    adds the engine diagnostics (uniq/range/step/rev) and the verbose state-signal styling."""
    if verbose:
        return _flow_label_verbose(key, fl)
    t = Text()
    t.append(_strip_prefix(key))
    if fl["verdict"]:
        label = NATURE_LABEL.get(fl["verdict"], fl["verdict"])
        if fl.get("late"):
            label += "*"   # classified from protocol semantics, not observed behaviour
        t.append("   ")
        t.append(label, style=VERDICT_STYLES.get(fl["verdict"], "white"))
    else:
        t.append(f"   [hint: {_fmt(fl['role_hint'])}]", style="dim")
    if fl["state"]:
        t.append(" ★", style="bold yellow")
    return t


def _flow_label_verbose(key, fl):
    t = Text()
    if fl["state"]:
        t.append("★ ", style="bold yellow")
    t.append(_strip_prefix(key))
    if fl["verdict"]:
        t.append("  → ")
        verdict_label = NATURE_LABEL.get(fl["verdict"], fl["verdict"])
        if fl.get("late"):
            verdict_label += "*"   # classified from protocol semantics, not observed behaviour
        t.append(verdict_label, style=VERDICT_STYLES.get(fl["verdict"], "white"))
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


# Shorter display label per nature (the underlying nature value is unchanged in the data).
NATURE_LABEL = {"CONSTANT_METADATA": "METADATA"}


def _fmt_value(x):
    """VARIABLES value cell: floats to at most one decimal (36.4, not 36.406); integer types as
    integers; whole-valued floats without a trailing .0; None -> em dash."""
    if x is None:
        return "-"
    if isinstance(x, bool):
        return str(int(x))
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        return str(int(x)) if x.is_integer() else f"{x:.1f}"
    return str(x)


# The two shared columns render at the SAME fixed widths in both tables, so silo and key line up
# vertically down the stacked panels. Every other column is fixed too, except one flexible trailing
# column per table (so expand=True fills the panel without stretching silo/key). All left-justified.
_SILO_W = 4
_KEY_W = 18


def build_variables(model):
    # One row per PROCESS variable; CONSTANT_METADATA is not shown here (it stays in the MAP). No
    # placeholder row: an empty table is self-evident. Rows are GROUPED by silo, in silo-id order,
    # with a thin separator between groups (Events stays chronological -- that ordering is right there).
    tbl = Table(expand=True, show_edge=False, header_style="bold")
    tbl.add_column("silo", width=_SILO_W, no_wrap=True, justify="left")
    tbl.add_column("key", width=_KEY_W, no_wrap=True, overflow="ellipsis", justify="left")
    tbl.add_column("nature", width=9, no_wrap=True, justify="left")
    tbl.add_column("type", width=7, no_wrap=True, justify="left")     # renamed from "datatype"
    tbl.add_column("value", width=9, no_wrap=True, justify="left")
    tbl.add_column("phase", justify="left")                          # flexible: absorbs remaining width
    by_silo = OrderedDict()
    for (silo, key), v in model.variables.items():
        if v["nature"] == "CONSTANT_METADATA":
            continue
        by_silo.setdefault(silo, []).append((key, v))
    ordered = sorted(by_silo.items(), key=lambda kv: model.silos.get(kv[0], {}).get("id", float("inf")))
    for gi, (silo, rows) in enumerate(ordered):
        for key, v in rows:
            nature = v["nature"]
            style = VERDICT_STYLES.get(nature, "white")
            dtype = v["datatype"] if (v["datatype_certain"] and v["datatype"]) else "?"
            value = _fmt_value(v["value"])
            phase = _fmt(v["phase"]) if (nature == "STATE" and v["phase"]) else "-"
            label = NATURE_LABEL.get(nature, _fmt(nature))
            if v.get("late"):
                label += "*"   # classified from protocol semantics, not observed behaviour
            tbl.add_row(Text(model.silo_id(silo), style="dim"), _strip_prefix(key),
                        Text(label, style=style), dtype, value, phase)
        if gi < len(ordered) - 1:
            tbl.add_section()                                        # thin separator between silos
    return Panel(tbl, title="VARIABLES", border_style="magenta")


def build_events(model):
    # Newest-first feed (most recent verdict on top). CHRONOLOGICAL, not grouped -- ordering matters
    # here. `silo` and `key` share the VARIABLES widths so the two tables line up. `key` is the same
    # thing VARIABLES calls key (the write target). No "no events yet" placeholder: an empty table
    # is self-evident.
    tbl = Table(expand=True, show_edge=False, header_style="bold")
    tbl.add_column("silo", width=_SILO_W, no_wrap=True, justify="left")
    tbl.add_column("key", width=_KEY_W, no_wrap=True, overflow="ellipsis", justify="left")
    tbl.add_column("phase", width=8, no_wrap=True, justify="left")
    tbl.add_column("result", justify="left")                        # flexible
    for v in reversed(model.verdicts):
        tbl.add_row(
            Text(model.silo_id(v.get("silo")), style="dim"), _strip_prefix(v.get("target")),
            _fmt(v.get("phase")),
            Text(_fmt(v.get("result")), style=RESULT_STYLES.get(v.get("result"), "white")),
        )
    return Panel(tbl, title="Events", border_style="cyan")


def build_others(model):
    if not model.others:
        return None
    t = Text()
    for o in model.others:
        rest = {k: val for k, val in o.items() if k != "type"}
        t.append(f"{o.get('type', '?')}: {json.dumps(rest)[:70]}\n", style="yellow")
    return Panel(t, title="other events (unorganised)", border_style="yellow")


# Pipeline stage -> header label. "evaluate" (continuous watch) reads "LIVE".
_STAGE_LABEL = {"observe": "OBSERVE", "learn": "LEARN", "evaluate": "LIVE"}


def _status_label(model):
    if model.ended:
        return "ENDED"
    # Before the first stage event, the run has just begun -> OBSERVE (the pipeline always
    # starts by observing).
    stage = model.stage or "observe"
    label = _STAGE_LABEL.get(stage, stage.upper())
    # Timed windows (observe/learn) count down; continuous evaluate is LIVE with no timer.
    if model.stage_seconds is not None and model.stage_started is not None:
        remaining = max(0, model.stage_seconds - (time.time() - model.stage_started))
        return f"{label}  ⏱ {remaining:0.0f}s"
    return label


def render(model):
    layout = Layout()
    status = _status_label(model)
    live_parts = [build_variables(model), build_events(model)]
    # The "grammar (learn)" panel was intentionally removed; grammar_learned events are still
    # accepted by the model, just no longer shown.
    others = build_others(model)
    if others is not None:
        live_parts.append(others)

    # VARIABLES (right) is now the primary panel, so give it the wider share.
    layout.split_row(Layout(name="map", ratio=2), Layout(name="live", ratio=3))
    layout["map"].update(Panel(build_tree(model), border_style="cyan"))
    layout["live"].update(
        Panel(Group(*live_parts), title=f"{status}  ·  {model.event_count} events",
              border_style=("green" if not model.ended else "dim"))
    )
    return layout


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Liscere discovery-map TUI. Reads the observer's JSON-Lines stream on stdin.")
    ap.add_argument("--version", action="version", version=f"liscere {__version__}")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="developer view: show engine diagnostics (uniq/range/step/rev) on the map")
    args = ap.parse_args(argv)

    model = DiscoveryModel(verbose=args.verbose)
    fd = sys.stdin.fileno()
    buf = b""
    # Fixed redraw cadence, decoupled from event arrival: incoming events only mutate the in-memory
    # model; the screen is redrawn on this timer, so a burst of events cannot cause a burst of
    # repaints. 6 fps sits in the 4-8 target band and is well below any terminal's own draw rate.
    redraw_period = 1.0 / 6.0
    # screen=True draws to the terminal's alternate screen buffer and presents each frame atomically,
    # so the panel borders and rule lines never tear (the old inline screen=False redraw overwrote the
    # region in place and flashed them, worse during event bursts). auto_refresh=False means the ONLY
    # redraws are the timer-driven ones in the loop below: single-threaded, no render thread, no lock.
    with Live(render(model), refresh_per_second=6, screen=True, auto_refresh=False) as live:
        # Read raw from the fd, NOT via sys.stdin.readline(): a buffered readline pulls several
        # flushed lines into Python's TextIOWrapper, and select() watches the fd (not that buffer),
        # so trailing lines get stuck until the fd next has data. os.read drains exactly what select
        # signalled. The select timeout is the redraw period, so the timer still fires (and the
        # countdown still ticks) while the observer is silent.
        last_draw = time.monotonic()
        eof = False
        while not eof:
            ready, _, _ = select.select([sys.stdin], [], [], redraw_period)
            if ready:
                chunk = os.read(fd, 65536)
                if chunk == b"":                     # EOF -> stream ended
                    eof = True
                else:
                    buf += chunk
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        _ingest(model, raw.decode("utf-8", "replace").strip())
            # Redraw on the fixed timer only: a burst drains into the model above without repainting;
            # the screen coalesces to the latest state here, at most once per period. select blocks up
            # to redraw_period between events, so a slow stream never busy-spins.
            now = time.monotonic()
            if now - last_draw >= redraw_period:
                live.update(render(model), refresh=True)
                last_draw = now
        if buf.strip():                              # a final line with no trailing newline
            _ingest(model, buf.decode("utf-8", "replace").strip())
        model.ended = True
        live.update(render(model), refresh=True)     # last frame inside the alternate screen
    # Live has exited and torn down the alternate screen, so reprint the final frame to the normal
    # screen: the run ends on a verdict, and stopping the capture must not wipe it from the terminal.
    Console().print(render(model))
    return 0


def _ingest(model, line):
    if not line:
        return
    try:
        ev = json.loads(line)
        if not isinstance(ev, dict) or "type" not in ev:
            raise ValueError("not an event object")
    except Exception:
        model.others.append({"type": "<malformed line>", "raw": line[:70]})
    else:
        model.update(ev)


if __name__ == "__main__":
    raise SystemExit(main())
