#!/usr/bin/env python3
"""Liscere passive observer — reference entrypoint.

Ties the already-committed modules into the validated passive-observer pipeline, with ZERO
process configuration:

    OBSERVE (capture a window)
      -> DISCOVER the state signal   (behavioural classifier + protocol role hints; Degrau 4a)
      -> CALIBRATE the phase params  (Level-2 auto-calibration, from the discovered state flow)
      -> INFER phase                 (PhaseTracker, using the calibrated config)
      -> LEARN the grammar           (learn mode)   |   EVALUATE coherence (evaluate mode)

Neither the state signal (which NodeId/register) nor the phase parameters are configured; both are
derived from observed traffic. tshark is driven entirely by the active extractor's
tshark_fields()/occurrence()/capture_filter(), so the captured columns always align with
parse_line — there is no hand-written field list here.

This supersedes the ad-hoc ~/OT-Lab/liscere_observe.py used during bench validation, and is the
reference implementation of the passive observer.

Standalone by design: it does NOT touch app.py or the legacy declarative engine, and does NOT
reconcile the two engines. State samples are fed directly to PhaseTracker (bypassing
LearnedEngineAdapter's single-sample path).

Known debts (see the modules for detail):
  - D1  single-tag telemetry: all publish Floats are treated as one state flow; multiple monitored
        items need ClientHandle->NodeId correlation from CreateMonitoredItems (= level 4b).
  - D4  LearnedEngineAdapter reads only the newest sample; this observer feeds ALL samples directly.
  - D5  discovery thresholds are fixed defaults (deriving them from observation is future work).
  - OBS-Rxxx rule-id collision between the learned evaluator and the legacy declarative engine in
        app.py, left unreconciled by design.

The observer probes the wire, discovers which protocols are present, and runs every claimed
extractor at once over ONE selector loop (no threads, no protocol configuration): what runs is what
is on the wire, not a hand-set protocol.

Usage:
    python scripts/liscere_observe.py --iface en6 --observe 60 --learn 120
    python scripts/liscere_observe.py --iface en6 --probe 30 --observe 60 --grammar learned_grammar.json
"""

from __future__ import annotations

import argparse
import json
import os
import select
import selectors
import subprocess
import sys
import time
import types
from collections import OrderedDict, deque
from dataclasses import dataclass, field

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from otlab_core import wire
from otlab_core.engine.autocalibrate import calibrate_phase_config, write_profile
from otlab_core.engine.discover import classify_flows
from otlab_core.engine.evaluator import evaluate
from otlab_core.engine.grammar import GrammarLearner
from otlab_core.engine.phase_tracker import PhaseTracker
from otlab_core.extractors import extractor_for_layer


# --------------------------------------------------------------------------- event emission (JSON Lines)

def emit(event, out=None):
    """Write one JSON object per line to `out` (default stdout), flushed immediately.

    The whole emission primitive: a downstream UI reads these lines live from stdout. Stamps a
    wall-clock `ts` if the caller did not set one. It knows nothing about any UI, colours, or
    rendering; it just prints a structured fact.
    """
    out = out or sys.stdout
    if "ts" not in event:
        event = {**event, "ts": round(time.time(), 3)}
    out.write(json.dumps(event) + "\n")
    out.flush()


def _protocol_label(extractor):
    """The short protocol label for a silo/announcement, e.g. "MODBUS", "OPCUA", "S7COMM". Derived
    from the extractor's own name -- so each silo is labelled by the extractor that parsed it, never
    by a single run-wide pointer (the multi-protocol bug where the last extractor stamped them all)."""
    return extractor.name.split("/")[0].upper()


class Emitter:
    """Emits structured discovery events as JSON Lines, in the order they are discovered.

    Every field comes from real observed data; nothing about the process or protocol is hardcoded.
    When ``enabled`` is False (``--no-emit-json``) every method is a no-op. Phase events are
    de-duplicated: one is emitted only when the inferred phase actually changes.
    """

    def __init__(self, enabled=True, out=None, _tag=None, _shared=None):
        self.enabled = enabled
        self.out = out or sys.stdout
        # A silo tag scopes this Emitter's events and de-dup to one silo. None = untagged (the whole
        # stream, or the N=1 case): events carry no silo field, so output is identical to the
        # pre-silo observer. De-dup state lives in `_shared` and is keyed by tag, so N silos share ONE
        # Emitter -- one stdout owner, one de-dup owner -- via bind() rather than N unsynchronised
        # Emitters (the 2b-safe structure: nothing to interleave on a shared stdout without a lock).
        self._tag = _tag
        self._shared = _shared if _shared is not None else {
            "last_phase": {},    # tag -> last emitted phase
            "last_values": {},   # (tag, key) -> last emitted value
            "announced": {},     # (tag, key) -> the nature it was announced with (so a write can
                                 #              reclassify a non-command key; see command_found)
            "state_keys": set(), # (tag, key) that IS the discovered state signal -> never reclassified
            "opaque": set(),     # endpoints already reported as opaque -> report once, not per frame
        }

    def bind(self, tag):
        """A view of this Emitter scoped to silo `tag`: same stdout and same (tag-keyed) de-dup state,
        stamping `tag` on each event. tag=None yields an untagged view whose JSON is byte-identical to
        the single pre-silo stream. Every silo uses a bound view, so there is no shared-vs-per-silo
        Emitter branch -- N=1 and N>1 differ only in the tag value.
        """
        return Emitter(enabled=self.enabled, out=self.out, _tag=tag, _shared=self._shared)

    def _emit(self, event):
        if self.enabled:
            if self._tag is not None and "silo" not in event:
                event = {**event, "silo": self._tag}   # attribute the event to its silo (N>1 only)
            emit(event, out=self.out)

    def stage(self, stage, seconds=None):
        # A pipeline-stage transition ("observe" | "learn" | "evaluate"), emitted once per move so
        # the UI header can reflect the current stage. `seconds` is the window duration when the
        # window is time-bounded (observe/learn), so the UI can show a countdown; continuous evaluate
        # passes none (it runs until Ctrl-C -> the UI shows LIVE with no timer).
        event = {"type": "stage", "stage": stage}
        if seconds is not None:
            event["seconds"] = seconds
        self._emit(event)

    def silo(self, endpoint, evaluable, reason=None, peer=None, protocol=None, kind=None):
        # A discovered silo -- one (protocol, server endpoint) as its own unit of evaluation. Each
        # silo carries its OWN protocol label (from the extractor that parsed it) so the UI never has
        # to guess -- the multi-protocol failure was one shared label stamped on every silo.
        #
        # `evaluable` False -> the silo exists but cannot be evaluated; `kind` says WHY, a distinction
        # the UI must not erase:
        #   "unreadable"  -- traffic no extractor can read (an encrypted variant on the port). A
        #                    vantage boundary: we cannot see in.
        #   "unevaluable" -- read perfectly, but no state signal to calibrate against (too sparse, or
        #                    no cycling variable). A data problem, not a blindness.
        # `reason` states the specific cause either way, so it is reported and never silently dropped.
        # `peer` is the observed client host(s) talking to this silo, when known (readable silos).
        event = {"type": "silo", "endpoint": endpoint, "evaluable": evaluable, "reason": reason}
        if protocol:
            event["protocol"] = protocol
        if kind:
            event["kind"] = kind
        if peer:
            event["peer"] = peer
        self._emit(event)

    def opaque_endpoint(self, endpoint, reason):
        # An endpoint whose traffic arrived on the capture filter but which no extractor can read
        # (e.g. an encrypted variant on the service port). Reported ONCE per endpoint as a silo that
        # exists but is UNREADABLE -- so hundreds of opaque frames yield a single event -- and never
        # routed to a tracker. Dedup lives in the shared bookkeeping, alongside announced keys.
        if not endpoint or endpoint in self._shared["opaque"]:
            return
        self._shared["opaque"].add(endpoint)
        self.silo(endpoint, evaluable=False, reason=reason, kind="unreadable")

    def protocol_seen(self, extractor):
        event = {"type": "protocol_seen", "protocol": _protocol_label(extractor)}
        port = getattr(getattr(extractor, "config", None), "port", None)
        if port is not None:
            event["port"] = port
        self._emit(event)

    def flow_found(self, flow):
        event = {"type": "flow_found", "key": flow.key, "role_hint": flow.role_hint}
        server = getattr(flow, "server", None)
        if server:
            event["endpoint"] = server   # observed server "ip:port"; UI derives the protocol port
        self._emit(event)

    def variable_found(self, verdict):
        # A discovered variable, first-class. `nature` is literally the behavioural verdict.
        f = verdict.features
        # Remember the nature this key was announced with, so a later WRITE_REQUEST can tell a
        # non-command classification (which a write outranks) from an already-command one.
        self._shared["announced"][(self._tag, verdict.key)] = verdict.verdict
        self._emit({
            "type": "variable_found", "key": verdict.key, "nature": verdict.verdict,
            "datatype": verdict.datatype, "datatype_certain": verdict.datatype_certain,
            "features": {
                "unique_values": f.unique_values,
                "value_range": round(f.value_range, 4),
                "median_step": round(f.median_step, 4),
                "reversals": f.reversals,
            },
        })

    def command_found(self, key, datatype, datatype_certain):
        """A WRITE_REQUEST is protocol PROOF the key is a command, so surface it as a late command
        (COMMAND*, nature=COMMAND, features=null, late=true; datatype from the extractor).

        Announce it when discovery never saw the key (first written in learn/evaluate), AND
        RE-announce it when discovery announced it as some NON-command nature (e.g. a read-only
        HR shown as CONSTANT_METADATA): a write outranks a behavioural classification inferred from
        one window. Guards:
          - a key already surfaced as COMMAND is not re-announced (a repeat write is suppressed, as
            before) -- the announced-nature map, not mere membership, is what distinguishes this;
          - the discovered STATE signal is NEVER reclassified here. It drives calibration and the
            tracker; a write to the state variable is a distinct, more interesting situation, and
            silently turning it into a command would be wrong. It is left untouched (see the report).
        """
        if key is None:
            return
        tk = (self._tag, key)
        if self._shared["announced"].get(tk) == "COMMAND":
            return   # already a command -> suppress the repeat write, exactly as before
        if tk in self._shared["state_keys"]:
            return   # the state signal -> not reclassified by a write (guarded, deliberately)
        # Unannounced, or announced as a non-command nature -> (re)announce as a late command.
        self._shared["announced"][tk] = "COMMAND"
        self._emit({
            "type": "variable_found", "key": key, "nature": "COMMAND",
            "datatype": datatype, "datatype_certain": datatype_certain,
            "features": None, "late": True,
        })

    def variable_value(self, key, value):
        # The current value of a variable (latest state sample / last written command value),
        # de-duped so an unchanged value is not re-emitted. Only key + value; no name or meaning.
        if key is None:
            return
        lv = self._shared["last_values"]
        if lv.get((self._tag, key)) != value:
            lv[(self._tag, key)] = value
            self._emit({"type": "variable_value", "key": key, "value": value})

    def state_signal_discovered(self, key):
        # Record the state signal so a later write can never silently reclassify it (command_found).
        self._shared["state_keys"].add((self._tag, key))
        self._emit({"type": "state_signal_discovered", "key": key})

    def phase(self, tracker, state_key=None):
        # state_key identifies which state variable this phase refers to (prep for multi-state;
        # today there is one). `level` is that variable's current value, not a domain "level".
        if tracker.phase != self._shared["last_phase"].get(self._tag):
            self._shared["last_phase"][self._tag] = tracker.phase
            level = tracker.last_level
            self._emit({
                "type": "phase", "state_key": state_key, "phase": tracker.phase,
                "confidence": round(tracker.confidence, 3),
                "level": round(level, 4) if level is not None else None,
            })

    def grammar_learned(self, target, phase):
        self._emit({"type": "grammar_learned", "target": target, "phase": phase})

    def verdict(self, target, phase, v):
        self._emit({"type": "verdict", "target": target, "phase": phase,
                    "result": v.verdict, "rule": v.rule})


# --------------------------------------------------------------------------- tshark I/O

def _tshark_cmd(extractor, iface):
    cmd = [
        "tshark", "-l", "-n", "-Q", "-i", iface,
        "-f", extractor.capture_filter(),
        "-T", "fields",
        "-E", "separator=\t",
        "-E", f"occurrence={extractor.occurrence()}",
        "-E", "quote=n",
    ]
    for f in extractor.tshark_fields():
        cmd.extend(["-e", f])
    return cmd


def _spawn(extractor, iface):
    # Bytes mode (no text=, bufsize=0): the multiplex reads the raw fd with os.read and splits
    # newlines itself. NOT a buffered readline -- select() watches the fd while readline reads through
    # Python's TextIOWrapper, so a flushed line can sit unread until the fd next has data.
    return subprocess.Popen(
        _tshark_cmd(extractor, iface),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
    )


def _kill(proc):
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _report_opaque(extractor, cols, emitter):
    """Ask the extractor (null-safe) whether a DECLINED frame is a captured-but-unreadable endpoint,
    and if so report it once. Mirrors _variable_key: the observer holds no protocol knowledge -- it
    only asks; an extractor without opaque_endpoint returns None and is never surfaced.
    """
    if emitter is None:
        return
    fn = getattr(extractor, "opaque_endpoint", None)
    opq = fn(cols) if fn else None
    if opq is not None:
        emitter.opaque_endpoint(*opq)


# --------------------------------------------------------------------------- probe (discover protocols)

# The probe captures with NO filter, so every application layer on the wire is seen -- including
# traffic no extractor can read. Endpoints + the dissector chain only.
_PROBE_FIELDS = ["frame.protocols", "ip.src", "tcp.srcport", "ip.dst", "tcp.dstport"]


def _probe_cmd(iface, seconds):
    cmd = ["tshark", "-l", "-n", "-Q", "-i", iface, "-a", f"duration:{int(seconds)}",
           "-T", "fields", "-E", "separator=\t", "-E", "quote=n"]
    for f in _PROBE_FIELDS:
        cmd += ["-e", f]
    return cmd


def probe_layers(iface, seconds, log=print, emitter=None):
    """Short, NO-filter capture -> (claimed_layers, unclaimed).

    ``claimed_layers`` is the set of wire-layer substrings seen on application traffic -- the
    extractors to run. ``unclaimed`` is {server_endpoint: sorted[app_chain]} for application traffic
    no extractor claims; it is surfaced via ``emitter.opaque_endpoint`` (the same UNREADABLE-ENDPOINTS
    path the opaque frames use), so the pipeline is never quiet about traffic it can see.

    A protocol SILENT during the window is never claimed -- extend --probe to catch slow talkers.
    Uses tshark's own ``-a duration:N`` (self-stops and flushes; a SIGTERM'd tshark loses its buffer),
    with a wall-clock backstop so a misbehaving tshark can never hang the run.
    """
    layers = wire.wire_layers()
    claimed, unclaimed = set(), {}
    log(f"[probe] {seconds:.0f}s on {iface} (no filter) -- discovering protocols on the wire")
    proc = subprocess.Popen(_probe_cmd(iface, seconds), stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, bufsize=0)
    fd = proc.stdout.fileno()
    os.set_blocking(fd, False)
    buf = b""
    deadline = time.time() + seconds + 3.0     # backstop only; -a duration is the real stop
    try:
        while time.time() < deadline:
            if not select.select([fd], [], [], 0.25)[0]:
                if proc.poll() is not None:
                    break
                continue
            chunk = os.read(fd, 65536)
            if chunk == b"":
                break
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                cols = raw.decode("utf-8", "replace").split("\t")
                protocols = cols[0] if cols else ""
                app = wire.app_layer(protocols)
                if not app:
                    continue                    # transport plumbing, not application traffic
                layer = wire.match_layer(protocols, layers)
                if layer:
                    claimed.add(layer)
                else:
                    src = wire.endpoint(cols[1] if len(cols) > 1 else "", cols[2] if len(cols) > 2 else "")
                    dst = wire.endpoint(cols[3] if len(cols) > 3 else "", cols[4] if len(cols) > 4 else "")
                    if src and dst:
                        unclaimed.setdefault(wire.server_of(src, dst), set()).add(app)
    finally:
        _kill(proc)
    log(f"[probe] claimed layers: {', '.join(sorted(claimed)) or '(none)'}")
    for ep, chains in sorted(unclaimed.items()):
        log(f"[probe] UNCLAIMED {ep}: {', '.join(sorted(chains))}")
        if emitter is not None:
            emitter.opaque_endpoint(ep, "application traffic no extractor reads: " + ", ".join(sorted(chains)))
    return claimed, {ep: sorted(c) for ep, c in unclaimed.items()}


# --------------------------------------------------------------------------- selector multiplex (N tshark)

#: Fed into an extractor's coalesce_writes generator to mean "no input right now" so the push-wrapper
#: can drain it one event at a time. Needs only ``.op`` (coalesce checks it, then passes non-writes
#: through); it is never a WRITE_REQUEST, so it is yielded straight back and recognised by identity.
_NODATA = types.SimpleNamespace(op=None, target=None)


class _Coalescer:
    """Push-wrapper over an extractor's coalesce_writes: feed one event, get the 0+ events it yields.

    coalesce_writes yields the FIRST event of a run immediately and DROPS its repeats, so nothing is
    ever buffered pending a flush -- feeding one event yields its coalesced result at once, which is
    what makes it work streaming (continuous evaluate) as well as bounded. An extractor without
    coalesce_writes (OPC UA, S7) passes events through unchanged. The observer stays protocol-neutral.
    """
    def __init__(self, extractor):
        self._inbox = deque()
        fn = getattr(extractor, "coalesce_writes", None)
        self._gen = fn(self._src()) if callable(fn) else None

    def _src(self):
        while True:
            yield self._inbox.popleft() if self._inbox else _NODATA

    def feed(self, evt):
        if self._gen is None:
            return [evt]
        self._inbox.append(evt)
        out = []
        for got in self._gen:                 # drains until the sentinel signals "no input now"
            if got is _NODATA:
                break
            out.append(got)
        return out


class _Source:
    """One extractor's live tshark: the process, its non-blocking fd, a byte buffer, its coalescer."""
    def __init__(self, extractor, proc):
        self.extractor = extractor
        self.proc = proc
        self.fd = proc.stdout.fileno()
        self.buf = b""
        self.coalescer = _Coalescer(extractor)


def multiplex(sources, on_event, emitter=None, log=print, until=None):
    """ONE selector loop over N tshark stdout fds -- single-threaded, no locks. Each line is routed to
    the extractor that owns its fd, parsed, coalesced, and handed to ``on_event(extractor, event)``.
    A declined frame goes to _report_opaque. ``until()`` is polled between selects (return True to
    stop) so observe/learn/evaluate windows are just deadlines in this one loop; a 0.25 s select
    timeout keeps it responsive during silence. A tshark that dies (EOF) is unregistered and reported;
    the loop continues on the survivors. ALL tshark are killed on exit -- normal, exception, or Ctrl-C
    -- so none leaks a promiscuous socket.

    Returns True if ``until()`` asked to stop (a deliberate end), False if the loop fell through because
    the selector EMPTIED -- every tshark exited. In unbounded continuous evaluate that second case is a
    capture failure, not a clean end: the caller must not treat it as success (see main). Silently
    returning there was the bug behind the bench "spin" -- the process exited when the capture dropped,
    and under restart supervision that re-probes and re-spawns promiscuous captures in a tight churn.
    """
    sel = selectors.DefaultSelector()
    for src in sources:
        os.set_blocking(src.fd, False)
        sel.register(src.fd, selectors.EVENT_READ, src)
    stopped = False
    try:
        while sel.get_map():
            if until is not None and until():
                stopped = True
                break
            # CONSTANT 0.25 s block -- NOT a deadline-derived timeout. Deriving it from the next phase
            # deadline would make it 0 in unbounded continuous evaluate (no deadline), and select(0)
            # spins a hot loop. The phase deadlines live in until(); this timeout only bounds silence.
            for key, _mask in sel.select(0.25):
                src = key.data
                chunk = os.read(src.fd, 65536)
                if chunk == b"":                          # EOF -> this tshark exited
                    sel.unregister(src.fd)
                    log(f"[capture] {src.extractor.name} tshark ended (rc={src.proc.poll()}); "
                        f"continuing on the {len(sel.get_map())} still live")
                    continue
                src.buf += chunk
                while b"\n" in src.buf:
                    raw, src.buf = src.buf.split(b"\n", 1)
                    cols = raw.decode("utf-8", "replace").split("\t")
                    evt = src.extractor.parse_line(cols)
                    if evt is not None:
                        for e in src.coalescer.feed(evt):
                            on_event(src.extractor, e)
                    else:
                        _report_opaque(src.extractor, cols, emitter)
    finally:
        for src in sources:
            _kill(src.proc)
    return stopped


# --------------------------------------------------------------------------- pipeline (pure, testable)

# Coalesce-window derivation bounds (seconds). Floor sits above the ~230 ms HMI repetition cadence
# so a held button still coalesces; ceiling bounds slow processes; 1/20 of a half-cycle keeps the
# window well below the phase duration. See ModbusConfig.coalesce_window_s.
_COALESCE_WINDOW_FLOOR_S = 0.5
_COALESCE_WINDOW_CEIL_S = 5.0
_COALESCE_HALF_CYCLE_FRACTION = 1.0 / 20.0


def derive_coalesce_window_s(period_samples, dt, measurable, default):
    """Coalesce window from the OBSERVED half-cycle: clamp(half_cycle_s / 20, 0.5, 5.0).

    half_cycle_s = period_samples * dt (samples times seconds-per-sample), both already measured by
    autocalibrate during OBSERVE. Returns ``default`` when the period was not measurable, so the
    ModbusConfig default (1.0 s) remains the fallback. Pure and unit-testable; no engine call.
    """
    if not measurable or not period_samples or not dt:
        return default
    half_cycle_s = period_samples * dt
    return max(_COALESCE_WINDOW_FLOOR_S,
               min(_COALESCE_WINDOW_CEIL_S, half_cycle_s * _COALESCE_HALF_CYCLE_FRACTION))


def apply_derived_coalesce_window(extractor, calib, log=print):
    """Plumb the derived coalesce window onto an extractor that coalesces (Modbus).

    Neutral: extractors without a coalesce_window_s config (OPC UA) are left untouched. Called once,
    after OBSERVE calibration and before the LEARN/EVALUATE captures, so those captures coalesce
    with the process-scaled window.
    """
    cfg = getattr(extractor, "config", None)
    if cfg is None or not hasattr(cfg, "coalesce_window_s"):
        return
    w = derive_coalesce_window_s(calib.period_samples, calib.dt, calib.measurable,
                                 cfg.coalesce_window_s)
    cfg.coalesce_window_s = w
    if calib.measurable:
        log(f"[calibrate] coalesce_window_s -> {w:.3f}s "
            f"(half-cycle {calib.period_samples * calib.dt:.2f}s / 20, clamped "
            f"{_COALESCE_WINDOW_FLOOR_S}..{_COALESCE_WINDOW_CEIL_S})")
    else:
        log(f"[calibrate] coalesce_window_s -> {w:.3f}s (period not measurable; config default)")


def discover_and_calibrate(extractor, events, profile_path=None, log=print, emitter=None):
    """First pass: discover the state flow, then auto-calibrate the phase params from it.

    Returns (CalibrationResult, DiscoveryResult, state_flow) or (None, DiscoveryResult, None) when
    no state signal is discovered.
    """
    if not hasattr(extractor, "group_flows"):
        raise RuntimeError(f"{extractor.name} does not support state-signal discovery yet")

    flows = extractor.group_flows(events)
    if emitter:
        for fl in flows:
            emitter.flow_found(fl)
    disc = classify_flows(flows)
    if emitter:
        for v in disc.flows:
            emitter.variable_found(v)

    log("[discover] flows:")
    for v in disc.flows:
        f = v.features
        log(f"  {v.verdict:17s} {v.key:26s} role={v.role_hint:9s} "
            f"unique={f.unique_values} range={f.value_range:.2f} step={f.median_step:.4f} rev={f.reversals}")

    if disc.state_flow_key is None:
        log("[discover] no state signal found; extend --observe to cover >=1.5 process cycles")
        return None, disc, None

    state_flow = next(f for f in flows if f.key == disc.state_flow_key)
    if emitter:
        emitter.state_signal_discovered(state_flow.key)
    log(f"[discover] state signal = {state_flow.key} ({len(state_flow.samples)} samples)")

    calib = calibrate_phase_config(state_flow.samples)
    c = calib.config
    if not calib.measurable:
        log(f"[calibrate] {calib.warning} -> using defaults")
    log(f"[calibrate] measured: move={calib.move:.3f} noise={calib.noise:.4f} P={calib.period_samples:.0f} "
        f"-> window={c.window} reversal_n={c.reversal_n} stable_n={c.stable_n} "
        f"slope={c.slope_rising:.4f} conf_full={c.conf_full_slope:.3f}")

    if profile_path:
        write_profile(c, profile_path)
        log(f"[calibrate] profile written -> {profile_path}")

    return calib, disc, state_flow


def _feed_or_judge(extractor, evt, tracker, grammar, learner=None, log=None, emitter=None, state_key=None):
    """Advance the tracker with any state samples, or learn/judge a write. Returns a Verdict or None."""
    samples = extractor.extract_state_samples(evt)
    if samples:
        for s in samples:
            tracker.update(s)
            if emitter:
                emitter.phase(tracker, state_key)     # de-duped: emits only on phase change
                emitter.variable_value(state_key, s)  # live current value of the state variable
        return None
    if evt.op == "WRITE_REQUEST" and evt.target is not None:
        if emitter:
            key = _variable_key(extractor, evt)
            # A write is protocol proof the key is a command. command_found surfaces it once with its
            # real datatype from the extractor: it announces a command discovery never saw, and also
            # RE-announces one discovery mislabelled as a non-command nature (e.g. a read-only
            # register seen as CONSTANT_METADATA). It suppresses repeats and never reclassifies the
            # state signal. The datatype source is per-protocol; the observer only asks (null-safe).
            emitter.command_found(key, *_write_datatype(extractor, evt))
            # last written value of this command variable (key as group_flows produced it)
            emitter.variable_value(key, evt.value)
        if learner is not None:
            learned = learner.observe(evt.target, tracker.phase, tracker.confidence, tracker.transitioning)
            if emitter and learned:
                emitter.grammar_learned(evt.target, tracker.phase)
            return None
        v = evaluate(grammar, evt.target, tracker.phase, tracker.confidence, tracker.transitioning)
        if log:
            # Name the silo (evt.server) so a verdict is attributable to an endpoint: a COHERENT at
            # one silo and an INCOHERENT at another for the same key are two grammars, not a conflict.
            log(f"[{v.verdict:11s}] {evt.server} write {evt.target} phase={tracker.phase} "
                f"conf={tracker.confidence:.2f} | {v.rule} | {v.reason}")
        if emitter:
            emitter.verdict(evt.target, tracker.phase, v)
        return v
    return None


def _variable_key(extractor, evt):
    """The variable key for an event, if the extractor exposes the mapping; else None."""
    fn = getattr(extractor, "variable_key", None)
    return fn(evt) if fn else None


def _write_datatype(extractor, evt):
    """(datatype, certain) a write declares, if the extractor answers; else (None, False).

    Duck-typed like _variable_key: an extractor that does not implement write_datatype degrades to
    "?" and the observer never needs to know it exists. Keeps the observer protocol-agnostic.
    """
    fn = getattr(extractor, "write_datatype", None)
    return fn(evt) if fn else (None, False)


def run_learn(extractor, events, tracker, emitter=None, state_key=None):
    """Learn mode: feed state samples, accumulate the artefact->phase grammar from writes."""
    learner = GrammarLearner()
    for evt in events:
        _feed_or_judge(extractor, evt, tracker, None, learner=learner, emitter=emitter, state_key=state_key)
    return learner.export_document()


def run_evaluate(extractor, events, tracker, grammar, log=None, emitter=None, state_key=None):
    """Evaluate mode: feed state samples, judge each write against the learned grammar."""
    out = []
    for evt in events:
        v = _feed_or_judge(extractor, evt, tracker, grammar, log=log, emitter=emitter, state_key=state_key)
        if v is not None:
            out.append(v)
    return out


# Observe/learn/evaluate all run in ONE selector loop (see _Run and multiplex below). The no-re-
# learning invariant holds in evaluate: grammars are frozen (learner=None), so an anomaly seen while
# evaluating is never absorbed into "normal".


# --------------------------------------------------------------------------- silos (demux by observed endpoint)

@dataclass
class Silo:
    """One unit of evaluation: a (protocol, server endpoint) with its OWN calibration, tracker,
    state signal, grammar and event stream.

    Silo identity is DISCOVERED from traffic -- a silo appears when a previously unseen evt.server is
    observed -- never configured, the same discipline as the state signal and phase params. The
    endpoint is the demux key; two conversations to different endpoints are two silos even under one
    capture filter (e.g. "tcp port 102" legitimately covers several S7 servers).

    EVERY run goes through silos: one silo is simply N=1. Each silo's `emitter` is a bound view of
    the run's single Emitter (emitter.bind(tag)); de-dup is tag-keyed inside that one Emitter, so
    phase/value de-duplication never crosses silos even though flow keys are not endpoint-qualified.
    At N=1 the tag is None and output is byte-identical to the pre-silo observer. See build_silos for
    the two remaining, deliberate N=1 differences (silo() suppression -- 2b debt -- and --profile).
    """
    endpoint: str
    extractor: object = None    # the extractor that parsed this silo's events (multi-protocol: N run at once)
    events: list = field(default_factory=list)
    calib: object = None
    state_flow: object = None
    state_key: object = None
    tracker: object = None
    learner: object = None      # GrammarLearner during the learn window (streamed, per silo)
    grammar: dict = field(default_factory=dict)
    doc: object = None          # full learn document (for the single-silo learn-to-file flow)
    emitter: object = None
    evaluable: bool = False
    reason: object = None


def partition_by_server(events):
    """Group events by observed server endpoint (evt.server), preserving first-seen order.

    The demux key IS the silo identity. Every event from all three extractors carries evt.server
    (set in parse_line for every op), so in practice there is no orphan bucket; defensively, an event
    without a server lands under a None key for the caller to REPORT rather than silently drop -- an
    event with no endpoint cannot be assigned to a silo.
    """
    buckets: "OrderedDict[object, list]" = OrderedDict()
    for evt in events:
        buckets.setdefault(getattr(evt, "server", None), []).append(evt)
    return buckets


def _calibrate_silo(extractor, silo, log, profile_path=None):
    """Discover + calibrate ONE silo from its own event partition. Sets evaluable/reason.

    A silo is evaluable iff a state signal was DISCOVERED (calib is not None) -- exactly the
    pre-silo observer's gate (v2-dev: ``if calib is None: return 2``). A discovered-but-UNMEASURABLE
    period (fewer than ~1.5 cycles in the window) is NOT a reason to decline: v2-dev proceeds on the
    default phase config with a warning, and continuous evaluation after learn is the default single
    run, so declining here would exit the run after observe and break that continuity. The warning is
    still surfaced via ``reason`` -- an honest note on an evaluable silo -- so nothing is hidden.

    Only a silo with NO state signal at all (calib is None) is non-evaluable, and it is reported, not
    silently dropped.
    """
    log(f"[silo] {silo.endpoint}: {len(silo.events)} observe events")
    calib, _disc, state_flow = discover_and_calibrate(
        extractor, silo.events, profile_path=profile_path, log=log, emitter=silo.emitter)
    if calib is None:
        silo.reason = "no state signal discovered (needs a bounded, cycling signal within --observe)"
        return
    if not calib.measurable:
        # Discovered but the period could not be measured: proceed on default config (as v2-dev does),
        # recording the warning. Evaluable stays True so the single-silo run reaches learn+evaluate.
        silo.reason = calib.warning or "phase period not measurable; using default phase config"
    silo.calib = calib
    silo.state_flow = state_flow
    silo.state_key = state_flow.key
    silo.tracker = PhaseTracker(calib.config)
    silo.evaluable = True


def build_silos(extractor, observe_events, emitter, log, profile_path=None):
    """Partition the observe window by server and discover+calibrate each silo independently.

    EVERY run comes through here; one silo is N=1. Returns the Silos in first-seen order.

    Every silo binds its OWN endpoint as the emitter tag (emitter.bind(endpoint)), N=1 included, so
    every per-silo event carries its silo tag and the silo() announcement is emitted for every silo.
    The UI reads the tag rather than inferring the silo. This is deliberate: an "untagged => the sole
    silo" inference would be correct only as long as a second endpoint that first speaks during learn
    or evaluate is skipped by _route_or_report_late (it is today), i.e. it would depend on a runtime
    gate rather than on the data -- exactly the kind of invariant 2b will change. Tagging makes the
    data self-describing. (Both the tag and the announcement retire former N=1 special-cases that
    existed only to keep the refactor byte-identical, now met and merged.) One difference remains:

      --profile is honoured for N=1 only. A real limitation (one path cannot hold N profiles),
      reported rather than hidden -- with N>1 the profile is simply not written.
    """
    buckets = partition_by_server(observe_events)
    multi = len(buckets) > 1
    label = _protocol_label(extractor)
    silos = []
    for endpoint, evs in buckets.items():
        if endpoint is None:
            log(f"[silo] {len(evs)} event(s) with no server endpoint -> unassignable, reported not evaluated")
            # Read (the events parsed) but unassignable to a silo -> unevaluable, not unreadable.
            emitter.silo(None, evaluable=False, reason="event carried no server endpoint",
                         protocol=label, kind="unevaluable")
            continue
        silo = Silo(endpoint=endpoint, extractor=extractor, events=evs, emitter=emitter.bind(endpoint))
        _calibrate_silo(extractor, silo, log, profile_path=(None if multi else profile_path))
        # The peer(s): the distinct client HOST(s) observed talking to this silo (evt.client is set
        # by every extractor; the ephemeral client port is dropped). Observed, not configured.
        peers = sorted({e.client.rsplit(":", 1)[0] for e in evs if getattr(e, "client", None)})
        # Every silo is announced (N=1 included): the UI keys everything by silo. A non-evaluable
        # silo here was READ (its flows were discovered) but has no state signal -> "unevaluable",
        # distinct from an unreadable endpoint. It carries its own protocol label.
        emitter.silo(silo.endpoint, evaluable=silo.evaluable, reason=silo.reason, protocol=label,
                     kind=(None if silo.evaluable else "unevaluable"), peer=", ".join(peers) or None)
        silos.append(silo)
    return silos


def _route_or_report_late(emitter, silos_by_ep, reported_late, endpoint):
    """Return the evaluable Silo for an event's endpoint, or None if it cannot be judged.

    A silo first seen AFTER the observe window (never in silos_by_ep) is REPORTED once as
    existing-but-not-evaluable -- too late to calibrate -- rather than silently skipped, the same
    honesty as late-command and UNCLAIMED reporting elsewhere. An observed-but-not-evaluable silo was
    already reported at build time, so it is skipped here without a duplicate announcement.
    """
    silo = silos_by_ep.get(endpoint)
    if silo is not None:
        return silo if silo.evaluable else None
    if endpoint not in reported_late:
        reported_late.add(endpoint)
        # We read it -- it simply arrived too late to calibrate. Unevaluable, not unreadable.
        emitter.silo(endpoint, evaluable=False, kind="unevaluable",
                     reason="silo first seen after the observe window; too late to calibrate")
    return None


class _Run:
    """The observe -> learn -> evaluate pipeline as ONE selector-loop driver over N tshark.

    dispatch() is the per-event callback (routes by the current phase); tick() is the between-select
    check that advances the phase at each window's deadline. The windows are deadlines in a single
    loop, so the N tshark stay alive throughout -- no gap at a phase boundary drops traffic. No
    re-learning during evaluate: grammars are frozen (learner=None), so an anomaly seen while
    evaluating can never be absorbed into "normal".
    """

    def __init__(self, sources, args, emitter, log):
        self.args, self.emitter, self.log = args, emitter, log
        self.extractors = [s.extractor for s in sources]
        self.phase = "observe"
        now = time.time()
        self.observe_end = now + args.observe
        self.learn_end = (self.observe_end + args.learn) if args.learn is not None else None
        self.obs_by_ext = {}              # extractor -> [observe-window events]
        self.silos, self.silos_by_ep, self.evaluable = [], {}, []
        self.reported_late = set()
        self.stop, self.rc = False, 0
        emitter.stage("observe", seconds=args.observe)
        log(f"[observe] {args.observe:.0f}s -- discover + calibrate each silo")

    # -- per-event dispatch (by phase) ------------------------------------
    def dispatch(self, extractor, evt):
        if self.phase == "observe":
            self.obs_by_ext.setdefault(extractor, []).append(evt)
            return
        silo = _route_or_report_late(self.emitter, self.silos_by_ep, self.reported_late,
                                     getattr(evt, "server", None))
        if silo is None:
            return
        if self.phase == "learn":
            _feed_or_judge(silo.extractor, evt, silo.tracker, None, learner=silo.learner,
                           emitter=silo.emitter, state_key=silo.state_key)
        elif self.phase == "evaluate":
            _feed_or_judge(silo.extractor, evt, silo.tracker, silo.grammar, log=self.log,
                           emitter=silo.emitter, state_key=silo.state_key)

    # -- between-select deadline check (advances the phase) ---------------
    def tick(self):
        now = time.time()
        if self.phase == "observe" and now >= self.observe_end:
            self._end_observe()
        elif self.phase == "learn" and self.learn_end is not None and now >= self.learn_end:
            self._end_learn()
        return self.stop

    def _end_observe(self):
        args, emitter, log = self.args, self.emitter, self.log
        single = len(self.extractors) == 1
        for ext in self.extractors:
            silos = build_silos(ext, self.obs_by_ext.get(ext, []), emitter, log,
                                profile_path=(args.profile if single else None))
            self.silos.extend(silos)
            if not any(s.evaluable for s in silos):
                # A claimed extractor that yields no evaluable silo is a finding, not silently absent.
                # We ran the reader and read the traffic -> unevaluable, not unreadable.
                log(f"[silo] claimed {ext.name}, no evaluable silo")
                emitter.silo(None, evaluable=False, protocol=_protocol_label(ext), kind="unevaluable",
                             reason=f"claimed {ext.name}, no evaluable silo")
        self.silos_by_ep = {s.endpoint: s for s in self.silos if s.endpoint}
        self.evaluable = [s for s in self.silos if s.evaluable]
        if not self.evaluable:
            log("[silo] no evaluable silo; extend --observe to cover >=1.5 process cycles")
            self.rc, self.stop = 2, True
            return
        # Scale each coalescing extractor's window from one of its own evaluable silos (no-op for
        # extractors that do not coalesce). Per-silo windows still cannot share one config; documented.
        for ext in self.extractors:
            cal = next((s.calib for s in self.evaluable if s.extractor is ext), None)
            if cal is not None:
                apply_derived_coalesce_window(ext, cal, log=log)
        if args.learn is not None:
            emitter.stage("learn", seconds=args.learn)
            log(f"[learn] {args.learn:.0f}s -- accumulate each silo's coherence grammar")
            for s in self.evaluable:
                s.learner = GrammarLearner()
            self.phase = "learn"
        elif args.grammar:
            if not self._require_single("--grammar evaluate"):
                return
            with open(args.grammar) as f:
                self.evaluable[0].grammar = json.load(f).get("grammar", {})
            self._start_evaluate()
        else:
            log("nothing to do: pass --learn <secs> for observe->learn->evaluate, or --grammar <path>")
            self.rc, self.stop = 1, True

    def _end_learn(self):
        args, log = self.args, self.log
        for s in self.evaluable:
            s.doc = s.learner.export_document()
            s.grammar = s.doc.get("grammar", {})
            for k, info in s.grammar.items():
                log(f"[learn:{s.endpoint}] {k}: coherent_phases={info.get('learned_coherent_phases')} "
                    f"from {info.get('total_writes_observed')} writes")
        if args.grammar:
            if not self._require_single("--grammar learn-to-file"):
                return
            with open(args.grammar, "w") as f:
                json.dump(self.evaluable[0].doc, f, indent=2)
            log(f"[learn] grammar -> {args.grammar} (stopping; omit --grammar to chain into evaluate)")
            self.rc, self.stop = 0, True
            return
        self._start_evaluate()

    def _require_single(self, what):
        """The --grammar file flows hold ONE silo's grammar; with several evaluable silos, report and
        stop rather than pick one silently."""
        if len(self.evaluable) == 1:
            return True
        self.log(f"[silo] {what} is single-silo only; {len(self.evaluable)} evaluable silos reported, "
                 f"none read/written")
        self.rc, self.stop = 1, True
        return False

    def _start_evaluate(self):
        self.emitter.stage("evaluate")
        self.log(f"[evaluate] continuous, {len(self.evaluable)} evaluable silo(s) -- each write judged "
                 f"against its OWN silo's grammar (Ctrl-C to stop)")
        self.phase = "evaluate"


# --------------------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Liscere passive observer — probe the wire, run every protocol found, "
                    "observe -> learn -> continuous evaluate (Ctrl-C to stop)")
    ap.add_argument("--iface", required=True, help="capture interface (mirror port)")
    ap.add_argument("--probe", type=float, default=15.0,
                    help="probe-window seconds: discover which protocols are on the wire before running "
                         "their extractors (a protocol silent during the probe is never claimed)")
    ap.add_argument("--observe", type=float, default=60.0, help="phase 1: observe window seconds (discover + calibrate)")
    ap.add_argument("--learn", type=float, default=None,
                    help="phase 2: learn-window seconds; then phase 3 evaluates continuously until Ctrl-C")
    ap.add_argument("--grammar", default=None,
                    help="with --learn: write the learned grammar here and stop (legacy learn-to-file); "
                         "without --learn: load this grammar and evaluate continuously (single silo)")
    ap.add_argument("--profile", default=None, help="optional path to write the auto-calibrated phase profile")
    ap.add_argument("--emit-json", action=argparse.BooleanOptionalAction, default=True,
                    help="emit structured discovery events as JSON Lines on stdout (human logs go to "
                         "stderr). --no-emit-json restores plain human output on stdout, no JSON.")
    args = ap.parse_args(argv)

    # With JSON emission on, stdout is a pure JSON stream and human logs go to stderr, so
    # `liscere_observe.py 2>/dev/null | ui` yields clean JSON. With --no-emit-json, behave as before.
    log = (lambda m: print(m, file=sys.stderr)) if args.emit_json else print
    emitter = Emitter(enabled=args.emit_json)

    # PROBE: discover which protocols are on the wire; surface traffic no extractor can read.
    claimed, _unclaimed = probe_layers(args.iface, args.probe, log=log, emitter=emitter)
    if not claimed:
        log(f"[probe] no known protocol claimed in {args.probe:.0f}s -- nothing to run")
        print(f"no known protocol on the wire during the {args.probe:.0f}s probe window; "
              f"extend --probe for slow-polling devices, or check the mirror port.", file=sys.stderr)
        return 2

    # Spawn one tshark per claimed extractor. extractor_for_layer maps a wire layer to its extractor.
    sources = []
    for layer in sorted(claimed):
        ext = extractor_for_layer(layer)
        if ext is None or not hasattr(ext, "group_flows"):
            log(f"[probe] claimed layer {layer!r} has no runnable extractor; skipped")
            continue
        emitter.protocol_seen(ext)
        sources.append(_Source(ext, _spawn(ext, args.iface)))
    if not sources:
        print("claimed layers have no runnable extractor", file=sys.stderr)
        return 2
    log(f"[run] {len(sources)} extractor(s): {', '.join(s.extractor.name for s in sources)}")

    run = _Run(sources, args, emitter, log)
    try:
        # ONE selector loop over the N tshark. run.dispatch handles each event; run.tick advances the
        # phase at each window's deadline. multiplex kills every tshark on exit (normal or Ctrl-C).
        stopped = multiplex(sources, on_event=run.dispatch, emitter=emitter, log=log, until=run.tick)
    except KeyboardInterrupt:
        log("[evaluate] stopped")
        return run.rc
    if not stopped:
        # multiplex fell through because the selector emptied: every tshark exited before any phase
        # asked to stop. For a continuous monitor that is a CAPTURE FAILURE, not a clean end -- make it
        # loud and non-zero so it is visible and a supervisor backs off, instead of a silent exit that
        # (under restart supervision) re-probes and re-spawns captures in a churn.
        log("[capture] every tshark exited; capture lost -- the observer is no longer monitoring")
        print("capture lost: every tshark exited (interface/mirror down, or a child was killed) "
              "before a stop was requested. Nothing is being observed.", file=sys.stderr)
        return 3
    return run.rc


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        # Ctrl-C outside the evaluate loop (e.g. during observe/learn): exit cleanly, no traceback.
        sys.exit(130)
