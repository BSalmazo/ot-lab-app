"""liscere-metrics: false positives and false negatives of a run against labelled scenarios.

A scenario file (TOML, read with the standard library) labels supervisory writes with the verdict a
domain expert expects. This command matches every verdict a run produced (``verdicts.jsonl``) to
those labels and counts, per scenario and for the run as a whole:

- the full table of expected {COHERENT, INCOHERENT} against observed {COHERENT, UNUSUAL, INCOHERENT,
  UNCERTAIN}, always published;
- the headline rates under the strict rule: only INCOHERENT is an alarm. A labelled-incoherent write
  judged anything else is a false negative; a labelled-coherent write judged INCOHERENT is a false
  positive. Rates carry their denominators;
- the abstention rate: the share of matched verdicts that were UNCERTAIN;
- the alternative headlines (alarm-inclusive: UNUSUAL also counts as an alarm; abstention: UNCERTAIN
  excluded from both denominators), derived from the same table so a reader can compare;
- what did not match: verdicts no label covers, and labels no verdict reached (a write the engine
  never judged).

Matching: a verdict matches a label when the target is equal, the value is equal if the label gives
one, and the write's frame time lies in [from, to). Times are absolute frame times, or relative to the
run's first frame with ``rel_from``/``rel_to``. The first label in file order wins if several match.

Nothing here touches the engine; it reads a run directory and writes numbers.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from collections import Counter
from typing import Any, Dict, List, Optional

EXPECTED = ("COHERENT", "INCOHERENT")
OBSERVED = ("COHERENT", "UNUSUAL", "INCOHERENT", "UNCERTAIN")
VALUE_TOL = 1e-6


class ScenarioError(ValueError):
    pass


def load_scenario(path: str) -> Dict[str, Any]:
    with open(path, "rb") as f:
        sc = tomllib.load(f)
    if "id" not in sc:
        raise ScenarioError(f"{path}: scenario needs an 'id'")
    labels = sc.get("labels") or []
    if not labels:
        raise ScenarioError(f"{path}: scenario has no [[labels]]")
    for i, lb in enumerate(labels):
        if lb.get("expected") not in EXPECTED:
            raise ScenarioError(f"{path}: label {i} expected must be one of {EXPECTED}")
        if "target" not in lb:
            raise ScenarioError(f"{path}: label {i} needs a target")
        has_abs = "from" in lb and "to" in lb
        has_rel = "rel_from" in lb and "rel_to" in lb
        if not (has_abs or has_rel):
            raise ScenarioError(f"{path}: label {i} needs from/to or rel_from/rel_to")
    sc["_path"] = path
    return sc


def load_run(run_dir: str):
    """(manifest, verdicts, first_frame_ts) from a run directory."""
    with open(os.path.join(run_dir, "run.json")) as f:
        manifest = json.load(f)
    verdicts: List[Dict[str, Any]] = []
    with open(os.path.join(run_dir, "verdicts.jsonl")) as f:
        for line in f:
            line = line.strip()
            if line:
                verdicts.append(json.loads(line))
    first = None
    events_path = os.path.join(run_dir, "events.jsonl")
    if os.path.exists(events_path):
        with open(events_path) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                ts = e.get("frame_ts")
                if ts is not None:
                    first = ts if first is None else min(first, ts)
    if first is None and verdicts:
        first = min(v["frame_ts"] for v in verdicts if v.get("frame_ts") is not None)
    return manifest, verdicts, first


def _bounds(label: Dict[str, Any], first_frame: Optional[float]):
    if "from" in label and "to" in label:
        return float(label["from"]), float(label["to"])
    if first_frame is None:
        raise ScenarioError("label uses rel_from/rel_to but the run has no frame timestamps")
    return first_frame + float(label["rel_from"]), first_frame + float(label["rel_to"])


def _matches(label: Dict[str, Any], verdict: Dict[str, Any], first_frame: Optional[float]) -> bool:
    if str(label["target"]) != str(verdict.get("target")):
        return False
    if "value" in label:
        vv = verdict.get("value")
        if vv is None:
            return False
        try:
            if abs(float(label["value"]) - float(vv)) > VALUE_TOL:
                return False
        except (TypeError, ValueError):
            return False
    if "silo" in label and label["silo"] != verdict.get("silo"):
        return False
    ts = verdict.get("frame_ts")
    if ts is None:
        return False
    lo, hi = _bounds(label, first_frame)
    return lo <= ts < hi


def evaluate_scenario(scenario: Dict[str, Any], verdicts: List[Dict[str, Any]], first_frame: Optional[float]):
    labels = scenario["labels"]
    table: Dict[str, Counter] = {e: Counter() for e in EXPECTED}
    matched, unmatched_verdicts, hit = [], [], [False] * len(labels)
    for v in verdicts:
        for i, lb in enumerate(labels):
            if _matches(lb, v, first_frame):
                hit[i] = True
                observed = v.get("result")
                if observed not in OBSERVED:
                    observed = "UNCERTAIN" if observed is None else str(observed)
                table[lb["expected"]][observed] += 1
                matched.append({"label": i, "expected": lb["expected"], "observed": observed,
                                "seq": v.get("seq"), "frame_ts": v.get("frame_ts"),
                                "target": v.get("target"), "value": v.get("value")})
                break
        else:
            unmatched_verdicts.append({"seq": v.get("seq"), "frame_ts": v.get("frame_ts"),
                                       "target": v.get("target"), "value": v.get("value"),
                                       "result": v.get("result")})
    unreached = [{"label": i, "expected": lb["expected"], "target": lb["target"], "note": lb.get("note")}
                 for i, lb in enumerate(labels) if not hit[i]]
    return {
        "scenario": scenario["id"], "description": scenario.get("description"),
        "capture": scenario.get("capture"), "labels": len(labels),
        "table": {e: {o: table[e][o] for o in OBSERVED} for e in EXPECTED},
        "counts": counts_from_table(table),
        "matched": matched, "unmatched_verdicts": unmatched_verdicts, "labels_without_verdict": unreached,
    }


def counts_from_table(table) -> Dict[str, Any]:
    """Headline (strict: only INCOHERENT is an alarm), abstention, and the alternative rules."""
    inc, coh = table["INCOHERENT"], table["COHERENT"]
    n_inc, n_coh = sum(inc.values()), sum(coh.values())

    def rule(alarm: set, exclude: set):
        tp = sum(inc[o] for o in alarm if o not in exclude)
        fn = sum(inc[o] for o in OBSERVED if o not in alarm and o not in exclude)
        fp = sum(coh[o] for o in alarm if o not in exclude)
        tn = sum(coh[o] for o in OBSERVED if o not in alarm and o not in exclude)
        return {
            "tp": tp, "fn": fn, "fp": fp, "tn": tn,
            "fp_rate": (fp / (fp + tn)) if (fp + tn) else None, "fp_denominator": fp + tn,
            "fn_rate": (fn / (fn + tp)) if (fn + tp) else None, "fn_denominator": fn + tp,
        }

    matched = n_inc + n_coh
    uncertain = inc["UNCERTAIN"] + coh["UNCERTAIN"]
    return {
        "matched_verdicts": matched,
        "labelled_incoherent": n_inc, "labelled_coherent": n_coh,
        "headline_rule": "A: only INCOHERENT is an alarm",
        "headline": rule({"INCOHERENT"}, set()),
        "abstention": {"uncertain": uncertain, "rate": (uncertain / matched) if matched else None},
        "alternatives": {
            "B_alarm_inclusive": rule({"INCOHERENT", "UNUSUAL"}, set()),
            "C_abstention_excluded": rule({"INCOHERENT", "UNUSUAL"}, {"UNCERTAIN"}),
        },
    }


def _sum_tables(results):
    total = {e: Counter() for e in EXPECTED}
    for r in results:
        for e in EXPECTED:
            for o in OBSERVED:
                total[e][o] += r["table"][e][o]
    return total


def compute(run_dir: str, scenario_paths: List[str]) -> Dict[str, Any]:
    manifest, verdicts, first_frame = load_run(run_dir)
    scenarios = [load_scenario(p) for p in scenario_paths]
    results = [evaluate_scenario(sc, verdicts, first_frame) for sc in scenarios]
    total = _sum_tables(results)
    covered = {m["seq"] for r in results for m in r["matched"]}
    return {
        "run_id": manifest.get("run_id"), "run_dir": os.path.abspath(run_dir),
        "version": manifest.get("version"), "source": manifest.get("source"), "clock": manifest.get("clock"),
        "first_frame_ts": first_frame, "verdicts_in_run": len(verdicts),
        "verdicts_matched_by_any_scenario": len(covered),
        "captures": sorted({r["capture"] for r in results if r.get("capture")}),
        "scenarios": results,
        "total": {"table": {e: {o: total[e][o] for o in OBSERVED} for e in EXPECTED},
                  "counts": counts_from_table(total)},
    }


def _fmt_rate(x):
    return "n/a" if x is None else f"{100 * x:.1f}%"


def render(report: Dict[str, Any]) -> str:
    out = []
    out.append(f"run {report['run_id']}  version {report.get('version')}  source {report.get('source')}")
    out.append(f"verdicts in run: {report['verdicts_in_run']}, matched by a label: "
               f"{report['verdicts_matched_by_any_scenario']}")
    blocks = [(r["scenario"], r) for r in report["scenarios"]] + [("TOTAL", report["total"])]
    for name, r in blocks:
        c = r["counts"]
        h = c["headline"]
        out.append("")
        out.append(f"[{name}]  labels matched {c['matched_verdicts']} "
                   f"(labelled incoherent {c['labelled_incoherent']}, coherent {c['labelled_coherent']})")
        out.append("  expected \\ observed  " + "  ".join(f"{o:>10s}" for o in OBSERVED))
        for e in EXPECTED:
            out.append(f"  {e:<19s}  " + "  ".join(f"{r['table'][e][o]:>10d}" for o in OBSERVED))
        out.append(f"  headline ({c['headline_rule']}): FP {h['fp']} of {h['fp_denominator']} "
                   f"({_fmt_rate(h['fp_rate'])}), FN {h['fn']} of {h['fn_denominator']} ({_fmt_rate(h['fn_rate'])}); "
                   f"TP {h['tp']}, TN {h['tn']}")
        a = c["abstention"]
        out.append(f"  abstention (UNCERTAIN): {a['uncertain']} of {c['matched_verdicts']} ({_fmt_rate(a['rate'])})")
        for key, alt in c["alternatives"].items():
            out.append(f"  alternative {key}: FP {alt['fp']}/{alt['fp_denominator']} ({_fmt_rate(alt['fp_rate'])}), "
                       f"FN {alt['fn']}/{alt['fn_denominator']} ({_fmt_rate(alt['fn_rate'])})")
        if name != "TOTAL":
            if r["labels_without_verdict"]:
                out.append(f"  labels no verdict reached: {len(r['labels_without_verdict'])} "
                           f"({', '.join(str(x['label']) for x in r['labels_without_verdict'])})")
            if r["unmatched_verdicts"]:
                out.append(f"  verdicts no label covers: {len(r['unmatched_verdicts'])}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FP and FN of a run against labelled scenarios (rates and raw counts).")
    ap.add_argument("--run", required=True, help="run directory (holds run.json and verdicts.jsonl)")
    ap.add_argument("--scenario", action="append", default=[], help="scenario TOML file (repeatable)")
    ap.add_argument("--scenario-dir", default=None, help="every *.toml in this directory")
    ap.add_argument("--out", default=None, help="write the full report as JSON here")
    ap.add_argument("--quiet", action="store_true", help="no text summary on stdout")
    args = ap.parse_args(argv)
    paths = list(args.scenario)
    if args.scenario_dir:
        paths += sorted(os.path.join(args.scenario_dir, f) for f in os.listdir(args.scenario_dir)
                        if f.endswith(".toml") and not f.startswith("TEMPLATE"))
    if not paths:
        print("no scenario given (--scenario FILE or --scenario-dir DIR)", file=sys.stderr)
        return 2
    try:
        report = compute(args.run, paths)
    except (ScenarioError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
            f.write("\n")
    if not args.quiet:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
