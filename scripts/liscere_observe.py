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

    def __init__(self, enabled=True, out=None):
        self.enabled = enabled
        self.out = out or sys.stdout
        self._last_phase = None

    def _emit(self, event):
        if self.enabled:
            emit(event, out=self.out)

    def protocol_seen(self, extractor):
        event = {"type": "protocol_seen", "protocol": extractor.name.split("/")[0].upper()}
        port = getattr(getattr(extractor, "config", None), "port", None)
        if port is not None:
            event["port"] = port
        self._emit(event)

    def flow_found(self, flow):
        self._emit({"type": "flow_found", "key": flow.key, "role_hint": flow.role_hint})

    def flow_classified(self, verdict):
        f = verdict.features
        self._emit({
            "type": "flow_classified", "key": verdict.key, "verdict": verdict.verdict,
            "features": {
                "unique_values": f.unique_values,
                "value_range": round(f.value_range, 4),
                "median_step": round(f.median_step, 4),
                "reversals": f.reversals,
            },
        })

    def state_signal_discovered(self, key):
        self._emit({"type": "state_signal_discovered", "key": key})

    def phase(self, tracker):
        if tracker.phase != self._last_phase:
            self._last_phase = tracker.phase
            level = tracker.last_level
            self._emit({
                "type": "phase", "phase": tracker.phase,
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


def capture_events(extractor, iface, seconds, log=print):
    """Capture for `seconds`, returning the parsed NormalizedEvents (bounded)."""
    log(f"[capture] {seconds:.0f}s on {iface} ({extractor.name}, filter='{extractor.capture_filter()}')")
    proc = _spawn(extractor, iface)
    events = []
    start = time.time()
    try:
        for line in proc.stdout:
            evt = extractor.parse_line(line.rstrip("\n").split("\t"))
            if evt is not None:
                events.append(evt)
            if time.time() - start >= seconds:
                break
    finally:
        _kill(proc)
    log(f"[capture] {len(events)} events")
    return events


def capture_stream(extractor, iface):
    """Yield parsed NormalizedEvents until interrupted (for continuous evaluate)."""
    proc = _spawn(extractor, iface)
    try:
        for line in proc.stdout:
            evt = extractor.parse_line(line.rstrip("\n").split("\t"))
            if evt is not None:
                yield evt
    finally:
        _kill(proc)


# --------------------------------------------------------------------------- pipeline (pure, testable)

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
            emitter.flow_classified(v)

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


def _feed_or_judge(extractor, evt, tracker, grammar, learner=None, log=None, emitter=None):
    """Advance the tracker with any state samples, or learn/judge a write. Returns a Verdict or None."""
    samples = extractor.extract_state_samples(evt)
    if samples:
        for s in samples:
            tracker.update(s)
            if emitter:
                emitter.phase(tracker)   # de-duped: emits only on phase change
        return None
    if evt.op == "WRITE_REQUEST" and evt.target is not None:
        if learner is not None:
            learned = learner.observe(evt.target, tracker.phase, tracker.confidence, tracker.transitioning)
            if emitter and learned:
                emitter.grammar_learned(evt.target, tracker.phase)
            return None
        v = evaluate(grammar, evt.target, tracker.phase, tracker.confidence, tracker.transitioning)
        if log:
            log(f"[{v.verdict:11s}] write {evt.target} phase={tracker.phase} "
                f"conf={tracker.confidence:.2f} | {v.rule} | {v.reason}")
        if emitter:
            emitter.verdict(evt.target, tracker.phase, v)
        return v
    return None


def run_learn(extractor, events, tracker, emitter=None):
    """Learn mode: feed state samples, accumulate the artefact->phase grammar from writes."""
    learner = GrammarLearner()
    for evt in events:
        _feed_or_judge(extractor, evt, tracker, None, learner=learner, emitter=emitter)
    return learner.export_document()


def run_evaluate(extractor, events, tracker, grammar, log=None, emitter=None):
    """Evaluate mode: feed state samples, judge each write against the learned grammar."""
    out = []
    for evt in events:
        v = _feed_or_judge(extractor, evt, tracker, grammar, log=log, emitter=emitter)
        if v is not None:
            out.append(v)
    return out


# --------------------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="Liscere passive observer (discover -> calibrate -> learn/evaluate)")
    ap.add_argument("--iface", required=True, help="capture interface (mirror port)")
    ap.add_argument("--observe", type=float, default=60.0, help="observe window seconds (discover + calibrate)")
    ap.add_argument("--learn", type=float, default=None, help="learn-mode window seconds (writes grammar)")
    ap.add_argument("--grammar", default=None, help="grammar JSON: input for evaluate, or output path for --learn")
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

    obs = capture_events(extractor, args.iface, args.observe, log=log)
    calib, _disc, _state = discover_and_calibrate(extractor, obs, profile_path=args.profile, log=log, emitter=emitter)
    if calib is None:
        return 2
    tracker = PhaseTracker(calib.config)

    if args.learn is not None:
        ev = capture_events(extractor, args.iface, args.learn, log=log)
        doc = run_learn(extractor, ev, tracker, emitter=emitter)
        out = args.grammar or "learned_grammar.json"
        with open(out, "w") as f:
            json.dump(doc, f, indent=2)
        log(f"[learn] grammar -> {out}")
        for k, info in doc.get("grammar", {}).items():
            log(f"  {k}: coherent_phases={info.get('learned_coherent_phases')} "
                f"from {info.get('total_writes_observed')} writes")
        return 0

    if args.grammar:
        with open(args.grammar) as f:
            grammar = json.load(f).get("grammar", {})
        log("[evaluate] judging writes against learned grammar (Ctrl-C to stop)")
        try:
            run_evaluate(extractor, capture_stream(extractor, args.iface), tracker, grammar, log=log, emitter=emitter)
        except KeyboardInterrupt:
            log("[evaluate] stopped")
        return 0

    print("nothing to do: pass --learn <secs> or --grammar <path>", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
