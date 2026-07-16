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

Usage:
    OTLAB_PROTOCOL=opcua python scripts/liscere_observe.py --iface en6 --observe 60 --learn 120
    OTLAB_PROTOCOL=opcua python scripts/liscere_observe.py --iface en6 --observe 60 --grammar learned_grammar.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, field

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from otlab_core.engine.autocalibrate import calibrate_phase_config, write_profile
from otlab_core.engine.discover import classify_flows
from otlab_core.engine.evaluator import evaluate
from otlab_core.engine.grammar import GrammarLearner
from otlab_core.engine.phase_tracker import PhaseTracker
from otlab_core.extractors import get_extractor


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

    def stage(self, stage):
        # A pipeline-stage transition ("observe" | "learn" | "evaluate"), emitted once per move so
        # the UI header can reflect the current stage instead of always reading "LIVE".
        self._emit({"type": "stage", "stage": stage})

    def silo(self, endpoint, evaluable, reason=None):
        # A discovered silo -- one (protocol, server endpoint) as its own unit of evaluation. An
        # ADDITIVE event type, emitted ONLY when more than one silo is present, so the single-silo
        # stream stays byte-identical to prior behaviour. `evaluable` is False for a silo that exists
        # but could not be calibrated (too few samples); `reason` states why, so it is reported and
        # never silently dropped.
        self._emit({"type": "silo", "endpoint": endpoint, "evaluable": evaluable, "reason": reason})

    def opaque_endpoint(self, endpoint, reason):
        # An endpoint whose traffic arrived on the capture filter but which no extractor can read
        # (e.g. an encrypted variant on the service port). Reported ONCE per endpoint as a silo that
        # exists but is not evaluable -- so hundreds of opaque frames yield a single event -- and
        # never routed to a tracker. Dedup lives in the shared bookkeeping, alongside announced keys.
        if not endpoint or endpoint in self._shared["opaque"]:
            return
        self._shared["opaque"].add(endpoint)
        self.silo(endpoint, evaluable=False, reason=reason)

    def protocol_seen(self, extractor):
        event = {"type": "protocol_seen", "protocol": extractor.name.split("/")[0].upper()}
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
    return subprocess.Popen(
        _tshark_cmd(extractor, iface),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
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


def _postprocess(extractor, events):
    """Apply any extractor-provided stream stage (e.g. Modbus write coalescing).

    Neutral hook: an extractor may expose ``coalesce_writes(events) -> events`` to fold wire-level
    repetition. Extractors without it (OPC UA) are passed through unchanged. This keeps all
    protocol-specific stream logic in the extractor while the observer stays protocol-neutral.
    """
    stage = getattr(extractor, "coalesce_writes", None)
    return stage(events) if callable(stage) else events


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


def capture_events(extractor, iface, seconds, log=print, emitter=None):
    """Capture for `seconds`, returning the parsed NormalizedEvents (bounded).

    A frame parse_line declines is offered to _report_opaque: if the extractor recognises it as an
    endpoint carrying traffic it cannot read, that endpoint is reported once (never as an event).
    """
    log(f"[capture] {seconds:.0f}s on {iface} ({extractor.name}, filter='{extractor.capture_filter()}')")
    proc = _spawn(extractor, iface)
    start = time.time()

    def _parsed():
        for line in proc.stdout:
            cols = line.rstrip("\n").split("\t")
            evt = extractor.parse_line(cols)
            if evt is not None:
                yield evt
            else:
                _report_opaque(extractor, cols, emitter)
            if time.time() - start >= seconds:
                break

    events = []
    try:
        for evt in _postprocess(extractor, _parsed()):
            events.append(evt)
    finally:
        _kill(proc)
    log(f"[capture] {len(events)} events")
    return events


def capture_stream(extractor, iface, emitter=None):
    """Yield parsed NormalizedEvents until interrupted (for continuous evaluate).

    As in capture_events, a declined frame is offered to _report_opaque so an endpoint that goes
    encrypted mid-run is reported the first time it is seen.
    """
    proc = _spawn(extractor, iface)

    def _parsed():
        for line in proc.stdout:
            cols = line.rstrip("\n").split("\t")
            evt = extractor.parse_line(cols)
            if evt is not None:
                yield evt
            else:
                _report_opaque(extractor, cols, emitter)

    try:
        yield from _postprocess(extractor, _parsed())
    finally:
        _kill(proc)


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


# Phase-3 continuous evaluation lives in evaluate_continuous_silos (below), which routes each event
# to its silo. Its no-re-learning invariant (grammars are read-only; an anomaly seen during evaluate
# is never absorbed into "normal") is unchanged from the pre-silo single-tracker version.


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
    events: list = field(default_factory=list)
    calib: object = None
    state_flow: object = None
    state_key: object = None
    tracker: object = None
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
    silos = []
    for endpoint, evs in buckets.items():
        if endpoint is None:
            log(f"[silo] {len(evs)} event(s) with no server endpoint -> unassignable, reported not evaluated")
            emitter.silo(None, evaluable=False, reason="event carried no server endpoint")
            continue
        silo = Silo(endpoint=endpoint, events=evs, emitter=emitter.bind(endpoint))
        _calibrate_silo(extractor, silo, log, profile_path=(None if multi else profile_path))
        # Every silo is announced (N=1 included): the UI keys everything by silo.
        emitter.silo(silo.endpoint, evaluable=silo.evaluable, reason=silo.reason)
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
        emitter.silo(endpoint, evaluable=False,
                     reason="silo first seen after the observe window; too late to calibrate")
    return None


def evaluate_continuous_silos(extractor, iface, emitter, silos_by_ep, reported_late, log):
    """Phase 3: watch continuously and judge every write against its silo's FROZEN grammar.

    DESIGN INVARIANT — no re-learning in phase 3. Grammars are read-only here (run through
    _feed_or_judge with no GrammarLearner), so an anomalous action seen during evaluate can never be
    absorbed into "normal". One stream, one extractor; each event is routed to its silo by server. A
    silo that cannot be judged (unknown/late/not-evaluable) is reported once and skipped, never
    misrouted to another silo's tracker. On Ctrl-C the stream is torn down and we return cleanly.
    """
    if emitter:
        emitter.stage("evaluate")
    n = sum(1 for s in silos_by_ep.values() if s.evaluable)
    log(f"[evaluate] continuous, {n} evaluable silo(s) -- each write judged against its OWN silo's "
        f"grammar (Ctrl-C to stop)")
    try:
        for evt in capture_stream(extractor, iface, emitter=emitter):
            silo = _route_or_report_late(emitter, silos_by_ep, reported_late, getattr(evt, "server", None))
            if silo is None:
                continue
            _feed_or_judge(extractor, evt, silo.tracker, silo.grammar,
                           log=log, emitter=silo.emitter, state_key=silo.state_key)
    except KeyboardInterrupt:
        log("[evaluate] stopped")
    return 0


# --------------------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Liscere passive observer — single run: observe -> learn -> continuous evaluate (Ctrl-C to stop)")
    ap.add_argument("--iface", required=True, help="capture interface (mirror port)")
    ap.add_argument("--observe", type=float, default=60.0, help="phase 1: observe window seconds (discover + calibrate)")
    ap.add_argument("--learn", type=float, default=None,
                    help="phase 2: learn-window seconds; then phase 3 evaluates continuously until Ctrl-C")
    ap.add_argument("--grammar", default=None,
                    help="with --learn: write the learned grammar here and stop (legacy learn-to-file); "
                         "without --learn: load this grammar and evaluate continuously")
    ap.add_argument("--profile", default=None, help="optional path to write the auto-calibrated phase profile")
    ap.add_argument("--emit-json", action=argparse.BooleanOptionalAction, default=True,
                    help="emit structured discovery events as JSON Lines on stdout (human logs go to "
                         "stderr). --no-emit-json restores plain human output on stdout, no JSON.")
    args = ap.parse_args(argv)

    # With JSON emission on, stdout is a pure JSON stream and human logs go to stderr, so
    # `liscere_observe.py 2>/dev/null | ui` yields clean JSON. With --no-emit-json, behave as before.
    log = (lambda m: print(m, file=sys.stderr)) if args.emit_json else print
    emitter = Emitter(enabled=args.emit_json)

    extractor = get_extractor(os.getenv("OTLAB_PROTOCOL", "opcua"))
    if not hasattr(extractor, "group_flows"):
        print(f"protocol {extractor.name} does not support state-signal discovery yet", file=sys.stderr)
        return 2
    emitter.protocol_seen(extractor)

    # Phase 1 — OBSERVE: capture, then demux into silos and discover+calibrate each. Every run goes
    # through silos; the normal bench case is simply N=1, which build_silos keeps byte-identical.
    emitter.stage("observe")
    obs = capture_events(extractor, args.iface, args.observe, log=log, emitter=emitter)
    silos = build_silos(extractor, obs, emitter, log, profile_path=args.profile)
    evaluable = [s for s in silos if s.evaluable]
    if not evaluable:
        # No silo could be calibrated. For N=1 this is exactly the old "no state signal -> return 2"
        # (discovery events were still emitted by build_silos); for N>1 every silo was reported.
        log("[silo] no evaluable silo; extend --observe to cover >=1.5 process cycles")
        return 2

    # Scale the write-coalescing window to the observed process (no-op for extractors that do not
    # coalesce). One shared extractor.config cannot hold a per-silo window yet, so with N>1 this uses
    # the first evaluable silo (documented limitation); with N=1 it is exactly the old single call.
    apply_derived_coalesce_window(extractor, evaluable[0].calib, log=log)

    silos_by_ep = {s.endpoint: s for s in silos}   # includes non-evaluable, so they are not re-reported
    reported_late = set()

    if args.learn is not None:
        # Phase 2 — LEARN: learn each silo's coherence grammar from this window. Temporal trust: the
        # environment is controlled during learn, so what is seen here is the baseline "normal".
        emitter.stage("learn")
        ev = capture_events(extractor, args.iface, args.learn, log=log, emitter=emitter)
        for endpoint, evs in partition_by_server(ev).items():
            silo = _route_or_report_late(emitter, silos_by_ep, reported_late, endpoint)
            if silo is None:
                continue
            silo.doc = run_learn(extractor, evs, silo.tracker, emitter=silo.emitter, state_key=silo.state_key)
            silo.grammar = silo.doc.get("grammar", {})
            for k, info in silo.grammar.items():
                log(f"[learn:{endpoint}] {k}: coherent_phases={info.get('learned_coherent_phases')} "
                    f"from {info.get('total_writes_observed')} writes")

        if args.grammar:
            # Legacy learn-to-file flow: write the grammar and stop (no evaluate). One file holds one
            # silo's document, so this is single-silo only; with N>1 the silos were reported but the
            # file is not written (a documented phase 2a-1 limitation, not a silent drop).
            if len(evaluable) != 1:
                log(f"[silo] --grammar learn-to-file is single-silo only; {len(evaluable)} evaluable "
                    f"silos were reported but no grammar file was written")
                return 1
            with open(args.grammar, "w") as f:
                json.dump(evaluable[0].doc, f, indent=2)
            log(f"[learn] grammar -> {args.grammar} (stopping; omit --grammar to chain into continuous evaluate)")
            return 0

        # Phase 3 — EVALUATE (continuous), the single-run default: freeze the just-learned grammars
        # and watch until Ctrl-C, routing each write to its silo. No re-learning.
        return evaluate_continuous_silos(extractor, args.iface, emitter, silos_by_ep, reported_late, log)

    if args.grammar:
        # Evaluate a previously saved grammar (no learn window). The grammar file is one silo's, so
        # this is single-silo only; with N>1 the silos were reported but the file is not loaded.
        if len(evaluable) != 1:
            log(f"[silo] --grammar evaluate is single-silo only; {len(evaluable)} evaluable silos "
                f"were reported but no grammar file was loaded")
            return 1
        with open(args.grammar) as f:
            evaluable[0].grammar = json.load(f).get("grammar", {})
        return evaluate_continuous_silos(extractor, args.iface, emitter, silos_by_ep, reported_late, log)

    print("nothing to do: pass --learn <secs> for a single observe->learn->evaluate run, "
          "or --grammar <path> to evaluate a saved grammar", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        # Ctrl-C outside the evaluate loop (e.g. during observe/learn): exit cleanly, no traceback.
        sys.exit(130)
